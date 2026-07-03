# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""OpenAI/Moonshot 兼容 provider 的 HTTP 调用(上传/抽取/对话/流式)。共享件来自 _common 与 models。"""
from __future__ import annotations

import base64
import binascii
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from _common import (
    IMAGE_ATTACHMENT_MIME_PREFIXES,
    MAX_FILE_ATTACHMENT_BYTES,
    MOONSHOT_FILE_EXTRACT_KINDS,
    _read_secret,
    _safe_attachment_name,
)
from models import model_selection_supports_file_extract, model_selection_supports_images
import rtime_models


def openai_message_content(prompt: str, attachments) -> str | list[dict]:
    content: list[dict] = [{"type": "text", "text": prompt}]
    count = 0
    if isinstance(attachments, list):
        for item in attachments[:8]:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "")
            mime = str(item.get("content_media_type") or item.get("mime") or "image/png")
            if kind != "image" and not mime.startswith(IMAGE_ATTACHMENT_MIME_PREFIXES):
                continue
            encoded = item.get("content_base64")
            data_url = item.get("preview_data_url")
            if isinstance(encoded, str) and encoded:
                data_url = f"data:{mime};base64,{encoded}"
            if not isinstance(data_url, str) or not data_url.startswith("data:image/"):
                continue
            content.append({"type": "image_url", "image_url": {"url": data_url}})
            count += 1
            if count >= 4:
                break
    return content if count else prompt


def _openai_secret_for_provider(provider_id: str, cfg: dict) -> str:
    """Read the provider secret from env/keyfile. The candidate env var names come
    from the registry (secret_env_names): ``*_FILE`` names are keyfile paths, the
    rest are direct values. An optional registry secret_file_cfg_key names a cfg
    default keyfile path to try too (the gateway's RTIME_USTC_API_KEY_FILE default)."""
    names = rtime_models.secret_env_names(provider_id)
    if not names:
        return ""
    direct = [n for n in names if not n.endswith("_FILE")]
    files = [n for n in names if n.endswith("_FILE")]
    literal_files = None
    cfg_key = rtime_models.secret_file_cfg_key(provider_id)
    if cfg_key:
        literal_files = [cfg.get(cfg_key)]
    return _read_secret(direct, files, literal_files)


def _multipart_form_data(fields: dict[str, str], files: list[tuple[str, str, str, bytes]]) -> tuple[bytes, str]:
    boundary = f"----rtime-assistant-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    for field_name, filename, content_type, data in files:
        safe_filename = _safe_attachment_name(filename, "attachment")
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="{field_name}"; '
                    f'filename="{safe_filename}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {content_type or 'application/octet-stream'}\r\n\r\n".encode("utf-8"),
                data,
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _attachment_bytes(item: dict) -> bytes | None:
    encoded = item.get("content_base64")
    if isinstance(encoded, str) and encoded:
        try:
            return base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return None
    path = item.get("path")
    if isinstance(path, str) and path:
        try:
            file_path = Path(path)
            if file_path.is_file() and file_path.stat().st_size <= MAX_FILE_ATTACHMENT_BYTES:
                return file_path.read_bytes()
        except OSError:
            return None
    return None


def _moonshot_upload_file(base_url: str, token: str, name: str, mime: str, data: bytes, timeout: float) -> str:
    body, content_type = _multipart_form_data(
        {"purpose": "file-extract"},
        [("file", name, mime or "application/octet-stream", data)],
    )
    request = urllib.request.Request(
        f"{base_url}/files",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:240]
        raise RuntimeError(f"moonshot file upload failed with HTTP {exc.code}: {detail}") from None
    file_id = payload.get("id")
    if not isinstance(file_id, str) or not file_id:
        raise RuntimeError("moonshot file upload response missing file id")
    return file_id


def _moonshot_file_content(base_url: str, token: str, file_id: str, timeout: float) -> str:
    request = urllib.request.Request(
        f"{base_url}/files/{file_id}/content",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:240]
        raise RuntimeError(f"moonshot file content failed with HTTP {exc.code}: {detail}") from None


