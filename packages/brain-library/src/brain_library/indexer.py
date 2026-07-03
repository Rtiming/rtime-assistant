# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""SQLite FTS/BM25 derived index support for brain-library."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 3  # BM25 baseline (no vectors)
VECTOR_SCHEMA_VERSION = 4  # set only when an embedding column is actually present
TOKENIZER = "jieba.cut_for_search+unicode61"
DEFAULT_MAX_FILES = 50000
DEFAULT_MAX_BYTES = 2_000_000
# Characters of body text appended to the title when embedding a document. bge/Qwen3
# both truncate at the token level; this keeps the embedded text focused on the lead.
EMBED_BODY_CHARS = 256
VEC_TABLE = "vec_documents"
# Reciprocal-rank-fusion constant and candidate-pool size for hybrid search. Each path
# (BM25, vector) contributes top-`HYBRID_POOL`; RRF then re-ranks the union.
RRF_K = 60
HYBRID_POOL = 50
SEARCH_MODES = ("bm25", "vector", "hybrid")
TEXT_SUFFIXES = {"bib", "csl", "md", "markdown", "txt"}
SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".obsidian",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    # brain 内的暂存/归档/索引产物/工具配置目录——不是知识，别进检索。
    "_inbox",
    "_archive",
    "_indexes",
    ".claude",
    ".stfolder",
}
# AI 助手指引/工具配置文件——不是知识内容，别进检索（按文件名跳过）。
SKIP_FILES = {"AGENTS.md", "CLAUDE.md"}
# 蒸馏流水线的「原始/中间」块目录，按 NN_raw* 命名（如 chat-logs 导出的
# 00_raw_chat_persona / 01_raw_chat_work）。这些是处理中间产物、不是知识，
# 文本宽泛会污染并挤占检索结果（用户实测："无犯罪/软著/3D打印" 被 persona.NNN
# 原始块挤到后面）。跳过它们，只索引精炼/分析产物（02_refined/03_analysis 等）。
# 与 .docpack 只索引 content.md/concept.md 同理。
SKIP_DIR_RE = re.compile(r"^\d{2}_raw(_|$)")
HEADING_RE = re.compile(r"^\s{0,3}#\s+(.+?)\s*$", re.MULTILINE)
FRONTMATTER_RE = re.compile(r"^﻿?---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)
# Frontmatter keys lifted into queryable columns. `source`/`type` are renamed to
# source_url/doc_type to avoid SQL keyword friction; `publish_date` falls back to
# `date`/`updated` so notes that only carry one of those still sort by time.
_META_DATE_KEYS = ("publish_date", "date", "updated")


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Best-effort YAML-frontmatter → flat dict (top-level `key: value` only).

    Deliberately tiny: no YAML dep, ignores nested/list values. Good enough for
    the flat metadata our notes carry (type/dept/category/publish_date/grade/...)."""
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}
    out: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line or line[0] in " \t#-" or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value:
            out[key] = value[:500]
    return out


def _strip_frontmatter(text: str) -> str:
    """Return ``text`` with a leading YAML-frontmatter block removed.

    Uses the *same* anchored regex as :func:`_parse_frontmatter`, so the slice removed
    here is exactly the block that was lifted into the metadata columns — the two stay
    in lockstep. Frontmatter is queryable metadata, not prose; keeping it out of the
    indexed/embedded body stops a few hundred chars of near-identical header
    (type/title/source/page/…) from polluting BM25, the snippet, and — most harmfully —
    the vector, whose input is only ``title + body[:EMBED_BODY_CHARS]``. A ``---``
    divider mid-document is left untouched (the regex is anchored at the start); text
    without frontmatter is returned unchanged."""
    match = FRONTMATTER_RE.match(text)
    return text[match.end():] if match else text


def _meta_columns(front: dict[str, str]) -> dict[str, Any]:
    """Map frontmatter dict to the documents-table metadata columns."""
    date = ""
    for key in _META_DATE_KEYS:
        if front.get(key):
            date = front[key][:32]
            break
    cc = front.get("course_count")
    try:
        course_count = int(cc) if cc not in (None, "") else None
    except (TypeError, ValueError):
        course_count = None
    return {
        "doc_type": front.get("type", "")[:64] or None,
        "dept": front.get("dept") or front.get("institution") or None,
        "category": front.get("category", "")[:128] or None,
        "publish_date": date or None,
        "grade": front.get("grade", "")[:16] or None,
        "source_url": front.get("source", "")[:500] or None,
        "course_count": course_count,
    }


def _parse_course_rows(text: str) -> list[dict[str, Any]]:
    """Parse a 培养方案 note's markdown course table into structured course rows.

    Table header is `| 模块 | 编号 | 课程 | 学分 | 学时 | 必修 | 建议学期 | 开课院系 |`.
    Returns [] for non-program notes or notes without a course table."""

    def _num(value: str | None) -> float | None:
        try:
            return float(value) if value not in (None, "", "—") else None
        except (TypeError, ValueError):
            return None

    rows: list[dict[str, Any]] = []
    header: list[str] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if header is None:
            if line.startswith("|") and "编号" in line and "课程" in line:
                header = [c.strip() for c in line.strip("|").split("|")]
            continue
        if not line.startswith("|"):
            break  # table ended
        if set(line) <= set("|-: "):
            continue  # markdown separator row
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < len(header):
            continue
        row = dict(zip(header, cells))
        code = row.get("编号")
        if not code or code == "编号":
            continue
        periods = _num(row.get("学时"))
        rows.append(
            {
                "module": row.get("模块") or None,
                "code": code,
                "name": row.get("课程") or None,
                "credits": _num(row.get("学分")),
                "periods": int(periods) if periods is not None else None,
                "required": 1 if row.get("必修") == "必" else 0,
                "term": row.get("建议学期") or None,
                "open_dept": row.get("开课院系") or None,
            }
        )
    return rows


JsonObject = dict[str, Any]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _relative(path: Path, root: Path) -> str:
    try:
        # POSIX separators so scan/index output is identical on Mac, orangepi,
        # and Windows (otherwise Windows emits backslashes and breaks consumers).
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _walk_index_files(root: Path, *, max_files: int) -> tuple[list[Path], bool]:
    files: list[Path] = []
    for current, dirs, names in os.walk(root):
        current_path = Path(current)
        # Inside a `*.docpack/`: index only the human-readable extracts
        # (content.md / concept.md), never the generated internals
        # (pages/, source/, ai/, logs/, raw/json). DocPack is the L3 deep-
        # processing surface, so its OCR'd/extracted text must be searchable.
        if current_path.name.endswith(".docpack"):
            dirs[:] = []
            for name in names:
                if name in ("content.md", "concept.md"):
                    files.append(current_path / name)
                    if len(files) >= max_files:
                        return files, True
            continue
        # Keep `.docpack` dirs in the walk so os.walk descends into them above;
        # only skip the usual noise dirs + raw distillation intermediates (NN_raw*).
        dirs[:] = [
            name for name in dirs
            if name not in SKIP_DIRS and not SKIP_DIR_RE.match(name)
        ]
        for name in names:
            if name in SKIP_FILES:
                continue
            path = current_path / name
            suffix = path.suffix.lower().lstrip(".")
            if suffix in TEXT_SUFFIXES:
                files.append(path)
                if len(files) >= max_files:
                    return files, True
    return files, False


def _read_index_text(path: Path, *, max_bytes: int) -> tuple[str, str | None]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        return "", f"stat_failed: {exc}"
    if size > max_bytes:
        return "", "file_too_large"
    try:
        return path.read_text(encoding="utf-8", errors="ignore"), None
    except OSError as exc:
        return "", f"read_failed: {exc}"


def _title_for(path: Path, text: str) -> str:
    match = HEADING_RE.search(text)
    if match:
        return match.group(1).strip()[:300] or path.stem
    return path.stem


def _segment_for_fts(text: str) -> str:
    import jieba  # lazy: keep read-only doctor/scan usable without the index dep

    tokens = [token.strip() for token in jieba.cut_for_search(text) if token.strip()]
    return " ".join(tokens)


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    return connection


def _embed_text(title: str, body: str) -> str:
    """Text fed to the embedder for one document: title + lead of the body."""
    return f"{title} {body[:EMBED_BODY_CHARS]}".strip()


def _load_vec_extension(connection: sqlite3.Connection) -> bool:
    """Load the sqlite-vec extension on a connection. False (never raises) if the
    extension or the Python sqlite build can't load it -> caller stays BM25-only."""
    try:
        import sqlite_vec  # lazy: optional dep, only needed for vector index/query

        connection.enable_load_extension(True)
        sqlite_vec.load(connection)
        connection.enable_load_extension(False)
        return True
    except Exception:
        return False


def _vec_table_exists(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (VEC_TABLE,)
    ).fetchone()
    return row is not None


def index_embed_meta(index: Path) -> JsonObject | None:
    """Return ``{"model","dim"}`` if the index carries a vector table, else ``None``.

    Used by query to decide hybrid vs BM25 and to pick the matching query embedder.
    Returns ``None`` for any older/BM25-only index (graceful: no vectors -> BM25)."""
    try:
        # closing(): a bare ``with sqlite3.connect()`` commits but does NOT close the
        # handle, leaving a lingering connection that locks the file against a later
        # ATTACH (incremental reuse). closing() guarantees release.
        with closing(_connect(index)) as connection:
            if not _vec_table_exists(connection):
                return None
            meta = _read_meta(connection)
    except sqlite3.Error:
        return None
    dim = meta.get("embed_dim")
    model = meta.get("embed_model")
    if not isinstance(dim, int) or not isinstance(model, str) or not model:
        return None
    return {"model": model, "dim": dim}


def _old_schema_version(index: Path) -> int | None:
    """读旧索引的 schema_version；读不到返回 None。"""
    try:
        with closing(_connect(index)) as connection:
            return _read_meta(connection).get("schema_version")  # type: ignore[return-value]
    except sqlite3.Error:
        return None


def _incremental_reuse(
    connection: sqlite3.Connection,
    *,
    out: Path,
    root: Path,
    files: list[Path],
    embedder: Any | None,
) -> tuple[list[Path], int, int, bool]:
    """增量复用：把既有索引中**未变文档**的全部行（documents/documents_fts/courses
    + 向量）原样搬进正在构建的临时库，只需重新处理新增/改动的文档。

    做法：ATTACH 旧库 → ``INSERT … SELECT *`` 全量拷贝（DB→DB，快）→ 只删除
    stale（改动∪删除）的行 → 返回需重新读取/分词/嵌入的 fresh 文件列表。未变文档
    因此零读取、零分词、零嵌入。仅当旧库 schema 兼容（要嵌入则模型/维度一致）才复用，
    否则原样返回全部文件走全量。

    返回 ``(fresh_files, reused_docs, reused_vectors, vec_ready)``。"""
    want_vec = embedder is not None
    old_schema = _old_schema_version(out)
    if old_schema not in (SCHEMA_VERSION, VECTOR_SCHEMA_VERSION):
        return files, 0, 0, False
    info = index_embed_meta(out)
    if want_vec:
        # 要写向量：旧库必须是同模型+同维度的 schema-4，否则全量（避免混模型）。
        if old_schema != VECTOR_SCHEMA_VERSION or not info \
                or info.get("model") != embedder.model_name or info.get("dim") != embedder.dim:
            return files, 0, 0, False

    try:
        with closing(_connect(out)) as oc:
            old_meta = {
                row["path"]: (row["size_bytes"], row["mtime_ns"])
                for row in oc.execute("SELECT path, size_bytes, mtime_ns FROM documents")
            }
    except sqlite3.Error:
        return files, 0, 0, False
    if not old_meta:
        return files, 0, 0, False

    # 分区：current 文件按 (size,mtime) 比对旧库 → unchanged 复用 / fresh(改动∪新增) 重做。
    fresh: list[Path] = []
    current: set[str] = set()
    changed: set[str] = set()
    for f in files:
        try:
            st = f.stat()
        except OSError:
            continue
        rel = _relative(f, root)
        current.add(rel)
        prev = old_meta.get(rel)
        if prev is not None and prev[0] == st.st_size and prev[1] == st.st_mtime_ns:
            continue  # 未变 → 复用旧行
        fresh.append(f)
        if rel in old_meta:
            changed.add(rel)
    deleted = set(old_meta) - current
    stale = changed | deleted
    reused_docs = len(old_meta) - len(stale)

    vec_ready = False
    reused_vectors = 0
    old_has_vec = info is not None
    connection.execute("ATTACH DATABASE ? AS old", (str(out),))
    try:
        # 全量拷贝行（同 schema，列序一致）。documents_fts/courses 按 rowid/program_path 关联。
        connection.execute("INSERT INTO documents SELECT * FROM old.documents")
        connection.execute(
            "INSERT INTO documents_fts(rowid, path_index, title_index, body_index) "
            "SELECT rowid, path_index, title_index, body_index FROM old.documents_fts"
        )
        connection.execute("INSERT INTO courses SELECT * FROM old.courses")
        if want_vec and old_has_vec and _load_vec_extension(connection):
            connection.execute(
                f"CREATE VIRTUAL TABLE {VEC_TABLE} USING vec0(embedding float[{embedder.dim}])"
            )
            connection.execute(
                f"INSERT INTO main.{VEC_TABLE}(rowid, embedding) "
                f"SELECT rowid, embedding FROM old.{VEC_TABLE}"
            )
            vec_ready = True
        # 删除 stale（改动+删除）的行；未变行留下。
        if stale:
            connection.execute("CREATE TEMP TABLE _stale(path TEXT PRIMARY KEY)")
            connection.executemany(
                "INSERT INTO _stale(path) VALUES (?)", [(p,) for p in stale]
            )
            ids = [
                int(r[0])
                for r in connection.execute(
                    "SELECT id FROM documents WHERE path IN (SELECT path FROM _stale)"
                )
            ]
            for rid in ids:
                connection.execute("DELETE FROM documents_fts WHERE rowid = ?", (rid,))
                if vec_ready:
                    connection.execute(f"DELETE FROM {VEC_TABLE} WHERE rowid = ?", (rid,))
            connection.execute(
                "DELETE FROM courses WHERE program_path IN (SELECT path FROM _stale)"
            )
            connection.execute(
                "DELETE FROM documents WHERE path IN (SELECT path FROM _stale)"
            )
            connection.execute("DROP TABLE _stale")
        if vec_ready:
            reused_vectors = int(
                connection.execute(f"SELECT count(*) FROM {VEC_TABLE}").fetchone()[0]
            )
        connection.commit()  # 提交隐式事务，否则带挂起事务无法 DETACH("database old is locked")
    finally:
        try:
            connection.rollback()  # 异常路径回滚残留事务，确保 DETACH 可执行
        except sqlite3.Error:
            pass
        connection.execute("DETACH DATABASE old")
    return fresh, reused_docs, reused_vectors, vec_ready


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE documents (
          id INTEGER PRIMARY KEY,
          path TEXT UNIQUE NOT NULL,
          suffix TEXT NOT NULL,
          title TEXT NOT NULL,
          body TEXT NOT NULL,
          size_bytes INTEGER NOT NULL,
          mtime_ns INTEGER NOT NULL,
          doc_type TEXT,
          dept TEXT,
          category TEXT,
          publish_date TEXT,
          grade TEXT,
          source_url TEXT,
          course_count INTEGER
        );
        CREATE INDEX idx_documents_type ON documents(doc_type);
        CREATE INDEX idx_documents_dept ON documents(dept);
        CREATE INDEX idx_documents_date ON documents(publish_date);
        CREATE TABLE courses (
          id INTEGER PRIMARY KEY,
          program_path TEXT NOT NULL,
          program_name TEXT,
          dept TEXT,
          grade TEXT,
          module TEXT,
          code TEXT,
          name TEXT,
          credits REAL,
          periods INTEGER,
          required INTEGER,
          term TEXT,
          open_dept TEXT
        );
        CREATE INDEX idx_courses_code ON courses(code);
        CREATE INDEX idx_courses_program ON courses(program_path);
        CREATE INDEX idx_courses_dept ON courses(dept);
        CREATE VIRTUAL TABLE documents_fts USING fts5(
          path_index,
          title_index,
          body_index,
          tokenize='unicode61'
        );
        """
    )


def _insert_meta(connection: sqlite3.Connection, data: JsonObject) -> None:
    rows = [(key, json.dumps(value, ensure_ascii=False, sort_keys=True)) for key, value in data.items()]
    connection.executemany("INSERT INTO meta(key, value) VALUES (?, ?)", rows)


def _iter_index_records(
    root: Path,
    *,
    files: Iterable[Path],
    max_bytes: int,
    suffix_counts: Counter[str],
    skipped: Counter[str],
) -> Iterable[JsonObject]:
    for path in files:
        suffix = path.suffix.lower().lstrip(".")
        suffix_counts[suffix] += 1
        text, error = _read_index_text(path, max_bytes=max_bytes)
        if error:
            skipped[error] += 1
            continue
        try:
            stat = path.stat()
        except OSError as exc:
            skipped[f"stat_failed: {exc}"] += 1
            continue
        relative_path = _relative(path, root)
        is_markdown = suffix in ("md", "markdown")
        # Title still derives from the full text: frontmatter carries no markdown
        # heading (HEADING_RE matches `# …`, not `key: value`), so this is
        # behaviour-preserving for titles.
        title = _title_for(path, text)
        meta = _meta_columns(_parse_frontmatter(text)) if is_markdown else _meta_columns({})
        # Index/embed the prose only: drop the leading frontmatter block (already
        # captured in the metadata columns above) so boilerplate headers don't pollute
        # BM25, the snippet, or the vector. size_bytes/mtime_ns stay as the file stat
        # below so incremental-reuse change detection is unaffected.
        body = _strip_frontmatter(text) if is_markdown else text
        yield {
            "path": relative_path,
            "suffix": suffix,
            "title": title,
            "body": body,
            "path_index": _segment_for_fts(relative_path),
            "title_index": _segment_for_fts(title),
            "body_index": _segment_for_fts(body),
            "size_bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            **meta,
        }


def build_index(
    root: Path,
    out: Path,
    *,
    force: bool = False,
    allow_root_output: bool = False,
    max_files: int = DEFAULT_MAX_FILES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    embed: bool | None = None,
    incremental: bool = False,
) -> JsonObject:
    """Build the derived index. ``embed`` controls the vector layer:
    None=auto (embed iff a model is available), True=require embedding (skip with a
    note if no model/deps), False=BM25 only. When vectors are written the schema is
    bumped to 4; otherwise it stays 3 and the index is a pure-BM25 schema-3 index."""
    root = root.expanduser().resolve()
    out = out.expanduser().resolve()

    if max_files < 1:
        return {"ok": False, "errors": ["max_files must be >= 1"], "root": str(root), "out": str(out)}
    if max_bytes < 1:
        return {"ok": False, "errors": ["max_bytes must be >= 1"], "root": str(root), "out": str(out)}
    if not root.is_dir():
        return {"ok": False, "errors": ["root is not a directory"], "root": str(root), "out": str(out)}
    if _is_under(out, root) and not allow_root_output:
        return {
            "ok": False,
            "errors": ["refusing to write index under brain root without allow_root_output"],
            "root": str(root),
            "out": str(out),
        }
    if out.exists() and not force and not incremental:
        return {"ok": False, "errors": ["output already exists; pass --force or --incremental"], "root": str(root), "out": str(out)}

    files, truncated = _walk_index_files(root, max_files=max_files)
    suffix_counts: Counter[str] = Counter()
    skipped: Counter[str] = Counter()
    documents_indexed = 0
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{out.name}.", suffix=".tmp", dir=str(out.parent))
    os.close(fd)
    tmp = Path(tmp_name)

    # Resolve the embedding backend up front so a missing model degrades to BM25
    # cleanly. embed=False -> never embed; otherwise probe (returns None when no
    # model/deps), and embed=True records a note rather than failing the build.
    embedder = None
    if embed is not False:
        from .embed import get_embedder

        embedder = get_embedder()
    embed_rowids: list[int] = []
    embed_texts: list[str] = []
    embed_note = ""
    reused_docs = 0
    reused_vectors = 0
    vec_ready = False

    try:
        # contextlib.closing guarantees the SQLite connection is closed before
        # os.replace(tmp, out); a bare `with sqlite3.connect()` only commits and
        # leaves the handle open, which fails on Windows (WinError 32) where an
        # open file cannot be renamed or unlinked. POSIX tolerated the open handle.
        with closing(_connect(tmp)) as connection:
            _create_schema(connection)
            # 增量：把未变文档的全部行(documents/fts/courses/向量)从旧库搬过来，
            # 只对 fresh(新增∪改动)的文件重新读取/分词/嵌入。零变更重建≈秒级。
            index_files = files
            # truncated 时文件清单不全，用它算 deleted 会误删未遍历到的文档 → 退回全量。
            if incremental and out.exists() and not truncated:
                index_files, reused_docs, reused_vectors, vec_ready = _incremental_reuse(
                    connection, out=out, root=root, files=files, embedder=embedder
                )
                documents_indexed = reused_docs
            for record in _iter_index_records(
                root,
                files=index_files,
                max_bytes=max_bytes,
                suffix_counts=suffix_counts,
                skipped=skipped,
            ):
                cursor = connection.execute(
                    """
                    INSERT INTO documents(
                      path, suffix, title, body, size_bytes, mtime_ns,
                      doc_type, dept, category, publish_date, grade, source_url, course_count
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["path"],
                        record["suffix"],
                        record["title"],
                        record["body"],
                        record["size_bytes"],
                        record["mtime_ns"],
                        record["doc_type"],
                        record["dept"],
                        record["category"],
                        record["publish_date"],
                        record["grade"],
                        record["source_url"],
                        record["course_count"],
                    ),
                )
                rowid = cursor.lastrowid
                if embedder is not None and rowid is not None:
                    embed_rowids.append(int(rowid))
                    embed_texts.append(_embed_text(record["title"], record["body"]))
                connection.execute(
                    """
                    INSERT INTO documents_fts(rowid, path_index, title_index, body_index)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        rowid,
                        record["path_index"],
                        record["title_index"],
                        record["body_index"],
                    ),
                )
                if record.get("doc_type") == "ustc-program":
                    connection.executemany(
                        """
                        INSERT INTO courses(
                          program_path, program_name, dept, grade,
                          module, code, name, credits, periods, required, term, open_dept
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                record["path"], record["title"], record["dept"], record["grade"],
                                crow["module"], crow["code"], crow["name"], crow["credits"],
                                crow["periods"], crow["required"], crow["term"], crow["open_dept"],
                            )
                            for crow in _parse_course_rows(record["body"])
                        ],
                    )
                documents_indexed += 1

            # Vector layer (schema 4). Embed in batches into a sqlite-vec vec0 table.
            # If the extension can't load on this host we stay BM25-only (schema 3).
            # 增量已把未变文档的向量从旧库拷进 vec 表(vec_ready=True,reused_vectors 篇)，
            # 这里只需对新增/改动的文档嵌入；全量则新建表嵌入全部。
            vectors_written = reused_vectors
            fresh_written = 0
            embed_model_name: str | None = embedder.model_name if (vec_ready and embedder) else None
            embed_dim_value: int | None = embedder.dim if (vec_ready and embedder) else None
            if embedder is not None and embed_texts:
                # A failure in the vector layer (extension quirk, inference error,
                # disk full) must not lose the BM25 index — drop any partial vec
                # table and fall back to a clean schema-3 build.
                can_build = vec_ready or (reused_docs == 0 and _load_vec_extension(connection))
                if can_build:
                    try:
                        if not vec_ready:
                            connection.execute(
                                f"CREATE VIRTUAL TABLE {VEC_TABLE} USING vec0(embedding float[{embedder.dim}])"
                            )
                            vec_ready = True
                        fresh_written = _embed_and_store(connection, embedder, embed_rowids, embed_texts)
                        vectors_written = reused_vectors + fresh_written
                        embed_model_name = embedder.model_name
                        embed_dim_value = embedder.dim
                    except Exception as exc:
                        # 向量层失败：连复用进来的旧向量一起丢弃，降级为纯 BM25(schema 3)。
                        # 必须把 reused_vectors/fresh_written 也清零，否则 meta 会写出
                        # "schema 3 但 reused_vectors>0" 的虚假信号(vec 表已删)。
                        connection.execute(f"DROP TABLE IF EXISTS {VEC_TABLE}")
                        vectors_written = 0
                        reused_vectors = 0
                        fresh_written = 0
                        embed_model_name = None
                        embed_dim_value = None
                        vec_ready = False
                        embed_note = f"vector layer failed ({type(exc).__name__}); built BM25-only"
                elif embed is True:
                    embed_note = "embed requested but sqlite-vec could not load; built BM25-only"
            elif embed is True and embedder is None:
                embed_note = "embed requested but no model/deps available; built BM25-only"
            if reused_docs and not embed_note:
                embed_note = (
                    "incremental: reused %d docs / %d vectors; indexed %d fresh, embedded %d"
                    % (reused_docs, reused_vectors, documents_indexed - reused_docs, fresh_written)
                )
            embedded = vectors_written > 0
            effective_schema = VECTOR_SCHEMA_VERSION if embedded else SCHEMA_VERSION

            scan = {
                "files_seen": len(files),
                "truncated": truncated,
                "suffix_counts": dict(sorted(suffix_counts.items())),
                "skipped": dict(sorted(skipped.items())),
            }
            meta_record: JsonObject = {
                "schema_version": effective_schema,
                "tokenizer": TOKENIZER,
                "created_at": _utc_now(),
                "root": str(root),
                "document_count": documents_indexed,
                "max_files": max_files,
                "max_bytes": max_bytes,
                "scan": scan,
            }
            if embedded:
                meta_record["embed_model"] = embed_model_name
                meta_record["embed_dim"] = embed_dim_value
                meta_record["vector_count"] = vectors_written
            if reused_docs:
                meta_record["reused_docs"] = reused_docs
                if embedded:  # 向量计数只在确有向量(schema 4)时写,避免 schema-3 留虚假向量数
                    meta_record["reused_vectors"] = reused_vectors
                    meta_record["embedded_fresh"] = fresh_written
            _insert_meta(connection, meta_record)
            connection.commit()
        os.replace(tmp, out)
    except sqlite3.Error as exc:
        tmp.unlink(missing_ok=True)
        return {
            "ok": False,
            "errors": [f"sqlite_error: {exc}"],
            "root": str(root),
            "out": str(out),
            "documents_indexed": 0,
        }
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        return {
            "ok": False,
            "errors": [f"write_failed: {exc}"],
            "root": str(root),
            "out": str(out),
            "documents_indexed": 0,
        }

    result = {
        "ok": True,
        "root": str(root),
        "out": str(out),
        "schema_version": effective_schema,
        "tokenizer": TOKENIZER,
        "documents_indexed": documents_indexed,
        "files_seen": scan["files_seen"],
        "truncated": scan["truncated"],
        "skipped": scan["skipped"],
        "suffix_counts": scan["suffix_counts"],
        "embedded": embedded,
        "vectors_written": vectors_written,
        "embed_model": embed_model_name,
    }
    if reused_docs:
        result["reused_docs"] = reused_docs
        if embedded:
            result["reused_vectors"] = reused_vectors
            result["embedded_fresh"] = fresh_written
    if embed_note:
        result["embed_note"] = embed_note
    return result


