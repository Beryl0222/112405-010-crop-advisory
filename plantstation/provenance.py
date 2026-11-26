"""溯源：让使用者看到建议/通知究竟来自哪些观察、化验与标签。

输出包含：
- 每条依据证据的来源编号与原句（含传闻条目及"不可用"原因）；
- 标签限制的逐字文本，确保"花期禁用"类限制不会在摘要中消失；
- 候选病害的状态来源（观察判断或检验排除/确诊）；
- 通知的冻结依据快照（已执行操作保留当时依据）。
"""

from __future__ import annotations

import json

from .models import CandidateStatus
from .repository import Repository


def explain_recommendation(repo: Repository, rec_id: str) -> dict:
    rec = repo.get("recommendations", rec_id)
    if rec is None:
        raise ValueError(f"建议不存在：{rec_id}")
    field = repo.get("fields", rec["field_id"])
    product = repo.get("products", rec["product_id"]) if rec["product_id"] else None

    evidence = []
    for eid in repo.rec_evidence_ids(rec_id):
        evidence.append(_evidence_detail(repo, eid))

    restrictions = []
    for cid in repo.rec_clause_ids(rec_id):
        clause = repo.get("label_clauses", cid)
        label = repo.get("labels", clause["label_id"])
        restrictions.append({
            "clause_id": cid,
            "label_id": clause["label_id"],
            "product": repo.get("products", label["product_id"])["name"] if label else None,
            "type": clause["restriction_type"],
            "stage": clause["stage"],
            "text": clause["text"],
        })

    candidates = []
    for cand in repo.candidates_for_field(rec["field_id"]):
        disease = repo.get("diseases", cand["disease_id"])
        basis = [_evidence_detail(repo, eid)
                 for eid in repo.candidate_evidence_ids(cand["id"])]
        candidates.append({
            "candidate_id": cand["id"],
            "disease_id": cand["disease_id"],
            "disease_name": disease["name"] if disease else cand["disease_id"],
            "status": cand["status"],
            "rationale": cand["rationale"],
            "evidence": basis,
        })

    hearsay = [_evidence_detail(repo, ev["id"])
               for ev in repo.hearsay_evidence_for_field(rec["field_id"])]

    return {
        "rec_id": rec_id,
        "version": rec["version"],
        "status": rec["status"],
        "rec_type": rec["rec_type"],
        "supersedes": rec["supersedes_id"],
        "field": {"id": field["id"], "farmer_id": field["farmer_id"],
                  "crop_id": field["crop_id"]},
        "current_stage": repo.current_stage(field["id"]),
        "product": product["name"] if product else None,
        "rationale": rec["rationale"],
        "evidence": evidence,
        "label_restrictions": restrictions,
        "candidates": candidates,
        "excluded_hearsay": hearsay,
    }


def explain_notice(repo: Repository, notice_id: str) -> dict:
    notice = repo.get("notices", notice_id)
    if notice is None:
        raise ValueError(f"通知不存在：{notice_id}")
    snapshot = json.loads(notice["basis_snapshot"]) if notice["basis_snapshot"] else None
    return {
        "notice_id": notice_id,
        "field_id": notice["field_id"],
        "farmer_id": notice["farmer_id"],
        "status": notice["status"],
        "rec_id": notice["rec_id"],
        "sent_at": notice["sent_at"],
        "executed_at": notice["executed_at"],
        "basis_snapshot": snapshot,
    }


def field_dossier(repo: Repository, field_id: str) -> dict:
    stage_rows = repo.query(
        "SELECT * FROM stage_updates WHERE field_id = ? ORDER BY rowid", (field_id,)
    )
    lab_rows = repo.query(
        "SELECT * FROM lab_results WHERE field_id = ? ORDER BY rowid", (field_id,)
    )
    return {
        "field_id": field_id,
        "current_stage": repo.current_stage(field_id),
        "stage_history": [
            {"stage": r["stage"], "is_correction": bool(r["is_correction"]),
             "source_id": r["source_id"]}
            for r in stage_rows
        ],
        "usable_evidence": [
            _evidence_detail(repo, r["id"])
            for r in repo.usable_evidence_for_field(field_id)
        ],
        "hearsay": [
            _evidence_detail(repo, r["id"])
            for r in repo.hearsay_evidence_for_field(field_id)
        ],
        "lab_results": [
            {"disease_id": r["disease_id"], "detected": bool(r["detected"]),
             "source_id": r["source_id"]}
            for r in lab_rows
        ],
        "candidates": [
            {
                "id": r["id"], "disease_id": r["disease_id"],
                "status": r["status"], "rationale": r["rationale"],
            }
            for r in repo.candidates_for_field(field_id)
        ],
        "notices": [
            explain_notice(repo, r["id"])
            for r in repo.query(
                "SELECT id FROM notices WHERE field_id = ? ORDER BY rowid",
                (field_id,),
            )
        ],
    }


def _evidence_detail(repo: Repository, evidence_id: str) -> dict:
    ev = repo.get("evidence", evidence_id)
    src = repo.get("sources", ev["source_id"])
    return {
        "evidence_id": evidence_id,
        "source_id": ev["source_id"],
        "kind": ev["kind"],
        "is_hearsay": bool(ev["is_hearsay"]),
        "usable": bool(ev["usable"]),
        "symptom_codes": [c for c in ev["symptom_codes"].split(",") if c],
        "text": ev["text"],
        "original_text": src["text"] if src else None,
    }
