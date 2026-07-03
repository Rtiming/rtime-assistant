# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""T8 热调项落地 (design §2.10): the HOT profile fields take effect on the next
message WITHOUT rebuilding the pipeline / restarting the container, and the steady
state adds NO per-message file I/O beyond a stat (the owner no-regression gate).

Everything runs the REAL chain: a ``ConfigProvider`` over an on-disk profile fed
into ``build_pipeline(..., config_provider=provider)``, driven by synthetic OneBot
events through ``process_event`` with a ``FakeModelRunner`` (zero network / subprocess).

Hot fields asserted here (§2.10): system_prompt content, model.default, the user
lists (allowed_users → a previously-denied user is served after a reload), and the
direct_rules file. RESTART-level fields (read_only, mcp_config, channels) are covered
by ``test_profile_readonly_e2e`` and are deliberately NOT hot.

Skips cleanly if the profile stack (admin-core / rtime-config profile loader) is
unavailable, same guard the other profile suites use.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
from pathlib import Path

import pytest

BOT = "479"
ADMIN = "111"
ALLOWED = "222"
STRANGER = "333"
GROUP = "600"

_HAS_PROFILE_STACK = (
    importlib.util.find_spec("rtime_admin_core") is not None
    and importlib.util.find_spec("rtime_config.profile") is not None
)
pytestmark = pytest.mark.skipif(
    not _HAS_PROFILE_STACK,
    reason="rtime-admin-core / rtime-config profile loader not importable",
)


def _bump_mtime(path: Path, delta: float = 5.0) -> None:
    """Push a file's mtime forward so the (mtime,size) signature always moves,
    even on a coarse-resolution FS where a same-second rewrite would not."""
    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime + delta))


def _write_base(root: Path) -> None:
    (root / "_base" / "prompts").mkdir(parents=True, exist_ok=True)
    (root / "_base" / "prompts" / "qq-system.md").write_text("base\n", encoding="utf-8")
    (root / "_base" / "qq.yaml").write_text(
        "schema_version: 1\nprofile:\n  id: _base-qq\n", encoding="utf-8"
    )


def _write_profile(
    root: Path,
    *,
    pid: str = "su",
    model: str = "ds",
    prompt: str = "第一版提示词",
    allowed: list[str] | None = None,
) -> str:
    _write_base(root)
    pdir = root / pid
    (pdir / "prompts").mkdir(parents=True, exist_ok=True)
    (pdir / "prompts" / "system.md").write_text(prompt + "\n", encoding="utf-8")
    allowed_line = ""
    if allowed is not None:
        ids = ", ".join(f"'{a}'" for a in allowed)
        allowed_line = f"  allowed: [{ids}]\n"
    (pdir / "profile.yaml").write_text(
        "schema_version: 1\n"
        f"profile:\n  id: {pid}\n  extends: _base/qq\n"
        "identity:\n  name: 学生会答疑助手\n  system_prompt_file: prompts/system.md\n"
        f"model:\n  default: {model}\n"
        "permissions:\n  read_only: false\n"
        "users:\n  admins: ['" + ADMIN + "']\n" + allowed_line,
        encoding="utf-8",
    )
    return pid


def _provider(root: Path, pid: str, tmp_path: Path):
    """A ConfigProvider over the profile, patched with a model CLI + sessions dir so
    build_model_handler runs the FakeModelRunner path (not echo)."""
    from qq_bridge.config import ConfigProvider, QQBridgeConfig

    config, watch = QQBridgeConfig._build_from_profile(pid, profiles_root=str(root))
    config = config.model_copy(
        update={
            "claude_cli": "/x/claude",
            "sessions_dir": str(tmp_path / "sessions"),
        }
    )
    prov = ConfigProvider(
        config, watch_files=watch, profile_id=pid, profiles_root=str(root)
    )
    # keep the CLI/sessions patch across hot rebuilds: wrap the rebuild so re-reads
    # keep the test-only fields (a real deploy gets these from env, not needed here).
    orig_current = prov.current

    def current():
        cfg = orig_current()
        if cfg.claude_cli != "/x/claude":
            cfg = cfg.model_copy(
                update={
                    "claude_cli": "/x/claude",
                    "sessions_dir": str(tmp_path / "sessions"),
                }
            )
            prov._config = cfg
        return cfg

    prov.current = current
    return prov


def _pipeline(prov):
    from qq_bridge.app import build_pipeline
    from rtime_chat_runtime.testing import FakeModelRunner

    runner = FakeModelRunner("答案")
    pipeline = build_pipeline(prov.current(), model_runner=runner, config_provider=prov)
    return pipeline, runner


