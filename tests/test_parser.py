import unittest

from plantstation.ingestion import load_json
from plantstation.models import EvidenceKind
from plantstation.parser import RecordParser


class ParserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = load_json("data/reference.json")
        cls.parser = RecordParser(cls.reference)

    def test_forwarded_story_is_hearsay_and_unusable(self):
        entries = self.parser.parse("CP-11", "forwarded-story",
                                    "邻田说喷过青宁后好转，作物品种和施用日不详。", "T1")
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.kind, EvidenceKind.HEARSAY)
        self.assertTrue(entry.is_hearsay)
        self.assertFalse(entry.usable)

    def test_farmer_observation_keeps_symptoms_and_negation(self):
        entries = self.parser.parse(
            "CP-10", "farmer-observation",
            "本田东侧三株叶背有白点，近两日未见扩散。", "T1"
        )
        self.assertEqual(len(entries), 2)
        first = entries[0]
        self.assertEqual(first.kind, EvidenceKind.OBSERVATION)
        self.assertIn("white_leaf_back_spots", first.symptom_codes)
        self.assertNotIn("spreading_lesions", first.symptom_codes)
        # 否定分句不得被当作扩散证据
        self.assertEqual(entries[1].symptom_codes, [])

    def test_embedded_forwarded_clause_split_out_as_hearsay(self):
        entries = self.parser.parse(
            "CP-24", "observation",
            "技术员现场见T1东侧三株白点可擦除，西侧未检查；农户转发的打药好转来自T2。",
            "T1",
        )
        kinds = {(e.kind, e.is_hearsay) for e in entries}
        self.assertIn((EvidenceKind.OBSERVATION, False), kinds)
        self.assertIn((EvidenceKind.HEARSAY, True), kinds)
        hearsay = [e for e in entries if e.is_hearsay]
        self.assertFalse(hearsay[0].usable)

    def test_label_clauses_keep_forbidden_stage_verbatim(self):
        entries = self.parser.parse(
            "CP-12", "label", "青宁标签：仅适用于抽穗前；开花期不得施用。"
        )
        payload = entries[0].payload
        clause_types = {c["type"] for c in payload["clauses"]}
        self.assertIn("allowed_before", clause_types)
        self.assertIn("forbidden_stage", clause_types)
        forbidden = next(c for c in payload["clauses"]
                         if c["type"] == "forbidden_stage")
        self.assertEqual(forbidden["stage"], "开花期")
        # 原文逐字保留
        self.assertIn("开花期不得施用", forbidden["text"])

    def test_label2_relaxation_scoped_to_cr2_only(self):
        entries = self.parser.parse(
            "CP-22", "label",
            "药剂青宁LABEL1旧版仅适用于抽穗前，花期禁用；LABEL2补充只对CR2作物放宽，不改变CR1限制。"
        )
        scopes = {e.crop_id for e in entries}
        self.assertEqual(scopes, {"CR1", "CR2"})
        cr2 = [e for e in entries if e.crop_id == "CR2"][0]
        self.assertTrue(
            any(c["type"] == "relaxed" for c in cr2.payload["clauses"])
        )

    def test_lab_negative_does_not_prove_other_disease(self):
        entries = self.parser.parse(
            "CP-23", "lab", "T1样本未检出D1标记，不能证明D2，未采T2。"
        )
        negative = [e for e in entries
                    if e.payload.get("disease_id") == "D1"]
        self.assertEqual(len(negative), 1)
        self.assertFalse(negative[0].payload["detected"])
        # D2 没有产生任何结论性条目，只有说明性 note
        d2_conclusions = [e for e in entries
                          if e.payload.get("disease_id") == "D2"]
        self.assertEqual(d2_conclusions, [])
        self.assertTrue(any(e.payload.get("note_only") for e in entries))

    def test_stage_correction_marked_and_limited_to_field(self):
        entries = self.parser.parse(
            "CP-27", "correction",
            "CR1此前生育期记录由孕穗更正为已开花，变化仅指T1，不涉及T2。"
        )
        stage_entries = [e for e in entries if e.payload.get("stage")]
        self.assertEqual(len(stage_entries), 1)
        entry = stage_entries[0]
        self.assertEqual(entry.field_id, "T1")
        self.assertEqual(entry.payload["stage"], "开花期")
        self.assertTrue(entry.payload["is_correction"])
        self.assertTrue(entry.payload["scope_limited"])


if __name__ == "__main__":
    unittest.main()
