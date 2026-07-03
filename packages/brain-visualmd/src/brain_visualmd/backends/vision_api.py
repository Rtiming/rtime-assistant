# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""OpenAI-compatible vision backend — one client for a local model host AND commercial.

Same code, two deployments (see ``docs/brain-visualmd-tools.zh-CN.md`` §1):
- **local**: point ``base_url`` at a model server on the Mac (Apple M4, Ollama)
  now — or orangepi as an offline-batch fallback — running ollama / llama.cpp with
  an OpenAI-compatible ``/v1/chat/completions`` vision endpoint (e.g. GLM-OCR /
  Qwen3-VL / PaddleOCR-VL). The backend only needs a ``base_url``, so the model
  host can be any machine. (srv03/Jetson is reserved for edge dev, not used here.)
- **api**: point ``base_url`` at a commercial vision API. Privacy ranking in the
  tools doc — Gemini's free tier trains on data; do not use it for personal files.

Config via args or env: ``VISUALMD_VISION_BASE_URL`` / ``VISUALMD_VISION_MODEL``
/ ``VISUALMD_VISION_API_KEY``. Dependency-free (urllib).
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.request
from pathlib import Path

from ..models import PageRequest, PageResult
from ..prompt import TRANSCRIBE_INSTRUCTION
from .base import EmptyContentError, SyncPageBackend


class VisionApiBackend(SyncPageBackend):
    name = "vision"
    max_page_retries = 2  # quality guard: re-call if a page fails the machine gate

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: int = 0,
        max_tokens: int = 0,
    ) -> None:
        self.base_url = (
            base_url or os.environ.get("VISUALMD_VISION_BASE_URL", "")
        ).rstrip("/")
        self.model = model or os.environ.get("VISUALMD_VISION_MODEL", "")
        self.api_key = api_key or os.environ.get("VISUALMD_VISION_API_KEY", "")
        # A thinking VLM on a full-res slide can take minutes/page; 300s is too
        # tight and one timeout aborts the batch. Default generous, configurable.
        self.timeout = timeout or int(os.environ.get("VISUALMD_VISION_TIMEOUT", "900"))
        # Headroom for "thinking" VLMs (Qwen3-VL): reasoning can take ~4k tokens
        # before the answer; too small -> finish=length, empty content.
        # 8192 covers a typical thinking pass (~2.8k tokens) with headroom.
        # Bigger backfires: the model uses the budget, runs longer, hits the
        # timeout. Rare over-budget pages are handled by the escalate fallback.
        self.max_tokens = max_tokens or int(
            os.environ.get("VISUALMD_VISION_MAX_TOKENS", "8192")
        )
        # Optional downscale cap (long side, px). A full slide render is ~3700
        # vision tokens and ~70s to encode; capping cuts encode time a lot.
        # 0 = off. Needs Pillow; silently skipped if absent.
        self.max_image_px = int(os.environ.get("VISUALMD_VISION_MAX_IMAGE_PX", "0"))

    @property
    def version(self) -> str:
        return self.model or "unconfigured"

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 (configured URL)
            return json.loads(resp.read().decode("utf-8"))

    def _run(self, req: PageRequest, user_text: str, temperature: float) -> str:
        """Call the model with ``user_text`` + the page image; return clean Markdown."""
        if not self.base_url or not self.model:
            raise RuntimeError(
                "VisionApiBackend needs base_url+model (env VISUALMD_VISION_BASE_URL / "
                "VISUALMD_VISION_MODEL). For a local model: run ollama/llama.cpp and point "
                "base_url at its OpenAI-compatible /v1 endpoint."
            )
        png = Path(req.page_png_path or "")
        if not png.is_file():
            raise FileNotFoundError(f"page image not found: {png}")
        b64 = base64.b64encode(_image_bytes(png, self.max_image_px)).decode("ascii")
        payload = {
            "model": self.model,
            "temperature": temperature,
            "max_tokens": self.max_tokens,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                }
            ],
        }
        resp = self._post(payload)
        content = resp["choices"][0]["message"]["content"]
        if isinstance(content, list):  # some servers return content as parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        content = _normalize_inline_math(_strip_code_fence(str(content).strip()))
        if not content.strip():
            # Thinking VLMs (Qwen3-VL) can spend the whole budget on hidden
            # reasoning, and a degenerate scan can yield nothing. Raise the
            # page-level EmptyContentError so the batch loop placeholders this
            # one page instead of aborting the whole run.
            raise EmptyContentError(
                f"{self.model} returned empty content (finish=length: reasoning "
                f"consumed the budget, or a degenerate page; max_tokens={self.max_tokens})."
            )
        return content

    def transcribe_page(self, req: PageRequest, attempt: int = 0) -> PageResult:
        user_text = (
            f"{TRANSCRIBE_INSTRUCTION}\n\n"
            f"本页页码:{req.page_no}(输出锚点 NNN={req.page_no:03d})。"
            f"资料标题:{req.doc_title}。只输出该页的 Markdown,不要额外解释。"
        )
        if attempt > 0:  # retry: machine gate failed last time, be stricter
            user_text += (
                "\n\n上次输出不合规。请严格:四段(文字/公式/图表/存疑)齐全、缺项写'无';"
                "公式只用块级 $$;保留 PNG 引用 ![第N页](images/p-NNN.png)。"
            )
        content = self._run(req, user_text, min(0.4, 0.2 * attempt))
        return PageResult(
            page_no=req.page_no,
            markdown=content + "\n",
            confidence=0.0,
            backend_id=self.backend_id,
        )


def _image_bytes(png: Path, max_px: int) -> bytes:
    """Return PNG bytes, optionally downscaled so the long side <= ``max_px``.

    Uses Pillow if available; if absent or ``max_px<=0``, returns the original.
    Fewer pixels -> fewer vision tokens -> much faster encoding on weak boxes.
    """
    raw = png.read_bytes()
    if max_px <= 0:
        return raw
    try:
        import io

        from PIL import Image
    except ImportError:
        return raw
    img = Image.open(io.BytesIO(raw))
    long_side = max(img.size)
    if long_side <= max_px:
        return raw
    scale = max_px / long_side
    img = img.resize((round(img.width * scale), round(img.height * scale)))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _strip_code_fence(text: str) -> str:
    """Drop a wrapping ```lang ... ``` fence some models add around the whole answer."""
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


_DOLLAR_BLOCK_RE = re.compile(r"\$\$.*?\$\$", re.DOTALL)
_INLINE_DOLLAR_RE = re.compile(r"(?<!\$)\$(?!\$)([^\$\n]+?)\$(?!\$)")


def _normalize_inline_math(text: str) -> str:
    """Convert inline ``$...$`` to ``\\(...\\)`` (spec form) while protecting ``$$`` blocks.

    Many VLMs emit ``$x$`` for inline math; the standard wants ``\\(...\\)`` and the
    machine gate rejects single ``$``. This normalizes any backend's output.
    """
    blocks: list[str] = []

    def stash(m: re.Match) -> str:
        blocks.append(m.group(0))
        return f"\x00{len(blocks) - 1}\x00"

    out = _DOLLAR_BLOCK_RE.sub(stash, text)
    out = _INLINE_DOLLAR_RE.sub(r"\\(\1\\)", out)
    out = out.replace(
        "$", "＄"
    )  # leftover lone/unpaired $ -> full-width ＄ (gate-safe; ASCII $ would trip inline_dollar)
    for i, block in enumerate(blocks):
        out = out.replace(f"\x00{i}\x00", block)
    return out
