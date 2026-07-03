# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Tests for the schema-4 vector / hybrid search layer.

Split into two groups:
- "degrade" tests run everywhere (no model/deps needed): they assert that without an
  embedder the build stays BM25-only (schema 3) and that vector/hybrid queries fall
  back to BM25 instead of erroring. These keep CI independent of the embedding model.
- "roundtrip" tests need a real embedding model on the host; they ``skip`` otherwise.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "packages" / "brain-library" / "src"


def _load_indexer():
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    return importlib.import_module("brain_library.indexer")


def _make_fixture(tmp_path: Path) -> Path:
    brain = tmp_path / "brain"
    (brain / "knowledge").mkdir(parents=True)
    docs = {
        "电子论.md": "# 自由电子论\n自由电子论把金属中的价电子近似看作自由电子气，用于解释电导和热导。",
        "布里渊.md": "# 布里渊区\n布里渊区是倒易空间中的基本区域，用于描述能带和晶格周期性。",
        "转专业.md": "# 转专业流程\n本科生转专业需在学期初提交申请，经学院审核后办理。",
        "选课.md": "# 选课时间\n每学期选课在开学前两周开始，分初选和补退选阶段。",
        "奖学金.md": "# 奖学金评定\n本科生奖学金按学业成绩和综合表现评定，每学年一次。",
    }
    for name, text in docs.items():
        (brain / "knowledge" / name).write_text(text, encoding="utf-8")
    return brain


def _model_available() -> bool:
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    try:
        from brain_library.embed import get_embedder
    except Exception:
        return False
    return get_embedder() is not None


def _vector_stack_available() -> bool:
    """The vector roundtrip needs BOTH a real embedder AND the sqlite-vec extension.
    A host can have one without the other (e.g. the bge model sits in the HF cache but
    the ``sqlite_vec`` wheel isn't installed); without sqlite-vec the build degrades to
    a BM25-only schema-3 index, which would make the "expects schema 4 / hybrid" tests
    fail rather than skip. Guard on both so such hosts skip cleanly."""
    if not _model_available():
        return False
    try:
        import sqlite_vec  # noqa: F401
    except Exception:
        return False
    return True


# --------------------------------------------------------------------------
# Degrade path — always runs, never needs a model.
# --------------------------------------------------------------------------


def test_build_without_model_stays_bm25(tmp_path, monkeypatch):
    """An unknown model key -> no embedder -> schema-3 BM25 build with a note."""
    monkeypatch.setenv("BRAIN_LIBRARY_EMBED_MODEL", "definitely-not-a-real-model")
    indexer = _load_indexer()
    brain = _make_fixture(tmp_path)
    out = tmp_path / "idx.sqlite"

    build = indexer.build_index(brain, out, force=True, embed=True)
    assert build["ok"] is True
    assert build["schema_version"] == 3
    assert build["embedded"] is False
    assert build["vectors_written"] == 0
    assert "embed_note" in build  # records that embed was requested but unavailable
    assert indexer.index_embed_meta(out) is None


def test_hybrid_query_on_bm25_index_degrades(tmp_path):
    """mode=hybrid against a BM25-only index falls back to BM25 (no error)."""
    indexer = _load_indexer()
    brain = _make_fixture(tmp_path)
    out = tmp_path / "idx.sqlite"
    assert indexer.build_index(brain, out, force=True, embed=False)["ok"]

    res = indexer.query_index(out, "选课", limit=3, mode="hybrid")
    assert res["ok"] is True
    assert res["filters"]["mode"] == "bm25"
    assert res["filters"]["requested_mode"] == "hybrid"
    assert res["filters"]["has_vectors"] is False
    assert res["filters"]["degraded_reason"] == "index has no vectors"


def test_invalid_mode_rejected(tmp_path):
    indexer = _load_indexer()
    brain = _make_fixture(tmp_path)
    out = tmp_path / "idx.sqlite"
    assert indexer.build_index(brain, out, force=True, embed=False)["ok"]
    res = indexer.query_index(out, "选课", mode="semantic")
    assert res["ok"] is False
    assert "mode must be one of" in res["errors"][0]


