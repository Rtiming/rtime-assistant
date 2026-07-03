# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""附件处理:检测图片/文件/压缩包附件、构造附件提示段、落地图片附件。共享件来自 _common。"""
from __future__ import annotations

import base64
import binascii
import uuid
from pathlib import Path

from _common import (
    ARCHIVE_ATTACHMENT_KINDS,
    FILE_ATTACHMENT_KINDS,
    IMAGE_ATTACHMENT_MIME_PREFIXES,
    MAX_FILE_ATTACHMENT_BYTES,
    MAX_IMAGE_ATTACHMENT_BYTES,
    TOOL_ATTACHMENT_KINDS,
    _safe_attachment_name,
)


def build_attachments_section(attachments, *, full_access: bool = False) -> str | None:
    if not isinstance(attachments, list) or not attachments:
        return None
    if full_access:
        lines = [
            "用户随本轮请求附带的文件（可按用户明确请求用于整理、入库或生成伴生材料；"
            "写入长期目录时遵守_inbox ticket、sha256去重、敏感确认和入库规范）："
        ]
    else:
        lines = ["用户随本轮请求附带的文件（只作本轮上下文；不得自动写入长期记忆或最终知识目录）："]
    for item in attachments[:8]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "unnamed")
        kind = str(item.get("kind") or "unknown")
        size = item.get("size") if isinstance(item.get("size"), int) else None
        line = f"- {name} ({kind}, {size or 0} bytes, 本轮临时附件)"
        if item.get("path"):
            line += f" path={item.get('path')}"
        lines.append(line)
        extracted = item.get("extracted_text")
        if isinstance(extracted, str) and extracted.strip():
            lines.append(f"  文本摘录：{extracted[:1200]}")
        if kind == "image" and item.get("path"):
            lines.append(f"  图片内容：本轮临时文件，可按需使用Read读取：{item.get('path')}")
        elif kind == "image" and item.get("content_base64"):
            lines.append("  图片内容：已随请求提供base64内容；若当前模型支持视觉输入，应直接查看图片。")
        elif kind == "image" and item.get("preview_data_url"):
            lines.append("  图片预览：仅有预览data URL；当前请求未提供可直接读取的图片内容。")
        elif kind == "archive" and item.get("path"):
            lines.append(
                "  压缩包内容：本轮临时文件；先用 `unzip -l`/`bsdtar -tf` 或 Python zipfile "
                f"生成清单，再按用户要求复制到_inbox或临时展开目录处理：{item.get('path')}"
            )
        elif kind == "archive" and item.get("content_base64"):
            lines.append("  压缩包内容：已随请求提供base64；gateway会转为本轮临时zip文件供工具模型检查。")
        elif kind in FILE_ATTACHMENT_KINDS and item.get("path"):
            lines.append(f"  文件内容：本轮临时文件，可按需使用Read读取：{item.get('path')}")
        elif kind in FILE_ATTACHMENT_KINDS and item.get("content_base64"):
            lines.append("  文件内容：已随请求提供base64内容；gateway会按模型能力转为临时文件或文件抽取上下文。")
    return "\n".join(lines) if len(lines) > 1 else None


def request_has_image_attachments(body: dict) -> bool:
    attachments = ((body.get("context") or {}).get("attachments") or [])
    if not isinstance(attachments, list):
        return False
    for item in attachments:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "")
        mime = str(item.get("mime") or item.get("content_media_type") or "")
        if kind == "image" or mime.startswith(IMAGE_ATTACHMENT_MIME_PREFIXES):
            return True
    return False


def request_has_file_attachments(body: dict) -> bool:
    attachments = ((body.get("context") or {}).get("attachments") or [])
    if not isinstance(attachments, list):
        return False
    for item in attachments:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "")
        mime = str(item.get("mime") or item.get("content_media_type") or "")
        if kind == "image" or mime.startswith(IMAGE_ATTACHMENT_MIME_PREFIXES):
            continue
        if kind in TOOL_ATTACHMENT_KINDS:
            return True
    return False


def request_has_archive_attachments(body: dict) -> bool:
    attachments = ((body.get("context") or {}).get("attachments") or [])
    if not isinstance(attachments, list):
        return False
    for item in attachments:
        if not isinstance(item, dict):
            continue
        if str(item.get("kind") or "") in ARCHIVE_ATTACHMENT_KINDS:
            return True
    return False


def materialize_image_attachments(body: dict, cfg: dict) -> Path | None:
    """Decode inline attachments into per-request temp files.

    Historical name kept for tests/callers. Images and binary document
    attachments use the same request-scoped temp directory so the default
    tool-model path stays model-driven: the model can decide whether to call
    Read on a provided path. Temp files are removed after the model run.
    """
    context = body.get("context") or {}
    attachments = context.get("attachments")
    if not isinstance(attachments, list):
        return None
    tmp_dir: Path | None = None
    for index, item in enumerate(attachments[:8]):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "")
        mime = str(item.get("content_media_type") or item.get("mime") or "")
        is_image = kind == "image" or mime.startswith(IMAGE_ATTACHMENT_MIME_PREFIXES)
        if not is_image and kind not in TOOL_ATTACHMENT_KINDS:
            continue
        encoded = item.get("content_base64")
        if not isinstance(encoded, str) or not encoded:
            continue
        max_bytes = MAX_IMAGE_ATTACHMENT_BYTES if is_image else MAX_FILE_ATTACHMENT_BYTES
        label = "image" if is_image else "file"
        if len(encoded) * 3 // 4 > max_bytes:
            item["status"] = "error"
            item["error"] = f"{label} exceeds inline attachment limit"
            continue
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            item["status"] = "error"
            item["error"] = f"{label} content_base64 is invalid"
            continue
        if len(data) > max_bytes:
            item["status"] = "error"
            item["error"] = f"{label} exceeds inline attachment limit"
            continue
        if tmp_dir is None:
            tmp_dir = Path(cfg["log_dir"]) / "tmp-chat-attachments" / f"chat-{uuid.uuid4().hex[:12]}"
            tmp_dir.mkdir(parents=True, exist_ok=True)
        raw_name = str(item.get("name") or f"attachment-{index + 1}")
        safe_name = _safe_attachment_name(raw_name, f"attachment-{index + 1}")
        path = tmp_dir / safe_name
        if path.exists():
            stem = path.stem or f"attachment-{index + 1}"
            suffix = path.suffix or ""
            path = tmp_dir / f"{stem}-{index + 1}{suffix}"
        path.write_bytes(data)
        item["path"] = str(path)
        item["content_available"] = True
    return tmp_dir
