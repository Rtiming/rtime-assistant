# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""M3 multimodal helpers for the QQ bridge.

Inbound images/stickers carry an HTTP ``url`` on the QQ CDN, so the bridge downloads
the bytes itself (HTTP GET) into its own state dir; the model then understands them
with the Read tool (the Kimi coding endpoint reads images via Read — verified).

Inbound *files* are different: NapCat's file segment has no url, and ``get_file``
returns only a path *inside the NapCat container* (not a url, not base64). Since
NapCat's QQ data dir is bind-mounted on the host, the bridge mounts NapCat's file temp
dir read-only (``QQ_NAPCAT_FILE_DIR``) and, on a file message, fires ``get_file`` to
materialize the bytes there, then copies them into its own state dir to Read.

Outbound: files are sent back as ``base64://`` segments, which cross the container
boundary without a shared disk. Voice (record) STT is intentionally out of scope here —
it is an orange-pi-local ASR task tracked separately.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import shutil
import time
import wave
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import aiohttp

from .onebot.cqcode import MediaSegment

log = logging.getLogger("qq_bridge.media")

_IMAGE_CT_SUFFIX = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
}
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"}
DEFAULT_MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024

# Files are understood by extracting TEXT server-side (the model never Reads the binary:
# making kimi-code Read a PDF renders pages to images and hangs). Plain-text/code suffixes
# are read directly; PDFs go through pypdf; everything else is reported as unsupported.
_TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".tsv",
    ".log",
    ".json",
    ".yaml",
    ".yml",
    ".xml",
    ".html",
    ".htm",
    ".tex",
    ".rtf",
    ".ini",
    ".toml",
    ".srt",
    ".py",
    ".js",
    ".ts",
    ".java",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cc",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".sh",
    ".bash",
    ".sql",
    ".m",
    ".r",
}
MAX_FILE_TEXT_CHARS = 8000
MAX_PDF_PAGES = 12


@dataclass(frozen=True)
class DownloadedMedia:
    """An inbound media segment after its bytes have been fetched locally."""

    media: MediaSegment
    path: str


def _safe_suffix(name: str, content_type: str) -> str:
    _, ext = os.path.splitext(name or "")
    ext = ext.lower()
    if ext in _IMAGE_SUFFIXES:
        return ext
    ct = (content_type or "").split(";")[0].strip().lower()
    return _IMAGE_CT_SUFFIX.get(ct, ext or ".bin")


def _prune_oldest(dest_dir: str, keep: int) -> None:
    """Best-effort: keep only the newest ``keep`` files in ``dest_dir``."""
    try:
        entries = [
            os.path.join(dest_dir, f)
            for f in os.listdir(dest_dir)
            if os.path.isfile(os.path.join(dest_dir, f))
        ]
        if len(entries) <= keep:
            return
        entries.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for stale in entries[keep:]:
            try:
                os.remove(stale)
            except OSError:
                pass
    except OSError:
        pass


async def download_url(
    url: str,
    dest_dir: str,
    *,
    stem: str,
    suggested_name: str = "",
    max_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    timeout: float = 30.0,
    keep_recent: int = 60,
) -> str:
    """Download ``url`` into ``dest_dir`` and return the local path. Raises on failure
    or when the body exceeds ``max_bytes``."""
    if not url:
        raise ValueError("empty media url")
    os.makedirs(dest_dir, exist_ok=True)
    _prune_oldest(dest_dir, keep_recent)
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")
            declared = resp.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise ValueError(f"media too large ({declared} bytes > {max_bytes})")
            suffix = _safe_suffix(suggested_name, content_type)
            path = os.path.join(dest_dir, f"{stem}{suffix}")
            total = 0
            with open(path, "wb") as fh:
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        fh.close()
                        os.remove(path)
                        raise ValueError(f"media exceeded {max_bytes} bytes mid-stream")
                    fh.write(chunk)
    log.debug("downloaded %s -> %s (%d bytes)", url[:80], path, total)
    return path


def to_base64_uri(path: str) -> str:
    """Read a local file and return a OneBot ``base64://…`` URI for outbound send."""
    with open(path, "rb") as fh:
        return "base64://" + base64.b64encode(fh.read()).decode("ascii")