def test_model_located_by_convention_without_env(tmp_path, monkeypatch):
    """A model fetched to the default state dir is found with no env var (deployment
    relies on this: the gateway is launched via ssh and can't depend on env). Also
    checks bge/qwen3 don't cross-wire when both are present."""
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    from brain_library import embed as embmod

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("BRAIN_LIBRARY_EMBED_MODEL_DIR", raising=False)
    monkeypatch.setattr(embmod, "_HF_CACHE", tmp_path / "no-hf-cache")
    models = tmp_path / "state" / "rtime-assistant" / "brain-library" / "models"
    # bge at its conventional subdir; a qwen3 subdir alongside to test isolation.
    bge = models / "bge-small-zh-v1.5"
    (bge / "onnx").mkdir(parents=True)
    (bge / "onnx" / "model_quantized.onnx").write_bytes(b"x")
    (bge / "tokenizer.json").write_text("{}", encoding="utf-8")
    qwen = models / "Qwen3-Embedding-0.6B-ONNX"
    (qwen / "onnx").mkdir(parents=True)
    (qwen / "onnx" / "model_quantized.onnx").write_bytes(b"x")
    (qwen / "tokenizer.json").write_text("{}", encoding="utf-8")

    onnx, tok = embmod._locate_files(embmod._SPECS["bge-small"])
    assert onnx is not None and "bge-small-zh" in str(onnx)
    assert tok is not None and tok.parent == bge  # tokenizer scoped to bge's root
    o2, t2 = embmod._locate_files(embmod._SPECS["qwen3-0.6b"])
    assert o2 is not None and "Qwen3-Embedding-0.6B" in str(o2)
    assert t2 is not None and t2.parent == qwen


def test_build_vector_failure_degrades_to_bm25(tmp_path, monkeypatch):
    """A failure inside the vector layer keeps the BM25 index (schema 3), not aborts."""
    pytest.importorskip("sqlite_vec")
    np = pytest.importorskip("numpy")
    indexer = _load_indexer()
    from brain_library import embed as embmod

    class FakeEmbedder:
        model_name = "fake"
        dim = 8

        def embed(self, texts, *, is_query=False, batch=32):
            return np.ones((len(texts), 8), dtype=np.float32)

    monkeypatch.setattr(embmod, "get_embedder", lambda *a, **k: FakeEmbedder())

    def _boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(indexer, "_embed_and_store", _boom)

    brain = _make_fixture(tmp_path)
    out = tmp_path / "idx.sqlite"
    build = indexer.build_index(brain, out, force=True, embed=True)
    assert build["ok"] is True
    assert build["schema_version"] == 3
    assert build["embedded"] is False
    assert "vector layer failed" in build.get("embed_note", "")
    # BM25 index is intact and the dropped vec table leaves no trace.
    assert indexer.index_embed_meta(out) is None
    assert indexer.query_index(out, "选课")["ok"] is True


# --------------------------------------------------------------------------
# Roundtrip path — needs a real embedding model; skipped otherwise.
# --------------------------------------------------------------------------

requires_model = pytest.mark.skipif(
    not _vector_stack_available(),
    reason="no embedding model + sqlite-vec stack available on this host",
)


@requires_model
def test_build_with_vectors_is_schema_4(tmp_path):
    indexer = _load_indexer()
    brain = _make_fixture(tmp_path)
    out = tmp_path / "idx.sqlite"

    build = indexer.build_index(brain, out, force=True, embed=True)
    assert build["ok"] is True
    assert build["schema_version"] == 4
    assert build["embedded"] is True
    assert build["vectors_written"] == build["documents_indexed"]

    status = indexer.index_status(out)
    assert status["has_vectors"] is True
    assert isinstance(status["embed_dim"], int) and status["embed_dim"] > 0
    assert status["vector_count"] == build["vectors_written"]
    meta = indexer.index_embed_meta(out)
    assert meta is not None and meta["model"] == build["embed_model"]
    assert meta["dim"] == status["embed_dim"]


