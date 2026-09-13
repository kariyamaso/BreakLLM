# SPDX-License-Identifier: AGPL-3.0-or-later
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from heretic.prompt_lab.data import Template
from heretic.prompt_lab.engine import PromptEngine, load_artifact

JAPANESE_SYSTEM = "特に言語の指定がなければ、日本語で回答してください。"
log = logging.getLogger(__name__)


class ChatEngine(PromptEngine):
    def serialize(self, messages: list[dict], *, tools: list | None = None) -> str:
        messages = [dict(message) for message in messages]
        if self.system:
            if messages and messages[0]["role"] == "system":
                if not messages[0]["content"].startswith(self.system):
                    messages[0]["content"] = self.system + "\n" + messages[0]["content"]
            else:
                messages.insert(0, {"role": "system", "content": self.system})
        options = {
            "tokenize": False,
            "add_generation_prompt": True,
            "enable_thinking": False,
        }
        if tools:
            options["tools"] = tools
        return self.tokenizer.apply_chat_template(messages, **options)

    def encode_prompt(self, prompt: str, template: Template) -> list[int]:
        messages = []
        if self.system:
            messages.append({"role": "system", "content": self.system})
        messages.append({"role": "user", "content": template.render(prompt)})
        return self.tokenizer.encode(self.serialize(messages), add_special_tokens=False)

    def identity(self) -> dict:
        return dict(super().identity(), enable_thinking=False)


class Runtime:
    def __init__(
        self, model_id: str, artifact_root: Path, device: str = "cuda", system: str = ""
    ):
        self.model_id = model_id
        self.artifact_root = artifact_root
        self.lock = threading.Lock()
        tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=False)
        if tokenizer is None:
            raise ValueError("The model did not provide a tokenizer.")
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token
        dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
        # AutoModel chooses a concrete class dynamically; its decorated .to()
        # currently has an incompatible static signature in Transformers 5.10.
        model: Any = AutoModelForCausalLM.from_pretrained(
            model_id, dtype=dtype, trust_remote_code=False, attn_implementation="eager"
        )
        model = model.to(device)
        self.engine = ChatEngine(
            model, tokenizer, model_id=model_id, max_length=4096, system=system
        )
        self.methods = {"baseline": (Template(), None)}
        self.metadata = {"baseline": {"label": "Original", "method": "baseline"}}
        if artifact_root.is_dir():
            for directory in sorted(artifact_root.iterdir()):
                if (directory / "artifact.json").is_file():
                    try:
                        metadata, soft = load_artifact(directory, self.engine)
                    except ValueError as error:
                        # Artifacts are trained per model; skip ones from another model.
                        log.warning("Skipping artifact %s for %s: %s", directory.name, model_id, error)
                        continue
                    self.methods[directory.name] = (
                        Template(**metadata["template"]),
                        soft,
                    )
                    self.metadata[directory.name] = metadata

    @torch.no_grad()
    def complete(
        self, messages: list[dict], method: str, *, tools=None, max_new_tokens=384,
        return_metadata=False,
    ):
        if method not in self.methods:
            raise ValueError(f"Unknown method: {method}")
        template, soft = self.methods[method]
        messages = [dict(m) for m in messages]
        for message in reversed(messages):
            if message["role"] == "user":
                message["content"] = template.render(message["content"])
                break
        ids = self.engine.tokenizer.encode(
            self.engine.serialize(messages, tools=tools), add_special_tokens=False
        )
        self.engine._check_length(
            len(ids) + (0 if soft is None else len(soft)) + max_new_tokens
        )
        inputs = self.engine._embeddings(ids, soft)
        output = self.engine.model.generate(
            inputs_embeds=inputs,
            attention_mask=torch.ones(
                inputs.shape[:2], dtype=torch.long, device=inputs.device
            ),
            max_new_tokens=max_new_tokens,
            do_sample=False,
            pad_token_id=self.engine.tokenizer.pad_token_id,
            eos_token_id=self.engine.model.generation_config.eos_token_id,
            use_cache=True,
        )
        response = self.engine.tokenizer.decode(output[0], skip_special_tokens=True).strip()
        if return_metadata:
            eos = self.engine.model.generation_config.eos_token_id
            eos_ids = set(eos if isinstance(eos, list) else [eos])
            tokens = output[0].tolist()
            return {"response": response, "generated_tokens": len(tokens),
                    "finish_reason": "eos" if any(token in eos_ids for token in tokens) else "length"}
        return response

    def chat(
        self, messages: list[dict], method: str, use_tools: bool, max_new_tokens: int
    ) -> dict:
        from .tools import TOOL_SCHEMAS, execute_tool

        start = time.monotonic()
        trace = []
        with self.lock:
            conversation = [dict(m) for m in messages]
            if use_tools:
                conversation.insert(
                    0,
                    {
                        "role": "system",
                        "content": (
                            "Use the available tools when needed for calculation, current time, public encyclopedia "
                            "search, or experiment results. Never invent a tool result. After tool results, answer "
                            "the user in their language and cite returned sources."
                        ),
                    },
                )
            for step in range(3):
                schemas = TOOL_SCHEMAS if use_tools and step < 2 else None
                response = self.complete(
                    conversation, method, tools=schemas, max_new_tokens=max_new_tokens
                )
                matches = re.findall(
                    r"<tool_call>\s*(.*?)\s*</tool_call>", response, re.DOTALL
                )
                if not schemas or not matches:
                    break
                conversation.append({"role": "assistant", "content": response})
                for call in matches[:2]:
                    try:
                        payload = json.loads(call)
                        arguments = payload.get("arguments", {})
                        if isinstance(arguments, str):
                            arguments = json.loads(arguments)
                        result = execute_tool(
                            payload["name"], arguments, self.artifact_root.parent
                        )
                        trace.append(
                            {
                                "name": payload["name"],
                                "arguments": arguments,
                                "result": result,
                            }
                        )
                    except (ValueError, KeyError, TypeError) as error:
                        result = {"error": str(error)}
                        trace.append({"name": "invalid_call", "result": result})
                    conversation.append(
                        {
                            "role": "tool",
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )
        return {
            "response": response,
            "method": method,
            "tools": trace,
            "seconds": time.monotonic() - start,
        }
