"""资料摄取：合成参考数据与案件记录入库，并执行不可绕过的硬规则。

硬规则在摄取阶段即生效：
- 传闻证据（邻田/转发）以 ``usable=0`` 入库，任何后续流程无法引用；
- 化验阴性只把对应病害候选置为"已排除"，绝不自动确诊其他病害；
- 标签限制条款逐字保存，建议与通知只能引用，不能改写或省略；
- 已执行通知的依据以快照形式冻结，后续更新不会改动它。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .models import (
    CandidateStatus,
    EvidenceKind,
    NoticeStatus,
    RecStatus,
    RecType,
    RestrictionType,
)
from .parser import RecordParser
from .repository import Repository, new_id

_FIELD_ID = re.compile(r"T\d+")
_FARMER_ID = re.compile(r"F\d+")

DEFAULT_ACTORS = [
    ("U-STAFF", "植保员甲", "staff"),
    ("U-AGRO", "农艺师乙", "agronomist"),
    ("U-SVC", "服务员丙", "service"),
]


def load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


class IngestionService:
    def __init__(self, repo: Repository, reference: dict):
        self.repo = repo
        self.reference = reference
        self.parser = RecordParser(reference)

    # ---------- 参考数据 ----------
    def seed_reference(self) -> None:
        for crop in self.reference["crops"]:
            self.repo.insert("crops", {
                "id": crop["id"],
                "name": crop["name"],
                "stage_order": json.dumps(crop["stages"], ensure_ascii=False),
            })
            for alias in crop.get("aliases", []):
                self.repo.insert("crop_aliases",
                                 {"alias": alias, "crop_id": crop["id"]})
        for disease in self.reference["diseases"]:
            self.repo.insert("diseases", {
                "id": disease["id"],
                "name": disease["name"],
                "symptom_codes": ",".join(disease["symptom_codes"]),
            })
        for product in self.reference["products"]:
            self.repo.insert("products", {"id": product["id"], "name": product["name"]})
            for label in product["labels"]:
                for crop_id in label["crops"]:
                    self.repo.insert("labels", {
                        "id": label["id"],
                        "product_id": product["id"],
                        "crop_id": crop_id,
                        "source_id": None,
                    })
        for actor_id, name, role in DEFAULT_ACTORS:
            self.repo.insert("actors",
                             {"id": actor_id, "name": name, "role": role})
        self.repo.commit()

    def seed_fields(self, case: dict) -> None:
        for item in case.get("context", {}).get("fields", []):
            crop_id = self.repo.resolve_crop(item["crop"])
            self.repo.insert("fields", {
                "id": item["id"],
                "farmer_id": item["farmer"],
                "crop_id": crop_id,
            })
        self.repo.commit()

    # ---------- 记录摄取 ----------
    def ingest_case(self, case: dict) -> dict[str, list[str]]:
        summary: dict[str, list[str]] = {
            "sources": [], "evidence": [], "stage": [], "lab": [],
            "labels": [], "knowledge": [], "notices": [], "candidates": [],
        }
        default_field = case["context"]["fields"][0]["id"]
        for record in case["records"]:
            if not self.repo.source_exists(record["id"]):
                self.repo.insert("sources", {
                    "id": record["id"],
                    "text": record["text"],
                    "kind": record["kind"],
                })
                summary["sources"].append(record["id"])
            for entry in self.parser.parse(
                record["id"], record["kind"], record["text"], default_field
            ):
                self._store_entry(entry, record, summary)
        self._materialize_existing_notices(case, summary)
        self.repo.commit()
        return summary

    def _store_entry(self, entry, record: dict, summary: dict) -> None:
        if entry.kind is EvidenceKind.LABEL:
            self._store_label(entry, summary)
            return
        eid = self.repo.add_evidence(entry)
        summary["evidence"].append(eid)
        if entry.kind is EvidenceKind.STAGE:
            self._store_stage(entry, summary)
        elif entry.kind is EvidenceKind.LAB:
            self._store_lab(entry, summary)
        elif entry.kind is EvidenceKind.KNOWLEDGE:
            self._store_knowledge(entry, summary)
        elif entry.kind is EvidenceKind.NOTICE_RECORD:
            summary["notices"].append(eid)

    def _store_label(self, entry, summary: dict) -> None:
        payload = entry.payload
        label_id = payload.get("label_id")
        if not label_id:
            label_id = new_id("LABEL")
        if not self.repo.get("labels", label_id):
            self.repo.insert("labels", {
                "id": label_id,
                "product_id": payload["product_id"],
                "crop_id": entry.crop_id,
                "source_id": entry.source_id,
            })
        eid = self.repo.add_evidence(entry)
        summary["labels"].append(eid)
        # 同一作物下相同原文的限制条款只存一份，避免多条标签来源造成重复
        existing = {
            r["text"] for r in self.repo.query(
                "SELECT lc.text FROM label_clauses lc "
                "JOIN labels l ON l.id = lc.label_id WHERE l.crop_id = ?",
                (entry.crop_id,),
            )
        }
        for clause in payload["clauses"]:
            if clause["text"] in existing:
                continue
            existing.add(clause["text"])
            cid = new_id("CL")
            self.repo.insert("label_clauses", {
                "id": cid,
                "label_id": label_id,
                "restriction_type": clause["type"],
                "stage": clause["stage"],
                "text": clause["text"],
            })

    def _store_stage(self, entry, summary: dict) -> None:
        payload = entry.payload
        if entry.field_id:
            self._ensure_field(entry)
        if payload.get("stage"):
            self.repo.insert("stage_updates", {
                "id": new_id("ST"),
                "field_id": entry.field_id,
                "crop_id": entry.crop_id,
                "stage": payload["stage"],
                "is_correction": int(payload.get("is_correction", False)),
                "source_id": entry.source_id,
            })
            summary["stage"].append(f"{entry.field_id}:{payload['stage']}")

    def _ensure_field(self, entry) -> None:
        if self.repo.get("fields", entry.field_id):
            return
        farmers = _FARMER_ID.findall(entry.text)
        self.repo.insert("fields", {
            "id": entry.field_id,
            "farmer_id": farmers[0] if farmers else "F-UNK",
            "crop_id": entry.crop_id or self.reference["crops"][0]["id"],
        })

    def _store_lab(self, entry, summary: dict) -> None:
        payload = entry.payload
        if payload.get("note_only"):
            return
        result_id = new_id("LAB")
        self.repo.insert("lab_results", {
            "id": result_id,
            "field_id": entry.field_id,
            "disease_id": payload["disease_id"],
            "detected": int(payload["detected"]),
            "source_id": entry.source_id,
        })
        summary["lab"].append(
            f"{entry.field_id}:{payload['disease_id']}:"
            f"{'positive' if payload['detected'] else 'negative'}"
        )
        # 检验结果入库即固定事实；候选状态的排除/确诊由植保员工作流显式应用，
        # 且阴性只针对该病害本身，其他候选不受任何影响。

    def _store_knowledge(self, entry, summary: dict) -> None:
        payload = entry.payload
        self.repo.insert("knowledge", {
            "id": new_id("KN"),
            "disease_id": payload["disease_id"],
            "compatible_symptoms": ",".join(payload["compatible_symptoms"]),
            "required_symptoms": ",".join(payload["required_symptoms"]),
            "note": payload["note"],
            "source_id": entry.source_id,
        })

    # ---------- 既存通知 ----------
    def _materialize_existing_notices(self, case: dict, summary: dict) -> None:
        for notice in case.get("context", {}).get("notices", []):
            if self.repo.get("notices", notice["id"]):
                continue
            field = self.repo.get("fields", notice["field"])
            rec_id = f"REC-{notice['id']}"
            self.repo.insert("recommendations", {
                "id": rec_id,
                "field_id": notice["field"],
                "crop_id": field["crop_id"],
                "rec_type": RecType.PRODUCT_APPLICATION.value,
                "product_id": self._case_product_id(),
                "status": RecStatus.APPROVED.value,
                "version": 1,
                "supersedes_id": None,
                "rationale": f"历史通知依据（来源 {notice['source']}）",
                "created_by": "U-AGRO",
                "approved_by": "U-AGRO",
            })
            clause_ids = self._clause_ids_for(field["crop_id"])
            for cid in clause_ids:
                self.repo.insert("rec_labels", {"rec_id": rec_id, "clause_id": cid})
            snapshot = self._basis_snapshot(notice, rec_id, clause_ids)
            self.repo.insert("notices", {
                "id": notice["id"],
                "field_id": notice["field"],
                "farmer_id": field["farmer_id"],
                "status": notice["state"],
                "rec_id": rec_id,
                "basis_snapshot": json.dumps(snapshot, ensure_ascii=False),
                "sent_at": None,
                "executed_at": "历史已执行" if notice["state"] == "executed" else None,
                "source_id": notice["source"],
            })
            summary["notices"].append(notice["id"])

    def _case_product_id(self) -> str:
        return self.reference["products"][0]["id"]

    def _clause_ids_for(self, crop_id: str) -> list[str]:
        rows = self.repo.query(
            "SELECT lc.id FROM label_clauses lc "
            "JOIN labels l ON l.id = lc.label_id WHERE l.crop_id = ? "
            "ORDER BY lc.rowid",
            (crop_id,),
        )
        return [r["id"] for r in rows]

    def _basis_snapshot(self, notice: dict, rec_id: str,
                        clause_ids: list[str]) -> dict:
        source = self.repo.get("sources", notice["source"])
        restrictions = []
        for cid in clause_ids:
            row = self.repo.get("label_clauses", cid)
            restrictions.append({
                "clause_id": cid,
                "type": row["restriction_type"],
                "stage": row["stage"],
                "text": row["text"],
            })
        return {
            "notice_id": notice["id"],
            "rec_id": rec_id,
            "source_id": notice["source"],
            "source_text": source["text"] if source else None,
            "label_restrictions": restrictions,
            "frozen": True,
        }