@requires_model
def test_modes_and_hybrid_default(tmp_path):
    indexer = _load_indexer()
    brain = _make_fixture(tmp_path)
    out = tmp_path / "idx.sqlite"
    assert indexer.build_index(brain, out, force=True, embed=True)["ok"]

    # Default mode is hybrid once the index carries vectors.
    default = indexer.query_index(out, "选课", limit=3)
    assert default["filters"]["mode"] == "hybrid"
    assert default["filters"]["score_kind"] == "rrf"

    for mode, kind in (("bm25", "bm25"), ("vector", "distance"), ("hybrid", "rrf")):
        res = indexer.query_index(out, "金属里的电子怎么导电", limit=3, mode=mode)
        assert res["ok"] is True
        assert res["filters"]["mode"] == mode
        assert res["filters"]["score_kind"] == kind
        assert res["result_count"] >= 1


@requires_model
def test_vector_recalls_semantic_synonym(tmp_path):
    """The vector path should surface 转专业 for a synonym query ('换专业')."""
    indexer = _load_indexer()
    brain = _make_fixture(tmp_path)
    out = tmp_path / "idx.sqlite"
    assert indexer.build_index(brain, out, force=True, embed=True)["ok"]

    vec = indexer.query_index(out, "怎么换专业", limit=3, mode="vector")
    assert vec["ok"] is True
    paths = [r["path"] for r in vec["results"]]
    assert any("转专业" in p for p in paths)
    # Hybrid keeps it ranked at or near the top.
    hyb = indexer.query_index(out, "怎么换专业", limit=3, mode="hybrid")
    assert "转专业" in hyb["results"][0]["path"]


@requires_model
def test_title_only_routes_to_bm25(tmp_path):
    indexer = _load_indexer()
    brain = _make_fixture(tmp_path)
    out = tmp_path / "idx.sqlite"
    assert indexer.build_index(brain, out, force=True, embed=True)["ok"]

    res = indexer.query_index(out, "选课", limit=3, title_only=True)
    assert res["filters"]["mode"] == "bm25"
    assert "title_index" in (res["fts_query"] or "")


@requires_model
def test_query_embed_error_degrades_to_bm25(tmp_path, monkeypatch):
    """If the query-side embedder raises, vector/hybrid fall back to BM25, not crash."""
    indexer = _load_indexer()
    brain = _make_fixture(tmp_path)
    out = tmp_path / "idx.sqlite"
    assert indexer.build_index(brain, out, force=True, embed=True)["ok"]

    from brain_library import embed as embmod

    class BoomEmbedder:
        model_name = "bge-small"
        dim = 512

        def embed(self, *_a, **_k):
            raise RuntimeError("inference exploded")

    monkeypatch.setattr(embmod, "get_embedder", lambda *a, **k: BoomEmbedder())
    res = indexer.query_index(out, "选课", limit=3, mode="hybrid")
    assert res["ok"] is True
    assert res["filters"]["mode"] == "bm25"
    assert res["filters"]["degraded_reason"] == "query embedder unavailable"
    assert res["result_count"] >= 1


@requires_model
def test_filters_apply_in_hybrid(tmp_path):
    """suffix filter must hold on every hybrid result (vector path is post-filtered)."""
    indexer = _load_indexer()
    brain = _make_fixture(tmp_path)
    out = tmp_path / "idx.sqlite"
    assert indexer.build_index(brain, out, force=True, embed=True)["ok"]

    res = indexer.query_index(out, "选课", limit=5, mode="hybrid", suffix="md")
    assert res["ok"] is True
    assert all(r["suffix"] == "md" for r in res["results"])


