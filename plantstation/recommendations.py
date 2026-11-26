"""农艺师工作流：防治建议的起草、核对、批准与版本更替。

核对项（缺一不可）：
1. 建议作物与田块实际作物一致；标签放宽只对标签载明的作物生效；
2. 施用时点不超过标签"仅适用于……前"的窗口，且不落入禁用生育期
   （如"开花期不得施用"，该限制逐字保留，不能被摘要省略）；
3. 必须有同田块、非传闻的可用证据，且至少存在一个未被排除/未判不符的候选；
4. 所有标签条款与依据证据必须与建议关联，发送内容可完整溯源。

建议版本化：更新产生新版本，旧版本置 ``superseded``；由服务层据此顶替
仍未执行的通知，已执行通知保留冻结快照。
"""

from __future__ import annotations

from .models import (
    Actor,
    CandidateStatus,
    HearsayNotEvidence,
    LabelRestrictionViolation,
    NotFound,
    RecStatus,
    RecType,
    RestrictionType,
    Role,
    ValidationReport,
    WorkflowStateError,
)
from .repository import Repository, new_id
from .roles import require_role

_VIABLE = {
    CandidateStatus.CANDIDATE.value,
    CandidateStatus.MATCHED.value,
    CandidateStatus.CONFIRMED.value,
}


def _field(repo: Repository, field_id: str):
    row = repo.get("fields", field_id)
    if row is None:
        raise NotFound(f"田块不存在：{field_id}")
    return row


def _check_evidence(repo: Repository, field_id: str, evidence_ids: list[str]) -> None:
    for eid in evidence_ids:
        row = repo.get("evidence", eid)
        if row is None:
            raise NotFound(f"证据不存在：{eid}")
        if row["is_hearsay"]:
            raise HearsayNotEvidence(
                f"证据 {eid} 是邻田/转发经验，不能作为施药建议依据"
            )
        if not row["usable"]:
            raise LabelRestrictionViolation(
                f"证据 {eid} 已标注为不可用，不能支撑建议"
            )
        if row["field_id"] != field_id:
            raise LabelRestrictionViolation(
                f"证据 {eid} 属于 {row['field_id']}，不能用于田块 {field_id}"
            )


def _clauses_for_crop(repo: Repository, product_id: str,
                      crop_id: str) -> list:
    return repo.query(
        "SELECT lc.* FROM label_clauses lc "
        "JOIN labels l ON l.id = lc.label_id "
        "WHERE l.product_id = ? AND l.crop_id = ? ORDER BY lc.rowid",
        (product_id, crop_id),
    )


def validate_product_application(repo: Repository, field_id: str,
                                 product_id: str,
                                 evidence_ids: list[str]) -> ValidationReport:
    """独立校验，不修改数据；批准流程内部也会再次调用。"""
    field = _field(repo, field_id)
    errors: list[str] = []
    restriction_texts: list[str] = []

    _check_evidence(repo, field_id, evidence_ids)

    candidates = repo.candidates_for_field(field_id)
    viable = [c for c in candidates if c["status"] in _VIABLE]
    if not viable:
        errors.append("该田块没有未被排除且未判不符的病害候选，施药缺乏对应病害依据")

    clauses = _clauses_for_crop(repo, product_id, field["crop_id"])
    if not clauses:
        errors.append(
            f"产品 {product_id} 没有适合作物 {field['crop_id']} 的标签条款，禁止跨作物套用"
        )
        return ValidationReport("", False, errors, restriction_texts)

    current_stage = repo.current_stage(field_id)
    stage_order = repo.crop_stage_order(field["crop_id"])
    for clause in clauses:
        restriction_texts.append(clause["text"])
        ctype = RestrictionType(clause["restriction_type"])
        if ctype is RestrictionType.FORBIDDEN_STAGE and clause["stage"]:
            if current_stage == clause["stage"]:
                errors.append(
                    f"当前生育期 {current_stage} 命中标签禁用限制：{clause['text']}"
                )
        elif ctype is RestrictionType.ALLOWED_BEFORE and clause["stage"]:
            if current_stage and current_stage in stage_order:
                if stage_order.index(current_stage) >= stage_order.index(clause["stage"]):
                    errors.append(
                        f"当前生育期 {current_stage} 已超出标签施用窗口：{clause['text']}"
                    )
        # relaxed / scope_note 为范围说明：逐字保留并随建议展示，不放宽其他限制

    # 关联其他作物标签的"放宽"条款不能作用于本作物
    foreign = repo.query(
        "SELECT lc.* FROM label_clauses lc JOIN labels l ON l.id = lc.label_id "
        "WHERE l.product_id = ? AND l.crop_id != ?",
        (product_id, field["crop_id"]),
    )
    for clause in foreign:
        if clause["restriction_type"] == RestrictionType.RELAXED.value:
            restriction_texts.append(
                f"{clause['text']}（仅适用其他作物，对 {field['crop_id']} 不生效）"
            )

    return ValidationReport("", not errors, errors, restriction_texts)


