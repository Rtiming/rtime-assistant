# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Data models shared across stages and backends.

Language-neutral JSON shapes mirror
``docs/brain-visualmd-module.zh-CN.md`` §5.2 so out-of-process backends
(agents, remote workers) can implement the same contract.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path

from .spec import SPEC_VERSION


@dataclass
class PageRequest:
    """One unit of work handed to a transcription backend."""

    page_no: int
    page_png: str  # path relative to the docpack dir, e.g. "images/p-003.png"
    page_png_path: str | None = None  # absolute path, for backends that read the image
    doc_title: str = ""
    draft_text: str | None = None
    context_hint: str | None = None
    prev_tail: str | None = None
    spec_version: str = SPEC_VERSION

    def to_dict(self) -> dict:
        return {k: v for k, v in dataclasses.asdict(self).items() if v is not None}


@dataclass
class PageResult:
    """A backend's transcription of one page."""

    page_no: int
    markdown: str
    confidence: float = 0.0
    suspicious: list[str] = field(default_factory=list)
    backend_id: str = ""

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> PageResult:
        return cls(
            page_no=int(d["page_no"]),
            markdown=str(d["markdown"]),
            confidence=float(d.get("confidence", 0.0)),
            suspicious=list(d.get("suspicious", [])),
            backend_id=str(d.get("backend_id", "")),
        )


@dataclass
class Batch:
    """A contiguous page range routed to one backend invocation."""

    index: int
    start: int
    end: int

    @property
    def name(self) -> str:
        return f"pages-{self.start:03d}-{self.end:03d}"

    @property
    def pages(self) -> list[int]:
        return list(range(self.start, self.end + 1))


@dataclass
class Plan:
    """``plan.json`` — the deterministic scaffold for a single material."""

    slug: str
    source: str
    source_sha256: str
    pages: int
    batches: list[Batch] = field(default_factory=list)
    spec_version: str = SPEC_VERSION

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "source": self.source,
            "source_sha256": self.source_sha256,
            "pages": self.pages,
            "spec_version": self.spec_version,
            "batches": [dataclasses.asdict(b) for b in self.batches],
        }

    @classmethod
    def from_dict(cls, d: dict) -> Plan:
        return cls(
            slug=str(d["slug"]),
            source=str(d.get("source", "")),
            source_sha256=str(d.get("source_sha256", "")),
            pages=int(d["pages"]),
            spec_version=str(d.get("spec_version", SPEC_VERSION)),
            batches=[Batch(**b) for b in d.get("batches", [])],
        )

    def write(self, docpack_dir: Path) -> Path:
        out = docpack_dir / "plan.json"
        out.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return out

    @classmethod
    def read(cls, docpack_dir: Path) -> Plan:
        return cls.from_dict(json.loads((docpack_dir / "plan.json").read_text("utf-8")))
