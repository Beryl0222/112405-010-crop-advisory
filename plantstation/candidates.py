"""植保人员工作流：观察现象与病害候选的符合性判断。

边界（硬规则）：
- 只能引用同田块、可用、非传闻的现场观察；邻田经验直接拒绝；
- "相符" 仅表示现象与候选相容（matched），不等于确诊；
- 确诊（confirmed）唯一来源是阳性检验结果；阴性检验只能排除（ruled_out）；
- 新检验排除一种病害时，其他候选状态保持不变。
"""

from __future__ import annotations

from .models import (
    Actor,
    CandidateStatus,
    HearsayNotEvidence,
    IncompatibleEvidence,
    NotFound,
    Role,
    WorkflowStateError,
)
from .repository import Repository, new_id
from .roles import require_role


def _check_evidence(repo: Repository, field_id: str, evidence_ids: list[str]):
    rows = []
    for eid in evidence_ids:
        row = repo.get("evidence", eid)
        if row is None:
            raise NotFound(f"证据不存在：{eid}")
        if row["is_hearsay"]:
            raise HearsayNotEvidence(
                f"证据 {eid} 来自邻田/转发传闻，不能作为本田病害判断依据"
            )
        if not row["usable"] or row["kind"] != "observation":
            raise IncompatibleEvidence(
                f"证据 {eid} 不是可用的现场观察（{row['kind']}）"
            )
        if row["field_id"] != field_id:
            raise IncompatibleEvidence(
                f"证据 {eid} 属于田块 {row['field_id']}，不能用于 {field_id}"
            )
        rows.append(row)
    return rows


def _knowledge_for(repo: Repository, disease_id: str):
    return repo.query(
        "SELECT * FROM knowledge WHERE disease_id = ? ORDER BY rowid",
        (disease_id,),
    )


def _compatible(repo: Repository, disease_id: str,
                symptom_codes: set[str]) -> tuple[bool, str]:
    knowledge = _knowledge_for(repo, disease_id)
    if not knowledge:
        return bool(symptom_codes), "无知识条可比对，仅依据现象保留候选"
    for kn in knowledge:
        compatible = set(filter(None, kn["compatible_symptoms"].split(",")))
        required = set(filter(None, kn["required_symptoms"].split(",")))
        if required and not required.issubset(symptom_codes):
            return False, f"缺少必要症状 {sorted(required)}（知识来源 {kn['source_id']}）"
        if compatible and not (compatible & symptom_codes):
            return False, f"现象与相容症状 {sorted(compatible)} 无交集（来源 {kn['source_id']}）"
    return True, "现象与候选相容，仅作符合性判断，不作确诊"


def propose_candidate(repo: Repository, actor: Actor, field_id: str,
                      disease_id: str, evidence_ids: list[str],
                      rationale: str = "") -> str:
    """登记病害候选。已存在则返回既有候选，不重复建单。"""
    require_role(actor, Role.STAFF)
    existing = repo.find_candidate(field_id, disease_id)
    if existing is not None:
        return existing["id"]
    _check_evidence(repo, field_id, evidence_ids)
    cid = new_id("CAND")
    repo.insert("candidates", {
        "id": cid,
        "field_id": field_id,
        "disease_id": disease_id,
        "status": CandidateStatus.CANDIDATE.value,
        "rationale": rationale or f"依据 {len(evidence_ids)} 条本田观察登记候选",
        "created_source_id": None,
    })
    for eid in evidence_ids:
        repo.insert("candidate_evidence",
                    {"candidate_id": cid, "evidence_id": eid})
    repo.commit()
    return cid


def judge_candidate(repo: Repository, actor: Actor, candidate_id: str,
                    matched: bool, rationale: str = "") -> CandidateStatus:
    """植保人员给出"相符/不符"判断。相符仍是 matched，不允许越权确诊。"""
    require_role(actor, Role.STAFF)
    row = repo.get("candidates", candidate_id)
    if row is None:
        raise NotFound(f"候选不存在：{candidate_id}")
    if row["status"] == CandidateStatus.RULED_OUT.value:
        raise WorkflowStateError(
            f"候选 {candidate_id} 已被检验排除，不能再做相符判断"
        )
    if row["status"] == CandidateStatus.CONFIRMED.value:
        raise WorkflowStateError(f"候选 {candidate_id} 已由阳性检验确诊")

    if matched:
        evidence_ids = repo.candidate_evidence_ids(candidate_id)
        rows = _check_evidence(repo, row["field_id"], evidence_ids)
        symptoms: set[str] = set()
        for ev in rows:
            symptoms.update(filter(None, ev["symptom_codes"].split(",")))
        ok, reason = _compatible(repo, row["disease_id"], symptoms)
        if not ok:
            raise IncompatibleEvidence(
                f"不能判为相符：{reason}"
            )
        status = CandidateStatus.MATCHED
        rationale = rationale or reason
    else:
        status = CandidateStatus.MISMATCHED
        rationale = rationale or "植保人员判断现象与候选不符，保留记录但不升级"

    repo.execute(
        "UPDATE candidates SET status = ?, rationale = ? WHERE id = ?",
        (status.value, rationale, candidate_id),
    )
    repo.commit()
    return status


def apply_lab_result(repo: Repository, actor: Actor, field_id: str,
                     disease_id: str, detected: bool, source_id: str) -> CandidateStatus:
    """检验结果应用：阴性→排除；阳性→确诊（确诊的唯一通道）。"""
    require_role(actor, Role.STAFF)
    row = repo.find_candidate(field_id, disease_id)
    if detected:
        status = CandidateStatus.CONFIRMED
        rationale = f"检验阳性确诊（来源 {source_id}）"
    else:
        status = CandidateStatus.RULED_OUT
        rationale = f"检验阴性排除，不影响其他候选（来源 {source_id}）"
    if row is None:
        cid = new_id("CAND")
        repo.insert("candidates", {
            "id": cid, "field_id": field_id, "disease_id": disease_id,
            "status": status.value, "rationale": rationale,
            "created_source_id": source_id,
        })
        repo.commit()
        return status
    repo.execute(
        "UPDATE candidates SET status = ?, rationale = ? WHERE id = ?",
        (status.value, rationale, row["id"]),
    )
    repo.commit()
    return status