def test_incremental_reuses_unchanged_vectors(tmp_path, monkeypatch):
    """增量重建：未变文档复用旧向量(不重嵌入)，只对改动/新增的嵌入。"""
    pytest.importorskip("sqlite_vec")
    np = pytest.importorskip("numpy")
    indexer = _load_indexer()
    from brain_library import embed as embmod

    embedded = []  # 每次 embed 调用的文本数，用于验证"只嵌入改动的"

    class CountingEmbedder:
        model_name = "fake"
        dim = 8

        def embed(self, texts, *, is_query=False, batch=32):
            embedded.append(len(texts))
            return np.ones((len(texts), 8), dtype=np.float32)

    monkeypatch.setattr(embmod, "get_embedder", lambda *a, **k: CountingEmbedder())
    brain = _make_fixture(tmp_path)
    out = tmp_path / "idx.sqlite"

    # 1) 全量构建 → 5 篇全嵌入
    b1 = indexer.build_index(brain, out, force=True, embed=True)
    assert b1["ok"] and b1["embedded"] and b1["vectors_written"] == 5

    # 2) 无改动增量 → 全复用、零嵌入
    embedded.clear()
    b2 = indexer.build_index(brain, out, embed=True, incremental=True)
    assert b2["ok"] and b2["vectors_written"] == 5
    assert b2.get("reused_vectors") == 5 and b2.get("embedded_fresh") == 0
    assert sum(embedded) == 0  # 一次嵌入都没跑

    # 3) 改一篇 → 只重嵌入那 1 篇，其余 4 复用
    (brain / "knowledge" / "选课.md").write_text(
        "# 选课时间\n内容已修改用于触发增量重嵌入这一篇文档而其它保持不变。",
        encoding="utf-8",
    )
    embedded.clear()
    b3 = indexer.build_index(brain, out, embed=True, incremental=True)
    assert b3["vectors_written"] == 5
    assert b3.get("reused_vectors") == 4 and b3.get("embedded_fresh") == 1
    assert sum(embedded) == 1
    assert indexer.query_index(out, "选课")["ok"] is True


def test_incremental_model_mismatch_falls_back_to_full(tmp_path, monkeypatch):
    """旧索引是别的嵌入模型时,增量不复用(避免混模型),全量重嵌入。"""
    pytest.importorskip("sqlite_vec")
    np = pytest.importorskip("numpy")
    indexer = _load_indexer()
    from brain_library import embed as embmod

    class E1:
        model_name = "m1"
        dim = 8

        def embed(self, texts, *, is_query=False, batch=32):
            return np.ones((len(texts), 8), dtype=np.float32)

    class E2:
        model_name = "m2"
        dim = 8

        def embed(self, texts, *, is_query=False, batch=32):
            return np.ones((len(texts), 8), dtype=np.float32)

    brain = _make_fixture(tmp_path)
    out = tmp_path / "idx.sqlite"
    monkeypatch.setattr(embmod, "get_embedder", lambda *a, **k: E1())
    indexer.build_index(brain, out, force=True, embed=True)
    monkeypatch.setattr(embmod, "get_embedder", lambda *a, **k: E2())
    b = indexer.build_index(brain, out, embed=True, incremental=True)
    assert b["ok"] and b.get("reused_vectors") is None  # 模型不匹配→不复用
    assert b["vectors_written"] == 5


def test_incremental_reuses_fts_rows_and_handles_delete(tmp_path, monkeypatch):
    """tier-2：未变文档连 FTS/jieba 分词都复用(不重读不重切)；删除的文档从索引移除。"""
    pytest.importorskip("sqlite_vec")
    np = pytest.importorskip("numpy")
    indexer = _load_indexer()
    from brain_library import embed as embmod

    class FakeEmbedder:
        model_name = "fake"
        dim = 8

        def embed(self, texts, *, is_query=False, batch=32):
            return np.ones((len(texts), 8), dtype=np.float32)

    monkeypatch.setattr(embmod, "get_embedder", lambda *a, **k: FakeEmbedder())

    # 计数 jieba 分词调用：未变文档应当一次都不切。
    seg_calls = []
    real_segment = indexer._segment_for_fts
    monkeypatch.setattr(
        indexer, "_segment_for_fts",
        lambda text: (seg_calls.append(1), real_segment(text))[1],
    )

    brain = _make_fixture(tmp_path)  # 5 篇
    out = tmp_path / "idx.sqlite"
    indexer.build_index(brain, out, force=True, embed=True)

    # 无改动增量：零分词、零嵌入、全复用。
    seg_calls.clear()
    b2 = indexer.build_index(brain, out, embed=True, incremental=True)
    assert b2["ok"] and b2["documents_indexed"] == 5
    assert b2.get("reused_docs") == 5 and b2.get("embedded_fresh") == 0
    assert len(seg_calls) == 0  # 未变文档没有被重新分词

    # 删一篇 + 改一篇：只对改动的分词；删除的从索引消失。
    (brain / "knowledge" / "奖学金.md").unlink()
    (brain / "knowledge" / "选课.md").write_text(
        "# 选课时间\n这是改动后的内容用来触发重新分词与嵌入仅此一篇。", encoding="utf-8"
    )
    seg_calls.clear()
    b3 = indexer.build_index(brain, out, embed=True, incremental=True)
    assert b3["ok"] and b3["documents_indexed"] == 4  # 5 - 1 删 = 4
    assert b3.get("reused_docs") == 3 and b3.get("embedded_fresh") == 1
    # 只有改动的那篇被分词(每篇切 path/title/body 三次)，远少于全量 4*3。
    assert 0 < len(seg_calls) <= 3

    # 删除的文档不再出现在检索里。
    q = indexer.query_index(out, "奖学金")
    paths = [r.get("path") for r in q.get("results", [])]
    assert "knowledge/奖学金.md" not in paths
    # 索引仍可用、且与全量重建文档数一致。
    full = indexer.build_index(brain, out, force=True, embed=True)
    assert full["documents_indexed"] == 4


