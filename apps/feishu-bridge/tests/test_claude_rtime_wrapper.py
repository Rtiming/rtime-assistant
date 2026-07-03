# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import json
import os
import pathlib
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
WRAPPER = REPO_ROOT / "deploy" / "bin" / "claude-rtime"
DEEPSEEK_WRAPPER = REPO_ROOT / "deploy" / "bin" / "claude-deepseek"
QWEN_WRAPPER = REPO_ROOT / "deploy" / "bin" / "claude-qwen"
USTC_WRAPPER = REPO_ROOT / "deploy" / "bin" / "claude-ustc"
OLLAMA_WRAPPER = REPO_ROOT / "deploy" / "bin" / "claude-ollama"


class UstcHandler(BaseHTTPRequestHandler):
    requests = []

    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        self.__class__.requests.append({"path": self.path, "body": body})
        payload = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "model": body["model"],
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "OK Qwen"},
                    "finish_reason": "stop",
                }
            ],
        }
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        return


def _start_server():
    UstcHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), UstcHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_claude_rtime_emits_claude_stream_json_for_ustc_model(tmp_path):
    # RTIME_USTC_AGENT=0 opts back into the legacy tool-less inline chat path;
    # the default (1) now delegates USTC models to the claude-ustc wrapper.
    server = _start_server()
    try:
        env = os.environ.copy()
        env.update(
            {
                "RTIME_USTC_AGENT": "0",
                "RTIME_USTC_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
                "RTIME_USTC_API_KEY": "test-key",
                "RTIME_USTC_MODELS": "qwen3.6-chat",
                "RTIME_USTC_SESSION_DIR": str(tmp_path / "sessions"),
            }
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(WRAPPER),
                "--print",
                "--output-format",
                "stream-json",
                "--model",
                "qwen3.6-chat",
            ],
            input="hello\n",
            text=True,
            capture_output=True,
            env=env,
            check=True,
        )
    finally:
        server.shutdown()

    events = [json.loads(line) for line in completed.stdout.splitlines()]
    assert events[0]["type"] == "system"
    assert events[1]["event"]["delta"]["text"] == "OK Qwen"
    assert events[-1]["type"] == "result"
    assert events[-1]["result"] == "OK Qwen"
    assert UstcHandler.requests[0]["path"] == "/v1/chat/completions"
    assert UstcHandler.requests[0]["body"]["model"] == "qwen3.6-chat"
    assert UstcHandler.requests[0]["body"]["messages"][-1] == {
        "role": "user",
        "content": "hello",
    }


def test_claude_rtime_accepts_print_flag_before_model_with_positional_prompt(tmp_path):
    server = _start_server()
    try:
        env = os.environ.copy()
        env.update(
            {
                "RTIME_USTC_AGENT": "0",
                "RTIME_USTC_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
                "RTIME_USTC_API_KEY": "test-key",
                "RTIME_USTC_MODELS": "deepseek-v4-flash-ascend",
                "RTIME_USTC_SESSION_DIR": str(tmp_path / "sessions"),
            }
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(WRAPPER),
                "-p",
                "--model",
                "deepseek-v4-flash-ascend",
                "hello",
            ],
            text=True,
            capture_output=True,
            env=env,
            check=True,
        )
    finally:
        server.shutdown()

    events = [json.loads(line) for line in completed.stdout.splitlines()]
    assert events[-1]["type"] == "result"
    assert events[-1]["result"] == "OK Qwen"
    assert UstcHandler.requests[0]["path"] == "/v1/chat/completions"
    assert UstcHandler.requests[0]["body"]["model"] == "deepseek-v4-flash-ascend"
    assert UstcHandler.requests[0]["body"]["messages"][-1] == {
        "role": "user",
        "content": "hello",
    }


