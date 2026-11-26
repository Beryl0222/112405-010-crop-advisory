"""服务人员工作流：面向具体农户的通知发送、顶替与执行登记。

规则：
- 只有 ``approved`` 的建议可以发送；发送决定只能由服务人员作出；
- 新建议发出时，同田块"仍未执行"的旧通知一律置 ``superseded`` 并关联新建议，
  已执行（``executed``）的通知不动，其依据快照永久保留；
- 每条通知保存发送时的依据快照（观察、化验、标签限制原文），
  使已完成的田间操作始终可以回溯当时使用的依据。
"""

from __future__ import annotations

import datetime as _dt
import json

from .models import (
    Actor,
    NoticeStatus,
    NotFound,
    RecStatus,
    Role,
    WorkflowStateError,
)
from .repository import Repository, new_id
from .roles import require_role


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def issue_notice(repo: Repository, actor: Actor, rec_id: str) -> str:
    require_role(actor, Role.SERVICE)
    rec = repo.get("recommendations", rec_id)
    if rec is None:
        raise NotFound(f"建议不存在：{rec_id}")
    if rec["status"] != RecStatus.APPROVED.value:
        raise WorkflowStateError(
            f"建议 {rec_id} 状态为 {rec['status']}，仅批准后的建议可发送"
        )
    field = repo.get("fields", rec["field_id"])

    # 顶替仍未执行的旧通知；已执行通知原样保留
    superseded: list[str] = []
    for old in repo.open_notices_for_field(field["id"]):
        repo.execute(
            "UPDATE notices SET status = ? WHERE id = ?",
            (NoticeStatus.SUPERSEDED.value, old["id"]),
        )
        superseded.append(old["id"])

    notice_id = new_id("N")
    snapshot = build_basis_snapshot(repo, rec_id, superseded)
    repo.insert("notices", {
        "id": notice_id,
        "field_id": field["id"],
        "farmer_id": field["farmer_id"],
        "status": NoticeStatus.NOT_EXECUTED.value,
        "rec_id": rec_id,
        "basis_snapshot": json.dumps(snapshot, ensure_ascii=False),
        "sent_at": _now(),
        "executed_at": None,
        "source_id": None,
    })
    repo.commit()
    return notice_id


def mark_executed(repo: Repository, actor: Actor, notice_id: str) -> None:
    require_role(actor, Role.SERVICE)
    row = repo.get("notices", notice_id)
    if row is None:
        raise NotFound(f"通知不存在：{notice_id}")
    if row["status"] != NoticeStatus.NOT_EXECUTED.value:
        raise WorkflowStateError(
            f"通知 {notice_id} 状态为 {row['status']}，不能登记执行"
        )
    repo.execute(
        "UPDATE notices SET status = ?, executed_at = ? WHERE id = ?",
        (NoticeStatus.EXECUTED.value, _now(), notice_id),
    )
    repo.commit()


def withdraw_notice(repo: Repository, actor: Actor, notice_id: str) -> None:
    require_role(actor, Role.SERVICE)
    row = repo.get("notices", notice_id)
    if row is None:
        raise NotFound(f"通知不存在：{notice_id}")
    if row["status"] == NoticeStatus.EXECUTED.value:
        raise WorkflowStateError("已执行通知不可撤回，依据快照必须保留")
    repo.execute(
        "UPDATE notices SET status = ? WHERE id = ?",
        (NoticeStatus.WITHDRAWN.value, notice_id),
    )
    repo.commit()


def build_basis_snapshot(repo: Repository, rec_id: str,
                         superseded_notices: list[str]) -> dict:
    rec = repo.get("recommendations", rec_id)
    evidence_items = []
    for eid in repo.rec_evidence_ids(rec_id):
        ev = repo.get("evidence", eid)
        src = repo.get("sources", ev["source_id"])
        evidence_items.append({
            "evidence_id": eid,
            "source_id": ev["source_id"],
            "kind": ev["kind"],
            "is_hearsay": bool(ev["is_hearsay"]),
            "text": ev["text"],
            "original_text": src["text"] if src else None,
        })
    restrictions = []
    for cid in repo.rec_clause_ids(rec_id):
        clause = repo.get("label_clauses", cid)
        label = repo.get("labels", clause["label_id"])
        restrictions.append({
            "clause_id": cid,
            "label_id": clause["label_id"],
            "product_id": label["product_id"] if label else None,
            "type": clause["restriction_type"],
            "stage": clause["stage"],
            "text": clause["text"],
        })
    return {
        "rec_id": rec_id,
        "rec_type": rec["rec_type"],
        "field_id": rec["field_id"],
        "crop_id": rec["crop_id"],
        "product_id": rec["product_id"],
        "rationale": rec["rationale"],
        "evidence": evidence_items,
        "label_restrictions": restrictions,
        "superseded_notices": superseded_notices,
        "frozen_at": _now(),
    }