def _fake_embedder_factory(np):
    class FakeEmbedder:
        model_name = "fake"
        dim = 8

        def embed(self, texts, *, is_query=False, batch=32):
            return np.ones((len(texts), 8), dtype=np.float32)
    return FakeEmbedder


def test_incremental_falls_back_to_full_when_truncated(tmp_path, monkeypatch):
    """max_files 截断时文件清单不全，增量会误删未遍历文档 → 退回全量(不复用)。"""
    pytest.importorskip("sqlite_vec")
    np = pytest.importorskip("numpy")
    indexer = _load_indexer()
    from brain_library import embed as embmod
    monkeypatch.setattr(embmod, "get_embedder", lambda *a, **k: _fake_embedder_factory(np)())
    brain = _make_fixture(tmp_path)  # 5 篇
    out = tmp_path / "idx.sqlite"
    indexer.build_index(brain, out, force=True, embed=True)
    b2 = indexer.build_index(brain, out, embed=True, incremental=True, max_files=2)
    assert b2["ok"] and b2["truncated"] is True
    assert "reused_docs" not in b2  # 截断 → 没走增量复用，退回全量
    assert b2["documents_indexed"] == 2


def test_incremental_schema3_to_4_forces_full(tmp_path, monkeypatch):
    """旧库是 BM25-only(schema 3)、本次要嵌入 → 增量自动回退全量,升到 schema 4 全嵌入。"""
    pytest.importorskip("sqlite_vec")
    np = pytest.importorskip("numpy")
    indexer = _load_indexer()
    from brain_library import embed as embmod
    monkeypatch.setattr(embmod, "get_embedder", lambda *a, **k: _fake_embedder_factory(np)())
    brain = _make_fixture(tmp_path)
    out = tmp_path / "idx.sqlite"
    indexer.build_index(brain, out, force=True, embed=False)  # schema 3
    b2 = indexer.build_index(brain, out, embed=True, incremental=True)
    assert b2["ok"] and b2["schema_version"] == 4
    assert "reused_docs" not in b2  # 不兼容 → 全量
    assert b2["vectors_written"] == 5 and b2["embedded"] is True


def test_incremental_bm25_only_reuses_rows_from_schema4(tmp_path, monkeypatch):
    """旧库 schema 4、本次 --no-embed → 复用 BM25 行产出 schema 3,不带虚假向量计数。"""
    pytest.importorskip("sqlite_vec")
    np = pytest.importorskip("numpy")
    indexer = _load_indexer()
    from brain_library import embed as embmod
    monkeypatch.setattr(embmod, "get_embedder", lambda *a, **k: _fake_embedder_factory(np)())
    brain = _make_fixture(tmp_path)
    out = tmp_path / "idx.sqlite"
    indexer.build_index(brain, out, force=True, embed=True)  # schema 4
    b2 = indexer.build_index(brain, out, embed=False, incremental=True)
    assert b2["ok"] and b2["schema_version"] == 3 and b2["embedded"] is False
    assert b2.get("reused_docs") == 5  # BM25 行复用
    assert "reused_vectors" not in b2  # 无向量 → 不写虚假向量计数
    assert b2["documents_indexed"] == 5
    assert indexer.query_index(out, "转专业")["ok"] is True


