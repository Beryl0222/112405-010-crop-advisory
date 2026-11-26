"""SQLite 连接与建表。仅使用标准库。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    kind TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS crops (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    stage_order TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS crop_aliases (
    alias TEXT PRIMARY KEY,
    crop_id TEXT NOT NULL REFERENCES crops(id)
);

CREATE TABLE IF NOT EXISTS fields (
    id TEXT PRIMARY KEY,
    farmer_id TEXT NOT NULL,
    crop_id TEXT NOT NULL REFERENCES crops(id)
);

CREATE TABLE IF NOT EXISTS diseases (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    symptom_codes TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS labels (
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES products(id),
    crop_id TEXT NOT NULL REFERENCES crops(id),
    source_id TEXT REFERENCES sources(id)
);
CREATE TABLE IF NOT EXISTS label_clauses (
    id TEXT PRIMARY KEY,
    label_id TEXT NOT NULL REFERENCES labels(id),
    restriction_type TEXT NOT NULL,
    stage TEXT,
    text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS actors (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id),
    field_id TEXT REFERENCES fields(id),
    crop_id TEXT REFERENCES crops(id),
    kind TEXT NOT NULL,
    is_hearsay INTEGER NOT NULL DEFAULT 0,
    usable INTEGER NOT NULL DEFAULT 1,
    symptom_codes TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS stage_updates (
    id TEXT PRIMARY KEY,
    field_id TEXT NOT NULL REFERENCES fields(id),
    crop_id TEXT REFERENCES crops(id),
    stage TEXT NOT NULL,
    is_correction INTEGER NOT NULL DEFAULT 0,
    source_id TEXT NOT NULL REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS lab_results (
    id TEXT PRIMARY KEY,
    field_id TEXT NOT NULL REFERENCES fields(id),
    disease_id TEXT NOT NULL REFERENCES diseases(id),
    detected INTEGER NOT NULL,
    source_id TEXT NOT NULL REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS knowledge (
    id TEXT PRIMARY KEY,
    disease_id TEXT REFERENCES diseases(id),
    compatible_symptoms TEXT NOT NULL DEFAULT '',
    required_symptoms TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY,
    field_id TEXT NOT NULL REFERENCES fields(id),
    disease_id TEXT NOT NULL REFERENCES diseases(id),
    status TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    created_source_id TEXT REFERENCES sources(id)
);
CREATE TABLE IF NOT EXISTS candidate_evidence (
    candidate_id TEXT NOT NULL REFERENCES candidates(id),
    evidence_id TEXT NOT NULL REFERENCES evidence(id),
    PRIMARY KEY (candidate_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS recommendations (
    id TEXT PRIMARY KEY,
    field_id TEXT NOT NULL REFERENCES fields(id),
    crop_id TEXT NOT NULL REFERENCES crops(id),
    rec_type TEXT NOT NULL,
    product_id TEXT REFERENCES products(id),
    status TEXT NOT NULL,
    version INTEGER NOT NULL,
    supersedes_id TEXT REFERENCES recommendations(id),
    rationale TEXT NOT NULL,
    created_by TEXT NOT NULL REFERENCES actors(id),
    approved_by TEXT REFERENCES actors(id)
);
CREATE TABLE IF NOT EXISTS rec_evidence (
    rec_id TEXT NOT NULL REFERENCES recommendations(id),
    evidence_id TEXT NOT NULL REFERENCES evidence(id),
    PRIMARY KEY (rec_id, evidence_id)
);
CREATE TABLE IF NOT EXISTS rec_labels (
    rec_id TEXT NOT NULL REFERENCES recommendations(id),
    clause_id TEXT NOT NULL REFERENCES label_clauses(id),
    PRIMARY KEY (rec_id, clause_id)
);

CREATE TABLE IF NOT EXISTS notices (
    id TEXT PRIMARY KEY,
    field_id TEXT NOT NULL REFERENCES fields(id),
    farmer_id TEXT NOT NULL,
    status TEXT NOT NULL,
    rec_id TEXT REFERENCES recommendations(id),
    basis_snapshot TEXT,
    sent_at TEXT,
    executed_at TEXT,
    source_id TEXT REFERENCES sources(id)
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    if path.parent and str(path.parent) not in ("", "."):
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
