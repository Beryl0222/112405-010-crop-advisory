"""领域模型、状态枚举与业务异常。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Role(str, Enum):
    """系统中的三类经办角色，权限在各服务函数中强制校验。"""

    STAFF = "staff"          # 植保人员：观察—病害候选符合性判断
    AGRONOMIST = "agronomist"  # 农艺师：建议的作物/时点/现场条件核对
    SERVICE = "service"      # 服务人员：面向农户的发送决定


class EvidenceKind(str, Enum):
    OBSERVATION = "observation"        # 本田/本块现场观察
    HEARSAY = "hearsay"                # 邻田、转发等传闻，不可作依据
    LABEL = "label"                    # 农资标签摘录
    LAB = "lab"                        # 检验结果
    STAGE = "stage"                    # 生育期登记/更正
    KNOWLEDGE = "knowledge"            # 病害—症状相容性知识条
    NOTICE_RECORD = "notice_record"    # 既有通知记录
    REPLY = "reply"                    # 农户回复，不构成诊断证据


class CandidateStatus(str, Enum):
    CANDIDATE = "candidate"      # 已登记，尚未完成符合性判断
    MATCHED = "matched"          # 植保人员判断现象与候选相符（仍非确诊）
    MISMATCHED = "mismatched"    # 植保人员判断现象与候选不符
    RULED_OUT = "ruled_out"      # 被检验结果排除
    CONFIRMED = "confirmed"      # 仅可由阳性检验结果确立


class RecType(str, Enum):
    PRODUCT_APPLICATION = "product_application"  # 施药建议
    MONITOR_ONLY = "monitor_only"                # 暂不施药、继续观察


class RecStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    SUPERSEDED = "superseded"


class NoticeStatus(str, Enum):
    NOT_EXECUTED = "not_executed"
    EXECUTED = "executed"
    SUPERSEDED = "superseded"
    WITHDRAWN = "withdrawn"


class RestrictionType(str, Enum):
    ALLOWED_BEFORE = "allowed_before"      # 仅适用于某生育期之前
    FORBIDDEN_STAGE = "forbidden_stage"    # 某生育期禁用
    RELAXED = "relaxed"                    # 对特定作物放宽
    SCOPE_NOTE = "scope_note"              # 适用范围说明


@dataclass
class Actor:
    id: str
    name: str
    role: Role


@dataclass
class ParsedEntry:
    """解析器产出的单条结构化条目，尚未入库。"""

    source_id: str
    kind: EvidenceKind
    text: str
    field_id: str | None = None
    crop_id: str | None = None
    is_hearsay: bool = False
    usable: bool = True
    symptom_codes: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationReport:
    rec_id: str
    ok: bool
    errors: list[str] = field(default_factory=list)
    restriction_texts: list[str] = field(default_factory=list)


class PlantStationError(Exception):
    """业务错误基类。"""


class PermissionDenied(PlantStationError):
    """角色无权执行该操作。"""


class NotFound(PlantStationError):
    """引用的对象不存在。"""


class HearsayNotEvidence(PlantStationError):
    """邻田/转发经验被尝试当作本田证据使用。"""


class IncompatibleEvidence(PlantStationError):
    """观察现象与病害候选不相容，不能判为相符。"""


class LabelRestrictionViolation(PlantStationError):
    """建议违反标签载明的作物或生育期限制。"""


class WorkflowStateError(PlantStationError):
    """对象当前状态不允许该操作。"""
