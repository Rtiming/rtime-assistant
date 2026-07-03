# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""T2 acceptance gate — the read_only END-TO-END hard door (defect #5, the #5 handoff).

This REPLACES the deleted T1 boundary-lock test (``rtime-config/tests/
test_profile_boundary.py``), which asserted the GAP: a profile ``permissions.
read_only: true`` compiled to ``qq.read_only=True`` but did NOT enforce read-only
because the bridge never consumed the profile layer.

T2 connects the consumption chain. These tests prove the door is now DRIVEN by the
profile: a studentunion-shaped profile (read_only:true), built via
``QQBridgeConfig.from_profile`` and run through the REAL ``process_event`` seam
(design §3.1) with a ``FakeModelRunner`` (§3.2, zero network/subprocess), makes the
run's ``permission_mode == dontAsk`` AND the disallowed set ⊇ {Edit, Write, Task}
(the full write-tool deny set) — identical to the ``QQ_READ_ONLY=1`` env behaviour,
now also driven by the profile. A non-read_only profile does NOT force it.

Skips cleanly if admin-core / the profile loader are unavailable (same guard the
boundary test used), so a bare qq-bridge checkout still runs its other suites.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

BOT = "479"
OWNER = "2000000001"  # 假号夹具
GROUP = "100000001"  # 假群号夹具

_HAS_PROFILE_STACK = (
    importlib.util.find_spec("rtime_admin_core") is not None
    and importlib.util.find_spec("rtime_config.profile") is not None
)
pytestmark = pytest.mark.skipif(
    not _HAS_PROFILE_STACK,
    reason="rtime-admin-core / rtime-config profile loader not importable",
)


def _write_profiles(root: Path, *, read_only: bool) -> str:
    """Write a minimal _base/qq + studentunion-shaped profile; return its id."""
    (root / "_base" / "prompts").mkdir(parents=True)
    (root / "_base" / "prompts" / "qq-system.md").write_text(
        "base prompt\n", encoding="utf-8"
    )
    (root / "_base" / "qq.yaml").write_text(
        "schema_version: 1\nprofile:\n  id: _base-qq\n",
        encoding="utf-8",
    )
    pid = "su_readonly" if read_only else "su_writable"
    pdir = root / pid
    (pdir / "prompts").mkdir(parents=True)
    (pdir / "prompts" / "system.md").write_text(
        "你是学生会答疑助手。\n", encoding="utf-8"
    )
    ro = "true" if read_only else "false"
    (pdir / "profile.yaml").write_text(
        "schema_version: 1\n"
        f"profile:\n  id: {pid}\n  extends: _base/qq\n"
        "identity:\n  name: 学生会答疑助手\n  system_prompt_file: prompts/system.md\n"
        "model:\n  default: ds\n"
        f"permissions:\n  read_only: {ro}\n  permission_mode: dontAsk\n"
        "users:\n  admins: ['" + OWNER + "']\n"
        "channels:\n  qq:\n"
        f"    public_groups: ['{GROUP}']\n"
        f"    group_allowlist: ['{GROUP}']\n"
        "    autoleave: false\n",
        encoding="utf-8",
    )
    return pid


def _build_cfg(tmp_path: Path, *, read_only: bool, permission_mode: str):
    from qq_bridge.config import QQBridgeConfig

    root = tmp_path / "profiles"
    pid = _write_profiles(root, read_only=read_only)
    cfg = QQBridgeConfig.from_profile(pid, profiles_root=str(root))
    # A model CLI + sessions dir so build_model_handler runs (not echo); the model
    # side is the FakeModelRunner so nothing spawns. permission_mode carries the
    # session default the door must (or must not) override.
    return cfg.model_copy(
        update={
            "claude_cli": "/x/claude",
            "sessions_dir": str(tmp_path / "sessions"),
            "permission_mode": permission_mode,
        }
    )


def _process(cfg, event):
    from qq_bridge.app import build_pipeline
    from rtime_chat_runtime.testing import FakeModelRunner

    runner = FakeModelRunner("答案")
    pipeline = build_pipeline(cfg, model_runner=runner)
    asyncio.run(pipeline.process_event(event))
    return runner


def test_profile_read_only_forces_dontask_and_write_deny(tmp_path, monkeypatch):
    """profile read_only:true -> dontAsk + write-tool deny set (the acceptance gate)."""
    # No env flag: the door must be driven by the PROFILE, not QQ_READ_ONLY.
    monkeypatch.delenv("QQ_READ_ONLY", raising=False)
    from rtime_chat_runtime.testing import make_qq_group_at

    cfg = _build_cfg(
        tmp_path,
        read_only=True,
        permission_mode="bypassPermissions",  # session default MUST NOT be trusted
    )
    assert cfg.read_only is True  # profile reached config (consumption chain)

    runner = _process(
        cfg,
        make_qq_group_at(GROUP, "222", "宿舍报修流程", at_bot=True, self_id=BOT),
    )
    call = runner.last
    # permission_mode forced to READONLY (dontAsk), NOT the session's bypass.
    assert call.permission_mode == "dontAsk"
    # the full write-tool deny set (headline assertion ⊇ {Edit, Write, Task}).
    for tool in ("Edit", "Write", "MultiEdit", "NotebookEdit", "Task", "Agent"):
        assert tool in call.disallowed_tools, tool
    # bare Bash never in deny (deny > allow would kill Bash(rtime-web-fetch *)).
    assert "Bash" not in call.disallowed_tools
    # closed read-only allowlist reached the runner (personal-lib unlock ignored).
    assert "Read" in call.allowed_tools and "Grep" in call.allowed_tools
    assert "mcp__rtime-library-gateway__*" in call.allowed_tools


