# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .attachments import MAX_ATTACHMENTS, ContentPart, flatten_content
from .registry import ModelRegistry, artifact_root_for, load_catalog, parse_model_list

ROOT = Path(__file__).resolve().parents[3]
REPORT_ROOT = Path(
    os.environ.get("BREAKLLM_REPORT_ROOT", str(ROOT / "prompt-runs/comparison-ja"))
)


class Message(BaseModel):
    role: Literal["user", "assistant"]
    # OpenAI chat-completions shape: plain text, or text/image/file parts.
    content: str | list[ContentPart] = Field(max_length=16000)

    @field_validator("content")
    @classmethod
    def limit_attachments(cls, value):
        if not isinstance(value, str):
            attached = sum(1 for part in value if part.type != "text")
            if attached > MAX_ATTACHMENTS:
                raise ValueError(f"添付は1メッセージあたり{MAX_ATTACHMENTS}件までです。")
        return value

    def text(self, supports_images: bool = False) -> str:
        return flatten_content(self.content, supports_images=supports_images)


class DefaultRuntime:
    """Resolve the default model through the registry on every use, so nothing
    keeps a direct reference and an evicted model's memory is actually freed."""

    def __init__(self, registry: ModelRegistry):
        self.registry = registry

    def __getattr__(self, name):
        return getattr(self.registry.get(self.registry.default_id), name)


class ChatRequest(BaseModel):
    model_config = ConfigDict(
        extra="ignore"
    )  # OpenUI also sends thread/run/context fields.
    messages: list[Message] = Field(min_length=1, max_length=24)
    model: Annotated[str | None, Field(max_length=200)] = None
    method: str = "baseline"
    enable_tools: bool = False
    assess_response: bool = False
    max_new_tokens: int = Field(default=384, ge=16, le=768)


@asynccontextmanager
async def lifespan(app):
    from .response_audit import AuditedChat
    from .runtime import JAPANESE_SYSTEM, Runtime

    default_model = os.environ.get("BREAKLLM_MODEL", "Qwen/Qwen3-1.7B")
    # Models come from BREAKLLM_MODELS and from the imported-model catalog
    # (models.json), which is where the weight-level decensoring script registers
    # its output so decensored checkpoints appear in the UI automatically.
    catalog = load_catalog(REPORT_ROOT / "models.json")
    model_ids = parse_model_list(os.environ.get("BREAKLLM_MODELS"), default_model)
    for catalog_id in catalog:
        if catalog_id not in model_ids:
            model_ids.append(catalog_id)
    device = os.environ.get("BREAKLLM_DEVICE", "cuda")
    system = os.environ.get("BREAKLLM_SYSTEM_PROMPT", JAPANESE_SYSTEM)

    def load(model_id: str):
        return Runtime(
            model_id, artifact_root_for(REPORT_ROOT, model_id, default_model), device, system
        )

    # The GPU is shared; keep one model resident unless configured otherwise.
    app.state.registry = ModelRegistry(
        model_ids, load, max_loaded=int(os.environ.get("BREAKLLM_MAX_LOADED", "1")),
        catalog=catalog,
    )
    await asyncio.to_thread(app.state.registry.get, default_model)
    app.state.runtime = DefaultRuntime(app.state.registry)
    app.state.jailbreak_chat = AuditedChat(app.state.runtime, REPORT_ROOT / "jailbreak")
    app.state.busy = asyncio.Lock()
    app.state.audit_context = app.state.registry.audit_context(default_model)
    app.state.audit_context_id = app.state.audit_context["id"]
    yield


app = FastAPI(title="BreakLLM Research Chat", lifespan=lifespan)


@app.get("/api/health")
async def health():
    registry = app.state.registry
    default = registry.default_id
    return {
        "status": "ready",
        "model": default,
        "models": registry.model_ids,
        "loaded_models": list(registry.loaded),
        "methods": sorted(registry.get(default).methods) if registry.is_loaded(default) else None,
        "busy": app.state.busy.locked(),
    }


@app.get("/api/models")
async def models():
    registry = app.state.registry
    return {"default": registry.default_id, "models": registry.describe()}


async def runtime_for(model: str | None):
    registry = app.state.registry
    try:
        model_id = registry.resolve(model)
    except ValueError as error:
        raise HTTPException(422, "指定したモデルは利用できません。") from error
    if registry.is_loaded(model_id):
        return registry.get(model_id)
    if app.state.busy.locked():
        raise HTTPException(
            429, "別の質問に回答中です。少し待ってから再度送信してください。"
        )
    async with app.state.busy:
        return await asyncio.to_thread(registry.get, model_id)


