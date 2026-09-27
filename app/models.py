"""数据模型：编码、资源、版本与操作主体。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ResourceType(str, Enum):
    PATIENT = "Patient"
    OBSERVATION = "Observation"


class Role(str, Enum):
    ADMIN = "admin"
    CLINICIAN = "clinician"
    RESEARCHER = "researcher"


class Purpose(str, Enum):
    TREATMENT = "treatment"
    RESEARCH = "research"
    OPERATIONS = "operations"


@dataclass(frozen=True)
class Coding:
    """统一编码体系下的编码（system + code）。"""

    system: str
    code: str
    display: str = ""


@dataclass(frozen=True)
class Actor:
    """操作主体：角色 + 用途。"""

    subject: str
    role: Role
    purpose: Purpose


@dataclass
class Resource:
    """归一化后的临床资源（Patient / Observation 的公共形态）。"""

    resource_type: ResourceType
    resource_id: str
    source: str
    payload: dict
    effective_time: datetime | None = None  # 统一为 UTC 的业务时间


@dataclass
class ResourceVersion:
    """资源的一个历史版本。"""

    resource: Resource
    version: int
    updated_at: datetime
    updated_by: str