def test_profile_not_read_only_does_not_force_door(tmp_path, monkeypatch):
    """A non-read_only profile leaves the session permission mode + write tools alone."""
    monkeypatch.delenv("QQ_READ_ONLY", raising=False)
    from rtime_chat_runtime.testing import make_qq_private

    cfg = _build_cfg(
        tmp_path,
        read_only=False,
        permission_mode="bypassPermissions",
    )
    assert cfg.read_only is False

    runner = _process(cfg, make_qq_private(OWNER, "帮我看下课程表", self_id=BOT))
    call = runner.last
    # not forced: the session default survives, no write-tool deny, all tools allowed.
    assert call.permission_mode == "bypassPermissions"
    assert "Write" not in call.disallowed_tools
    assert "Edit" not in call.disallowed_tools
    assert call.allowed_tools is None  # plain text: all tools


def test_profile_read_only_ignores_personal_lib_and_write_hints(tmp_path, monkeypatch):
    """Under a profile read_only, personal-lib unlock is ignored + narrow-write hints
    are suppressed even for a message that would otherwise trigger them (§2.9)."""
    monkeypatch.delenv("QQ_READ_ONLY", raising=False)
    # even with the personal-library env unlock set, read_only must ignore it.
    monkeypatch.setenv("QQ_OWNER_PERSONAL_LIBRARY_ACCESS", "1")
    from rtime_chat_runtime.testing import make_qq_group_at

    cfg = _build_cfg(tmp_path, read_only=True, permission_mode="default")
    # a message that hits both the personal-library intent AND the reminder intent.
    runner = _process(
        cfg,
        make_qq_group_at(
            GROUP,
            "222",
            "看下我的个人档案，然后提醒我明天交表",
            at_bot=True,
            self_id=BOT,
        ),
    )
    call = runner.last
    # closed read-only allowlist: personal-library read tools NOT unlocked, reminder
    # write tool NOT added; only the read-only base set (+web) is present.
    assert call.allowed_tools is not None
    assert "Bash(rtime-reminder-register *)" not in call.allowed_tools
    # personal-library unlock adds Read/Glob/Grep/LS — but those are ALSO in the
    # read-only base, so assert the personal-library HINT was suppressed in the prompt
    # instead (the observable signal that the unlock path did not run).
    assert "owner 明确授权的单用户" not in call.prompt  # personal-library hint text
    assert "rtime-reminder-register add" not in call.prompt  # reminder hint suppressed
    assert call.permission_mode == "dontAsk"


def test_env_read_only_still_wins_over_writable_profile(tmp_path, monkeypatch):
    """QQ_READ_ONLY=1 with a writable profile STILL forces the door (env is top layer)."""
    monkeypatch.setenv("QQ_READ_ONLY", "1")
    from rtime_chat_runtime.testing import make_qq_private

    cfg = _build_cfg(
        tmp_path,
        read_only=False,  # profile says writable...
        permission_mode="bypassPermissions",
    )
    assert cfg.read_only is True  # ...but env QQ_READ_ONLY=1 won (top layer)

    runner = _process(cfg, make_qq_private(OWNER, "问题", self_id=BOT))
    call = runner.last
    assert call.permission_mode == "dontAsk"
    assert "Write" in call.disallowed_tools


# =====================================================================
# SECURITY: fail-closed union — env=0 must NOT downgrade a profile restriction.
# This is the high-severity fix: read_only is a monotonic-true restriction field;
# env can only UPGRADE it (=1), never DOWNGRADE it (=0 must not disable a profile's
# read_only). compose.prod.yml's ${QQ_READ_ONLY:-0} default made this a fail-OPEN.
# =====================================================================
def test_env_read_only_zero_does_not_downgrade_profile_readonly(tmp_path, monkeypatch):
    """QQ_READ_ONLY=0 + read_only:true profile -> door STAYS ON (fail-closed union).

    This is the regression for the downgrade vuln: before the union fix, env=0 pulled
    config.read_only down to False and silently bypassed the hard door in prod (the
    compose default injects 0). It must now be True.
    """
    monkeypatch.setenv("QQ_READ_ONLY", "0")  # compose.prod.yml's default injection
    from qq_bridge.tool_policy import policy_for_config
    from rtime_chat_runtime.testing import make_qq_group_at

    cfg = _build_cfg(tmp_path, read_only=True, permission_mode="bypassPermissions")
    # config.read_only stays True despite env=0 (env cannot downgrade the profile).
    assert cfg.read_only is True
    # and the policy hard door is ON.
    assert policy_for_config(cfg).is_read_only() is True

    runner = _process(
        cfg, make_qq_group_at(GROUP, "222", "报修流程", at_bot=True, self_id=BOT)
    )
    call = runner.last
    assert call.permission_mode == "dontAsk"  # forced, NOT the session bypass
    for tool in ("Edit", "Write", "Task"):
        assert tool in call.disallowed_tools, tool


def test_env_read_only_zero_leaves_writable_profile_writable(tmp_path, monkeypatch):
    """QQ_READ_ONLY=0 + read_only:false profile -> writable (union does not over-restrict).

    The union only ADDS restrictions; when no layer asserts read_only, it stays off.
    """
    monkeypatch.setenv("QQ_READ_ONLY", "0")
    from qq_bridge.tool_policy import policy_for_config
    from rtime_chat_runtime.testing import make_qq_private

    cfg = _build_cfg(tmp_path, read_only=False, permission_mode="default")
    assert cfg.read_only is False
    assert policy_for_config(cfg).is_read_only() is False

    runner = _process(cfg, make_qq_private(OWNER, "帮我改下笔记", self_id=BOT))
    call = runner.last
    assert call.permission_mode == "default"
    assert "Write" not in call.disallowed_tools
