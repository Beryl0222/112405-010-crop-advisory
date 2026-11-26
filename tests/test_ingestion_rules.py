import tempfile
import unittest
from pathlib import Path

from plantstation.database import connect, init_db
from plantstation.ingestion import IngestionService, load_json
from plantstation.models import (
    CandidateStatus,
    EvidenceKind,
    NoticeStatus,
)
from plantstation.repository import Repository


class IngestionRuleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "case.db"
        conn = connect(self.db_path)
        init_db(conn)
        self.repo = Repository(conn)
        reference = load_json("data/reference.json")
        self.case = load_json("data/case.json")
        self.ingestion = IngestionService(self.repo, reference)
        self.ingestion.seed_reference()
        self.ingestion.seed_fields(self.case)
        self.ingestion.ingest_case(self.case)

    def tearDown(self):
        self.repo.conn.close()
        self.tmp.cleanup()

    def test_hearsay_stored_unusable_and_never_in_usable_set(self):
        rows = self.repo.query(
            "SELECT * FROM evidence WHERE is_hearsay = 1"
        )
        self.assertGreaterEqual(len(rows), 2)  # CP-11 与 CP-24 转述分句
        self.assertTrue(all(r["usable"] == 0 for r in rows))
        usable = self.repo.usable_evidence_for_field("T1")
        self.assertFalse(any(r["is_hearsay"] for r in usable))

    def test_negative_lab_stored_as_fact_without_auto_diagnosis(self):
        # 检验事实入库，但摄取阶段不自动创建/确诊任何候选
        rows = self.repo.query("SELECT * FROM lab_results WHERE field_id = 'T1'")
        self.assertTrue(any(r["disease_id"] == "D1" and r["detected"] == 0
                            for r in rows))
        candidates = {r["disease_id"]: r for r
                      in self.repo.candidates_for_field("T1")}
        self.assertNotIn("D2", candidates)  # 未核实的 D2 不会被自动登记或确诊
        self.assertNotIn(CandidateStatus.CONFIRMED.value,
                         {r["status"] for r in candidates.values()})

    def test_current_stage_prefers_correction_and_is_flowering(self):
        self.assertEqual(self.repo.current_stage("T1"), "开花期")

    def test_label_restrictions_persisted_verbatim(self):
        rows = self.repo.query(
            "SELECT lc.text, COUNT(*) AS n FROM label_clauses lc "
            "JOIN labels l ON l.id = lc.label_id WHERE l.crop_id = 'CR1' "
            "GROUP BY lc.text"
        )
        texts = [r["text"] for r in rows]
        self.assertTrue(any("开花期不得施用" in t or "花期禁用" in t
                            for t in texts))
        # 两个标签来源（CP-12 摘录与 LABEL1）各保留一份，同文本不重复
        self.assertTrue(all(r["n"] == 1 for r in rows))
        self.assertGreaterEqual(
            sum(1 for t in texts if "不得施用" in t or "禁用" in t), 2
        )

    def test_existing_notices_materialized_with_snapshots(self):
        n1 = self.repo.get("notices", "N1")
        n2 = self.repo.get("notices", "N2")
        self.assertEqual(n1["status"], NoticeStatus.NOT_EXECUTED.value)
        self.assertEqual(n2["status"], NoticeStatus.EXECUTED.value)
        self.assertTrue(n1["basis_snapshot"])
        self.assertTrue(n2["basis_snapshot"])
        # N2 快照包含当时标签依据，且标记为冻结
        import json
        snapshot = json.loads(n2["basis_snapshot"])
        self.assertTrue(snapshot["frozen"])
        self.assertIn("label_restrictions", snapshot)

    def test_stage_scope_correction_does_not_touch_t2(self):
        # T2 没有摄取到生育期更新，当前生育期应为 None
        self.assertIsNone(self.repo.current_stage("T2"))


if __name__ == "__main__":
    unittest.main()