def _embed_and_store(
    connection: sqlite3.Connection,
    embedder: Any,
    rowids: list[int],
    texts: list[str],
    *,
    batch: int = 64,
) -> int:
    """Embed ``texts`` (document side) in batches and insert into the vec0 table,
    keyed by the documents rowid. Returns the number of vectors written."""
    written = 0
    for start in range(0, len(texts), batch):
        ids = rowids[start : start + batch]
        vectors = embedder.embed(texts[start : start + batch])
        connection.executemany(
            f"INSERT INTO {VEC_TABLE}(rowid, embedding) VALUES (?, ?)",
            [(rid, vec.astype("float32").tobytes()) for rid, vec in zip(ids, vectors)],
        )
        written += len(ids)
    return written


def _read_meta(connection: sqlite3.Connection) -> JsonObject:
    meta: JsonObject = {}
    for row in connection.execute("SELECT key, value FROM meta ORDER BY key"):
        try:
            meta[str(row["key"])] = json.loads(row["value"])
        except json.JSONDecodeError:
            meta[str(row["key"])] = row["value"]
    return meta


def index_status(index: Path) -> JsonObject:
    index = index.expanduser().resolve()
    if not index.is_file():
        return {"ok": False, "index": str(index), "errors": ["index file not found"]}
    try:
        with _connect(index) as connection:
            meta = _read_meta(connection)
            document_count = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            fts_count = connection.execute("SELECT COUNT(*) FROM documents_fts").fetchone()[0]
    except sqlite3.Error as exc:
        return {"ok": False, "index": str(index), "errors": [f"sqlite_error: {exc}"]}
    return {
        "ok": True,
        "index": str(index),
        "schema_version": meta.get("schema_version"),
        "tokenizer": meta.get("tokenizer"),
        "root": meta.get("root"),
        "created_at": meta.get("created_at"),
        "document_count": document_count,
        "fts_count": fts_count,
        "embed_model": meta.get("embed_model"),
        "embed_dim": meta.get("embed_dim"),
        "vector_count": meta.get("vector_count"),
        "has_vectors": meta.get("embed_dim") is not None,
        "meta": meta,
    }


