# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import os
import sys

import pytest

os.environ.setdefault("FEISHU_APP_ID", "test_app_id")
os.environ.setdefault("FEISHU_APP_SECRET", "test_app_secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import session_store as session_store_module
import commands as commands_module
from commands import handle_command
from session_store import SessionStore


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "state"
    sessions_dir.mkdir()
    monkeypatch.setattr(session_store_module, "SESSIONS_DIR", str(sessions_dir))
    monkeypatch.setattr(session_store_module, "SESSIONS_FILE", str(sessions_dir / "sessions.json"))
    return SessionStore()


@pytest.mark.asyncio
async def test_model_menu_prefers_common_aliases_and_explains_ustc(isolated_store, monkeypatch):
    monkeypatch.setattr(
        commands_module,
        "MODEL_ALIASES",
        {
            "opus": "claude-opus-4-6",
            "sonnet": "claude-sonnet-4-6",
            "haiku": "claude-haiku-4-5-20251001",
            "kimi": "",
            "deepseek-code": "deepseek-v4-pro[1m]",
            "qwen-code": "qwen3-coder-next",
            "ds": "deepseek-v4-flash-ascend",
            "deepseek": "deepseek-v4-flash-ascend",
            "qwen": "qwen3.6-chat",
            "qianwen": "qwen3.6-chat",
            "qwen-reasoner": "qwen3.6-reasoner",
        },
    )

    result = await handle_command("models", "", "user_123", "chat_001", isolated_store)

    assert "当前模型" in result["text"]
    assert "Claude Code" in result["text"]
    labels = [button["text"] for button in result["buttons"]]
    assert labels[:6] == [
        "Kimi Code",
        "DeepSeek Code",
        "Qwen Code",
        "USTC DeepSeek Chat",
        "USTC Qwen Chat",
        "USTC Qwen 推理",
    ]
    assert labels.count("DeepSeek Code") == 1
    assert labels.count("Qwen Code") == 1
    assert labels.count("USTC DeepSeek Chat") == 1
    assert labels.count("USTC Qwen Chat") == 1


@pytest.mark.asyncio
async def test_model_command_switches_to_ustc_alias_with_capability_note(isolated_store, monkeypatch):
    monkeypatch.setattr(
        commands_module,
        "MODEL_ALIASES",
        {"qwen": "qwen3.6-chat", "ds": "deepseek-v4-flash-ascend", "kimi": ""},
    )

    reply = await handle_command("model", "qwen", "user_123", "chat_001", isolated_store)
    current = await isolated_store.get_current("user_123", "chat_001")

    assert current.model == "qwen3.6-chat"
    assert "USTC chat 模型" in reply


@pytest.mark.asyncio
async def test_model_command_switches_to_code_alias_with_tool_note(isolated_store, monkeypatch):
    monkeypatch.setattr(
        commands_module,
        "MODEL_ALIASES",
        {
            "deepseek-code": "deepseek-v4-pro[1m]",
            "qwen-code": "qwen3-coder-next",
        },
    )

    reply = await handle_command("model", "deepseek-code", "user_123", "chat_001", isolated_store)
    current = await isolated_store.get_current("user_123", "chat_001")

    assert current.model == "deepseek-v4-pro[1m]"
    assert "DeepSeek Code" in reply
    assert "工具调用" in reply


@pytest.mark.asyncio
async def test_workspace_binding_isolated_per_group(isolated_store, tmp_path):
    user_id = "user_123"
    group_a = "group_a"
    group_b = "group_b"
    project1 = tmp_path / "project1"
    project2 = tmp_path / "project2"
    project1.mkdir()
    project2.mkdir()

    reply1 = await handle_command("workspace", f'save proj1 "{project1}"', user_id, group_a, isolated_store)
    reply2 = await handle_command("workspace", f'save proj2 "{project2}"', user_id, group_a, isolated_store)
    bind1 = await handle_command("workspace", "use proj1", user_id, group_a, isolated_store)
    bind2 = await handle_command("workspace", "use proj2", user_id, group_b, isolated_store)

    session_a = await isolated_store.get_current(user_id, group_a)
    session_b = await isolated_store.get_current(user_id, group_b)

    assert "已保存工作空间" in reply1
    assert "已保存工作空间" in reply2
    assert "当前群组已绑定工作空间 `proj1`" in bind1
    assert "当前群组已绑定工作空间 `proj2`" in bind2
    assert session_a.workspace == "proj1"
    assert session_a.cwd == str(project1)
    assert session_b.workspace == "proj2"
    assert session_b.cwd == str(project2)


@pytest.mark.asyncio
async def test_workspace_save_uses_current_cwd_by_default(isolated_store, tmp_path):
    user_id = "user_123"
    chat_id = "group_001"
    project = tmp_path / "project"
    project.mkdir()

    await isolated_store.set_cwd(user_id, chat_id, str(project))
    reply = await handle_command("workspace", "save backend", user_id, chat_id, isolated_store)

    assert "已保存工作空间 `backend`" in reply
    assert isolated_store.list_workspaces(user_id)["backend"] == str(project)


@pytest.mark.asyncio
async def test_cd_clears_named_workspace_binding(isolated_store, tmp_path):
    user_id = "user_123"
    chat_id = "group_001"
    project = tmp_path / "project"
    other = tmp_path / "other"
    project.mkdir()
    other.mkdir()

    await handle_command("workspace", f'save backend "{project}"', user_id, chat_id, isolated_store)
    await handle_command("workspace", "use backend", user_id, chat_id, isolated_store)

    reply = await handle_command("cd", str(other), user_id, chat_id, isolated_store)
    current = await isolated_store.get_current(user_id, chat_id)

    assert "解除原工作空间绑定" in reply
    assert current.workspace == ""
    assert current.cwd == str(other)


@pytest.mark.asyncio
async def test_ls_lists_current_workspace_contents(isolated_store, tmp_path):
    user_id = "user_123"
    chat_id = "group_001"
    project = tmp_path / "project"
    project.mkdir()
    (project / "src").mkdir()
    (project / "README.md").write_text("hi", encoding="utf-8")

    await isolated_store.set_cwd(user_id, chat_id, str(project))
    reply = await handle_command("ls", "", user_id, chat_id, isolated_store)

    assert "目录内容" in reply
    assert f"绝对路径：`{project}`" in reply
    assert "`src/`" in reply
    assert "`README.md`" in reply


@pytest.mark.asyncio
async def test_ls_supports_relative_subdir(isolated_store, tmp_path):
    user_id = "user_123"
    chat_id = "group_001"
    project = tmp_path / "project"
    nested = project / "backend"
    project.mkdir()
    nested.mkdir()
    (nested / "app.py").write_text("print('ok')", encoding="utf-8")

    await isolated_store.set_cwd(user_id, chat_id, str(project))
    reply = await handle_command("ls", "backend", user_id, chat_id, isolated_store)

    assert "请求路径：`backend`" in reply
    assert f"绝对路径：`{nested}`" in reply
    assert "`app.py`" in reply