# --------------------------------------------------------------------------
# 增量索引：与全量重建的等价性 + 更多边界(rename/courses/读取错误/首建)
# --------------------------------------------------------------------------

def _dump(indexer, path):
    """把索引内容抽成可比较的快照(忽略自增 id,按 path/编号排序)。"""
    import sqlite3
    c = sqlite3.connect(path)
    docs = sorted(c.execute(
        "SELECT path,title,body,size_bytes,mtime_ns,doc_type,course_count FROM documents"
    ).fetchall())
    fts = sorted(c.execute(
        "SELECT d.path, f.path_index, f.title_index, f.body_index "
        "FROM documents_fts f JOIN documents d ON d.id=f.rowid"
    ).fetchall())
    courses = sorted(c.execute(
        "SELECT program_path,module,code,name,credits,periods,required,term,open_dept FROM courses"
    ).fetchall())
    nvec = 0
    try:
        nvec = c.execute(f"SELECT count(*) FROM {indexer.VEC_TABLE}").fetchone()[0]
    except sqlite3.Error:
        pass
    c.close()
    return docs, fts, courses, nvec


def test_incremental_parity_with_full(tmp_path, monkeypatch):
    """无改动增量重建产出的索引应与全量重建**逐行等价**(documents/FTS/courses/向量数)，
    且对多个查询返回同样的结果。增量正确性的金标准。"""
    pytest.importorskip("sqlite_vec")
    np = pytest.importorskip("numpy")
    indexer = _load_indexer()
    from brain_library import embed as embmod
    monkeypatch.setattr(embmod, "get_embedder", lambda *a, **k: _fake_embedder_factory(np)())
    brain = _make_fixture(tmp_path)

    out_full = tmp_path / "full.sqlite"
    indexer.build_index(brain, out_full, force=True, embed=True)

    out_incr = tmp_path / "incr.sqlite"
    indexer.build_index(brain, out_incr, force=True, embed=True)      # 先建
    b = indexer.build_index(brain, out_incr, incremental=True, embed=True)  # 无改动增量
    assert b["ok"] and b.get("reused_docs") == 5 and b.get("embedded_fresh") == 0

    assert _dump(indexer, out_full) == _dump(indexer, out_incr)  # 逐行等价

    for q in ("转专业", "选课时间", "自由电子论", "奖学金"):
        rf = [r["path"] for r in indexer.query_index(out_full, q).get("results", [])]
        ri = [r["path"] for r in indexer.query_index(out_incr, q).get("results", [])]
        assert rf == ri, f"query {q}: full={rf} incr={ri}"


def test_incremental_rename(tmp_path, monkeypatch):
    """改名(删旧路径+加新路径,内容不变)：旧路径消失、新路径可检索、总数不变。"""
    pytest.importorskip("sqlite_vec")
    np = pytest.importorskip("numpy")
    indexer = _load_indexer()
    from brain_library import embed as embmod
    monkeypatch.setattr(embmod, "get_embedder", lambda *a, **k: _fake_embedder_factory(np)())
    brain = _make_fixture(tmp_path)
    out = tmp_path / "idx.sqlite"
    indexer.build_index(brain, out, force=True, embed=True)

    (brain / "knowledge" / "选课.md").rename(brain / "knowledge" / "选课须知.md")
    b = indexer.build_index(brain, out, incremental=True, embed=True)
    assert b["ok"] and b["documents_indexed"] == 5  # 删1加1，净不变
    import sqlite3
    paths = {r[0] for r in sqlite3.connect(out).execute("SELECT path FROM documents")}
    assert "knowledge/选课.md" not in paths and "knowledge/选课须知.md" in paths
    assert indexer.query_index(out, "选课时间")["ok"] is True


