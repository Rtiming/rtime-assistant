# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Read-only MCP stdio server for brain-docpack tooling.

The server intentionally avoids SDK dependencies so it can run from the same
source tree on Mac and orangepi. It implements the MCP stdio JSON-RPC surface
needed for read-only DocPack audit, sample selection, validation, status, and
doctor checks.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from .cli import find_repo_root


PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "brain-docpack"
SERVER_VERSION = "0.1.0"


JsonObject = dict[str, Any]


def _negotiate_protocol_version(client_version: Any) -> str:
    if isinstance(client_version, str) and client_version:
        return client_version
    return PROTOCOL_VERSION


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class ToolError(Exception):
    """A tool-level failure that should be returned as an MCP tool error."""

    def __init__(self, message: str, *, data: JsonObject | None = None):
        super().__init__(message)
        self.data = data or {}


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _tool_result(data: JsonObject, *, is_error: bool = False) -> JsonObject:
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": data,
        "isError": is_error,
    }


def _response(request_id: Any, result: JsonObject) -> JsonObject:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error_response(request_id: Any, code: int, message: str, data: Any = None) -> JsonObject:
    error: JsonObject = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _schema(properties: JsonObject, required: list[str] | None = None) -> JsonObject:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def _repo_root_from_args(arguments: JsonObject) -> Path:
    raw = arguments.get("repo_root") or os.environ.get("RTIME_ASSISTANT_ROOT")
    if raw:
        return Path(str(raw)).expanduser().resolve()
    return find_repo_root()


def _path_argument(arguments: JsonObject, key: str, *, required: bool = True) -> Path | None:
    raw = arguments.get(key)
    if raw is None or raw == "":
        if required:
            raise ToolError(f"missing required argument: {key}")
        return None
    return Path(str(raw)).expanduser().resolve()


def _default_brain_root() -> Path | None:
    env_root = os.environ.get("BRAIN_ROOT")
    if env_root:
        path = Path(env_root).expanduser().resolve()
        if path.is_dir():
            knowledge = path / "knowledge"
            return knowledge if knowledge.is_dir() else path

    for raw in (
        "/mnt/brain/knowledge",
        str(Path.home() / "brain" / "knowledge"),
        str(Path.home() / "OrangePi-Store" / "sync" / "brain" / "knowledge"),
    ):
        path = Path(raw)
        if path.is_dir():
            return path.resolve()
    return None


def _knowledge_root_argument(arguments: JsonObject) -> Path:
    path = _path_argument(arguments, "root", required=False)
    if path is not None:
        return path
    default_root = _default_brain_root()
    if default_root is None:
        raise ToolError("knowledge root not found; pass root or set BRAIN_ROOT")
    return default_root


def _run(command: Sequence[str], *, cwd: Path, timeout: int) -> CommandResult:
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            capture_output=True,
            check=False,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise ToolError(
            f"missing command: {exc.filename}",
            data={"ok": False, "missing_command": exc.filename},
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolError(
            f"command timed out after {timeout}s",
            data={
                "ok": False,
                "timeout_seconds": timeout,
                "stdout_excerpt": (exc.stdout or "")[:1200],
                "stderr_excerpt": (exc.stderr or "")[:1200],
            },
        ) from exc
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _parse_script_json(result: CommandResult, *, command_name: str) -> JsonObject:
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "command": command_name,
            "returncode": result.returncode,
            "errors": [f"invalid JSON output: {exc.msg}"],
            "stdout_excerpt": result.stdout[:1200],
            "stderr_excerpt": result.stderr[:1200],
        }
    if not isinstance(data, dict):
        return {
            "ok": False,
            "command": command_name,
            "returncode": result.returncode,
            "errors": ["script JSON output must be an object"],
        }
    data.setdefault("ok", result.returncode == 0)
    data.setdefault("command", command_name)
    data.setdefault("returncode", result.returncode)
    if result.stderr:
        data.setdefault("stderr_excerpt", result.stderr[:1200])
    return data


def _parse_count(raw: str) -> int | None:
    try:
        return int(raw.strip().strip("`"))
    except ValueError:
        return None


