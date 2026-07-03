# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
# -*- coding: utf-8 -*-
"""WEMP_* 环境变量新名优先 / 旧名兜底的最小回归测试。

archiver.py 顶层要 import markdownify/fastapi/uvicorn(归档服务的重依赖),本机
未必装齐。这里只验环境变量解析这一段纯逻辑,故用 sys.modules stub 把重依赖挡掉,
再按文件路径加载 archiver 模块,拿到 _env_compat + 顶层解析出的 WEMP/USER/PWD。

跑法:.venv/bin/python -m pytest tools/chat-intake/wechat-archiver/test_env_compat.py -q
"""
import importlib.util
import os
import sys
import types

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ARCHIVER_PY = os.path.join(_HERE, "archiver.py")


def _load_archiver(env):
    """在给定 env 下(其余 WEMP_* 清空)重新加载 archiver.py,返回模块。

    stub 掉 markdownify/fastapi/uvicorn 这些顶层重依赖,只为拿到纯逻辑。
    """
    # 清掉所有 WEMP_* 以隔离用例,再套上本用例的 env。
    saved = {k: os.environ[k] for k in list(os.environ) if k.startswith("WEMP_")}
    for k in saved:
        del os.environ[k]
    os.environ.update(env)

    stubbed = []
    for name in ("markdownify", "fastapi", "fastapi.responses", "uvicorn"):
        if name not in sys.modules:
            mod = types.ModuleType(name)
            if name == "markdownify":
                mod.markdownify = lambda *a, **k: ""
            if name == "fastapi":
                mod.FastAPI = lambda *a, **k: types.SimpleNamespace(
                    post=lambda *a, **k: (lambda f: f),
                    get=lambda *a, **k: (lambda f: f),
                )
            if name == "fastapi.responses":
                mod.JSONResponse = lambda *a, **k: None
            if name == "uvicorn":
                mod.run = lambda *a, **k: None
            sys.modules[name] = mod
            stubbed.append(name)
    try:
        spec = importlib.util.spec_from_file_location("archiver_under_test", _ARCHIVER_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for name in stubbed:
            sys.modules.pop(name, None)
        for k in list(os.environ):
            if k.startswith("WEMP_"):
                del os.environ[k]
        os.environ.update(saved)


def test_new_names_win():
    m = _load_archiver(
        {
            "WEMP_BASE_URL": "http://new:9001",
            "WEMP_USERNAME": "newuser",
            "WEMP_PASSWORD": "newpass",
        }
    )
    assert m.WEMP == "http://new:9001"
    assert m.USER == "newuser"
    assert m.PWD == "newpass"


def test_old_names_fallback(capsys):
    m = _load_archiver(
        {
            "WEMP_BASE": "http://old:9002",
            "WEMP_USER": "olduser",
            "WEMP_PWD": "oldpass",
        }
    )
    assert m.WEMP == "http://old:9002"
    assert m.USER == "olduser"
    assert m.PWD == "oldpass"
    # 读旧名要向 stderr 打弃用告警(每个旧名一次)。
    err = capsys.readouterr().err
    assert "WEMP_BASE" in err and "WEMP_BASE_URL" in err
    assert "deprecated" in err


def test_new_name_wins_over_old():
    m = _load_archiver(
        {
            "WEMP_BASE_URL": "http://new:9001",
            "WEMP_BASE": "http://old:9002",
        }
    )
    assert m.WEMP == "http://new:9001"


def test_defaults_when_unset():
    m = _load_archiver({})
    assert m.WEMP == "http://127.0.0.1:8001"
    assert m.USER == "admin"
    assert m.PWD == ""  # 无默认密码:凭据只走env


def test_env_compat_helper_direct():
    m = _load_archiver({})
    # 新名优先
    os.environ["WEMP_X_NEW"] = "n"
    os.environ["WEMP_X_OLD"] = "o"
    try:
        assert m._env_compat("WEMP_X_NEW", "WEMP_X_OLD", "d") == "n"
    finally:
        del os.environ["WEMP_X_NEW"]
    # 旧名兜底
    try:
        assert m._env_compat("WEMP_X_NEW", "WEMP_X_OLD", "d") == "o"
    finally:
        del os.environ["WEMP_X_OLD"]
    # 都没有 => 默认
    assert m._env_compat("WEMP_X_NEW", "WEMP_X_OLD", "d") == "d"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
