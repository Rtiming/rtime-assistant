#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""rtime-project-check — 跨设备代码项目的严格可移植性校验器。

纯标准库、零依赖,本身就跨平台(win/mac/linux 一致运行),用作 pre-commit 钩子或 CI 闸门。

设计原则(经真实项目验证后收窄,避免误报):
  * 只查 git **已跟踪**的源码 —— 生成物、被 .gitignore 忽略的运行产物不算账。
  * 真正的可移植性杀手只有"个人桌面主目录": `C:\\Users\\<人>` 和 `/Users/<人>`,
    它们在另一个平台/服务器上根本不存在。服务器路径(/home、/mnt、/opt 等)不报。
  * 文档(.md)和部署描述(.env/compose/Dockerfile/.service/.conf)里出现路径是常态,降为警告。

用法:
    python rtime-project-check.py [PATH] [--strict] [--json] [--no-git]

检查项:
    [E] 源码里硬编码个人主目录绝对路径(Windows C 盘 Users 或 macOS /Users 下的个人目录)
    [E] 损坏的符号链接 / reparse point
    [E] 过长的 Windows 路径 (>= 260)
    [W] 文档/部署配置里的个人主目录路径
    [W] git 仓库脏 / 落后上游 / 游离 HEAD / 无 remote
    [W] 含 CRLF 行尾且仓库缺 .gitattributes 归一化
    [W] 缺少 .editorconfig

退出码: 出现任何 [E] 即非零; 加 --strict 时 [W] 也算失败。

豁免: 行内注释  rtime-project: allow-abs  跳过该行; 或仓库根 .rtime-project-allow(每行一个子串,命中行内容或文件路径即豁免)。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ALLOW_MARKER = "rtime-project: allow-abs"
WIN_PATH_LIMIT = 260
MAX_FILE_BYTES = 2_000_000

# 非 git 仓库时的兜底忽略目录;同时用于过滤"已跟踪但其实是生成物"的路径
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "build", "dist", ".next", ".turbo", ".gradle", ".idea",
    ".codex", ".claude", ".playwright-mcp", ".stversions", ".stfolder",
    # 运行时/生成产物(机械臂等项目的约定;即便误被 git 跟踪也不该计入可移植性)
    ".runtime", ".cache", "coverage", "htmlcov", ".tox", ".nox",
    ".ipynb_checkpoints", ".svelte-kit", ".parcel-cache", ".vite",
    "site-packages", "artifacts",
}
TEXT_EXT = {
    ".py", ".pyi", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".json",
    ".jsonc", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".sh",
    ".bash", ".zsh", ".ps1", ".bat", ".cmd", ".md", ".txt", ".rst", ".env",
    ".astro", ".vue", ".svelte", ".css", ".scss", ".html", ".xml",
    ".gradle", ".properties", ".rs", ".go", ".c", ".h", ".cpp", ".hpp",
    ".java", ".kt", ".rb", ".php", ".sql", ".mk", ".tf",
}
TEXT_NAMES = {
    "Dockerfile", "Makefile", ".gitattributes", ".editorconfig",
    ".gitignore", ".stignore", ".env.example",
}

# 只命中"个人桌面主目录"——这是唯一在另一平台必然不存在、真正破坏可移植性的形态。
# 服务器/挂载路径(/home、/mnt、/opt、/srv...)是合法基建,不报。
# 这些定义行自带豁免标记,使本脚本扫描自身时不会自我报错。
ABS_PATTERNS = [
    re.compile(r"[A-Za-z]:[\\/]Users[\\/][^\\/\s\"'<>|`)]+"),  # rtime-project: allow-abs
    re.compile(r"/Users/[^/\s\"'<>|`)]+"),                      # rtime-project: allow-abs
]


class Finding:
    __slots__ = ("level", "kind", "loc", "detail")

    def __init__(self, level: str, kind: str, loc: str, detail: str):
        self.level = level
        self.kind = kind
        self.loc = loc
        self.detail = detail

    def as_dict(self) -> dict:
        return {"level": self.level, "kind": self.kind, "loc": self.loc, "detail": self.detail}


def is_text(p: Path) -> bool:
    return p.suffix.lower() in TEXT_EXT or p.name in TEXT_NAMES


def in_skip(p: Path) -> bool:
    # 路径任一段命中忽略目录即跳过(覆盖"被 git 跟踪的生成物"如 .runtime/...)
    return any(part in SKIP_DIRS for part in p.parts)


def is_doc(p: Path) -> bool:
    # 文档里出现路径是说明/示例,不是会执行的代码 -> 不做硬编码路径判定
    return p.suffix.lower() in (".md", ".txt", ".rst")


def is_deploy_cfg(p: Path) -> bool:
    # 部署/配置描述:绝对路径是环境值/bind 挂载,常态合法 -> 降为警告
    n = p.name.lower()
    if n.startswith(".env") or n.endswith(".env"):
        return True
    if "compose" in n or n.startswith("dockerfile") or n.endswith(".service") or n.endswith(".conf"):
        return True
    return False


def load_allow(root: Path) -> list[str]:
    f = root / ".rtime-project-allow"
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def find_repos(root: Path) -> list[Path]:
    repos = []
    for dirpath, dirnames, _ in os.walk(root):
        d = Path(dirpath)
        if (d / ".git").exists():
            repos.append(d)
        dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS]
    return repos


def git_tracked(repo: Path):
    try:
        r = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"],
                           capture_output=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return [repo / n for n in r.stdout.decode("utf-8", "ignore").split("\0") if n]


def walk_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS]
        for name in filenames:
            yield Path(dirpath) / name


