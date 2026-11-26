"""仓储层：封装 SQLite 读写，服务层只通过本模块访问数据。"""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from .models import NoticeStatus


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class Repository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---- 通用 ----
    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def commit(self) -> None:
        self.conn.commit()

    def insert(self, table: str, row: dict[str, Any]) -> None:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        self.conn.execute(
            f"INSERT INTO {table} ({cols}) VALUES ({marks})",
            tuple(row.values()),
        )

    def get(self, table: str, row_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            f"SELECT * FROM {table} WHERE id = ?", (row_id,)
        ).fetchone()

    def list_all(self, table: str) -> list[sqlite3.Row]:
        return list(self.conn.execute(f"SELECT * FROM {table}").fetchall())

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    # ---- 参考数据 ----
    def resolve_crop(self, raw: str | None) -> str | None:
        if not raw:
            return None
        row = self.query_one("SELECT id FROM crops WHERE id = ?", (raw,))
        if row:
            return row["id"]
        row = self.query_one("SELECT crop_id FROM crop_aliases WHERE alias = ?", (raw,))
        return row["crop_id"] if row else None

    def crop_stage_order(self, crop_id: str) -> list[str]:
        row = self.get("crops", crop_id)
        if not row:
            return []
        return json.loads(row["stage_order"])

    # ---- 来源与证据 ----
    def source_exists(self, source_id: str) -> bool:
        return self.get("sources", source_id) is not None

    def add_evidence(self, entry) -> str:
        eid = new_id("EV")
        self.insert(
            "evidence",
            {
                "id": eid,
                "source_id": entry.source_id,
                "field_id": entry.field_id,
                "crop_id": entry.crop_id,
                "kind": entry.kind.value,
                "is_hearsay": int(entry.is_hearsay),
                "usable": int(entry.usable),
                "symptom_codes": ",".join(entry.symptom_codes),
                "text": entry.text,
                "payload": _json_dumps(entry.payload),
            },
        )
        return eid

    def usable_evidence_for_field(self, field_id: str) -> list[sqlite3.Row]:
        return self.query(
            "SELECT * FROM evidence WHERE field_id = ? AND usable = 1 "
            "AND is_hearsay = 0 ORDER BY rowid",
            (field_id,),
        )

    def hearsay_evidence_for_field(self, field_id: str) -> list[sqlite3.Row]:
        return self.query(
            "SELECT * FROM evidence WHERE field_id = ? AND is_hearsay = 1 ORDER BY rowid",
            (field_id,),
        )

    # ---- 生育期 ----
    def current_stage(self, field_id: str) -> str | None:
        correction = self.query_one(
            "SELECT stage FROM stage_updates WHERE field_id = ? AND is_correction = 1 "
            "ORDER BY rowid DESC LIMIT 1",
            (field_id,),
        )
        if correction:
            return correction["stage"]
        row = self.query_one(
            "SELECT stage FROM stage_updates WHERE field_id = ? ORDER BY rowid DESC LIMIT 1",
            (field_id,),
        )
        return row["stage"] if row else None

    # ---- 候选 ----
    def find_candidate(self, field_id: str, disease_id: str) -> sqlite3.Row | None:
        return self.query_one(
            "SELECT * FROM candidates WHERE field_id = ? AND disease_id = ?",
            (field_id, disease_id),
        )

    def candidates_for_field(self, field_id: str) -> list[sqlite3.Row]:
        return self.query(
            "SELECT * FROM candidates WHERE field_id = ? ORDER BY rowid", (field_id,)
        )

    def candidate_evidence_ids(self, candidate_id: str) -> list[str]:
        rows = self.query(
            "SELECT evidence_id FROM candidate_evidence WHERE candidate_id = ?",
            (candidate_id,),
        )
        return [r["evidence_id"] for r in rows]

    # ---- 建议 ----
    def rec_evidence_ids(self, rec_id: str) -> list[str]:
        rows = self.query(
            "SELECT evidence_id FROM rec_evidence WHERE rec_id = ?", (rec_id,)
        )
        return [r["evidence_id"] for r in rows]

    def rec_clause_ids(self, rec_id: str) -> list[str]:
        rows = self.query(
            "SELECT clause_id FROM rec_labels WHERE rec_id = ?", (rec_id,)
        )
        return [r["clause_id"] for r in rows]

    # ---- 通知 ----
    def open_notices_for_field(self, field_id: str) -> list[sqlite3.Row]:
        return self.query(
            "SELECT * FROM notices WHERE field_id = ? AND status = ? ORDER BY rowid",
            (field_id, NoticeStatus.NOT_EXECUTED.value),
        )
