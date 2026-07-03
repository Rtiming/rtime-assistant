# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Internal attachment-send directives for Feishu output rendering."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

DIRECTIVE_RE = re.compile(r"\[\[rtime-send-(file|image):([^\]\n]+)\]\]")
IMAGE_SUFFIXES = {
    ".bmp",
    ".gif",
    ".heic",
    ".ico",
    ".jpeg",
    ".jpg",
    ".png",
    ".tiff",
    ".webp",
}
DENIED_COMPONENTS = {
    ".aws",
    ".claude",
    ".codex",
    ".config",
    ".docker",
    ".gnupg",
    ".lark-channel",
    ".ssh",
}
DENIED_SUFFIXES = {".env", ".key", ".pem", ".p12", ".pfx"}
DENIED_NAME_RE = re.compile(
    r"(?:api[_-]?key|app[_-]?secret|credential|password|secret|token)", re.I
)


@dataclass(frozen=True)
class AttachmentDirective:
    kind: str
    path: str


@dataclass(frozen=True)
class AttachmentValidation:
    ok: bool
    path: str = ""
    reason: str = ""
    size: int = 0


def extract_attachment_directives(text: str) -> tuple[str, list[AttachmentDirective]]:
    """Strip internal attachment directives and return them in output order."""
    directives: list[AttachmentDirective] = []

    def replace(match: re.Match[str]) -> str:
        directives.append(
            AttachmentDirective(kind=match.group(1), path=match.group(2).strip())
        )
        return ""

    cleaned = DIRECTIVE_RE.sub(replace, text or "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, directives


def validate_attachment_path(
    raw_path: str,
    *,
    base_dir: str | None = None,
    kind: str = "file",
    max_bytes: int = 30 * 1024 * 1024,
) -> AttachmentValidation:
    """Validate a local path before sending it back to Feishu."""
    value = (raw_path or "").strip().strip("\"'")
    if not value:
        return AttachmentValidation(False, reason="附件路径为空")

    candidate = Path(os.path.expanduser(value))
    if not candidate.is_absolute():
        root = Path(os.path.expanduser(base_dir or os.getcwd()))
        candidate = root / candidate

    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        return AttachmentValidation(False, reason=f"文件不存在：{value}")
    except OSError as exc:
        return AttachmentValidation(False, reason=f"无法解析文件路径：{exc}")

    if not resolved.is_file():
        return AttachmentValidation(
            False, path=str(resolved), reason="路径不是普通文件"
        )

    lowered_parts = {part.lower() for part in resolved.parts}
    if lowered_parts & DENIED_COMPONENTS:
        return AttachmentValidation(
            False, path=str(resolved), reason="路径位于敏感配置目录，已拒绝发送"
        )

    name = resolved.name
    suffix = resolved.suffix.lower()
    if suffix in DENIED_SUFFIXES or DENIED_NAME_RE.search(name):
        return AttachmentValidation(
            False, path=str(resolved), reason="文件名疑似包含密钥或凭据，已拒绝发送"
        )

    if kind == "image" and suffix not in IMAGE_SUFFIXES:
        return AttachmentValidation(
            False, path=str(resolved), reason="图片发送只接受常见图片格式"
        )

    try:
        size = resolved.stat().st_size
    except OSError as exc:
        return AttachmentValidation(
            False, path=str(resolved), reason=f"无法读取文件大小：{exc}"
        )

    if size <= 0:
        return AttachmentValidation(False, path=str(resolved), reason="文件为空")
    if size > max_bytes:
        mb = max_bytes // (1024 * 1024)
        return AttachmentValidation(
            False, path=str(resolved), reason=f"文件超过 {mb}MB 上限"
        )

    return AttachmentValidation(True, path=str(resolved), size=size)