async def fetch_napcat_file(
    seg: MediaSegment,
    *,
    napcat_file_dir: str,
    dest_dir: str,
    stem: str,
    send_action: Callable[[str, dict], Awaitable[None]] | None,
    max_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    poll_timeout: float = 12.0,
) -> str | None:
    """Materialize an inbound QQ file into ``dest_dir`` and return its local path.

    NapCat's file segment has no url; ``get_file`` only yields a NapCat-container path.
    Fire ``get_file`` (fire-and-forget) so NapCat writes the bytes into its temp dir —
    bind-mounted into this container at ``napcat_file_dir`` — then poll for the file by
    name and copy it into our own state dir. Returns None when it can't be fetched.
    """
    if not (seg.file_id and napcat_file_dir and send_action and seg.name):
        return None
    try:
        await send_action("get_file", {"file_id": seg.file_id})
    except Exception as exc:
        log.warning("get_file action failed: %s", exc)
    src = os.path.join(napcat_file_dir, os.path.basename(seg.name))
    waited = 0.0
    while waited < poll_timeout:
        if os.path.isfile(src):
            break
        await asyncio.sleep(0.5)
        waited += 0.5
    if not os.path.isfile(src):
        return None
    try:
        size = os.path.getsize(src)
    except OSError:
        return None
    if size <= 0 or size > max_bytes:
        log.warning("napcat file %s size %d out of range", src, size)
        return None
    os.makedirs(dest_dir, exist_ok=True)
    _prune_oldest(dest_dir, 60)
    _, ext = os.path.splitext(seg.name)
    dest = os.path.join(dest_dir, f"{stem}{ext or '.bin'}")
    try:
        shutil.copyfile(src, dest)
    except OSError as exc:
        log.warning("copy napcat file failed: %s", exc)
        return None
    log.debug("fetched napcat file %s -> %s (%d bytes)", src, dest, size)
    return dest


def _readable_ratio(text: str) -> float:
    """Fraction of chars that are normal CJK / ASCII / common punctuation. A low ratio
    flags garbled extraction (e.g. LaTeX PDFs whose subset fonts lack a ToUnicode map)."""
    if not text:
        return 0.0
    good = 0
    for ch in text:
        if (
            (ch.isascii() and (ch.isprintable() or ch.isspace()))
            or ("一" <= ch <= "鿿")
            or ch in "，。；：（）【】、？！…—《》“”‘’±×÷≈≤≥→"
        ):
            good += 1
    return good / len(text)


def extract_file_text(
    path: str,
    *,
    max_chars: int = MAX_FILE_TEXT_CHARS,
    max_pdf_pages: int = MAX_PDF_PAGES,
) -> tuple[str, str]:
    """Extract text from a downloaded file. Returns (text, note).

    The model never Reads the raw file: PDFs go through pypdf, text/code suffixes are
    read directly, other types are reported as unsupported. ``note`` is a short status
    (page count, truncation, failure reason) suitable for the prompt.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(path)
            total = len(reader.pages)
            chunks = []
            for page in reader.pages[:max_pdf_pages]:
                try:
                    chunks.append(page.extract_text() or "")
                except Exception:  # one bad page shouldn't sink the rest
                    continue
            text = "\n".join(c for c in chunks if c).strip()
            if not text:
                return (
                    "",
                    f"PDF 共 {total} 页，但未提取到文本（可能是扫描/图片型 PDF），建议把要问的页截图发我",
                )
            if _readable_ratio(text) < 0.6:
                # LaTeX/CID-font PDFs extract as garbled glyphs — don't feed the model junk.
                return "", (
                    f"PDF 共 {total} 页，但文本提取为乱码（公式/特殊字体 PDF，无字符映射），"
                    "建议把要问的页截图发我，我能准确读图"
                )
            note = f"PDF 共 {total} 页，已提取前 {min(total, max_pdf_pages)} 页文本"
        except Exception as exc:
            return "", f"PDF 文本提取失败：{type(exc).__name__}"
    elif ext in _TEXT_SUFFIXES:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read().strip()
            note = "文本文件已读取"
        except OSError as exc:
            return "", f"文件读取失败：{type(exc).__name__}"
    else:
        return "", f"暂不支持自动解析 {ext or '该'} 类型，未提取文本"

    if len(text) > max_chars:
        text = text[:max_chars] + "\n…（内容较长，已截断）"
    return text, note


# --- voice STT (sherpa-onnx Paraformer, loaded once; the bridge is a persistent process) ---
_RECOGNIZER = None
_RECOGNIZER_DIR = ""


def _get_recognizer(model_dir: str):
    """Lazy-load (once) the sherpa-onnx offline Paraformer recognizer. Returns None if the
    model files or sherpa_onnx are unavailable (STT then degrades gracefully)."""
    global _RECOGNIZER, _RECOGNIZER_DIR
    if _RECOGNIZER is not None and _RECOGNIZER_DIR == model_dir:
        return _RECOGNIZER
    model = os.path.join(model_dir, "model.int8.onnx")
    tokens = os.path.join(model_dir, "tokens.txt")
    if not (os.path.isfile(model) and os.path.isfile(tokens)):
        log.warning("STT model files missing in %s", model_dir)
        return None
    try:
        import sherpa_onnx

        rec = sherpa_onnx.OfflineRecognizer.from_paraformer(
            paraformer=model, tokens=tokens, num_threads=2, debug=False
        )
    except Exception as exc:  # noqa: BLE001 — STT is best-effort
        log.warning("STT recognizer load failed: %s", exc)
        return None
    _RECOGNIZER, _RECOGNIZER_DIR = rec, model_dir
    log.info("STT recognizer loaded from %s", model_dir)
    return rec


def _read_wav(path: str):
    """Read a 16-bit PCM wav into (sample_rate, float32 samples in [-1,1])."""
    import numpy as np

    with wave.open(path, "rb") as w:
        sample_rate = w.getframerate()
        frames = w.readframes(w.getnframes())
    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    return sample_rate, samples


def _decode(recognizer, sample_rate: int, samples) -> str:
    stream = recognizer.create_stream()
    stream.accept_waveform(sample_rate, samples)
    recognizer.decode_stream(stream)
    return stream.result.text


async def _napcat_get_record_wav(
    napcat_http: str, file_ref: str, napcat_file_dir: str, timeout: float
) -> str | None:
    """Ask NapCat (OneBot HTTP control API) to convert the voice (SILK/amr) to wav, then
    return the local path under the shared napcat temp mount. NapCat does the codec work."""
    body = {"file": file_ref, "out_format": "wav"}
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        async with session.post(f"{napcat_http}/get_record", json=body) as resp:
            payload = await resp.json()
    data = payload.get("data") or {}
    remote = data.get("file") or data.get("path") or data.get("url") or ""
    if not remote:
        return None
    local = os.path.join(napcat_file_dir, os.path.basename(remote))
    waited = 0.0
    while waited < timeout:
        if os.path.isfile(local):
            return local
        await asyncio.sleep(0.5)
        waited += 0.5
    return local if os.path.isfile(local) else None


async def transcribe_voice(seg: MediaSegment, *, config, get_wav=None) -> str | None:
    """Transcribe an inbound voice segment locally (sherpa-onnx Paraformer). Returns the
    text, or None if STT is off / model missing / conversion or decode fails. ``get_wav``
    is injectable for tests; default fetches via NapCat's get_record(out_format=wav)."""
    if not (config.stt_model_dir and config.napcat_file_dir):
        return None
    file_ref = seg.name or seg.file_id
    if not file_ref:
        return None
    recognizer = _get_recognizer(config.stt_model_dir)
    if recognizer is None:
        return None
    fetch = get_wav or _napcat_get_record_wav
    try:
        wav = await fetch(config.napcat_http, file_ref, config.napcat_file_dir, 20.0)
    except Exception as exc:  # noqa: BLE001
        log.warning("get_record failed: %s", exc)
        return None
    if not wav:
        return None
    try:
        sample_rate, samples = await asyncio.to_thread(_read_wav, wav)
        text = await asyncio.to_thread(_decode, recognizer, sample_rate, samples)
    except Exception as exc:  # noqa: BLE001
        log.warning("STT transcribe failed: %s", exc)
        return None
    return text.strip() or None