@app.get("/api/methods")
async def methods(model: str | None = None):
    runtime = await runtime_for(model)
    return {
        "model": runtime.model_id,
        "supports_images": app.state.registry.supports_images,
        "methods": [
            {
                "id": key,
                "label": key,
                "kind": "original" if key == "baseline" else value["kind"],
                "adaptation": value.get(
                    "adaptation", "Frozen-model continuous input prompt tuning."
                ),
            }
            for key, value in runtime.metadata.items()
        ],
    }


@app.get("/api/audit-context")
async def audit_context(model: str | None = None):
    if model is None:
        return {"id": app.state.audit_context_id, **app.state.audit_context}
    await runtime_for(model)
    return app.state.registry.audit_context(model)


@app.get("/reports/refusal-ja.html")
async def refusal_report():
    path = ROOT / "reports/refusal-ja.html"
    if not path.is_file():
        raise HTTPException(404, "拒否解除の検証レポートは準備中です。")
    return FileResponse(path, media_type="text/html")


@app.get("/api/refusal-report")
async def refusal_report_data():
    path = ROOT / "reports/refusal-ja.json"
    if not path.is_file():
        raise HTTPException(404, "拒否解除の検証レポートは準備中です。")
    return FileResponse(path, media_type="application/json")


@app.get("/reports/comparison.html")
async def report_html():
    path = REPORT_ROOT / "comparison.html"
    if not path.is_file():
        raise HTTPException(404, "Comparison report is not ready.")
    return FileResponse(path, media_type="text/html")


@app.get("/api/report")
async def report_data():
    path = REPORT_ROOT / "comparison.json"
    if not path.is_file():
        raise HTTPException(404, "Comparison report is not ready.")
    return FileResponse(path, media_type="application/json")


@app.get("/reports/comparison-zh.html")
async def archived_report():
    path = ROOT / "reports/comparison-zh.html"
    if not path.is_file():
        raise HTTPException(404, "Archived comparison report is not ready.")
    return FileResponse(path, media_type="text/html")


def validate_chat(request: ChatRequest):
    registry = app.state.registry
    try:
        model_id = registry.resolve(request.model)
    except ValueError as error:
        raise HTTPException(422, "指定したモデルは利用できません。") from error
    if registry.is_loaded(model_id) and request.method not in registry.get(model_id).methods:
        raise HTTPException(422, "指定した方式は利用できません。")
    if request.messages[-1].role != "user":
        raise HTTPException(422, "最後のメッセージには質問を指定してください。")
    if not request.messages[-1].text().strip():
        raise HTTPException(422, "質問または添付ファイルを指定してください。")
    if sum(len(m.text()) for m in request.messages) > 20000:
        raise HTTPException(
            413, "会話が長くなりました。新しいチャットを開始してください。"
        )
    if app.state.busy.locked():
        raise HTTPException(
            429, "別の質問に回答中です。少し待ってから再度送信してください。"
        )


def plain_messages(request: ChatRequest) -> list[dict]:
    supports_images = app.state.registry.supports_images
    return [
        {"role": m.role, "content": m.text(supports_images)} for m in request.messages
    ]


async def execute(request: ChatRequest):
    async with app.state.busy:
        runtime = await asyncio.to_thread(app.state.registry.get, request.model)
        if request.method not in runtime.methods:
            raise ValueError("指定した方式はこのモデルでは利用できません。")
        messages = plain_messages(request)
        work = asyncio.create_task(
            asyncio.to_thread(
                runtime.chat,
                messages,
                request.method,
                request.enable_tools,
                request.max_new_tokens,
            )
        )
        try:
            result = await asyncio.shield(work)
            result["model"] = runtime.model_id
            if request.assess_response:
                judgment = asyncio.create_task(asyncio.to_thread(
                    app.state.jailbreak_chat.judge.assess,
                    messages[-1]["content"], result["response"],
                ))
                try:
                    result["assessment"] = await asyncio.shield(judgment)
                except asyncio.CancelledError:
                    await judgment
                    raise
            return result
        except asyncio.CancelledError:
            await work
            raise