def test_claude_rtime_delegates_non_ustc_models(tmp_path):
    fallback = tmp_path / "fallback.py"
    output = tmp_path / "args.json"
    fallback.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "pathlib.Path(os.environ['FALLBACK_ARGS']).write_text(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    fallback.chmod(0o755)

    env = os.environ.copy()
    env["RTIME_CLAUDE_FALLBACK"] = str(fallback)
    env["FALLBACK_ARGS"] = str(output)

    subprocess.run(
        [
            sys.executable,
            str(WRAPPER),
            "--print",
            "--output-format",
            "stream-json",
            "--model",
            "claude-sonnet-4-6",
        ],
        input="hello\n",
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert json.loads(output.read_text(encoding="utf-8")) == [
        "--print",
        "--output-format",
        "stream-json",
        "--model",
        "claude-sonnet-4-6",
    ]


def test_claude_rtime_routes_deepseek_code_models_to_provider_wrapper(tmp_path):
    provider = tmp_path / "deepseek-wrapper.py"
    output = tmp_path / "provider-args.json"
    provider.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "pathlib.Path(os.environ['PROVIDER_ARGS']).write_text(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    provider.chmod(0o755)

    env = os.environ.copy()
    env["RTIME_DEEPSEEK_CLAUDE_WRAPPER"] = str(provider)
    env["PROVIDER_ARGS"] = str(output)

    subprocess.run(
        [
            sys.executable,
            str(WRAPPER),
            "--print",
            "--output-format",
            "stream-json",
            "--model",
            "deepseek-v4-pro[1m]",
        ],
        input="hello\n",
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert json.loads(output.read_text(encoding="utf-8")) == [
        "--print",
        "--output-format",
        "stream-json",
        "--model",
        "deepseek-v4-pro[1m]",
    ]


def test_claude_rtime_routes_qwen_code_models_to_provider_wrapper(tmp_path):
    provider = tmp_path / "qwen-wrapper.py"
    output = tmp_path / "provider-args.json"
    provider.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "pathlib.Path(os.environ['PROVIDER_ARGS']).write_text(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    provider.chmod(0o755)

    env = os.environ.copy()
    env["RTIME_QWEN_CLAUDE_WRAPPER"] = str(provider)
    env["PROVIDER_ARGS"] = str(output)

    subprocess.run(
        [
            sys.executable,
            str(WRAPPER),
            "--print",
            "--output-format",
            "stream-json",
            "--model",
            "qwen3-coder-next",
        ],
        input="hello\n",
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert json.loads(output.read_text(encoding="utf-8")) == [
        "--print",
        "--output-format",
        "stream-json",
        "--model",
        "qwen3-coder-next",
    ]


def _write_capture_wrapper(tmp_path: pathlib.Path, name: str) -> tuple[pathlib.Path, pathlib.Path]:
    wrapper = tmp_path / f"{name}-wrapper.py"
    output = tmp_path / f"{name}-args.json"
    wrapper.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "pathlib.Path(os.environ['PROVIDER_ARGS']).write_text(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return wrapper, output


def test_claude_rtime_routes_ustc_alias_to_claude_ustc_by_default(tmp_path):
    """Default RTIME_USTC_AGENT=1: USTC models (here via the `ds` alias) delegate
    to the claude-ustc agent wrapper with the original argv."""
    provider, output = _write_capture_wrapper(tmp_path, "ustc")

    env = os.environ.copy()
    env.pop("RTIME_USTC_AGENT", None)
    env["RTIME_USTC_CLAUDE_WRAPPER"] = str(provider)
    env["PROVIDER_ARGS"] = str(output)

    subprocess.run(
        [
            sys.executable,
            str(WRAPPER),
            "--print",
            "--output-format",
            "stream-json",
            "--model",
            "ds",
        ],
        input="hello\n",
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert json.loads(output.read_text(encoding="utf-8")) == [
        "--print",
        "--output-format",
        "stream-json",
        "--model",
        "ds",
    ]


def test_claude_rtime_routes_ollama_alias_to_claude_ollama(tmp_path):
    provider, output = _write_capture_wrapper(tmp_path, "ollama")

    env = os.environ.copy()
    env["RTIME_OLLAMA_CLAUDE_WRAPPER"] = str(provider)
    env["PROVIDER_ARGS"] = str(output)

    subprocess.run(
        [
            sys.executable,
            str(WRAPPER),
            "--print",
            "--output-format",
            "stream-json",
            "--model",
            "ollama",
        ],
        input="hello\n",
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert json.loads(output.read_text(encoding="utf-8")) == [
        "--print",
        "--output-format",
        "stream-json",
        "--model",
        "ollama",
    ]


def test_claude_rtime_ustc_agent_zero_uses_inline_chat_not_wrapper(tmp_path):
    """RTIME_USTC_AGENT=0 must NOT touch claude-ustc: the old inline HTTP chat
    path answers instead (regression guard for the rollback switch)."""
    provider, output = _write_capture_wrapper(tmp_path, "ustc-off")
    server = _start_server()
    try:
        env = os.environ.copy()
        env.update(
            {
                "RTIME_USTC_AGENT": "0",
                "RTIME_USTC_CLAUDE_WRAPPER": str(provider),
                "PROVIDER_ARGS": str(output),
                "RTIME_USTC_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
                "RTIME_USTC_API_KEY": "test-key",
                "RTIME_USTC_SESSION_DIR": str(tmp_path / "sessions"),
            }
        )
        completed = subprocess.run(
            [sys.executable, str(WRAPPER), "--print", "--model", "ds"],
            input="hello\n",
            text=True,
            capture_output=True,
            env=env,
            check=True,
        )
    finally:
        server.shutdown()

    assert not output.exists(), "claude-ustc wrapper must not be invoked when RTIME_USTC_AGENT=0"
    events = [json.loads(line) for line in completed.stdout.splitlines()]
    assert events[-1]["result"] == "OK Qwen"
    # The alias still resolves to the real USTC model id on the inline path.
    assert UstcHandler.requests[0]["body"]["model"] == "deepseek-v4-flash-ascend"


def _write_fake_claude(tmp_path: pathlib.Path) -> pathlib.Path:
    fake = tmp_path / "claude.py"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "payload = {\n"
        "  'args': sys.argv[1:],\n"
        "  'env': {\n"
        "    key: os.environ.get(key, '') for key in [\n"
        "      'ANTHROPIC_BASE_URL', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_MODEL',\n"
        "      'ANTHROPIC_DEFAULT_OPUS_MODEL', 'ANTHROPIC_DEFAULT_SONNET_MODEL',\n"
        "      'ANTHROPIC_DEFAULT_HAIKU_MODEL', 'ANTHROPIC_SMALL_FAST_MODEL',\n"
        "      'CLAUDE_CODE_SUBAGENT_MODEL', 'CLAUDE_CODE_EFFORT_LEVEL',\n"
        "      'CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS', 'DISABLE_NON_ESSENTIAL_MODEL_CALLS'\n"
        "    ]\n"
        "  }\n"
        "}\n"
        "pathlib.Path(os.environ['FAKE_CLAUDE_CAPTURE']).write_text(json.dumps(payload))\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake


def test_claude_deepseek_wrapper_configures_anthropic_environment(tmp_path):
    keyfile = tmp_path / "deepseek-key"
    keyfile.write_text("test-deepseek-key", encoding="utf-8")
    capture = tmp_path / "capture.json"

    env = os.environ.copy()
    env.update(
        {
            "CLAUDE_BIN": str(_write_fake_claude(tmp_path)),
            "FAKE_CLAUDE_CAPTURE": str(capture),
            "RTIME_DEEPSEEK_API_KEY_FILE": str(keyfile),
        }
    )

    subprocess.run(
        [str(DEEPSEEK_WRAPPER), "--model", "deepseek-code", "--print"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    payload = json.loads(capture.read_text(encoding="utf-8"))
    assert payload["args"] == ["--model", "deepseek-v4-pro[1m]", "--print"]
    assert payload["env"]["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    assert payload["env"]["ANTHROPIC_AUTH_TOKEN"] == "test-deepseek-key"
    assert payload["env"]["ANTHROPIC_MODEL"] == "deepseek-v4-pro[1m]"
    assert payload["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] == "deepseek-v4-flash"
    assert payload["env"]["CLAUDE_CODE_EFFORT_LEVEL"] == "max"


def test_claude_qwen_wrapper_configures_anthropic_environment(tmp_path):
    keyfile = tmp_path / "qwen-key"
    keyfile.write_text("test-qwen-key", encoding="utf-8")
    capture = tmp_path / "capture.json"

    env = os.environ.copy()
    env.update(
        {
            "CLAUDE_BIN": str(_write_fake_claude(tmp_path)),
            "FAKE_CLAUDE_CAPTURE": str(capture),
            "RTIME_QWEN_API_KEY_FILE": str(keyfile),
        }
    )

    subprocess.run(
        [str(QWEN_WRAPPER), "--model=qwen-code", "--print"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    payload = json.loads(capture.read_text(encoding="utf-8"))
    assert payload["args"] == ["--model=qwen3-coder-next", "--print"]
    assert payload["env"]["ANTHROPIC_BASE_URL"] == "https://dashscope-intl.aliyuncs.com/apps/anthropic"
    assert payload["env"]["ANTHROPIC_AUTH_TOKEN"] == "test-qwen-key"
    assert payload["env"]["ANTHROPIC_MODEL"] == "qwen3-coder-next"
    assert payload["env"]["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "qwen3-coder-plus"
    assert payload["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] == "qwen3-coder-flash"


def test_claude_ustc_wrapper_configures_litellm_environment(tmp_path):
    keyfile = tmp_path / "litellm-master-key"
    keyfile.write_text("sk-test-master\n", encoding="utf-8")
    capture = tmp_path / "capture.json"

    env = os.environ.copy()
    env.pop("RTIME_LITELLM_MASTER_KEY", None)
    env.pop("RTIME_LITELLM_BASE_URL", None)
    env.pop("RTIME_USTC_AGENT_MODEL", None)
    env.update(
        {
            "CLAUDE_BIN": str(_write_fake_claude(tmp_path)),
            "FAKE_CLAUDE_CAPTURE": str(capture),
            "RTIME_LITELLM_MASTER_KEY_FILE": str(keyfile),
        }
    )

    subprocess.run(
        [str(USTC_WRAPPER), "--model", "qwen", "--print"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    payload = json.loads(capture.read_text(encoding="utf-8"))
    # `qwen` alias resolves to the LiteLLM-served USTC id; every tier follows it.
    assert payload["args"] == ["--model", "qwen3.6-chat", "--print"]
    assert payload["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:4000"
    assert payload["env"]["ANTHROPIC_AUTH_TOKEN"] == "sk-test-master"
    assert payload["env"]["ANTHROPIC_MODEL"] == "qwen3.6-chat"
    assert payload["env"]["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "qwen3.6-chat"
    assert payload["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "qwen3.6-chat"
    assert payload["env"]["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "qwen3.6-chat"
    assert payload["env"]["ANTHROPIC_SMALL_FAST_MODEL"] == "qwen3.6-chat"
    assert payload["env"]["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] == "1"
    assert payload["env"]["DISABLE_NON_ESSENTIAL_MODEL_CALLS"] == "1"


def test_claude_ustc_wrapper_defaults_to_registry_model(tmp_path):
    capture = tmp_path / "capture.json"

    env = os.environ.copy()
    env.pop("RTIME_LITELLM_BASE_URL", None)
    env.pop("RTIME_USTC_AGENT_MODEL", None)
    env.update(
        {
            "CLAUDE_BIN": str(_write_fake_claude(tmp_path)),
            "FAKE_CLAUDE_CAPTURE": str(capture),
            "RTIME_LITELLM_MASTER_KEY": "sk-env-master",
            "RTIME_LITELLM_BASE_URL": "http://litellm:4000",
        }
    )

    subprocess.run(
        [str(USTC_WRAPPER), "--print"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    payload = json.loads(capture.read_text(encoding="utf-8"))
    assert payload["args"] == ["--print"]
    assert payload["env"]["ANTHROPIC_BASE_URL"] == "http://litellm:4000"
    assert payload["env"]["ANTHROPIC_AUTH_TOKEN"] == "sk-env-master"
    assert payload["env"]["ANTHROPIC_MODEL"] == "deepseek-v4-flash-ascend"


def test_claude_ustc_wrapper_fails_without_master_key(tmp_path):
    env = os.environ.copy()
    env.pop("RTIME_LITELLM_MASTER_KEY", None)
    env["RTIME_LITELLM_MASTER_KEY_FILE"] = str(tmp_path / "missing-key")
    env["CLAUDE_BIN"] = str(_write_fake_claude(tmp_path))
    env["FAKE_CLAUDE_CAPTURE"] = str(tmp_path / "capture.json")

    completed = subprocess.run(
        [str(USTC_WRAPPER), "--print"],
        text=True,
        capture_output=True,
        env=env,
    )

    assert completed.returncode == 1
    assert "missing LiteLLM master key" in completed.stderr


def test_claude_ollama_wrapper_configures_anthropic_environment(tmp_path):
    capture = tmp_path / "capture.json"

    env = os.environ.copy()
    env.pop("RTIME_OLLAMA_BASE_URL", None)
    env.pop("RTIME_OLLAMA_MODEL", None)
    env.update(
        {
            "CLAUDE_BIN": str(_write_fake_claude(tmp_path)),
            "FAKE_CLAUDE_CAPTURE": str(capture),
        }
    )

    subprocess.run(
        [str(OLLAMA_WRAPPER), "--model=qwen-local", "--print"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    payload = json.loads(capture.read_text(encoding="utf-8"))
    assert payload["args"] == ["--model=qwen3.5:9b", "--print"]
    assert payload["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:11434"
    assert payload["env"]["ANTHROPIC_AUTH_TOKEN"] == "ollama"
    assert payload["env"]["ANTHROPIC_MODEL"] == "qwen3.5:9b"
    # All three tiers pin to the ONE model (Jetson model-swap avoidance).
    assert payload["env"]["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "qwen3.5:9b"
    assert payload["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "qwen3.5:9b"
    assert payload["env"]["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "qwen3.5:9b"
    assert payload["env"]["ANTHROPIC_SMALL_FAST_MODEL"] == "qwen3.5:9b"
    assert payload["env"]["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] == "1"
    assert payload["env"]["DISABLE_NON_ESSENTIAL_MODEL_CALLS"] == "1"


def test_claude_ollama_wrapper_repins_all_tiers_to_explicit_model(tmp_path):
    capture = tmp_path / "capture.json"

    env = os.environ.copy()
    env.pop("RTIME_OLLAMA_MODEL", None)
    env.update(
        {
            "CLAUDE_BIN": str(_write_fake_claude(tmp_path)),
            "FAKE_CLAUDE_CAPTURE": str(capture),
            "RTIME_OLLAMA_BASE_URL": "http://127.0.0.1:21434",
        }
    )

    subprocess.run(
        [str(OLLAMA_WRAPPER), "--model", "qwen2.5:3b", "--print"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    payload = json.loads(capture.read_text(encoding="utf-8"))
    assert payload["args"] == ["--model", "qwen2.5:3b", "--print"]
    assert payload["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:21434"
    assert payload["env"]["ANTHROPIC_MODEL"] == "qwen2.5:3b"
    assert payload["env"]["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "qwen2.5:3b"
    assert payload["env"]["ANTHROPIC_SMALL_FAST_MODEL"] == "qwen2.5:3b"