def _send(pipeline, event):
    return asyncio.run(pipeline.process_event(event))


# =====================================================================
# HOT: system_prompt content re-reads on the next message after an edit
# =====================================================================
def test_system_prompt_hot_reloads(tmp_path, monkeypatch):
    monkeypatch.delenv("QQ_SYSTEM_PROMPT", raising=False)
    from rtime_chat_runtime.testing import make_qq_private

    root = tmp_path / "profiles"
    pid = _write_profile(root, prompt="第一版提示词")
    prov = _provider(root, pid, tmp_path)
    pipeline, runner = _pipeline(prov)

    _send(pipeline, make_qq_private(ADMIN, "在吗", self_id=BOT))
    assert runner.last.system_prompt.strip() == "第一版提示词"

    # edit the prompt FILE (not the yaml); the provider watches it.
    (root / pid / "prompts" / "system.md").write_text(
        "第二版提示词\n", encoding="utf-8"
    )
    _bump_mtime(root / pid / "prompts" / "system.md")

    _send(pipeline, make_qq_private(ADMIN, "再问", self_id=BOT))
    # same pipeline object, no restart — the new prompt reached the runner.
    assert runner.last.system_prompt.strip() == "第二版提示词"


# =====================================================================
# HOT: model.default re-reads on the next message after an edit
# =====================================================================
def test_model_default_hot_reloads(tmp_path, monkeypatch):
    monkeypatch.delenv("DEFAULT_MODEL", raising=False)
    from rtime_chat_runtime.testing import make_qq_private

    root = tmp_path / "profiles"
    pid = _write_profile(root, model="ds")
    prov = _provider(root, pid, tmp_path)
    pipeline, runner = _pipeline(prov)

    # a non-admin (allowed) user is pinned to the instance default -> model==ds.
    _write_profile(root, model="ds", allowed=[ALLOWED])
    _bump_mtime(root / pid / "profile.yaml")
    _send(pipeline, make_qq_private(ALLOWED, "问题", self_id=BOT))
    assert runner.last.model == "ds"

    # change the default in the profile.yaml; bump mtime.
    _write_profile(root, model="kimi", allowed=[ALLOWED])
    _bump_mtime(root / pid / "profile.yaml")
    _send(pipeline, make_qq_private(ALLOWED, "再问", self_id=BOT))
    assert runner.last.model == "kimi"  # hot: instance default changed live


# =====================================================================
# HOT: user lists — a newly-allowed user is served after the reload
# =====================================================================
def test_allowed_users_hot_reload_admits_new_user(tmp_path, monkeypatch):
    from rtime_chat_runtime.testing import make_qq_private

    root = tmp_path / "profiles"
    pid = _write_profile(root, allowed=[])  # only ADMIN may private-chat
    prov = _provider(root, pid, tmp_path)
    pipeline, runner = _pipeline(prov)

    # STRANGER is denied (not admin, not allowed).
    actions = _send(pipeline, make_qq_private(STRANGER, "让我进来", self_id=BOT))
    assert actions == [] and runner.calls == []

    # add STRANGER to allowed; bump mtime.
    _write_profile(root, allowed=[STRANGER])
    _bump_mtime(root / pid / "profile.yaml")

    actions = _send(pipeline, make_qq_private(STRANGER, "现在呢", self_id=BOT))
    assert len(runner.calls) == 1  # now served, no restart
    assert any("答案" in a.message_text for a in actions)


def test_blocked_user_hot_reload_denies(tmp_path, monkeypatch):
    """Adding a user to the profile blocklist takes effect live (fail-closed field)."""
    from rtime_chat_runtime.testing import make_qq_private

    root = tmp_path / "profiles"
    pid = _write_profile(root, allowed=[ALLOWED])
    prov = _provider(root, pid, tmp_path)
    pipeline, runner = _pipeline(prov)

    _send(pipeline, make_qq_private(ALLOWED, "问题", self_id=BOT))
    assert len(runner.calls) == 1  # allowed user served

    # block them via the profile users.blocked; bump mtime.
    _write_base(root)
    pdir = root / pid
    (pdir / "profile.yaml").write_text(
        "schema_version: 1\n"
        f"profile:\n  id: {pid}\n  extends: _base/qq\n"
        "identity:\n  system_prompt_file: prompts/system.md\n"
        "model:\n  default: ds\n"
        "permissions:\n  read_only: false\n"
        "users:\n  admins: ['" + ADMIN + "']\n"
        f"  allowed: ['{ALLOWED}']\n"
        f"  blocked: ['{ALLOWED}']\n",  # blocked wins over allowed
        encoding="utf-8",
    )
    _bump_mtime(pdir / "profile.yaml")

    before = len(runner.calls)
    actions = _send(pipeline, make_qq_private(ALLOWED, "再问", self_id=BOT))
    assert actions == [] and len(runner.calls) == before  # now blocked, live