def moonshot_file_extract_messages(attachments, cfg: dict, token: str, base_url: str) -> list[dict]:
    if not isinstance(attachments, list):
        return []
    max_files = max(0, int(cfg.get("file_extract_max_files", 4)))
    max_chars = max(1000, int(cfg.get("file_extract_max_chars", 80000)))
    timeout = float(cfg.get("claude_timeout", 110))
    messages: list[dict] = []
    for item in attachments[:8]:
        if len(messages) >= max_files:
            break
        if not isinstance(item, dict) or item.get("status") == "error":
            continue
        kind = str(item.get("kind") or "")
        mime = str(item.get("content_media_type") or item.get("mime") or "application/octet-stream")
        if kind == "image" or mime.startswith(IMAGE_ATTACHMENT_MIME_PREFIXES):
            continue
        if kind not in MOONSHOT_FILE_EXTRACT_KINDS:
            continue
        data = _attachment_bytes(item)
        if not data:
            continue
        if len(data) > MAX_FILE_ATTACHMENT_BYTES:
            item["status"] = "error"
            item["error"] = "file exceeds inline attachment limit"
            continue
        name = str(item.get("name") or "attachment")
        file_id = _moonshot_upload_file(base_url, token, name, mime, data, timeout)
        content = _moonshot_file_content(base_url, token, file_id, timeout).strip()
        if len(content) > max_chars:
            content = content[:max_chars].rstrip() + "\n[attachment content truncated]"
        item["file_extract_status"] = "ok"
        item["file_extract_chars"] = len(content)
        messages.append(
            {
                "role": "system",
                "content": f"用户本轮附件 {name} ({kind}) 的抽取内容如下：\n{content}",
            }
        )
    return messages


def run_openai_chat(prompt: str, cfg: dict, model_selection: dict, attachments=None) -> str:
    provider_id = str(model_selection.get("provider_id") or "")
    request = _openai_chat_request(prompt, cfg, model_selection, attachments, stream=False)
    try:
        with urllib.request.urlopen(request, timeout=float(cfg.get("claude_timeout", 110))) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:240]
        raise RuntimeError(f"{provider_id} request failed with HTTP {exc.code}: {detail}") from None
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        text = "\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict)).strip()
    else:
        text = str(content or "").strip()
    if not text:
        raise RuntimeError(f"{provider_id} response did not include assistant content")
    return text


def _openai_chat_request(prompt: str, cfg: dict, model_selection: dict, attachments, stream: bool) -> urllib.request.Request:
    """Build the chat-completions request shared by the blocking and streaming
    paths so they can never drift in message construction."""
    provider_id = str(model_selection.get("provider_id") or "")
    token = _openai_secret_for_provider(provider_id, cfg)
    if not token:
        raise RuntimeError(f"{provider_id} API key is not configured")
    base_url = str(model_selection.get("base_url") or "").rstrip("/")
    if not base_url:
        raise RuntimeError(f"{provider_id} base URL is not configured")
    messages = [
        {"role": "system", "content": "你是rtime Obsidian助手。本次为chat-only模型调用，不能使用本地文件工具。"},
    ]
    if model_selection_supports_file_extract(model_selection):
        messages.extend(moonshot_file_extract_messages(attachments, cfg, token, base_url))
    messages.append(
        {"role": "user", "content": openai_message_content(prompt, attachments) if model_selection_supports_images(model_selection) else prompt}
    )
    payload = {
        "model": model_selection["model_id"],
        "messages": messages,
        "stream": stream,
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if stream:
        headers["Accept"] = "text/event-stream"
    return urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )


def stream_openai_chat(prompt: str, cfg: dict, model_selection: dict, attachments=None):
    """Yield assistant text pieces from an OpenAI-compatible chat-completions
    SSE stream (Moonshot/USTC support it), so first-token shows in ~1-2s instead
    of blocking for the whole completion. Raises on setup/HTTP errors before any
    piece is produced; the caller falls back to the blocking path when nothing
    was streamed."""
    provider_id = str(model_selection.get("provider_id") or "")
    request = _openai_chat_request(prompt, cfg, model_selection, attachments, stream=True)
    try:
        resp = urllib.request.urlopen(request, timeout=float(cfg.get("claude_timeout", 110)))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:240]
        raise RuntimeError(f"{provider_id} request failed with HTTP {exc.code}: {detail}") from None
    with resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            choice = (obj.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            piece = delta.get("content")
            if isinstance(piece, list):
                piece = "".join(str(part.get("text") or "") for part in piece if isinstance(part, dict))
            if piece:
                yield str(piece)