def collect_files(root: Path) -> list[Path]:
    # 常态: 在单个项目(git 仓库)上跑 -> 只查已跟踪文件,生成物/被忽略文件自动排除
    files = None
    if (root / ".git").exists():
        files = git_tracked(root)
    if files is None:
        files = list(walk_files(root))
    return [p for p in files if not in_skip(p)]


def scan_files(root: Path, files: list[Path], allow: list[str], findings: list[Finding]):
    crlf = []
    for p in files:
        try:
            full = str(p)
            if len(full) >= WIN_PATH_LIMIT:
                findings.append(Finding("E", "long-path", _rel(p, root),
                                        f"路径长度 {len(full)} >= {WIN_PATH_LIMIT},Windows 上会失败"))
            if not is_text(p):
                continue
            if p.stat().st_size > MAX_FILE_BYTES:
                continue
            raw = p.read_bytes()
        except OSError:
            continue
        if b"\r\n" in raw:
            crlf.append(p)
        text = raw.decode("utf-8", errors="ignore")
        rel = _rel(p, root)
        if is_doc(p):
            continue  # 文档不做路径判定(CRLF/超长路径已在上面查过)
        if any(a in rel for a in allow):
            continue  # .rtime-project-allow 按文件路径豁免(如生成的数据/缓存文件,塞不进行内注释)
        lvl = "W" if is_deploy_cfg(p) else "E"
        for i, line in enumerate(text.splitlines(), 1):
            if ALLOW_MARKER in line or any(a in line for a in allow):
                continue
            for pat in ABS_PATTERNS:
                m = pat.search(line)
                if m:
                    findings.append(Finding(lvl, "abs-path", f"{rel}:{i}",
                                            f"硬编码个人主目录 `{m.group(0)}`,改用相对/计算路径"))
                    break
    return crlf


def check_symlinks(root: Path, files: list[Path], findings: list[Finding]):
    for p in files:
        try:
            if p.is_symlink() and not p.exists():
                findings.append(Finding("E", "broken-link", _rel(p, root), "符号链接目标不存在"))
        except OSError:
            continue


def check_git(repos: list[Path], findings: list[Finding]):
    for repo in repos:
        st = _git(repo, "status", "--porcelain")
        if st is None:
            continue
        if st.strip():
            findings.append(Finding("W", "git-dirty", _name(repo),
                                    f"{len(st.strip().splitlines())} 个未提交改动"))
        if not _git(repo, "remote"):
            findings.append(Finding("W", "git-no-remote", _name(repo),
                                    "仓库无 remote,代码历史无法跨设备 push/pull"))
            continue
        head = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        if head == "HEAD":
            findings.append(Finding("W", "git-detached", _name(repo), "游离 HEAD"))
            continue
        ab = _git(repo, "rev-list", "--left-right", "--count", "@{upstream}...HEAD")
        if ab:
            try:
                behind, ahead = (int(x) for x in ab.split())
                if behind or ahead:
                    findings.append(Finding("W", "git-diverge", _name(repo),
                                            f"落后 {behind} / 领先 {ahead} 个提交未与上游同步"))
            except ValueError:
                pass


def check_config(root: Path, repos: list[Path], findings: list[Finding], crlf: list[Path]):
    if crlf and not (root / ".gitattributes").exists():
        sample = ", ".join(_rel(p, root) for p in crlf[:3])
        findings.append(Finding("W", "crlf", "(工作区)",
                                f"{len(crlf)} 个文件含 CRLF 且根目录缺 .gitattributes(如 {sample});"
                                f"加 `* text=auto eol=lf` 归一化"))
    if repos and not (root / ".editorconfig").exists():
        findings.append(Finding("W", "no-editorconfig", "(工作区)",
                                "缺少 .editorconfig,跨编辑器/平台缩进与行尾易漂移"))


def _git(repo: Path, *args: str):
    try:
        r = subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                           text=True, encoding="utf-8", errors="ignore", timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else ""


def _rel(p: Path, root: Path) -> str:
    try:
        return str(p.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(p)


def _name(repo: Path) -> str:
    return repo.name or str(repo)


def main() -> int:
    # Windows 控制台默认 GBK,Python 的 UTF-8 中文输出会乱码 —— 跨平台工具应自己兜住
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:
            pass
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="跨设备代码项目严格可移植性校验器")
    ap.add_argument("path", nargs="?", default=".", help="项目路径(默认当前目录)")
    ap.add_argument("--strict", action="store_true", help="把 [W] 也当作失败")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--no-git", action="store_true", help="跳过 git 检查(更快)")
    args = ap.parse_args()

    root = Path(args.path).resolve()
    if not root.exists():
        print(f"路径不存在: {root}", file=sys.stderr)
        return 2

    allow = load_allow(root)
    repos = [] if args.no_git else find_repos(root)
    files = collect_files(root)
    findings: list[Finding] = []

    crlf = scan_files(root, files, allow, findings)
    check_symlinks(root, files, findings)
    if not args.no_git:
        check_git(repos, findings)
    check_config(root, repos, findings, crlf)

    errors = [f for f in findings if f.level == "E"]
    warns = [f for f in findings if f.level == "W"]

    if args.json:
        print(json.dumps({
            "root": str(root), "scanned": len(files),
            "errors": len(errors), "warnings": len(warns),
            "findings": [f.as_dict() for f in findings],
        }, ensure_ascii=False, indent=2))
    else:
        for f in findings:
            tag = "ERROR" if f.level == "E" else "warn "
            print(f"[{tag}] {f.kind:14} {f.loc}\n        {f.detail}")
        print(f"\n扫描根: {root}  (查了 {len(files)} 个文件, git 仓库 {len(repos)} 个)")
        print(f"错误 {len(errors)} | 警告 {len(warns)}")
        if not findings:
            print("通过:未发现可移植性问题。")

    if errors:
        return 1
    if args.strict and warns:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