def _fts_tokens(query: str) -> list[str]:
    return [token for token in re.split(r"\s+", _segment_for_fts(query)) if token]


def _normalize_fts_query(query: str, *, operator: str = "AND") -> str:
    # operator="AND" (FTS5 implicit, space-joined) is precise but for a multi-word
    # natural-language query ("什么时候开始选课") it requires EVERY token in one doc
    # and usually returns nothing; query_index falls back to "OR" so such queries
    # still recall (BM25 then ranks docs matching more tokens higher).
    quoted = [f'"{t.replace(chr(34), chr(34) + chr(34))}"' for t in _fts_tokens(query)]
    return (" OR " if operator == "OR" else " ").join(quoted)


def _like_escape(value: str) -> str:
    r"""Escape SQL LIKE metacharacters so a path_prefix is matched literally.

    ``%`` and ``_`` are LIKE wildcards; a caller prefix like ``_meta`` or a stray
    ``%`` would otherwise over-match. Paired with ``ESCAPE '\'`` in the query."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _has_meta_columns(index: Path) -> bool:
    """True if the index carries schema-3 metadata columns (older indexes lack them)."""
    try:
        with _connect(index) as connection:
            cols = {row[1] for row in connection.execute("PRAGMA table_info(documents)")}
        return "doc_type" in cols
    except sqlite3.Error:
        return False


def _meta_filter_clauses(
    *,
    meta_ok: bool,
    suffix: str | None,
    path_prefix: str | None,
    doc_type: str | None,
    dept: str | None,
    category: str | None,
    date_from: str | None,
    date_to: str | None,
) -> tuple[list[str], list[Any], bool]:
    """Build the non-FTS WHERE clauses (referencing ``documents.<col>``) shared by the
    BM25 SQL and the vector post-filter. ``applied`` mirrors the BM25 path: it is True
    only when a schema-3 *metadata* filter (not suffix/path) was applied."""
    where: list[str] = []
    params: list[Any] = []
    applied = False
    if suffix:
        where.append("documents.suffix = ?")
        params.append(suffix.lower().lstrip("."))
    if path_prefix:
        where.append("documents.path LIKE ? ESCAPE '\\'")
        params.append(_like_escape(path_prefix) + "%")
    if meta_ok:
        for column, value in (("doc_type", doc_type), ("dept", dept), ("category", category)):
            if value:
                where.append(f"documents.{column} = ?")
                params.append(value)
                applied = True
        if date_from:
            where.append("documents.publish_date >= ?")
            params.append(date_from)
            applied = True
        if date_to:
            where.append("documents.publish_date <= ?")
            params.append(date_to)
            applied = True
    return where, params, applied


def _bm25_select(meta_ok: bool) -> str:
    meta_select = (
        "documents.doc_type AS doc_type, documents.dept AS dept, "
        "documents.category AS category, documents.publish_date AS publish_date, "
        "documents.source_url AS source_url,"
        if meta_ok
        else "NULL AS doc_type, NULL AS dept, NULL AS category, NULL AS publish_date, NULL AS source_url,"
    )
    return f"""
        SELECT
          documents.id AS doc_id,
          documents.path AS path,
          documents.title AS title,
          documents.suffix AS suffix,
          documents.size_bytes AS size_bytes,
          {meta_select}
          bm25(documents_fts, 2.0, 6.0, 1.0) AS score,
          snippet(documents_fts, 2, '[', ']', ' ... ', 12) AS snippet
        FROM documents_fts
        JOIN documents ON documents_fts.rowid = documents.id
        WHERE {{where}}
        ORDER BY {{order}}
        LIMIT ?
        """


def _bm25_search(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int,
    title_only: bool,
    meta_ok: bool,
    meta_where: list[str],
    meta_params: list[Any],
    order_by: str,
) -> tuple[list[sqlite3.Row], str, str]:
    """Run the BM25/FTS query (with the AND→OR natural-language fallback). Returns
    ``(rows, matched_operator, fts_query)``. Rows carry ``doc_id`` for fusion."""
    tokens = _fts_tokens(query)

    def _match_expr(operator: str) -> str:
        expr = _normalize_fts_query(query, operator=operator)
        return f"title_index:({expr})" if title_only else expr

    where = ["documents_fts MATCH ?", *meta_where]
    order = "documents.publish_date DESC, score ASC" if (order_by == "date" and meta_ok) else "score ASC"
    sql = _bm25_select(meta_ok).format(where=" AND ".join(where), order=order)

    fts_query = _match_expr("AND")
    params: list[Any] = [fts_query, *meta_params, limit]
    matched_operator = "AND"
    rows = connection.execute(sql, params).fetchall()
    # A multi-token natural-language query under implicit-AND often matches nothing;
    # retry once with OR so it recalls (bm25 still ranks first).
    if not rows and len(tokens) >= 2:
        matched_operator = "OR"
        fts_query = _match_expr("OR")
        params[0] = fts_query
        rows = connection.execute(sql, params).fetchall()
    return rows, matched_operator, fts_query


def _vector_search(
    connection: sqlite3.Connection,
    embed_meta: JsonObject,
    query: str,
    *,
    pool: int,
    meta_where: list[str],
    meta_params: list[Any],
) -> list[tuple[int, float]] | None:
    """KNN over the vec0 table for ``query``, post-filtered by the metadata clauses.

    Returns ``[(doc_id, distance), ...]`` ordered by ascending distance, or ``None``
    when the vector path is unavailable for any reason — missing/mismatched embedder,
    extension that won't load, or an inference error. Any failure degrades to BM25;
    this never raises so the overall query stays answerable."""
    from .embed import get_embedder

    try:
        embedder = get_embedder(str(embed_meta["model"]))
        if embedder is None or embedder.dim != embed_meta["dim"]:
            return None
        if not _load_vec_extension(connection):
            return None
        vectors = embedder.embed([query], is_query=True)
        if vectors.shape[0] == 0:
            return []
        blob = vectors[0].astype("float32").tobytes()
        knn = connection.execute(
            f"SELECT rowid AS doc_id, distance FROM {VEC_TABLE} "
            "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (blob, pool),
        ).fetchall()
    except Exception:
        return None
    order = [(int(row["doc_id"]), float(row["distance"])) for row in knn]
    if meta_where and order:
        ids = [doc_id for doc_id, _ in order]
        placeholders = ",".join("?" * len(ids))
        allowed = {
            row[0]
            for row in connection.execute(
                f"SELECT id FROM documents WHERE id IN ({placeholders}) AND {' AND '.join(meta_where)}",
                [*ids, *meta_params],
            )
        }
        order = [(doc_id, dist) for doc_id, dist in order if doc_id in allowed]
    return order


def _rrf_scores(rank_lists: list[list[int]], *, k: int = RRF_K) -> dict[int, float]:
    scores: dict[int, float] = {}
    for ranked in rank_lists:
        for rank, key in enumerate(ranked):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
    return scores


def _plain_snippet(body: str, *, limit: int = 160) -> str:
    """Whitespace-collapsed lead of a document body, for vector-only hits that have no
    FTS-generated snippet."""
    return " ".join((body or "").split())[:limit]


def _fetch_documents(connection: sqlite3.Connection, ids: list[int], meta_ok: bool) -> dict[int, sqlite3.Row]:
    if not ids:
        return {}
    meta_cols = (
        "doc_type, dept, category, publish_date, source_url"
        if meta_ok
        else "NULL AS doc_type, NULL AS dept, NULL AS category, NULL AS publish_date, NULL AS source_url"
    )
    placeholders = ",".join("?" * len(ids))
    rows = connection.execute(
        f"SELECT id AS doc_id, path, title, suffix, size_bytes, body, {meta_cols} "
        f"FROM documents WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    return {int(row["doc_id"]): row for row in rows}


def _format_row(row: sqlite3.Row, *, score: float, snippet: str) -> JsonObject:
    return {
        "path": row["path"],
        "title": row["title"],
        "suffix": row["suffix"],
        "size_bytes": row["size_bytes"],
        "doc_type": row["doc_type"],
        "dept": row["dept"],
        "category": row["category"],
        "publish_date": row["publish_date"],
        "source_url": row["source_url"],
        "score": score,
        "snippet": snippet,
    }


def query_index(
    index: Path,
    query: str,
    *,
    limit: int = 10,
    suffix: str | None = None,
    path_prefix: str | None = None,
    title_only: bool = False,
    doc_type: str | None = None,
    dept: str | None = None,
    category: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    order_by: str = "relevance",
    mode: str | None = None,
) -> JsonObject:
    """Search the derived index.

    ``mode`` selects the retrieval path: ``bm25`` (lexical only), ``vector`` (semantic
    only), or ``hybrid`` (BM25 + vector fused with RRF). Default: ``hybrid`` when the
    index carries vectors, else ``bm25``. A vector/hybrid request on a BM25-only index,
    or when the query embedder is unavailable, degrades to BM25 — never errors."""
    index = index.expanduser().resolve()
    if limit < 1:
        return {"ok": False, "index": str(index), "query": query, "errors": ["limit must be >= 1"]}
    if not query.strip():
        return {"ok": False, "index": str(index), "query": query, "errors": ["query must not be empty"]}
    if not index.is_file():
        return {"ok": False, "index": str(index), "query": query, "errors": ["index file not found"]}
    if mode is not None and mode not in SEARCH_MODES:
        return {
            "ok": False, "index": str(index), "query": query,
            "errors": [f"mode must be one of: {', '.join(SEARCH_MODES)}"],
        }

    # Metadata columns/filters exist from schema 3+; on an older index fall back to
    # the plain FTS query so a stale index keeps answering instead of erroring.
    meta_ok = _has_meta_columns(index)
    embed_meta = index_embed_meta(index)
    has_vectors = embed_meta is not None
    requested_mode = mode
    effective_mode = mode or ("hybrid" if has_vectors else "bm25")
    degraded_reason = ""
    if effective_mode in ("vector", "hybrid") and not has_vectors:
        effective_mode = "bm25"
        degraded_reason = "index has no vectors"
    elif effective_mode in ("vector", "hybrid") and title_only:
        # Vectors embed title+body, not the title alone; a title_only query is a
        # lexical-precision request, so route it through BM25 over title_index.
        effective_mode = "bm25"
        degraded_reason = "title_only restricts to BM25 (vectors are not title-scoped)"

    meta_where, meta_params, applied_meta = _meta_filter_clauses(
        meta_ok=meta_ok, suffix=suffix, path_prefix=path_prefix, doc_type=doc_type,
        dept=dept, category=category, date_from=date_from, date_to=date_to,
    )

    try:
        with _connect(index) as connection:
            result = _run_search(
                connection,
                query=query,
                limit=limit,
                title_only=title_only,
                order_by=order_by,
                meta_ok=meta_ok,
                meta_where=meta_where,
                meta_params=meta_params,
                embed_meta=embed_meta,
                effective_mode=effective_mode,
            )
    except sqlite3.Error as exc:
        return {"ok": False, "index": str(index), "query": query, "errors": [f"sqlite_error: {exc}"]}

    results, mode_used, fts_query, matched_operator, score_kind, degraded = result
    if degraded:
        degraded_reason = degraded_reason or "query embedder unavailable"

    filters: JsonObject = {
        "suffix": suffix,
        "path_prefix": path_prefix,
        "title_only": title_only,
        "doc_type": doc_type,
        "dept": dept,
        "category": category,
        "date_from": date_from,
        "date_to": date_to,
        "order_by": order_by,
        "metadata_available": meta_ok,
        "metadata_applied": applied_meta,
        "matched_operator": matched_operator,
        "requested_mode": requested_mode,
        "mode": mode_used,
        "score_kind": score_kind,
        "has_vectors": has_vectors,
    }
    if degraded_reason:
        filters["degraded_reason"] = degraded_reason
    return {
        "ok": True,
        "index": str(index),
        "query": query,
        "fts_query": fts_query,
        "limit": limit,
        "filters": filters,
        "result_count": len(results),
        "results": results,
    }


def _run_search(
    connection: sqlite3.Connection,
    *,
    query: str,
    limit: int,
    title_only: bool,
    order_by: str,
    meta_ok: bool,
    meta_where: list[str],
    meta_params: list[Any],
    embed_meta: JsonObject | None,
    effective_mode: str,
) -> tuple[list[JsonObject], str, str | None, str, str, bool]:
    """Execute the search for ``effective_mode`` on an open connection.

    Returns ``(results, mode_used, fts_query, matched_operator, score_kind, degraded)``.
    ``degraded`` is True when a vector/hybrid request fell back to BM25 because the
    embedder was unavailable at query time."""

    def _bm25_only() -> tuple[list[JsonObject], str, str | None, str, str, bool]:
        rows, matched_operator, fts_query = _bm25_search(
            connection, query, limit=limit, title_only=title_only, meta_ok=meta_ok,
            meta_where=meta_where, meta_params=meta_params, order_by=order_by,
        )
        out = [_format_row(row, score=row["score"], snippet=row["snippet"]) for row in rows]
        return out, "bm25", fts_query, matched_operator, "bm25", False

    if effective_mode == "bm25":
        return _bm25_only()

    assert embed_meta is not None  # vector/hybrid only reached when the index has vectors

    if effective_mode == "vector":
        vector = _vector_search(
            connection, embed_meta, query, pool=limit, meta_where=meta_where, meta_params=meta_params
        )
        if vector is None:
            results, _, fts_query, matched_operator, _, _ = _bm25_only()
            return results, "bm25", fts_query, matched_operator, "bm25", True
        ids = [doc_id for doc_id, _ in vector][:limit]
        distance = dict(vector)
        rows_by_id = _fetch_documents(connection, ids, meta_ok)
        out: list[JsonObject] = []
        for doc_id in ids:
            row = rows_by_id.get(doc_id)
            if row is not None:
                out.append(_format_row(row, score=distance[doc_id], snippet=_plain_snippet(row["body"])))
        return out, "vector", None, "vector", "distance", False

    # hybrid: BM25 candidate pool + vector candidate pool fused with RRF.
    bm_rows, matched_operator, fts_query = _bm25_search(
        connection, query, limit=HYBRID_POOL, title_only=title_only, meta_ok=meta_ok,
        meta_where=meta_where, meta_params=meta_params, order_by=order_by,
    )
    vector = _vector_search(
        connection, embed_meta, query, pool=HYBRID_POOL, meta_where=meta_where, meta_params=meta_params
    )
    if vector is None:
        out = [_format_row(row, score=row["score"], snippet=row["snippet"]) for row in bm_rows[:limit]]
        return out, "bm25", fts_query, matched_operator, "bm25", True

    bm_ids = [int(row["doc_id"]) for row in bm_rows]
    vec_ids = [doc_id for doc_id, _ in vector]
    scores = _rrf_scores([bm_ids, vec_ids])
    fused_ids = sorted(scores, key=lambda doc_id: -scores[doc_id])[:limit]

    bm_snippets = {int(row["doc_id"]): row["snippet"] for row in bm_rows}
    rows_by_id = _fetch_documents(connection, fused_ids, meta_ok)
    out = []
    for doc_id in fused_ids:
        row = rows_by_id.get(doc_id)
        if row is None:
            continue
        snippet = bm_snippets.get(doc_id) or _plain_snippet(row["body"])
        out.append(_format_row(row, score=round(scores[doc_id], 6), snippet=snippet))
    if order_by == "date" and meta_ok:
        out.sort(key=lambda r: (r["publish_date"] or ""), reverse=True)
    return out, "hybrid", fts_query, matched_operator, "rrf", False


def recent_documents(
    index: Path,
    *,
    limit: int = 20,
    suffix: str | None = None,
    path_prefix: str | None = None,
) -> JsonObject:
    """Most-recently-modified indexed documents (by mtime_ns), newest first."""
    index = index.expanduser().resolve()
    if limit < 1:
        return {"ok": False, "index": str(index), "errors": ["limit must be >= 1"]}
    if not index.is_file():
        return {"ok": False, "index": str(index), "errors": ["index file not found"]}
    where: list[str] = []
    params: list[Any] = []
    if suffix:
        where.append("suffix = ?")
        params.append(suffix.lower().lstrip("."))
    if path_prefix:
        where.append("path LIKE ? ESCAPE '\\'")
        params.append(_like_escape(path_prefix) + "%")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    params.append(limit)
    try:
        with _connect(index) as connection:
            rows = connection.execute(
                f"SELECT path, suffix, title, size_bytes, mtime_ns FROM documents{clause} "
                "ORDER BY mtime_ns DESC LIMIT ?",
                params,
            ).fetchall()
    except sqlite3.Error as exc:
        return {"ok": False, "index": str(index), "errors": [f"sqlite_error: {exc}"]}
    docs = [
        {
            "path": row["path"],
            "suffix": row["suffix"],
            "title": row["title"],
            "size_bytes": row["size_bytes"],
            "mtime": datetime.fromtimestamp(row["mtime_ns"] / 1e9, timezone.utc).isoformat(),
        }
        for row in rows
    ]
    return {"ok": True, "index": str(index), "count": len(docs), "documents": docs}


def query_courses(
    index: Path,
    *,
    code: str | None = None,
    name_like: str | None = None,
    program_path: str | None = None,
    dept: str | None = None,
    grade: str | None = None,
    min_credits: float | None = None,
    required_only: bool = False,
    limit: int = 200,
) -> JsonObject:
    """Structured query over the courses table (培养方案 课程行).

    e.g. code="PHYS1001B" → every program that lists it; dept+grade → a major's
    courses; min_credits=4 → heavy courses. Returns [] cleanly on a pre-schema-3
    index that has no courses table."""
    index = index.expanduser().resolve()
    if limit < 1:
        return {"ok": False, "index": str(index), "errors": ["limit must be >= 1"]}
    if not index.is_file():
        return {"ok": False, "index": str(index), "errors": ["index file not found"]}
    where: list[str] = []
    params: list[Any] = []
    if code:
        where.append("code = ?")
        params.append(code)
    if name_like:
        where.append("name LIKE ?")
        params.append(f"%{name_like}%")
    if program_path:
        where.append("program_path = ?")
        params.append(program_path)
    if dept:
        where.append("dept = ?")
        params.append(dept)
    if grade:
        where.append("grade = ?")
        params.append(grade)
    if min_credits is not None:
        where.append("credits >= ?")
        params.append(min_credits)
    if required_only:
        where.append("required = 1")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    params.append(limit)
    try:
        with _connect(index) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "courses" not in tables:
                return {"ok": False, "index": str(index), "errors": ["index has no courses table; rebuild with schema 3+"]}
            rows = connection.execute(
                "SELECT program_name, dept, grade, module, code, name, credits, periods, "
                "required, term, open_dept, program_path "
                f"FROM courses{clause} ORDER BY dept, grade, code LIMIT ?",
                params,
            ).fetchall()
    except sqlite3.Error as exc:
        return {"ok": False, "index": str(index), "errors": [f"sqlite_error: {exc}"]}
    courses = [
        {
            "program_name": row["program_name"],
            "dept": row["dept"],
            "grade": row["grade"],
            "module": row["module"],
            "code": row["code"],
            "name": row["name"],
            "credits": row["credits"],
            "periods": row["periods"],
            "required": bool(row["required"]),
            "term": row["term"],
            "open_dept": row["open_dept"],
            "program_path": row["program_path"],
        }
        for row in rows
    ]
    return {
        "ok": True,
        "index": str(index),
        "count": len(courses),
        "filters": {
            "code": code,
            "name_like": name_like,
            "program_path": program_path,
            "dept": dept,
            "grade": grade,
            "min_credits": min_credits,
            "required_only": required_only,
        },
        "courses": courses,
    }


def update_meta_columns(index: Path, root: Path, rel_path: str) -> dict[str, Any]:
    """H M1:单文档 frontmatter 元数据列同步(annotate 后的索引一致性)。

    只 UPDATE documents 表的元数据列——annotate 只动 frontmatter,而 FTS 与向量的
    输入都是去 frontmatter 的正文(见 _strip_frontmatter),所以无需重嵌入/重建,
    检索延迟零影响。行不存在(未入索引的新文件)交给索引构建,不在此 insert。
    """
    target = (root / rel_path).resolve()
    if not target.is_file():
        return {"ok": False, "reason": "file_missing", "path": rel_path}
    if not _has_meta_columns(index):
        return {"ok": False, "reason": "no_meta_columns", "path": rel_path}
    text, err = _read_index_text(target, max_bytes=DEFAULT_MAX_BYTES)
    if err:
        return {"ok": False, "reason": err, "path": rel_path}
    meta = _meta_columns(_parse_frontmatter(text))
    try:
        connection = sqlite3.connect(index)
        try:
            cursor = connection.execute(
                "UPDATE documents SET doc_type=?, dept=?, category=?, publish_date=?, "
                "grade=?, source_url=?, course_count=? WHERE path=?",
                (
                    meta["doc_type"], meta["dept"], meta["category"],
                    meta["publish_date"], meta["grade"], meta["source_url"],
                    meta["course_count"], rel_path,
                ),
            )
            connection.commit()
            updated = cursor.rowcount
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return {"ok": False, "reason": f"sqlite: {exc}", "path": rel_path}
    if updated == 0:
        return {"ok": False, "reason": "not_indexed", "path": rel_path}
    return {"ok": True, "path": rel_path, "updated": updated}


def remove_from_index(index: Path, rel_path: str) -> dict[str, Any]:
    """H M3:从索引移除单个文档路径的所有行(retire/move-away 的索引一致性)。

    删除 documents / documents_fts / vec_documents / courses 中该 path 的行——与
    ``build_index`` 增量删除 stale 行走同一套 SQL(见 build_index 的 _stale 清理),
    但这里是"按 path 直接删一条",不重建、不重嵌入。用于:
      - retire:文件移进 _archive/(索引器 SKIP_DIRS 已跳过 _archive),行留在索引里
        会造成 drift + 检索仍命中,必须删;
      - move:旧路径不复存在(变墓碑,墓碑本身 status=moved 一般不需入检索),删旧
        path 行;新路径由后续增量重建收录(move 后需 reindex,handler 会标提示)。

    行不存在(未入索引)不是错误:返回 removed=0。索引不存在/无元数据列则报原因,
    调用方按"索引需重建"兜底。retire 不因索引同步失败而回滚文件(与 annotate 同纹理,
    夜巡 drift 兜底)。"""
    if not index.exists():
        return {"ok": False, "reason": "no_index", "path": rel_path}
    try:
        connection = _connect(index)
        try:
            vec_ready = _vec_table_exists(connection) and _load_vec_extension(connection)
            ids = [
                int(r[0])
                for r in connection.execute(
                    "SELECT id FROM documents WHERE path = ?", (rel_path,)
                )
            ]
            for rid in ids:
                connection.execute("DELETE FROM documents_fts WHERE rowid = ?", (rid,))
                if vec_ready:
                    connection.execute(f"DELETE FROM {VEC_TABLE} WHERE rowid = ?", (rid,))
            connection.execute("DELETE FROM courses WHERE program_path = ?", (rel_path,))
            cursor = connection.execute("DELETE FROM documents WHERE path = ?", (rel_path,))
            removed = cursor.rowcount
            connection.commit()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return {"ok": False, "reason": f"sqlite: {exc}", "path": rel_path}
    return {"ok": True, "path": rel_path, "removed": removed}