def test_incremental_program_courses_update(tmp_path, monkeypatch):
    """ustc-program 培养方案改动：旧 courses 行被清、新课程表被重新解析,不留孤儿。"""
    pytest.importorskip("sqlite_vec")
    np = pytest.importorskip("numpy")
    indexer = _load_indexer()
    from brain_library import embed as embmod
    monkeypatch.setattr(embmod, "get_embedder", lambda *a, **k: _fake_embedder_factory(np)())
    brain = _make_fixture(tmp_path)
    prog = brain / "knowledge" / "方案.md"
    prog.write_text(
        "---\ntype: ustc-program\n---\n# 培养方案\n\n"
        "| 模块 | 编号 | 课程 | 学分 | 学时 | 必修 | 建议学期 | 开课院系 |\n"
        "|---|---|---|---|---|---|---|---|\n"
        "| 通识 | MATH001 | 数学分析 | 5 | 80 | 必 | 1 | 数院 |\n",
        encoding="utf-8",
    )
    out = tmp_path / "idx.sqlite"
    indexer.build_index(brain, out, force=True, embed=True)
    import sqlite3
    codes0 = {r[0] for r in sqlite3.connect(out).execute("SELECT code FROM courses")}
    assert codes0 == {"MATH001"}

    prog.write_text(
        "---\ntype: ustc-program\n---\n# 培养方案\n\n"
        "| 模块 | 编号 | 课程 | 学分 | 学时 | 必修 | 建议学期 | 开课院系 |\n"
        "|---|---|---|---|---|---|---|---|\n"
        "| 通识 | PHYS002 | 力学 | 4 | 64 | 必 | 2 | 物院 |\n",
        encoding="utf-8",
    )
    indexer.build_index(brain, out, incremental=True, embed=True)
    codes1 = {r[0] for r in sqlite3.connect(out).execute("SELECT code FROM courses")}
    assert codes1 == {"PHYS002"}  # 旧 MATH001 已清,无孤儿


def test_incremental_read_error_matches_full(tmp_path, monkeypatch):
    """改动后的文件读取失败：该文档缺席(与全量重建语义一致)、不崩、其它文档完好。"""
    pytest.importorskip("sqlite_vec")
    np = pytest.importorskip("numpy")
    indexer = _load_indexer()
    from brain_library import embed as embmod
    monkeypatch.setattr(embmod, "get_embedder", lambda *a, **k: _fake_embedder_factory(np)())
    brain = _make_fixture(tmp_path)
    out = tmp_path / "idx.sqlite"
    indexer.build_index(brain, out, force=True, embed=True)

    target = brain / "knowledge" / "奖学金.md"
    target.write_text("# 奖学金评定\n改了内容变更 size/mtime 触发重新索引但读取会失败。", encoding="utf-8")
    real_read = indexer._read_index_text
    def _read(path, **kw):
        if path.name == "奖学金.md":
            return "", "read_failed"
        return real_read(path, **kw)
    monkeypatch.setattr(indexer, "_read_index_text", _read)

    b = indexer.build_index(brain, out, incremental=True, embed=True)
    assert b["ok"] and b["documents_indexed"] == 4  # 读失败的那篇缺席,其余4篇在
    import sqlite3
    paths = {r[0] for r in sqlite3.connect(out).execute("SELECT path FROM documents")}
    assert "knowledge/奖学金.md" not in paths
    assert "knowledge/选课.md" in paths and "knowledge/电子论.md" in paths


def test_incremental_first_build_no_prior(tmp_path, monkeypatch):
    """首建即用 --incremental(无旧索引)：等同全量,正常建好,不复用。"""
    pytest.importorskip("sqlite_vec")
    np = pytest.importorskip("numpy")
    indexer = _load_indexer()
    from brain_library import embed as embmod
    monkeypatch.setattr(embmod, "get_embedder", lambda *a, **k: _fake_embedder_factory(np)())
    brain = _make_fixture(tmp_path)
    out = tmp_path / "idx.sqlite"
    b = indexer.build_index(brain, out, incremental=True, embed=True)
    assert b["ok"] and b["documents_indexed"] == 5 and b["vectors_written"] == 5
    assert "reused_docs" not in b
