import json
import tempfile
import unittest
from pathlib import Path

from plantstation.candidates import (
    apply_lab_result,
    judge_candidate,
    propose_candidate,
)
from plantstation.database import connect, init_db
from plantstation.ingestion import IngestionService, load_json
from plantstation.models import (
    CandidateStatus,
    HearsayNotEvidence,
    IncompatibleEvidence,
    NoticeStatus,
    PermissionDenied,
    RecStatus,
    RecType,
    RestrictionType,
)
from plantstation.notices import issue_notice, mark_executed
from plantstation.pipeline import field_observation_ids, run_demo
from plantstation.provenance import explain_notice, explain_recommendation
from plantstation.recommendations import (
    approve,
    draft_monitor_only,
    draft_product_application,
    revise,
    validate_product_application,
)
from plantstation.repository import Repository
from plantstation.roles import load_actor


def build_db(tmpdir: str) -> Repository:
    conn = connect(Path(tmpdir) / "case.db")
    init_db(conn)
    return Repository(conn)


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = build_db(self.tmp.name)
        reference = load_json("data/reference.json")
        case = load_json("data/case.json")
        self.ingestion = IngestionService(self.repo, reference)
        self.ingestion.seed_reference()
        self.ingestion.seed_fields(case)
        self.ingestion.ingest_case(case)
        self.staff = load_actor(self.repo, "U-STAFF")
        self.agro = load_actor(self.repo, "U-AGRO")
        self.service = load_actor(self.repo, "U-SVC")
        self.obs = field_observation_ids(self.repo, "T1")
        self.assertTrue(self.obs)

    def tearDown(self):
        self.repo.conn.close()
        self.tmp.cleanup()

    def test_role_enforcement(self):
        with self.assertRaises(PermissionDenied):
            propose_candidate(self.repo, self.agro, "T1", "D2", self.obs)
        with self.assertRaises(PermissionDenied):
            draft_monitor_only(self.repo, self.staff, "T1", self.obs, "x")
        with self.assertRaises(PermissionDenied):
            issue_notice(self.repo, self.agro, "REC-N1")

    def test_hearsay_cannot_back_candidate(self):
        hearsay = self.repo.query(
            "SELECT id FROM evidence WHERE is_hearsay = 1"
        )[0]["id"]
        with self.assertRaises(HearsayNotEvidence):
            propose_candidate(self.repo, self.staff, "T1", "D2", [hearsay])

    def test_matched_is_not_confirmed_and_d2_requires_spreading(self):
        d1 = propose_candidate(self.repo, self.staff, "T1", "D1", self.obs)
        d2 = propose_candidate(self.repo, self.staff, "T1", "D2", self.obs)
        status_d1 = judge_candidate(self.repo, self.staff, d1, True)
        self.assertEqual(status_d1, CandidateStatus.MATCHED)
        self.assertNotEqual(status_d1, CandidateStatus.CONFIRMED)
        # D2 要求扩散症状，现有观察不满足，判相符必须被拦截
        with self.assertRaises(IncompatibleEvidence):
            judge_candidate(self.repo, self.staff, d2, True)
        status_d2 = judge_candidate(self.repo, self.staff, d2, False)
        self.assertEqual(status_d2, CandidateStatus.MISMATCHED)

    def test_lab_negative_excludes_d1_without_touching_d2(self):
        d1 = propose_candidate(self.repo, self.staff, "T1", "D1", self.obs)
        d2 = propose_candidate(self.repo, self.staff, "T1", "D2", self.obs)
        judge_candidate(self.repo, self.staff, d1, True)
        judge_candidate(self.repo, self.staff, d2, False)
        status = apply_lab_result(self.repo, self.staff, "T1", "D1", False, "CP-23")
        self.assertEqual(status, CandidateStatus.RULED_OUT)
        self.assertEqual(self.repo.get("candidates", d2)["status"],
                         CandidateStatus.MISMATCHED.value)

    def test_only_positive_lab_confirms(self):
        d1 = propose_candidate(self.repo, self.staff, "T1", "D1", self.obs)
        status = apply_lab_result(self.repo, self.staff, "T1", "D1", True, "LAB-X")
        self.assertEqual(status, CandidateStatus.CONFIRMED)
        self.assertEqual(self.repo.get("candidates", d1)["status"],
                         CandidateStatus.CONFIRMED.value)

    def test_flowering_label_blocks_application_verbatim(self):
        report = validate_product_application(
            self.repo, "T1", "PRD-QN", self.obs
        )
        self.assertFalse(report.ok)
        self.assertTrue(any("开花" in e for e in report.errors))
        # 限制原文出现在报告里，没有被摘要省略
        self.assertTrue(any("不得施用" in t or "禁用" in t
                            for t in report.restriction_texts))

        rec_id = draft_product_application(
            self.repo, self.agro, "T1", "PRD-QN", self.obs, "试拟"
        )
        report2 = approve(self.repo, self.agro, rec_id)
        self.assertFalse(report2.ok)
        self.assertEqual(self.repo.get("recommendations", rec_id)["status"],
                         RecStatus.REJECTED.value)

    def test_cr2_relaxation_does_not_apply_to_cr1(self):
        rows = self.repo.query(
            "SELECT lc.* FROM label_clauses lc JOIN labels l ON l.id = lc.label_id "
            "WHERE l.crop_id = 'CR1'"
        )
        # CR1 的条款里没有 relaxed 放宽项
        self.assertFalse(
            any(r["restriction_type"] == RestrictionType.RELAXED.value for r in rows)
        )

    def test_monitor_recommendation_approved_and_notice_supersedes_only_open(self):
        n1_snapshot_before = self.repo.get("notices", "N1")["basis_snapshot"]
        n2_snapshot_before = self.repo.get("notices", "N2")["basis_snapshot"]

        rec_id = draft_monitor_only(
            self.repo, self.agro, "T1", self.obs,
            "D1 排除且花期禁用，D2 未核实，继续观察"
        )
        report = approve(self.repo, self.agro, rec_id)
        self.assertTrue(report.ok)
        notice_id = issue_notice(self.repo, self.service, rec_id)

        n1 = self.repo.get("notices", "N1")
        n2 = self.repo.get("notices", "N2")
        self.assertEqual(n1["status"], NoticeStatus.SUPERSEDED.value)
        # 已执行的 N2 状态与快照完全不动
        self.assertEqual(n2["status"], NoticeStatus.EXECUTED.value)
        self.assertEqual(n2["basis_snapshot"], n2_snapshot_before)
        # N1 旧快照仍可追溯
        self.assertEqual(n1["basis_snapshot"], n1_snapshot_before)

        new_notice = self.repo.get("notices", notice_id)
        snapshot = json.loads(new_notice["basis_snapshot"])
        self.assertEqual(snapshot["superseded_notices"], ["N1"])
        self.assertTrue(snapshot["evidence"])

    def test_executed_notice_cannot_be_withdrawn_and_keeps_basis(self):
        with self.assertRaises(PermissionDenied):
            mark_executed(self.repo, self.agro, "N2")
        # N2 已处于 executed，再登记会被状态机拒绝
        from plantstation.models import WorkflowStateError
        with self.assertRaises(WorkflowStateError):
            mark_executed(self.repo, self.service, "N2")

    def test_revision_chain_preserves_old_evidence(self):
        first = draft_monitor_only(self.repo, self.agro, "T1", self.obs, "v1")
        approve(self.repo, self.agro, first)
        notice_id = issue_notice(self.repo, self.service, first)
        second = revise(
            self.repo, self.agro, first, RecType.MONITOR_ONLY, None,
            self.obs, "v2 依据新观察更新"
        )
        approve(self.repo, self.agro, second)
        issue_notice(self.repo, self.service, second)
        self.assertEqual(self.repo.get("recommendations", first)["status"],
                         RecStatus.SUPERSEDED.value)
        self.assertEqual(self.repo.get("recommendations", second)["version"], 2)
        self.assertEqual(self.repo.get("notices", notice_id)["status"],
                         NoticeStatus.SUPERSEDED.value)
        # 旧建议关联证据仍在
        self.assertTrue(self.repo.rec_evidence_ids(first))

    def test_provenance_explains_sources_and_excludes_hearsay(self):
        d1 = propose_candidate(self.repo, self.staff, "T1", "D1", self.obs)
        judge_candidate(self.repo, self.staff, d1, True)
        apply_lab_result(self.repo, self.staff, "T1", "D1", False, "CP-23")
        rec_id = draft_monitor_only(
            self.repo, self.agro, "T1", self.obs, "继续观察"
        )
        approve(self.repo, self.agro, rec_id)
        detail = explain_recommendation(self.repo, rec_id)
        source_ids = {e["source_id"] for e in detail["evidence"]}
        self.assertIn("CP-10", source_ids)
        self.assertIn("CP-24", source_ids)
        # 传闻单独列出且明确不可用，绝不混入依据
        self.assertTrue(detail["excluded_hearsay"])
        self.assertTrue(all(h["is_hearsay"] for h in detail["excluded_hearsay"]))
        self.assertFalse(any(e["is_hearsay"] for e in detail["evidence"]))
        # 施药类建议的标签限制逐字可见（观察类建议本身不携带标签）
        report = validate_product_application(
            self.repo, "T1", "PRD-QN", self.obs
        )
        self.assertTrue(any(
            "不得施用" in t or "禁用" in t
            for t in report.restriction_texts
        ))
        # 候选状态含 D1（排除后）与 D2 逻辑可追溯
        statuses = {c["disease_id"]: c["status"] for c in detail["candidates"]}
        self.assertEqual(statuses["D1"], CandidateStatus.RULED_OUT.value)

    def test_notice_snapshot_explains_executed_basis(self):
        detail = explain_notice(self.repo, "N2")
        self.assertEqual(detail["status"], NoticeStatus.EXECUTED.value)
        snapshot = detail["basis_snapshot"]
        self.assertTrue(snapshot["frozen"])
        self.assertTrue(snapshot["label_restrictions"])


class EndToEndDemoTest(unittest.TestCase):
    def test_demo_runs_full_chain(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = connect(Path(tmp.name) / "demo.db")
        init_db(conn)
        repo = Repository(conn)
        result = run_demo(repo, "data/reference.json", "data/case.json")

        self.assertFalse(result["blocked_application"]["approved"])
        self.assertTrue(result["blocked_application"]["errors"])
        self.assertEqual(result["new_notice"][0], "N")
        n1 = result["n1_after"]
        n2 = result["n2_after"]
        self.assertEqual(n1["status"], NoticeStatus.SUPERSEDED.value)
        self.assertEqual(n2["status"], NoticeStatus.EXECUTED.value)
        self.assertTrue(n2["basis_snapshot"]["frozen"])
        self.assertEqual(
            result["t1_dossier"]["current_stage"], "开花期"
        )
        # D1 排除，D2 未确诊
        self.assertEqual(result["candidates"]["D1_final"],
                         CandidateStatus.RULED_OUT.value)
        self.assertEqual(result["candidates"]["D2_final"],
                         CandidateStatus.MISMATCHED.value)


if __name__ == "__main__":
    unittest.main()
