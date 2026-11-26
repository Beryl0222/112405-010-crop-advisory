"""规则化中文记录解析器。

输入资料为合成短消息/田间记录/标签摘录，解析只做结构抽取与显式标注：
- 邻田、转发等传闻一律标记 ``is_hearsay=True`` 且不可用，绝不当作本田观察；
- 症状按参考表关键词抽取，"未见扩散" 等否定表述不会被误判为扩散症状；
- 标签按分句抽取限制条款，原文限制逐字保留在 ``text`` 中。
"""

from __future__ import annotations

import re
from typing import Any

from .models import EvidenceKind, ParsedEntry

_CLAUSE_SPLIT = re.compile(r"[；;。]")
_OBS_SPLIT = re.compile(r"[，,；;。]")
_LABEL_ID = re.compile(r"LABEL\d+")
_CROP_ID = re.compile(r"CR-?\d+")
_DISEASE_ID = re.compile(r"D\d+")
_FIELD_ID = re.compile(r"T\d+")
_FARMER_ID = re.compile(r"F\d+")
_NEGATIVE_SPREAD = re.compile(r"(未见|没有|未发生|无)扩散")


class RecordParser:
    def __init__(self, reference: dict[str, Any]):
        self.reference = reference
        self.symptoms = {s["code"]: s["keywords"] for s in reference["symptoms"]}
        self.hearsay_markers = reference["hearsay_markers"]
        self.stage_aliases = reference["stage_aliases"]
        self.product_names = [p["name"] for p in reference["products"]]

    # ---------- 基础工具 ----------
    def _looks_hearsay(self, text: str) -> bool:
        return any(marker in text for marker in self.hearsay_markers)

    def extract_symptoms(self, text: str) -> list[str]:
        codes: list[str] = []
        for code, keywords in self.symptoms.items():
            if code == "spreading_lesions" and _NEGATIVE_SPREAD.search(text):
                continue
            if all(keyword in text for keyword in keywords):
                codes.append(code)
        return codes

    def _normalize_crop(self, raw: str | None) -> str | None:
        if not raw:
            return None
        for crop in self.reference["crops"]:
            if raw in (crop["id"], *crop.get("aliases", [])):
                return crop["id"]
        return None

    def _find_stage(self, text: str) -> str | None:
        # 长别名优先（"已开花" 先于 "开花"），避免被短词截胡
        for alias in sorted(self.stage_aliases, key=len, reverse=True):
            if alias in text:
                return self.stage_aliases[alias]
        return None

    def _find_product(self, text: str) -> str | None:
        for product in self.reference["products"]:
            if product["name"] in text:
                return product["id"]
        return None

    # ---------- 入口 ----------
    def parse(self, source_id: str, kind: str, text: str,
              default_field: str | None = None) -> list[ParsedEntry]:
        text = text.strip()
        if kind == "forwarded-story":
            return [self._parse_hearsay(source_id, text, default_field)]
        if kind in ("farmer-observation", "observation"):
            return self._parse_observation(source_id, kind, text, default_field)
        if kind == "label":
            return self._parse_label(source_id, text)
        if kind == "lab":
            return self._parse_lab(source_id, text)
        if kind in ("field-register", "correction"):
            return self._parse_stage(source_id, kind, text)
        if kind == "knowledge-note":
            return self._parse_knowledge(source_id, text)
        if kind == "notice":
            return [ParsedEntry(
                source_id=source_id, kind=EvidenceKind.NOTICE_RECORD, text=text,
                field_id=default_field, payload=self._parse_notice(text),
            )]
        if kind == "farmer-reply":
            return self._parse_reply(source_id, text, default_field)
        # 未知种类保留原文，标注为不可直接使用，避免静默丢弃
        return [ParsedEntry(source_id=source_id, kind=EvidenceKind.REPLY,
                            text=text, field_id=default_field, usable=False)]

    # ---------- 各类记录 ----------
    def _parse_hearsay(self, source_id: str, text: str,
                       default_field: str | None) -> ParsedEntry:
        fields = _FIELD_ID.findall(text)
        return ParsedEntry(
            source_id=source_id,
            kind=EvidenceKind.HEARSAY,
            text=text,
            field_id=fields[0] if fields else default_field,
            is_hearsay=True,
            usable=False,  # 邻田经验不是本田证据
            payload={
                "product_id": self._find_product(text),
                "reason": "转发/邻田经验，来源田块、作物品种与施用条件不明",
            },
        )

    def _parse_observation(self, source_id: str, kind: str, text: str,
                           default_field: str | None) -> list[ParsedEntry]:
        entries: list[ParsedEntry] = []
        for clause in self._obs_clauses(text):
            fields = _FIELD_ID.findall(clause)
            field_id = fields[0] if fields else default_field
            if self._looks_hearsay(clause):
                # 技术员记录中夹带的转述分句单独切出，标记为传闻
                entries.append(ParsedEntry(
                    source_id=source_id, kind=EvidenceKind.HEARSAY, text=clause,
                    field_id=field_id, is_hearsay=True, usable=False,
                    payload={"reason": "记录内转述的邻田经验，非本田观察"},
                ))
                continue
            entries.append(ParsedEntry(
                source_id=source_id, kind=EvidenceKind.OBSERVATION, text=clause,
                field_id=field_id,
                symptom_codes=self.extract_symptoms(clause),
            ))
        return entries

    def _parse_label(self, source_id: str, text: str) -> list[ParsedEntry]:
        product_id = self._find_product(text)
        label_ids = _LABEL_ID.findall(text)
        crops_in_text = [self._normalize_crop(c) for c in _CROP_ID.findall(text)]
        crops_in_text = [c for c in crops_in_text if c]
        entries: list[ParsedEntry] = []
        if not label_ids:
            # 无编号标签：按案件主作物建一条，限制原文完整保留
            scope = crops_in_text or [self.reference["crops"][0]["id"]]
            entries.append(self._label_entry(
                source_id, text, None, product_id, scope[0], text))
            return entries
        segments = [s for s in _CLAUSE_SPLIT.split(text) if s.strip()]
        for label_id in label_ids:
            scope = self._label_scope(label_id, crops_in_text)
            related = [s for s in segments if label_id in s] or segments
            for segment in related:
                entries.append(self._label_entry(
                    source_id, segment, label_id, product_id, scope, segment))
        return entries

    def _label_scope(self, label_id: str, crops_in_text: list[str]) -> str:
        for product in self.reference["products"]:
            for label in product["labels"]:
                if label["id"] == label_id and label["crops"]:
                    return label["crops"][0]
        return crops_in_text[0] if crops_in_text else self.reference["crops"][0]["id"]

    def _label_entry(self, source_id: str, text: str, label_id: str | None,
                     product_id: str | None, crop_id: str, clause_text: str) -> ParsedEntry:
        clauses = self._extract_clauses(clause_text)
        return ParsedEntry(
            source_id=source_id, kind=EvidenceKind.LABEL, text=text,
            crop_id=crop_id,
            payload={
                "product_id": product_id,
                "label_id": label_id,  # None 时由入库层生成编号
                "clauses": clauses,
            },
        )

    def _extract_clauses(self, segment: str) -> list[dict[str, str | None]]:
        clauses: list[dict[str, str | None]] = []
        for part in re.split(r"[，,；;。]", segment):
            part = part.strip()
            if not part:
                continue
            if "放宽" in part:
                clauses.append({
                    "type": "relaxed",
                    "stage": None,
                    "text": part,
                })
            elif "不改变" in part or "不涉及" in part:
                clauses.append({
                    "type": "scope_note",
                    "stage": None,
                    "text": part,
                })
            elif "仅适用于" in part and "前" in part:
                stage = self._stage_before(part)
                clauses.append({
                    "type": "allowed_before",
                    "stage": stage,
                    "text": part,  # 限制原文逐字保留，摘要不得省略
                })
            elif ("不得施用" in part or "禁用" in part or "不得使用" in part):
                clauses.append({
                    "type": "forbidden_stage",
                    "stage": self._find_stage(part),
                    "text": part,
                })
        return clauses

    def _stage_before(self, part: str) -> str | None:
        for alias, canonical in self.stage_aliases.items():
            if alias in part:
                return canonical
        return None

    def _parse_lab(self, source_id: str, text: str) -> list[ParsedEntry]:
        entries: list[ParsedEntry] = []
        fields = _FIELD_ID.findall(text)
        diseases = _DISEASE_ID.findall(text)
        field_id = fields[0] if fields else None
        for disease_id in diseases:
            # 否定/阳性结论可能出现在病害编号之前（如"未检出D1标记"），
            # 因此在整句范围内判定，且"未检出"优先于"检出"匹配
            negative = bool(re.search(rf"(未检出|阴性){disease_id}", text)) or \
                bool(re.search(rf"{disease_id}[^，,；;。]*(未检出|阴性)", text))
            positive = bool(re.search(rf"(阳性){disease_id}", text)) or \
                bool(re.search(rf"{disease_id}[^，,；;。]*阳性", text)) or \
                (bool(re.search(rf"{disease_id}[^，,；;。]*检出", text)) and not negative)
            if negative:
                entries.append(ParsedEntry(
                    source_id=source_id, kind=EvidenceKind.LAB,
                    text=f"{disease_id} 未检出（{text}）",
                    field_id=field_id,
                    payload={"disease_id": disease_id, "detected": False},
                ))
            elif positive:
                entries.append(ParsedEntry(
                    source_id=source_id, kind=EvidenceKind.LAB,
                    text=f"{disease_id} 阳性（{text}）",
                    field_id=field_id,
                    payload={"disease_id": disease_id, "detected": True},
                ))
        # "不能证明D2"、"未采T2" 等保留为说明性条目，不产生任何确诊/排除结论
        note = ParsedEntry(
            source_id=source_id, kind=EvidenceKind.LAB, text=text,
            field_id=field_id, usable=False,
            payload={"note_only": True,
                     "note": "检验的否定/未采样表述不能推出其他病害成立"},
        )
        entries.append(note)
        return entries

    def _parse_stage(self, source_id: str, kind: str, text: str) -> list[ParsedEntry]:
        is_correction = kind == "correction"
        entries: list[ParsedEntry] = []
        for clause in self._clauses(text):
            fields = _FIELD_ID.findall(clause)
            crops = [self._normalize_crop(c) for c in _CROP_ID.findall(clause)]
            crop_id = crops[0] if crops else None
            stage = self._find_stage(clause)
            if stage is None and not is_correction:
                # 纯田块归属分句（如"相邻T2属F2，种植另一品种"）
                if fields:
                    entries.append(ParsedEntry(
                        source_id=source_id, kind=EvidenceKind.STAGE, text=clause,
                        field_id=fields[0], crop_id=crop_id or self._other_crop(crops),
                        payload={"field_registration": True},
                    ))
                continue
            entries.append(ParsedEntry(
                source_id=source_id, kind=EvidenceKind.STAGE, text=clause,
                field_id=fields[0] if fields else None, crop_id=crop_id,
                payload={"stage": stage, "is_correction": is_correction,
                         "scope_limited": "仅指" in clause or "不涉及" in clause},
            ))
        return entries

    def _other_crop(self, known: list[str | None]) -> str | None:
        known_set = {c for c in known if c}
        for crop in self.reference["crops"]:
            if crop["id"] not in known_set:
                return crop["id"]
        return None

    def _parse_knowledge(self, source_id: str, text: str) -> list[ParsedEntry]:
        entries: list[ParsedEntry] = []
        for disease_id in _DISEASE_ID.findall(text):
            match = re.search(rf"{disease_id}[^，,；;。]*", text)
            segment = match.group(0) if match else text
            compatible = self.extract_symptoms(segment)
            required: list[str] = []
            if "需伴随" in segment or "需要" in segment:
                required = self.extract_symptoms(segment)
            entries.append(ParsedEntry(
                source_id=source_id, kind=EvidenceKind.KNOWLEDGE, text=segment,
                payload={
                    "disease_id": disease_id,
                    "compatible_symptoms": compatible,
                    "required_symptoms": required,
                    "note": segment,
                },
            ))
        return entries

    def _parse_notice(self, text: str) -> dict[str, Any]:
        notices = []
        for match in re.finditer(
            r"(N\d+)[^，,；;。]*?(F\d+)[^，,；;。]*?(T\d+)[^，,；;。]*", text
        ):
            segment = match.group(0)
            state = ("executed" if ("已" in segment and "完成" in segment)
                     else "not_executed")
            notices.append({
                "notice_id": match.group(1),
                "farmer_id": match.group(2),
                "field_id": match.group(3),
                "state": state,
            })
        return {"notices": notices}

    def _parse_reply(self, source_id: str, text: str,
                     default_field: str | None) -> list[ParsedEntry]:
        fields = _FIELD_ID.findall(text)
        farmers = _FARMER_ID.findall(text)
        return [ParsedEntry(
            source_id=source_id, kind=EvidenceKind.REPLY, text=text,
            field_id=fields[0] if fields else default_field,
            payload={
                "farmer_id": farmers[0] if farmers else None,
                "stage": self._find_stage(text),
                "notice_refs": re.findall(r"N\d+", text),
                "not_executed": "尚未" in text,
            },
        )]

    @staticmethod
    def _clauses(text: str) -> list[str]:
        return [c.strip() for c in _CLAUSE_SPLIT.split(text) if c.strip()]

    @staticmethod
    def _obs_clauses(text: str) -> list[str]:
        # 观察记录按逗号细分，使"未见扩散"等否定分句独立成条
        return [c.strip() for c in _OBS_SPLIT.split(text) if c.strip()]
