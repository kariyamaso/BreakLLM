# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import hashlib
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[3]
REPORT_ROOT = Path(
    os.environ.get("BREAKLLM_REPORT_ROOT", str(ROOT / "prompt-runs/comparison-ja"))
)


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=16000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(
        extra="ignore"
    )  # OpenUI also sends thread/run/context fields.
    messages: list[Message] = Field(min_length=1, max_length=24)
    method: str = "baseline"
    enable_tools: bool = False
    max_new_tokens: int = Field(default=384, ge=16, le=768)


@asynccontextmanager
async def lifespan(app):
    from .runtime import JAPANESE_SYSTEM, Runtime

    app.state.runtime = await asyncio.to_thread(
        Runtime,
        os.environ.get("BREAKLLM_MODEL", "Qwen/Qwen3-1.7B"),
        REPORT_ROOT / "methods",
        os.environ.get("BREAKLLM_DEVICE", "cuda"),
        os.environ.get("BREAKLLM_SYSTEM_PROMPT", JAPANESE_SYSTEM),
    )
    app.state.busy = asyncio.Lock()
    runtime = app.state.runtime
    app.state.audit_context = {
        "engine": runtime.engine.identity(),
        "methods": runtime.metadata,
        "generation": {"do_sample": False, "enable_tools": False},
    }
    app.state.audit_context_id = hashlib.sha256(
        json.dumps(app.state.audit_context, sort_keys=True).encode()
    ).hexdigest()
    yield


app = FastAPI(title="BreakLLM Research Chat", lifespan=lifespan)


@app.get("/api/health")
async def health():
    runtime = app.state.runtime
    return {
        "status": "ready",
        "model": runtime.model_id,
        "methods": list(runtime.methods),
        "busy": app.state.busy.locked(),
    }


@app.get("/api/methods")
async def methods():
    return {
        "model": app.state.runtime.model_id,
        "methods": [
            {
                "id": key,
                "label": key,
                "kind": "original" if key == "baseline" else value["kind"],
                "adaptation": value.get(
                    "adaptation", "Frozen-model continuous input prompt tuning."
                ),
            }
            for key, value in app.state.runtime.metadata.items()
        ],
    }


@app.get("/api/audit-context")
async def audit_context():
    return {"id": app.state.audit_context_id, **app.state.audit_context}


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
    if request.method not in app.state.runtime.methods:
        raise HTTPException(422, "指定した方式は利用できません。")
    if request.messages[-1].role != "user":
        raise HTTPException(422, "最後のメッセージには質問を指定してください。")
    if sum(len(m.content) for m in request.messages) > 20000:
        raise HTTPException(
            413, "会話が長くなりました。新しいチャットを開始してください。"
        )
    if app.state.busy.locked():
        raise HTTPException(
            429, "別の質問に回答中です。少し待ってから再度送信してください。"
        )


async def execute(request: ChatRequest):
    async with app.state.busy:
        work = asyncio.create_task(
            asyncio.to_thread(
                app.state.runtime.chat,
                [m.model_dump() for m in request.messages],
                request.method,
                request.enable_tools,
                request.max_new_tokens,
            )
        )
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            await work
            raise


@app.post("/api/chat/complete")
async def complete(request: ChatRequest):
    validate_chat(request)
    try:
        result = await execute(request)
        return {**result, "context_id": app.state.audit_context_id}
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
                        "model": app.state.runtime.model_id,
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


UI_ROOT = ROOT / "chat-ui/dist"
if UI_ROOT.is_dir():
    app.mount("/", StaticFiles(directory=UI_ROOT, html=True), name="ui")