def _parse_audit_markdown(text: str, *, root: Path, deep: bool, returncode: int) -> JsonObject:
    file_types: dict[str, int] = {}
    pdf: JsonObject = {
        "count": 0,
        "pdfinfo_ok": 0,
        "pdfinfo_failed": 0,
        "total_pages": 0,
        "first_page_zero_text_count": 0,
        "first_page_zero_text": [],
    }
    office: dict[str, int] = {"doc": 0, "docx": 0, "ppt": 0, "pptx": 0, "xlsx": 0}
    tools: dict[str, str] = {}
    risks: list[str] = []
    section = ""

    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
            continue

        if section == "File Types":
            match = re.match(r"^\s*(\d+)\s+(.+?)\s*$", line)
            if match:
                file_types[match.group(2)] = int(match.group(1))
            continue

        if section == "Tool Availability":
            match = re.match(r"^\| `([^`]+)` \| ([^|]+) \|", line)
            if match:
                tools[match.group(1)] = match.group(2).strip()
            continue

        if section == "PDF Audit":
            count_match = re.match(r"^- PDF files: `?(\d+)`?", line)
            if count_match:
                pdf["count"] = int(count_match.group(1))
                continue
            ok_match = re.match(r"^- `pdfinfo` ok: `?(\d+)`?", line)
            if ok_match:
                pdf["pdfinfo_ok"] = int(ok_match.group(1))
                continue
            fail_match = re.match(r"^- `pdfinfo` failed: `?(\d+)`?", line)
            if fail_match:
                pdf["pdfinfo_failed"] = int(fail_match.group(1))
                continue
            pages_match = re.match(r"^- Total readable pages: `?(\d+)`?", line)
            if pages_match:
                pdf["total_pages"] = int(pages_match.group(1))
                continue
            zero_match = re.match(r"^- First-page zero-text PDFs: `?(\d+)`?", line)
            if zero_match:
                pdf["first_page_zero_text_count"] = int(zero_match.group(1))
                continue

        if section == "Office Audit":
            office_match = re.match(r"^- (docx?|pptx?|xlsx): `?(\d+)`?", line)
            if office_match:
                count = _parse_count(office_match.group(2))
                if count is not None:
                    office[office_match.group(1)] = count

    if returncode != 0:
        risks.append(f"audit_returncode_{returncode}")
    if not file_types:
        risks.append("file_type_summary_missing")

    return {
        "ok": returncode == 0,
        "root": str(root),
        "mode": "deep" if deep else "shallow",
        "file_types": file_types,
        "pdf": pdf,
        "office": office,
        "tools": tools,
        "risks": risks,
    }


def _status_for_docpack(docpack: Path) -> JsonObject:
    manifest_path = docpack / "manifest.json"
    verify_path = docpack / "verify.json"
    manifest_exists = manifest_path.exists()
    verify_exists = verify_path.exists()
    result: JsonObject = {
        "ok": False,
        "docpack": str(docpack),
        "manifest_exists": manifest_exists,
        "verify_exists": verify_exists,
        "status": "missing",
        "page_count": None,
        "risks": [],
    }

    manifest: JsonObject = {}
    verify: JsonObject = {}
    if manifest_exists:
        try:
            loaded = _load_json(manifest_path)
            if isinstance(loaded, dict):
                manifest = loaded
        except (OSError, json.JSONDecodeError) as exc:
            result["risks"].append(f"manifest_read_failed: {exc}")
    if verify_exists:
        try:
            loaded = _load_json(verify_path)
            if isinstance(loaded, dict):
                verify = loaded
        except (OSError, json.JSONDecodeError) as exc:
            result["risks"].append(f"verify_read_failed: {exc}")

    if manifest:
        display = manifest.get("display", {})
        if isinstance(display, dict):
            result["page_count"] = display.get("page_count")
        if isinstance(manifest.get("risks"), list):
            result["risks"].extend(str(item) for item in manifest["risks"])
    if verify:
        result["status"] = verify.get("status", "unknown")
        if result["page_count"] is None and isinstance(verify.get("pages"), list):
            result["page_count"] = len(verify["pages"])
        if isinstance(verify.get("risks"), list):
            result["risks"].extend(str(item) for item in verify["risks"])

    result["ok"] = bool(manifest_exists and verify_exists and result["status"] == "ok")
    return result


