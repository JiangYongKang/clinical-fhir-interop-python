import logging

import pytest

from app.audit import AuditLog
from app.security import Actor, Purpose, Role
from app.service import ClinicalInteropService
from app.store import Store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@pytest.fixture()
def service() -> ClinicalInteropService:
    return ClinicalInteropService(Store(), AuditLog())


@pytest.fixture()
def clinician() -> Actor:
    return Actor("dr.chen", Role.CLINICIAN, Purpose.TREATMENT)


@pytest.fixture()
def researcher() -> Actor:
    return Actor("lab.li", Role.RESEARCHER, Purpose.RESEARCH)


@pytest.fixture()
def admin() -> Actor:
    return Actor("root", Role.ADMIN, Purpose.BILLING)


@pytest.fixture()
def log() -> logging.Logger:
    return logging.getLogger("interop-test")


PATIENT_OK = {
    "id": "p1",
    "family": "Wang",
    "given": "Fang",
    "birthDate": "1980-05-01",
    "mrn": "MRN-123",
}

OBSERVATION_OK = {
    "id": "o1",
    "subject": "p1",
    "coding": {"system": "SNOMED-CT", "code": "271649006"},
    "value": "120",
    "unit": "mmHg",
    "effectiveDateTime": "2026-09-27T10:00:00+08:00",
}