# =====================================================================
# PERF (owner hard constraint): an UNCHANGED config adds NO file I/O beyond a stat.
# =====================================================================
def test_unchanged_config_does_no_extra_io_beyond_stat(tmp_path, monkeypatch):
    """The steady state (config unchanged between messages) must NOT open/read any
    profile file — only stat(). We prime the provider, then make ``open`` blow up
    for the watched profile files and confirm ``current()`` still returns the cached
    config (i.e. it took the fast path: stat only, no re-read/recompile). This is the
    §2.10 no-per-message-latency guarantee."""
    import builtins

    root = tmp_path / "profiles"
    pid = _write_profile(root, allowed=[ALLOWED])
    prov = _provider(root, pid, tmp_path)
    # prime: one full build already happened in _provider; call once more to settle.
    cfg1 = prov.current()

    watched = set(prov._watch)
    assert watched, "provider must watch at least the profile.yaml"

    real_open = builtins.open

    def _guard_open(f, *a, **k):
        if str(f) in watched:
            raise AssertionError(f"unchanged config re-opened a watched file: {f}")
        return real_open(f, *a, **k)

    # also guard against a full recompile (which would import + re-run the loader).
    def _no_rebuild(*_a, **_k):
        raise AssertionError("unchanged config must not recompile the profile")

    monkeypatch.setattr(builtins, "open", _guard_open)
    from qq_bridge.config import QQBridgeConfig

    monkeypatch.setattr(QQBridgeConfig, "_build_from_profile", _no_rebuild)

    # Several current() calls on the unchanged config: stat only, no open, no rebuild.
    for _ in range(5):
        cfg = prov.current()
    assert cfg is cfg1  # same cached object


def test_provider_current_on_unchanged_config_only_stats(tmp_path, monkeypatch):
    """Micro-check (owner hard constraint, §2.10): the per-message config read on an
    UNCHANGED profile does ONLY os.stat calls — zero open()/read of any profile file.

    Counts real I/O syscalls the hot path could make: we allow os.stat (the whole
    design of the mtime cache) but assert builtins.open is NEVER called for a watched
    file across repeated current() calls, and the recompile never runs. This is the
    concrete evidence that hot-reload adds no per-message file-read latency."""
    import builtins

    root = tmp_path / "profiles"
    pid = _write_profile(root, allowed=[ALLOWED])
    prov = _provider(root, pid, tmp_path)
    prov.current()  # prime the cache

    watched = set(prov._watch)
    stat_calls = {"n": 0}
    real_stat = os.stat

    def _count_stat(path, *a, **k):
        if str(path) in watched:
            stat_calls["n"] += 1
        return real_stat(path, *a, **k)

    real_open = builtins.open

    def _guard_open(f, *a, **k):
        assert str(f) not in watched, f"hot path opened a watched file: {f}"
        return real_open(f, *a, **k)

    from qq_bridge.config import QQBridgeConfig

    def _no_rebuild(*_a, **_k):
        raise AssertionError("hot path recompiled the profile on unchanged config")

    monkeypatch.setattr(os, "stat", _count_stat)
    monkeypatch.setattr(builtins, "open", _guard_open)
    monkeypatch.setattr(QQBridgeConfig, "_build_from_profile", _no_rebuild)

    for _ in range(3):
        prov.current()
    # stats happened (mtime cache), opens/rebuilds did not.
    assert stat_calls["n"] >= 3  # at least one stat per watched file per call
    # (the _guard_open / _no_rebuild asserts would have fired otherwise)


def test_stat_only_signature_moves_only_on_real_change(tmp_path):
    """The provider's stat signature is stable across calls on an unchanged file and
    moves only when a watched file actually changes (drives the rebuild decision)."""
    root = tmp_path / "profiles"
    pid = _write_profile(root)
    prov = _provider(root, pid, tmp_path)
    sig1 = prov._stat_all()
    sig2 = prov._stat_all()
    assert sig1 == sig2  # unchanged: identical signature (pure stat)

    (root / pid / "prompts" / "system.md").write_text("变了\n", encoding="utf-8")
    _bump_mtime(root / pid / "prompts" / "system.md")
    assert prov._stat_all() != sig1  # real change moves the signature