def face_label(seg: MediaSegment) -> str:
    name = seg.summary or ""
    return f"[表情:{name}]" if name else f"[表情#{seg.face_id}]"


def sticker_label(seg: MediaSegment) -> str:
    return f"[表情包:{seg.summary}]" if seg.summary else "[表情包]"


def stem_for(message_id: str, index: int) -> str:
    """Collision-free filename stem for an inbound item of a message."""
    mid = "".join(ch for ch in (message_id or "") if ch.isalnum()) or "msg"
    return f"{int(time.time())}-{mid}-{index}"


def build_media_prompt(
    user_text: str,
    images: list[str],
    *,
    inline_notes: list[str],
    file_notes: list[str],
    voice_texts: list[str] | None = None,
    voice_count: int,
    video_count: int,
) -> str:
    """Compose the model prompt for a message that carried media.

    Images (and downloaded stickers) are referenced by local path with an explicit
    instruction to Read them; faces / not-downloaded stickers become inline text labels;
    files are referenced by path for analysis. Voice is transcribed locally (sherpa-onnx)
    and inlined as text; un-transcribable voice and video are noted as not-yet-understood.
    """
    parts: list[str] = []
    if user_text:
        parts.append(user_text)

    if inline_notes:
        parts.append("用户消息里还包含：" + " ".join(inline_notes))

    if images:
        listed = "、".join(images)
        parts.append(
            f"用户发来 {len(images)} 张图片（含表情包），已下载到本机路径：{listed}。"
            "请用 Read 工具逐张读取并理解其内容，结合用户文字一起回答；用中文回复。"
        )
    if file_notes:
        parts.append(
            "用户发来文件，以下是已为你在本机提取好的内容（可能截断；不要再用 Read 去读原文件）：\n\n"
            + "\n\n".join(file_notes)
            + "\n\n请直接基于上面的内容回答用户；若内容为空或提取失败，就如实说明，"
            "并建议用户改发文本、截图或可解析的文件类型。"
        )
    if voice_texts:
        joined = "\n".join(f"语音{i + 1}：{t}" for i, t in enumerate(voice_texts))
        parts.append(
            "用户发来语音，已在本机转写为文字（可能有识别误差）：\n"
            + joined
            + "\n\n请把转写文字当作用户说的话来回答；若转写明显不通顺，可请用户确认。"
        )
    if voice_count:
        parts.append(
            f"用户发来 {voice_count} 条语音但本机未能转写（STT 未启用或识别失败），"
            "请提示用户暂时发文字或图片。"
        )
    if video_count:
        parts.append(f"用户发来 {video_count} 个视频；视频理解暂不支持，请如实说明。")

    return "\n\n".join(parts).strip()
