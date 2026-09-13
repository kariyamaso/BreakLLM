# SPDX-License-Identifier: AGPL-3.0-or-later
"""Convert OpenAI-style multipart user content into text for a text-only model.

Text-like attachments are inlined verbatim (bounded), images become a labelled
marker so the model and the reader both know an image was attached but not read.
"""
import base64
import binascii
from typing import Annotated, Literal

from pydantic import BaseModel, Field

MAX_FILE_CHARS = 12000
MAX_ATTACHMENTS = 5
TEXT_MIME_PREFIXES = ("text/",)
TEXT_MIME_TYPES = {
    "application/json",
    "application/xml",
    "application/x-yaml",
    "application/yaml",
    "application/toml",
    "application/javascript",
    "application/typescript",
    "application/x-python",
    "application/x-sh",
    "application/csv",
    "application/sql",
}
TEXT_EXTENSIONS = {
    "txt", "md", "markdown", "json", "jsonl", "csv", "tsv", "yaml", "yml",
    "toml", "xml", "html", "htm", "py", "js", "ts", "tsx", "jsx", "sh", "sql",
    "c", "cc", "cpp", "h", "hpp", "java", "go", "rs", "rb", "php", "tex",
    "log", "ini", "cfg", "conf", "env", "diff", "patch",
}


class TextPart(BaseModel):
    type: Literal["text"]
    text: str = Field(max_length=16000)


class ImageUrl(BaseModel):
    url: str = Field(max_length=6_000_000)  # data: URL, roughly 4 MB of image
    detail: str | None = None


class ImagePart(BaseModel):
    type: Literal["image_url"]
    image_url: ImageUrl
    filename: str | None = Field(default=None, max_length=255)


class FileData(BaseModel):
    filename: str = Field(max_length=255)
    file_data: str = Field(max_length=3_000_000)  # data: URL, roughly 2 MB
    mime_type: str | None = Field(default=None, max_length=120)


class FilePart(BaseModel):
    type: Literal["file"]
    file: FileData


ContentPart = Annotated[TextPart | ImagePart | FilePart, Field(discriminator="type")]


def split_data_url(value: str) -> tuple[str, bytes]:
    if not value.startswith("data:"):
        raise ValueError("Attachments must be sent as data: URLs.")
    header, _, payload = value[5:].partition(",")
    mime = header.split(";")[0] or "application/octet-stream"
    if ";base64" not in header:
        return mime, payload.encode("utf-8")
    try:
        return mime, base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("Attachment payload is not valid base64.") from error


def is_text_like(mime: str, filename: str) -> bool:
    if mime.startswith(TEXT_MIME_PREFIXES) or mime in TEXT_MIME_TYPES:
        return True
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return extension in TEXT_EXTENSIONS


def render_file(part: FilePart) -> str:
    name = part.file.filename or "attachment"
    try:
        mime, payload = split_data_url(part.file.file_data)
    except ValueError as error:
        return f"[添付ファイル: {name}]（読み込めませんでした: {error}）"
    mime = part.file.mime_type or mime
    if not is_text_like(mime, name):
        size = len(payload)
        return (
            f"[添付ファイル: {name}（{mime}、{size:,} バイト）]"
            "（このモデルはテキスト以外のファイル内容を読み取れません。）"
        )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return f"[添付ファイル: {name}]（UTF-8 のテキストとして読み込めませんでした。）"
    truncated = ""
    if len(text) > MAX_FILE_CHARS:
        text = text[:MAX_FILE_CHARS]
        truncated = f"\n…（{MAX_FILE_CHARS:,} 文字で打ち切り）"
    fence = "````" if "```" in text else "```"
    return f"[添付ファイル: {name}]\n{fence}\n{text}{truncated}\n{fence}"


def render_image(part: ImagePart, supports_images: bool) -> str:
    name = part.filename or "image"
    if supports_images:
        return f"[画像: {name}]"
    return f"[添付画像: {name}]（このモデルは画像の内容を読み取れません。ファイル名のみ渡しています。）"


def flatten_content(content, *, supports_images: bool = False) -> str:
    if isinstance(content, str):
        return content
    texts: list[str] = []
    attachments: list[str] = []
    for part in content:
        if isinstance(part, TextPart):
            texts.append(part.text)
        elif isinstance(part, ImagePart):
            attachments.append(render_image(part, supports_images))
        elif isinstance(part, FilePart):
            attachments.append(render_file(part))
    body = "\n".join(text for text in texts if text)
    if not attachments:
        return body
    joined = "\n\n".join(attachments)
    return f"{body}\n\n{joined}" if body else joined