class BrainDocpackMCP:
    def tools(self) -> list[JsonObject]:
        common_repo = {
            "repo_root": {
                "type": "string",
                "description": "Optional rtime-assistant repository root.",
            }
        }
        return [
            {
                "name": "docpack.doctor",
                "title": "DocPack Doctor",
                "description": "Read-only check for brain-docpack repository paths and scripts.",
                "inputSchema": _schema(common_repo),
            },
            {
                "name": "docpack.audit",
                "title": "Knowledge Materials Audit",
                "description": "Read-only audit of a brain knowledge-material root.",
                "inputSchema": _schema(
                    {
                        **common_repo,
                        "root": {
                            "type": "string",
                            "description": "brain/knowledge root. Defaults to BRAIN_ROOT or known local mounts.",
                        },
                        "deep": {
                            "type": "boolean",
                            "description": "Run bounded Office conversion checks when true.",
                        },
                    }
                ),
            },
            {
                "name": "docpack.select_samples",
                "title": "DocPack Sample Selector",
                "description": "Select representative read-only DocPack regression samples.",
                "inputSchema": _schema(
                    {
                        **common_repo,
                        "root": {
                            "type": "string",
                            "description": "brain/knowledge root. Defaults to BRAIN_ROOT or known local mounts.",
                        },
                        "limit_per_category": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "Maximum samples per category.",
                        },
                    }
                ),
            },
            {
                "name": "docpack.validate",
                "title": "DocPack Validator",
                "description": "Validate a DocPack directory without modifying it.",
                "inputSchema": _schema(
                    {
                        **common_repo,
                        "docpack": {
                            "type": "string",
                            "description": "Path to a <slug>.docpack directory.",
                        },
                    },
                    required=["docpack"],
                ),
            },
            {
                "name": "docpack.status",
                "title": "DocPack Status",
                "description": "Read manifest/verify status for a DocPack directory.",
                "inputSchema": _schema(
                    {
                        "docpack": {
                            "type": "string",
                            "description": "Path to a <slug>.docpack directory.",
                        }
                    },
                    required=["docpack"],
                ),
            },
            {
                "name": "docpack.course_intake_plan",
                "title": "Course Intake Plan",
                "description": (
                    "Read-only course-material intake plan with normalized categories, "
                    "target names, index-output expectations, and confirmation questions; "
                    "does not copy or write files."
                ),
                "inputSchema": _schema(
                    {
                        **common_repo,
                        "source_root": {
                            "type": "string",
                            "description": "Directory containing the proposed course-material batch.",
                        },
                        "brain_root": {
                            "type": "string",
                            "description": "brain root, not brain/knowledge.",
                        },
                        "course_id": {
                            "type": "string",
                            "description": "Stable course id under brain/knowledge/courses.",
                        },
                        "course_title": {
                            "type": "string",
                            "description": "Human-readable course title.",
                        },
                        "include_all": {
                            "type": "boolean",
                            "description": "Treat every supported file under source_root as part of this course batch.",
                        },
                        "keywords": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional filename/path keywords. Defaults are derived from course id/title.",
                        },
                    },
                    required=["source_root", "brain_root", "course_id", "course_title"],
                ),
            },
        ]

    def handle_message(self, message: JsonObject) -> JsonObject | None:
        if not isinstance(message, dict):
            return _error_response(None, -32600, "Invalid Request")

        request_id = message.get("id")
        method = message.get("method")
        has_id = "id" in message

        if not isinstance(method, str):
            if has_id:
                return _error_response(request_id, -32600, "Invalid Request")
            return None

        if method == "notifications/initialized":
            return None

        if method == "initialize":
            params = message.get("params", {})
            client_version = params.get("protocolVersion") if isinstance(params, dict) else None
            protocol_version = _negotiate_protocol_version(client_version)
            return _response(
                request_id,
                {
                    "protocolVersion": protocol_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "title": "Brain DocPack",
                        "version": SERVER_VERSION,
                        "description": "Read-only DocPack tools for the rtime brain knowledge library.",
                    },
                    "instructions": (
                        "Use read-only DocPack tools for audits, sample selection, validation, "
                        "and status checks. This server does not write to brain."
                    ),
                },
            )

        if method == "ping":
            return _response(request_id, {})

        if method == "tools/list":
            tools = self.tools()
            for _t in tools:
                _t["name"] = _t["name"].replace(".", "_")
            return _response(request_id, {"tools": tools})

        if method == "tools/call":
            params = message.get("params")
            if not isinstance(params, dict):
                return _error_response(request_id, -32602, "Invalid params")
            name = params.get("name")
            arguments = params.get("arguments", {})
            if not isinstance(name, str) or not isinstance(arguments, dict):
                return _error_response(request_id, -32602, "Invalid params")
            try:
                result = self.call_tool(name, arguments)
                return _response(request_id, result)
            except ToolError as exc:
                data = {"ok": False, "error": str(exc), **exc.data}
                return _response(request_id, _tool_result(data, is_error=True))
            except Exception as exc:  # pragma: no cover - defensive server guard
                return _response(
                    request_id,
                    _tool_result({"ok": False, "error": str(exc)}, is_error=True),
                )

        if method == "shutdown":
            return _response(request_id, {})

        if has_id:
            return _error_response(request_id, -32601, f"Method not found: {method}")
        return None

    def call_tool(self, name: str, arguments: JsonObject) -> JsonObject:
        handlers: dict[str, Callable[[JsonObject], JsonObject]] = {
            "docpack.doctor": self._tool_doctor,
            "docpack.audit": self._tool_audit,
            "docpack.select_samples": self._tool_select_samples,
            "docpack.validate": self._tool_validate,
            "docpack.status": self._tool_status,
            "docpack.course_intake_plan": self._tool_course_intake_plan,
        }
        if name not in handlers:
            name = {_k.replace(".", "_"): _k for _k in handlers}.get(name, name)
        handler = handlers.get(name)
        if handler is None:
            raise ToolError(f"unknown tool: {name}")

        run_id = f"docpack-mcp-{uuid.uuid4().hex}"
        started = time.monotonic()
        status = "ok"
        failure_reason = ""
        try:
            data = handler(arguments)
            if data.get("ok") is False:
                status = "failed"
                failure_reason = ",".join(str(item) for item in data.get("errors", []))[:300]
            data.setdefault("run_id", run_id)
            data.setdefault("tool", name)
            return _tool_result(data, is_error=status != "ok")
        except Exception as exc:
            status = "failed"
            failure_reason = str(exc)
            raise
        finally:
            duration_ms = int((time.monotonic() - started) * 1000)
            self._record_run(
                run_id=run_id,
                tool=name,
                arguments=arguments,
                status=status,
                duration_ms=duration_ms,
                failure_reason=failure_reason,
            )

    def _tool_doctor(self, arguments: JsonObject) -> JsonObject:
        repo = _repo_root_from_args(arguments)
        checks = {
            "brain_docpack": Path(__file__).exists(),
            "audit_script": (repo / "scripts" / "audit-knowledge-materials.sh").is_file(),
            "select_samples_script": (repo / "scripts" / "select-docpack-samples.py").is_file(),
            "build_script": (repo / "scripts" / "build-docpack.py").is_file(),
            "validate_script": (repo / "scripts" / "validate-docpack.py").is_file(),
            "schemas": (repo / "schemas" / "docpack").is_dir(),
        }
        commands = {name: "ok" if ok else "missing" for name, ok in checks.items()}
        risks = [name for name, ok in checks.items() if not ok]
        return {
            "ok": not risks,
            "repo_root": str(repo),
            "commands": commands,
            "risks": risks,
        }

    def _tool_audit(self, arguments: JsonObject) -> JsonObject:
        repo = _repo_root_from_args(arguments)
        root = _knowledge_root_argument(arguments)
        deep = bool(arguments.get("deep", False))
        command = [str(repo / "scripts" / "audit-knowledge-materials.sh"), str(root)]
        if deep:
            command.append("--deep")
        result = _run(command, cwd=repo, timeout=900 if deep else 180)
        data = _parse_audit_markdown(result.stdout, root=root, deep=deep, returncode=result.returncode)
        data["returncode"] = result.returncode
        if result.stderr:
            data["stderr_excerpt"] = result.stderr[:1200]
        return data

    def _tool_course_intake_plan(self, arguments: JsonObject) -> JsonObject:
        source_root = _path_argument(arguments, "source_root")
        brain_root = _path_argument(arguments, "brain_root")
        course_id = str(arguments.get("course_id") or "").strip()
        course_title = str(arguments.get("course_title") or "").strip()
        if not course_id:
            raise ToolError("missing required argument: course_id")
        if not course_title:
            raise ToolError("missing required argument: course_title")

        raw_keywords = arguments.get("keywords")
        if raw_keywords is None:
            keywords: list[str] = []
        elif isinstance(raw_keywords, list):
            keywords = [str(keyword) for keyword in raw_keywords if str(keyword).strip()]
        else:
            raise ToolError("keywords must be an array of strings")

        from .course_intake import build_plan, default_keywords

        plan = build_plan(
            source_root,
            brain_root,
            course_id,
            course_title,
            keywords=keywords or default_keywords(course_id, course_title),
            include_all=bool(arguments.get("include_all", False)),
            apply=False,
        )
        data = asdict(plan)
        data["ok"] = True
        data["permission_tier"] = "read_only"
        data["writes"] = []
        data["requires_user_confirmation"] = bool(plan.confirmation_questions)
        return data

    def _tool_select_samples(self, arguments: JsonObject) -> JsonObject:
        repo = _repo_root_from_args(arguments)
        root = _knowledge_root_argument(arguments)
        limit = int(arguments.get("limit_per_category", 1))
        if limit < 1:
            raise ToolError("limit_per_category must be >= 1")
        result = _run(
            [
                sys.executable,
                str(repo / "scripts" / "select-docpack-samples.py"),
                str(root),
                "--limit-per-category",
                str(limit),
                "--json",
            ],
            cwd=repo,
            timeout=180,
        )
        return _parse_script_json(result, command_name="select-docpack-samples")

    def _tool_validate(self, arguments: JsonObject) -> JsonObject:
        repo = _repo_root_from_args(arguments)
        docpack = _path_argument(arguments, "docpack")
        assert docpack is not None
        result = _run(
            [
                sys.executable,
                str(repo / "scripts" / "validate-docpack.py"),
                str(docpack),
                "--json",
            ],
            cwd=repo,
            timeout=120,
        )
        return _parse_script_json(result, command_name="validate-docpack")

    def _tool_status(self, arguments: JsonObject) -> JsonObject:
        docpack = _path_argument(arguments, "docpack")
        assert docpack is not None
        return _status_for_docpack(docpack)

    def _record_run(
        self,
        *,
        run_id: str,
        tool: str,
        arguments: JsonObject,
        status: str,
        duration_ms: int,
        failure_reason: str,
    ) -> None:
        log_path_raw = os.environ.get("BRAIN_DOCPACK_MCP_RUN_LOG")
        if not log_path_raw:
            return
        path = Path(log_path_raw).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        input_paths = [
            str(arguments[key])
            for key in ("repo_root", "root", "docpack")
            if key in arguments and arguments[key]
        ]
        record = {
            "run_id": run_id,
            "entry": "mcp-stdio",
            "tool": tool,
            "permission_tier": "read_only",
            "input_paths": input_paths,
            "status": status,
            "duration_ms": duration_ms,
            "failure_reason": failure_reason,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def serve_stdio(server: BrainDocpackMCP | None = None) -> int:
    server = server or BrainDocpackMCP()
    for line in sys.stdin:
        raw = line.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(_json_dumps(_error_response(None, -32700, f"Parse error: {exc.msg}")), flush=True)
            continue
        response = server.handle_message(message)
        if response is not None:
            print(_json_dumps(response), flush=True)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    return serve_stdio()


if __name__ == "__main__":
    raise SystemExit(main())
