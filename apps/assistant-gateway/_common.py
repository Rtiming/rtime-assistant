# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""gateway 共享底层:常量 + 无状态叶子助手。

gateway.py 与各子系统模块都从这里导入,作为唯一的共享层——避免子系统反向依赖 gateway
(那会因线上以 __main__ 运行而二次加载 gateway)。本模块只依赖标准库。
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _ensure_rtime_models_path() -> None:
    """Put packages/rtime-models/src on sys.path so the gateway can import the
    model registry loader. The gateway runs from the repo checkout (systemd) where
    the package sits at <repo>/packages/rtime-models/src; tests add it via conftest.
    Walks up from this file to the nearest ancestor that has the package."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "packages" / "rtime-models" / "src"
        if candidate.is_dir():
            sp = str(candidate)
            if sp not in sys.path:
                sys.path.insert(0, sp)
            return


_ensure_rtime_models_path()


SCHEMA_VERSION = 1


EXCLUDED_TOP_DIRS = {"personal-data"}


FRONTMATTER_PATH_KEYS = (
    "source",
    "brain_path",
    "pdf_file",
    "page_image_dir",
    "raw_text_dir",
    "page_text_dir",
)


SOURCE_LINE = re.compile(
    r"^[-*]?\s*\[?\[?([^\s\]|#]+\.(?:pdf|md|png|jpg|csv|json))\]?\]?"
    r"(?:#page=(\d+))?",
    re.IGNORECASE,
)


ZERO_HIT = re.compile(r"没有找到|未找到|找不到|无相关|no relevant|zero", re.IGNORECASE)


MAX_IMAGE_ATTACHMENT_BYTES = 2 * 1024 * 1024


MAX_FILE_ATTACHMENT_BYTES = 16 * 1024 * 1024


IMAGE_ATTACHMENT_MIME_PREFIXES = ("image/",)


FILE_ATTACHMENT_KINDS = {"pdf", "office", "spreadsheet", "markdown", "text", "csv"}


ARCHIVE_ATTACHMENT_KINDS = {"archive"}


TOOL_ATTACHMENT_KINDS = FILE_ATTACHMENT_KINDS | ARCHIVE_ATTACHMENT_KINDS


MOONSHOT_FILE_EXTRACT_KINDS = {"pdf", "office", "spreadsheet", "markdown", "text", "csv"}


RUNTIME_ERROR_QUERY = re.compile(
    r"报错|出错|错误|后端|网关|gateway|endpoint|error|max[_-]?turns|incomplete[_-]?answer|超时|没(?:有)?(?:回答|回复|结果)",
    re.IGNORECASE,
)


RUNTIME_ERROR_CODE = re.compile(
    r"error_max_turns|incomplete_answer|busy_or_queue|HTTP 503|模型流以非成功状态结束|工具调用后没有返回最终回答",
    re.IGNORECASE,
)


INVESTIGATION_QUERY = re.compile(
    r"(?:(?:重复|查重|去重).{0,16}(?:讲稿|课件|幻灯片|PDF|pdf|文件|资料|材料|课程)|"
    r"(?:讲稿|课件|幻灯片|PDF|pdf|文件|资料|材料|课程).{0,16}(?:重复|查重|去重)|"
    r"(?:扫描|遍历|查找|列出|对比).{0,16}(?:目录|讲稿|课件|幻灯片|PDF|pdf|文件|资料|材料)|"
    r"(?:duplicate|dedupe|de-duplicate).{0,32}(?:pdf|slide|lecture|file|material)|"
    r"(?:scan|compare|list|find).{0,32}(?:directory|pdf|slide|lecture|file|material))",
    re.IGNORECASE,
)


WEB_INTENT_QUERY = re.compile(
    r"https?://|网页|网站|上网|联网|网络搜索|网页搜索|网上|搜一下|搜索一下|搜索看看|查一下|近期热点|"
    r"\bweb\s*(search|fetch)?\b|\bbrowse\b",
    re.IGNORECASE,
)


PERMISSION_MODES = {"dontAsk", "default", "acceptEdits", "plan", "bypassPermissions"}


DEFAULT_PERMISSION_MODE = "dontAsk"


DEFAULT_WEB_ALLOWED_TOOLS = ("WebSearch", "WebFetch", "Bash(rtime-web-fetch *)")


FULL_ACCESS_MODES = {"full", "full-access", "write", "writes", "自由", "全自动"}


FULL_ACCESS_ALLOWED_TOOLS = (
    "Read",
    "Write",
    "Edit",
    "MultiEdit",
    "Glob",
    "Grep",
    "WebSearch",
    "WebFetch",
    "Bash(*)",
)


BEIJING_TZ = timezone(timedelta(hours=8))


SENSITIVE_TEXT_RE = re.compile(
    r"(open[_-]?id|union[_-]?id|tenant[_-]?key|app[_-]?secret|api[_-]?key|token|secret|credential|"
    r"password|passwd|私钥|密钥|验证码|身份证|银行卡|target\s*:|ou_[A-Za-z0-9_-]{8,})",
    re.IGNORECASE,
)


MEMORY_INTENT_RE = re.compile(
    r"(请?记住|帮我记(?:一下|住)?|记忆(?:一下|这个)?|以后.{0,16}(?:记得|按这个|偏好)|"
    r"(?:调整|修改|更新).{0,12}(?:偏好|记忆|画像)|remember this|save this preference)",
    re.IGNORECASE,
)


def env_bool(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def sanitize_permission_mode(value, fallback: str = DEFAULT_PERMISSION_MODE) -> str:
    text = str(value or "").strip()
    if text in PERMISSION_MODES:
        return text
    if fallback in PERMISSION_MODES:
        return fallback
    return DEFAULT_PERMISSION_MODE


def bool_option(value, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    return default


def access_mode(value) -> str:
    text = str(value or "").strip().lower()
    return "full" if text in FULL_ACCESS_MODES else "readonly"


def full_access_enabled(cfg: dict) -> bool:
    return access_mode(cfg.get("gateway_access_mode")) == "full"


def extract_frontmatter(text: str) -> dict:
    """Minimal YAML-subset parser: top-of-file --- block, key: value lines.

    Quoted values are unquoted; list/nested values are ignored. Good enough
    for the intake frontmatter schema; never raises.
    """
    if not text:
        return {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fm: dict = {}
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in line or line.startswith((" ", "\t", "-")):
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if value.startswith(("[", "{")):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if value:
            fm[key.strip()] = value
    return fm


def safe_brain_path(raw: str, brain_root: Path, anchor_dir: Path | None = None) -> Path | None:
    """Resolve a frontmatter path against brain_root (or anchor_dir for
    relative companions). Returns None unless the result exists, stays under
    brain_root, and is outside excluded sensitive areas."""
    if not raw or raw.startswith(("http://", "https://", "zotero://")):
        return None
    raw = raw.strip()
    candidates = []
    if anchor_dir is not None and not raw.startswith("knowledge/"):
        candidates.append(anchor_dir / raw)
    candidates.append(brain_root / raw)
    for cand in candidates:
        try:
            resolved = cand.resolve()
            root = brain_root.resolve()
        except OSError:
            continue
        if not resolved.is_relative_to(root):
            continue
        rel = resolved.relative_to(root)
        if rel.parts and rel.parts[0] in EXCLUDED_TOP_DIRS:
            continue
        if resolved.exists():
            return resolved
    return None


def _safe_attachment_name(name: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    if not cleaned:
        cleaned = fallback
    return cleaned[:96]


def _parse_memory_frontmatter(text: str) -> tuple[dict, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text.strip()
    fm: dict = {}
    body_start: int | None = None
    for idx, line in enumerate(lines[1:], start=1):
        stripped = line.strip()
        if stripped == "---":
            body_start = idx + 1
            break
        if not stripped or stripped.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            fm[key.strip()] = [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]
        elif value.startswith("{"):
            fm[key.strip()] = value
        else:
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            fm[key.strip()] = value
    body = "\n".join(lines[body_start:]).strip() if body_start is not None else ""
    return fm, body


def _memory_terms(text: str) -> list[str]:
    lowered = (text or "").lower()
    terms = re.findall(r"[a-z0-9_.\-/]{2,}", lowered)
    for run in re.findall(r"[\u4e00-\u9fff]+", lowered):
        if len(run) <= 4:
            terms.append(run)
        terms.extend(run[i : i + 2] for i in range(max(0, len(run) - 1)))
    return [term for term in terms if term not in {"用户", "助手", "当前", "请求", "相关", "回答"}]


def _today_beijing() -> datetime:
    return datetime.now(BEIJING_TZ)


def _beijing_date() -> str:
    return _today_beijing().date().isoformat()


def csv_env(name: str, defaults: list[str]) -> list[str]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return list(defaults)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _read_secret(direct_names: list[str], file_names: list[str], literal_files: list[object] | None = None) -> str:
    for name in direct_names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    for name in file_names:
        raw = os.environ.get(name, "").strip()
        if not raw:
            continue
        try:
            path = Path(raw).expanduser()
            if path.is_file():
                return path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    for raw in literal_files or []:
        if not raw:
            continue
        try:
            path = Path(str(raw)).expanduser()
            if path.is_file():
                return path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return ""


def runtime_error_context(body: dict) -> tuple[str, str] | None:
    context = body.get("context") or {}
    runtime = context.get("runtime") or {}
    if not isinstance(runtime, dict):
        return None
    last_error = runtime.get("last_error") or {}
    if not isinstance(last_error, dict):
        return None
    message = str(last_error.get("message") or "").strip()
    if not message:
        return None
    code = str(last_error.get("code") or "").strip()
    if not code:
        match = RUNTIME_ERROR_CODE.search(message)
        code = match.group(0) if match else "runtime_error"
    return code, message[:800]


def request_is_runtime_error_question(body: dict) -> bool:
    message = str(body.get("message") or "")
    if not RUNTIME_ERROR_QUERY.search(message):
        return False
    return runtime_error_context(body) is not None or RUNTIME_ERROR_CODE.search(message) is not None
