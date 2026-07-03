# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""K1 module manifest:声明式模块清单的加载/校验/doctor(config-and-access 邻域)。

设计: docs/design/module-system-and-open-source-2026-07.zh-CN.md。module manifest
(deploy/modules.json)是"所有可选配模块"的单一真相:装机向导、面板"模块"视图、doctor、
开源打包都读它——一处声明,四处受益。

本模块纯 stdlib、无 IO(读文件在 CLI/调用方);校验用依赖注入(已知 config_module 名、
已知 compose profile、docs 是否存在的判定函数)——保持 admin-core 不硬依赖 compose/repo 布局。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

MANIFEST_SCHEMA_VERSION = 1
KINDS = ("core", "channel", "gateway", "panel", "provider", "integration", "rules", "extension")
HOT_PLUGGABLE = ("hot", "restart", "none")


@dataclass(frozen=True)
class Module:
    """一个可选配模块的声明。"""

    id: str
    kind: str
    title: str
    optional: bool = True
    compose_profile: str | None = None  # 装机开关(COMPOSE_PROFILES 里加它即装);None=非 compose 或恒开
    config_module: str | None = None    # 面板配置:admin-core registry 的模块名;None=无面板配置
    depends_on: tuple[str, ...] = ()
    hot_pluggable: str = "restart"       # hot|restart|none
    data_paths: tuple[str, ...] = ()     # 该模块碰的数据(永在仓库外)
    docs: str | None = None
    setup_notes: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Module":
        return cls(
            id=str(d["id"]),
            kind=str(d["kind"]),
            title=str(d.get("title", d["id"])),
            optional=bool(d.get("optional", True)),
            compose_profile=d.get("compose_profile"),
            config_module=d.get("config_module"),
            depends_on=tuple(d.get("depends_on", ())),
            hot_pluggable=str(d.get("hot_pluggable", "restart")),
            data_paths=tuple(d.get("data_paths", ())),
            docs=d.get("docs"),
            setup_notes=str(d.get("setup_notes", "")),
        )


def load_manifest(text: str) -> list[Module]:
    """解析 modules.json。结构非法抛 ValueError(启动/校验期,不是请求期)。"""
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("modules"), list):
        raise ValueError("manifest 必须是 {schema_version, modules: [...]} 对象")
    mods = [Module.from_dict(m) for m in data["modules"]]
    ids = [m.id for m in mods]
    if len(ids) != len(set(ids)):
        raise ValueError("manifest 有重复 module id")
    return mods


def validate_manifest(
    modules: list[Module],
    *,
    known_config_modules: set[str],
    known_profiles: set[str],
    docs_exists: Callable[[str], bool] | None = None,
) -> list[dict[str, str]]:
    """对账 manifest 与现实,返回 issue 列表(空=全通过)。依赖注入,不碰 IO/registry。

    校验:kind/hot_pluggable 合法;compose_profile 真在 compose;config_module 真在
    registry;depends_on 指向存在的 module;docs 文件存在(给了 docs_exists 时)。
    """
    issues: list[dict[str, str]] = []
    ids = {m.id for m in modules}

    def add(mid: str, code: str, detail: str) -> None:
        issues.append({"module": mid, "code": code, "detail": detail})

    for m in modules:
        if m.kind not in KINDS:
            add(m.id, "bad_kind", f"kind={m.kind} 不在 {KINDS}")
        if m.hot_pluggable not in HOT_PLUGGABLE:
            add(m.id, "bad_hot_pluggable", f"hot_pluggable={m.hot_pluggable}")
        if m.compose_profile and m.compose_profile not in known_profiles:
            add(m.id, "compose_profile_missing", f"compose 里没有 profile {m.compose_profile!r}")
        if m.config_module and m.config_module not in known_config_modules:
            add(m.id, "config_module_unknown", f"registry 没有模块 {m.config_module!r}")
        for dep in m.depends_on:
            if dep not in ids:
                add(m.id, "dep_missing", f"依赖的 module {dep!r} 不在 manifest")
        if m.docs and docs_exists is not None and not docs_exists(m.docs):
            add(m.id, "docs_missing", f"docs 文件不存在: {m.docs}")
    return issues


def manifest_report(
    modules: list[Module],
    issues: list[dict[str, str]],
    *,
    enabled_profiles: set[str] | None = None,
) -> dict[str, Any]:
    """给部署者/agent 的 doctor 报告:模块清单 + 每个装没装(按 enabled_profiles)+ 校验。"""
    enabled = enabled_profiles if enabled_profiles is not None else set()

    def installed(m: Module) -> bool:
        if not m.optional:
            return True
        if m.compose_profile:
            return m.compose_profile in enabled
        return False  # optional 非 compose 模块:装没装 doctor 无法从 profile 判,标 unknown

    return {
        "ok": not issues,
        "total": len(modules),
        "by_kind": {k: sum(1 for m in modules if m.kind == k) for k in KINDS if any(mm.kind == k for mm in modules)},
        "modules": [
            {
                "id": m.id,
                "kind": m.kind,
                "title": m.title,
                "optional": m.optional,
                "installed": installed(m) if (not m.optional or m.compose_profile) else None,
                "compose_profile": m.compose_profile,
                "config_module": m.config_module,
                "hot_pluggable": m.hot_pluggable,
                "docs": m.docs,
            }
            for m in modules
        ],
        "issues": issues,
    }
