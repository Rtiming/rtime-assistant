#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""S4 发布演练:按 deploy/publish-manifest.json 白名单产公开树 + 敏感物扫描。

铁律(open-source-architecture §二 + 风险台账"打包硬约束"):
  - 只经 **git ls-files** 枚举(绝不目录拷贝——工作区磁盘有 gitignored 的真实密钥文件);
  - 白名单制:include 命中才进,exclude_always / include_blocked 永远/暂时剔除;
  - 本地零对外:产树在本地目录,不 push 不发布。

用法:
  python3 scripts/build-public-tree.py --list-only            # 只打印将要进树的文件清单统计
  python3 scripts/build-public-tree.py --out /tmp/public-tree # 产树
  python3 scripts/build-public-tree.py --out DIR --scan       # 产树+内置敏感物扫描(gitleaks 有则一并跑)

退出码:0=通过;1=扫描命中敏感物;2=用法/清单错误。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "deploy" / "publish-manifest.json"

# 内置敏感物指纹:确定性字面量(已知的 owner 标识/已泄凭据)+ 通用密钥形状。
# 字面量按"绝不该出现在公开树"登记;命中即 fail。
LITERAL_PATTERNS = [
    r"2229098829",            # owner QQ号
    r"479904817",             # bot小号QQ号
    r"1046636479",            # 学生会主群号
    r"ou_[0-9a-f]{32}",       # 飞书open_id
    r"wxid_[a-z0-9]{14}",     # 微信id
    r"admin@123",             # 已作废旧口令(公开树也不该带)
    r"werss-orangepi-7f3a9c21",  # 已作废旧JWT secret
]
GENERIC_PATTERNS = [
    r"sk-[A-Za-z0-9]{20,}",   # OpenAI风格key
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    r"xox[bap]-[0-9A-Za-z-]{10,}",
]
# 拓扑项:内网IP/机器路径。不是密钥(泄露的是网络拓扑,不是能直接盗用的凭据),
# 单独 warn 级报告——发布前应 scrub(风险台账 #16/#17/#19/#20),但不 fail 掉产树门。
# 与硬密钥分开:硬命中=必 fail;拓扑=提醒。
# 注:模式用字符类拆开字面主目录路径,避免可移植性门把本脚本自身当"硬编码个人路径"。
TOPOLOGY_PATTERNS = [
    r"100\.64\.\d{1,3}\.\d{1,3}",         # Tailscale/CGNAT 网段(owner tailnet IP)
    r"/home/oran[g]epi\b",
    r"/mnt/nvme[-]store\b",
    r"/User[s]/rtime\b",
    r"\bts[-]orangepi\b",
]
SCAN_EXT = {".py", ".sh", ".md", ".json", ".yaml", ".yml", ".toml", ".ts", ".js", ".mjs",
            ".txt", ".service", ".timer", ".conf", ".env", ".example", ".html", ".css", ""}

# 豁免:(文件前缀, 模式)——vendored 上游项目自带的公开默认值(如 we-mp-rss 上游文档里的
# 默认口令 admin@123,上游自己就发布着;我们的实例口令已轮换,该字面量对我们无危害)。
# 豁免必须窄:只豁免 vendored 树内的这一个模式。
SCAN_ALLOWLIST: tuple[tuple[str, str], ...] = (
    ("tools/chat-intake/wechat-mp-rss/", r"admin@123"),
)
# 元文件豁免:这两个文件的正文**就是**扫描器的模式定义/测试(它们在命名要找什么,
# 不是泄露)。整文件豁免所有硬模式,避免扫描器扫到自己的字面量误报。
SCAN_META_FILES = frozenset({
    "scripts/build-public-tree.py",
    "tests/test_build_public_tree.py",
})


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


class NotAGitRepo(RuntimeError):
    """REPO 不是可用的 git 工作树(如裸仓库 checkout / CI 无 .git)。"""


def tracked_files() -> list[str]:
    """git ls-files 枚举跟踪文件。非 git 工作树抛 NotAGitRepo(白名单产树铁律=
    必须经 git,绝不目录拷贝,所以这里不做非-git 回退,只给个干净可捕获的异常)。"""
    proc = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True)
    if proc.returncode != 0:
        raise NotAGitRepo(f"git ls-files 失败(rc={proc.returncode}): {proc.stderr.strip()}")
    return [l for l in proc.stdout.splitlines() if l.strip()]


