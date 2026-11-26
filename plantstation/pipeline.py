"""端到端演练编排：把合成案件跑通三角色完整链路。

该模块只做编排，所有规则仍由各服务模块强制；任何一步违反硬规则都会抛错。
"""

from __future__ import annotations

from .ingestion import IngestionService, load_json
from .models import CandidateStatus, RecStatus, RecType, Role
from .repository import Repository
from .candidates import apply_lab_result, judge_candidate, propose_candidate
from .notices import issue_notice, mark_executed
from .provenance import explain_notice, explain_recommendation, field_dossier
from .recommendations import (
    approve,
    draft_monitor_only,
    draft_product_application,
    revise,
    validate_product_application,
)
from .roles import load_actor


def field_observation_ids(repo: Repository, field_id: str) -> list[str]:
    return [r["id"] for r in repo.usable_evidence_for_field(field_id)
            if r["kind"] == "observation"]


def run_demo(repo: Repository, reference_path: str, case_path: str) -> dict:
    reference = load_json(reference_path)
    case = load_json(case_path)

    ingestion = IngestionService(repo, reference)
    ingestion.seed_reference()
    ingestion.seed_fields(case)
    summary = ingestion.ingest_case(case)

    staff = load_actor(repo, "U-STAFF")
    agronomist = load_actor(repo, "U-AGRO")
    service = load_actor(repo, "U-SVC")

    field_id = "T1"
    obs_ids = field_observation_ids(repo, field_id)

    # 1. 植保人员登记候选并做符合性判断（不确诊）
    cand_d1 = propose_candidate(
        repo, staff, field_id, "D1", obs_ids, "叶背白点与 D1 相容，先登记候选"
    )
    cand_d2 = propose_candidate(
        repo, staff, field_id, "D2", obs_ids, "D2 需要明显扩散，登记待判"
    )
    d1_status = judge_candidate(repo, staff, cand_d1, True)
    d2_status = judge_candidate(repo, staff, cand_d2, False)

    # 2. 化验结果：D1 阴性 -> 排除；D2 不被自动确诊
    lab_status = apply_lab_result(repo, staff, field_id, "D1", False, "CP-23")

    # 3. 农艺师试拟花期施药建议 -> 标签限制必然拦截
    blocked = draft_product_application(
        repo, agronomist, field_id, "PRD-QN", obs_ids,
        "农户询问是否按 N1 继续施用青宁"
    )
    blocked_report = validate_product_application(
        repo, field_id, "PRD-QN", obs_ids
    )
    approve(repo, agronomist, blocked)  # 预期转为 rejected

    # 4. 改为"暂不施药、继续观察"建议并批准
    monitor = draft_monitor_only(
        repo, agronomist, field_id, obs_ids,
        "D1 已被检验排除且花期禁用；D2 未核实，继续观察西侧与扩散情况"
    )
    approve(repo, agronomist, monitor)

    # 5. 服务人员发送：顶替仍未执行的 N1；N2 已执行，快照保留
    new_notice = issue_notice(repo, service, monitor)

    return {
        "ingestion": summary,
        "candidates": {
            "D1": cand_d1,
            "D2": cand_d2,
            "D1_final": lab_status.value,
            "D2_final": d2_status.value,
            "D1_judged": d1_status.value,
        },
        "blocked_application": {
            "rec_id": blocked,
            "approved": blocked_report.ok,
            "errors": blocked_report.errors,
            "restrictions": blocked_report.restriction_texts,
        },
        "monitor_rec": monitor,
        "new_notice": new_notice,
        "t1_dossier": field_dossier(repo, field_id),
        "monitor_explanation": explain_recommendation(repo, monitor),
        "n1_after": explain_notice(repo, "N1"),
        "n2_after": explain_notice(repo, "N2"),
    }