def draft_product_application(repo: Repository, actor: Actor, field_id: str,
                              product_id: str, evidence_ids: list[str],
                              rationale: str) -> str:
    require_role(actor, Role.AGRONOMIST)
    field = _field(repo, field_id)
    report = validate_product_application(repo, field_id, product_id, evidence_ids)
    rec_id = new_id("REC")
    repo.insert("recommendations", {
        "id": rec_id,
        "field_id": field_id,
        "crop_id": field["crop_id"],
        "rec_type": RecType.PRODUCT_APPLICATION.value,
        "product_id": product_id,
        "status": RecStatus.DRAFT.value,
        "version": 1,
        "supersedes_id": None,
        "rationale": rationale,
        "created_by": actor.id,
        "approved_by": None,
    })
    for eid in evidence_ids:
        repo.insert("rec_evidence", {"rec_id": rec_id, "evidence_id": eid})
    for clause in _clauses_for_crop(repo, product_id, field["crop_id"]):
        repo.insert("rec_labels", {"rec_id": rec_id, "clause_id": clause["id"]})
    repo.commit()
    if not report.ok:
        # 草稿允许留存，但报告决定它不能被批准
        return rec_id
    return rec_id


def draft_monitor_only(repo: Repository, actor: Actor, field_id: str,
                       evidence_ids: list[str], rationale: str) -> str:
    """暂不施药、继续观察类建议；仍须绑定可用证据以便溯源。"""
    require_role(actor, Role.AGRONOMIST)
    field = _field(repo, field_id)
    _check_evidence(repo, field_id, evidence_ids)
    rec_id = new_id("REC")
    repo.insert("recommendations", {
        "id": rec_id,
        "field_id": field_id,
        "crop_id": field["crop_id"],
        "rec_type": RecType.MONITOR_ONLY.value,
        "product_id": None,
        "status": RecStatus.DRAFT.value,
        "version": 1,
        "supersedes_id": None,
        "rationale": rationale,
        "created_by": actor.id,
        "approved_by": None,
    })
    for eid in evidence_ids:
        repo.insert("rec_evidence", {"rec_id": rec_id, "evidence_id": eid})
    repo.commit()
    return rec_id


def approve(repo: Repository, actor: Actor, rec_id: str) -> ValidationReport:
    require_role(actor, Role.AGRONOMIST)
    row = repo.get("recommendations", rec_id)
    if row is None:
        raise NotFound(f"建议不存在：{rec_id}")
    if row["status"] != RecStatus.DRAFT.value:
        raise WorkflowStateError(f"建议 {rec_id} 状态为 {row['status']}，不能批准")

    evidence_ids = repo.rec_evidence_ids(rec_id)
    if row["rec_type"] == RecType.PRODUCT_APPLICATION.value:
        report = validate_product_application(
            repo, row["field_id"], row["product_id"], evidence_ids
        )
        report.rec_id = rec_id
        if not report.ok:
            repo.execute(
                "UPDATE recommendations SET status = ? WHERE id = ?",
                (RecStatus.REJECTED.value, rec_id),
            )
            repo.commit()
            return report
    else:
        _check_evidence(repo, row["field_id"], evidence_ids)
        report = ValidationReport(rec_id, True, [], [])

    repo.execute(
        "UPDATE recommendations SET status = ?, approved_by = ? WHERE id = ?",
        (RecStatus.APPROVED.value, actor.id, rec_id),
    )
    repo.commit()
    return report


def revise(repo: Repository, actor: Actor, old_rec_id: str,
           rec_type: RecType, product_id: str | None,
           evidence_ids: list[str], rationale: str) -> str:
    """依据新资料发布新版本；旧版本标记 superseded 并保留全部关联。"""
    require_role(actor, Role.AGRONOMIST)
    old = repo.get("recommendations", old_rec_id)
    if old is None:
        raise NotFound(f"旧建议不存在：{old_rec_id}")
    if old["status"] not in (RecStatus.APPROVED.value, RecStatus.SUPERSEDED.value):
        raise WorkflowStateError(
            f"旧建议状态 {old['status']} 不允许作为修订基线"
        )
    version = int(old["version"]) + 1
    new_id_ = new_id("REC")
    repo.insert("recommendations", {
        "id": new_id_,
        "field_id": old["field_id"],
        "crop_id": old["crop_id"],
        "rec_type": rec_type.value,
        "product_id": product_id,
        "status": RecStatus.DRAFT.value,
        "version": version,
        "supersedes_id": old_rec_id,
        "rationale": rationale,
        "created_by": actor.id,
        "approved_by": None,
    })
    for eid in evidence_ids:
        repo.insert("rec_evidence", {"rec_id": new_id_, "evidence_id": eid})
    if product_id:
        for clause in _clauses_for_crop(repo, product_id, old["crop_id"]):
            repo.insert("rec_labels", {"rec_id": new_id_, "clause_id": clause["id"]})
    repo.execute(
        "UPDATE recommendations SET status = ? WHERE id = ?",
        (RecStatus.SUPERSEDED.value, old_rec_id),
    )
    repo.commit()
    return new_id_


def withdraw_draft(repo: Repository, actor: Actor, rec_id: str) -> None:
    require_role(actor, Role.AGRONOMIST)
    row = repo.get("recommendations", rec_id)
    if row is None:
        raise NotFound(f"建议不存在：{rec_id}")
    if row["status"] != RecStatus.DRAFT.value:
        raise WorkflowStateError("只有草稿可以撤回")
    repo.execute(
        "UPDATE recommendations SET status = ? WHERE id = ?",
        (RecStatus.WITHDRAWN.value, rec_id),
    )
    repo.commit()