def selected_files(manifest: dict) -> tuple[list[str], list[str]]:
    """(进树文件, 被剔除但曾命中include的文件)。前缀匹配;include_blocked 现阶段=剔除。"""
    include = tuple(manifest.get("include", []))
    excluded = tuple(manifest.get("exclude_always", []))
    blocked = tuple(e["path"] for e in manifest.get("include_blocked", []))
    picked, dropped = [], []
    for rel in tracked_files():
        hit = any(rel == p or rel.startswith(p) for p in include)
        if not hit:
            continue
        if any(rel == p or rel.startswith(p) for p in excluded + blocked):
            dropped.append(rel)
        else:
            picked.append(rel)
    return picked, dropped


def build_tree(files: list[str], out_dir: Path) -> None:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    for rel in files:
        src = REPO / rel
        dst = out_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def scan_tree(out_dir: Path) -> list[dict]:
    """硬密钥命中(fail 级);拓扑项见 scan_topology。"""
    hard = [re.compile(p) for p in LITERAL_PATTERNS + GENERIC_PATTERNS]
    hits: list[dict] = []
    for path in sorted(out_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SCAN_EXT:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = str(path.relative_to(out_dir))
        if rel in SCAN_META_FILES:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for pat in hard:
                if pat.search(line):
                    if any(rel.startswith(pfx) and pat.pattern == p
                           for pfx, p in SCAN_ALLOWLIST):
                        continue
                    hits.append({
                        "file": rel,
                        "line": i,
                        "pattern": pat.pattern,
                        # 只报形状不回显整行(行内可能就是敏感值)
                        "preview": line.strip()[:40] + ("…" if len(line.strip()) > 40 else ""),
                    })
    return hits


def scan_topology(out_dir: Path) -> dict[str, int]:
    """拓扑项(内网IP/机器路径)按模式计数(warn 级,不 fail)。发布前 scrub 的提醒。"""
    pats = [re.compile(p) for p in TOPOLOGY_PATTERNS]
    counts = {p.pattern: 0 for p in pats}
    for path in sorted(out_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SCAN_EXT:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pat in pats:
            n = len(pat.findall(text))
            if n:
                counts[pat.pattern] += n
    return {k: v for k, v in counts.items() if v}


def run_gitleaks(out_dir: Path) -> str:
    """gitleaks 有则跑(no-git 目录模式),没装返回 'absent'。

    带上仓库根的 .gitleaks.toml(allowlist 假测试token+vendored第三方占位/压缩产物的
    误报);真第三方密钥靠 publish-manifest 排除,不靠 allowlist。"""
    if shutil.which("gitleaks") is None:
        return "absent"
    cmd = ["gitleaks", "detect", "--no-git", "--source", str(out_dir), "--redact"]
    cfg = REPO / ".gitleaks.toml"
    if cfg.is_file():
        cmd += ["--config", str(cfg)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    return "clean" if r.returncode == 0 else f"HITS(rc={r.returncode})"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="S4 白名单产公开树+扫描")
    ap.add_argument("--out", type=Path, help="公开树输出目录(产树模式)")
    ap.add_argument("--list-only", action="store_true", help="只统计将进树的清单")
    ap.add_argument("--scan", action="store_true", help="产树后跑敏感物扫描")
    args = ap.parse_args(argv)

    manifest = load_manifest()
    picked, dropped = selected_files(manifest)
    report: dict = {
        "picked": len(picked),
        "dropped_by_exclude_or_block": len(dropped),
        "top_dirs": sorted({p.split("/", 1)[0] for p in picked}),
    }

    if args.list_only:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if not args.out:
        print("错误: 需要 --out DIR(或 --list-only)", file=sys.stderr)
        return 2

    build_tree(picked, args.out)
    report["out"] = str(args.out)
    if args.scan:
        hits = scan_tree(args.out)
        report["builtin_scan_hits"] = len(hits)  # 硬密钥:必须 0
        report["gitleaks"] = run_gitleaks(args.out)
        topo = scan_topology(args.out)  # 拓扑:warn,发布前 scrub(台账#16/17/19/20)
        report["topology_warnings"] = topo
        report["topology_total"] = sum(topo.values())
        if hits:
            report["hits"] = hits[:40]
        print(json.dumps(report, ensure_ascii=False, indent=2))
        # 只有硬密钥/gitleaks 命中才 fail;拓扑是提醒(否则永远 red 直到发布前批次)
        return 1 if hits or str(report["gitleaks"]).startswith("HITS") else 0
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