@app.post("/api/chat/complete")
async def complete(request: ChatRequest):
    validate_chat(request)
    try:
        result = await execute(request)
        context = app.state.registry.audit_context(request.model)
        return {**result, "context_id": context["id"]}
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.post("/api/chat")
async def chat(request: ChatRequest):
    validate_chat(request)

    async def stream():
        identifier = "chatcmpl-" + uuid.uuid4().hex

        def chunk(delta, finish=None):
            return (
                "data: "
                + json.dumps(
                    {
                        "id": identifier,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": app.state.registry.resolve(request.model),
                        "choices": [
                            {"index": 0, "delta": delta, "finish_reason": finish}
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )

        yield chunk({"role": "assistant"})
        task = asyncio.create_task(execute(request))
        try:
            while not task.done():
                done, _ = await asyncio.wait({task}, timeout=10)
                if not done:
                    yield ": working\n\n"
            result = task.result()
            content = result["response"]
            if "assessment" in result:
                judged = result["assessment"]
                label = {"refusal": "拒否を検出", "non_refusal": "非拒否・成功は未確認", "unknown": "判定不能"}[judged["status"]]
                content += f"\n\n---\n**LLMによる拒否判定：{label}**\n\n判定モデル：{judged['judge']}"
            if result["tools"]:
                evidence = "\n\n---\n**実行したツール**\n"
                for tool in result["tools"]:
                    payload = tool.get("result", {})
                    if tool["name"] == "calculate" and "value" in payload:
                        evidence += f"\n計算結果：**{payload['value']:g}**\n"
                    elif tool["name"] == "search_wikipedia":
                        evidence += "\n取得した出典：\n"
                        for source in payload.get("results", []):
                            title = (
                                source["title"]
                                .replace("[", "")
                                .replace("]", "")
                                .replace("\n", " ")
                            )
                            evidence += f"- [{title}]({source['url']})\n"
                    elif tool["name"] == "search_reports" and "matches" in payload:
                        evidence += (
                            "\n[実測応答の比較レポート](/reports/comparison.html)\n"
                        )
                    evidence += f"\n`{tool['name']}`\n```json\n{json.dumps(tool, ensure_ascii=False, indent=2)}\n```\n"
                content += evidence
            # Preserve one completed tool loop as a single assistant response.
            for start in range(0, len(content), 100):
                yield chunk({"content": content[start : start + 100]})
            yield chunk({}, "stop")
        except (ValueError, RuntimeError, OSError) as error:
            yield chunk({"content": f"処理を完了できませんでした: {str(error)[:240]}"})
            yield chunk({}, "stop")
        finally:
            # Keep the model lock until an in-flight GPU operation actually exits.
            if not task.done():
                await asyncio.shield(task)
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class JailbreakChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=24)
    max_attempts: int = Field(default=3, ge=1, le=10)
    max_new_tokens: int = Field(default=512, ge=16, le=768)
    # Ordered prompt methods to escalate through when the baseline is refused;
    # null uses the default order. Only methods the model has loaded are used.
    escalation: list[str] | None = None


@app.post("/api/jailbreak/chat")
async def jailbreak_chat(request: JailbreakChatRequest):
    """Answer once at baseline; if the judge calls it a refusal, escalate through
    the model's pre-trained prompt interventions until one is not refused."""
    from .response_audit import escalation_for

    validate_chat(ChatRequest(messages=request.messages))
    user_input = request.messages[-1].text()
    jailbreak = app.state.jailbreak_chat
    runtime = await runtime_for(None)
    escalation = escalation_for(runtime, request.escalation)
    async with app.state.busy:
        task = asyncio.create_task(
            asyncio.to_thread(jailbreak.chat, user_input, request.max_new_tokens, escalation)
        )
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise


class ResponseAssessmentRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=16000)
    response: str = Field(min_length=1, max_length=24000)


@app.post("/api/response-assessment")
async def response_assessment(request: ResponseAssessmentRequest):
    return await asyncio.to_thread(app.state.jailbreak_chat.judge.assess, request.prompt, request.response)


@app.get("/api/safety-report")
async def safety_report():
    return FileResponse(ROOT / "reports/safety-ja/summary.json", media_type="application/json")


@app.get("/reports/safety.html")
async def safety_report_html():
    return FileResponse(ROOT / "reports/safety-ja/dashboard.html", media_type="text/html")


@app.get("/api/verified-examples")
async def verified_examples():
    path = ROOT / "reports/verified-examples.json"
    if not path.is_file():
        return {"examples": [], "verified_success_count": 0}
    return FileResponse(path, media_type="application/json")


@app.get("/api/jailbreak/status")
async def jailbreak_status():
    """Get jailbreak optimization statistics."""
    jailbreak = app.state.jailbreak_chat
    return jailbreak.get_stats()


@app.get("/api/jailbreak/history")
async def jailbreak_history():
    """Get jailbreak optimization history."""
    jailbreak = app.state.jailbreak_chat
    return jailbreak.get_history()


UI_ROOT = ROOT / "chat-ui/dist"
if UI_ROOT.is_dir():
    app.mount("/", StaticFiles(directory=UI_ROOT, html=True), name="ui")
