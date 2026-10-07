import logging

import pytest

from app.models import Actor, Purpose, Role
from app.service import InteropService

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("case")


@pytest.fixture
def svc():
    return InteropService()


@pytest.fixture
def clinician():
    return Actor("dr-wang", Role.CLINICIAN, Purpose.TREATMENT)


@pytest.fixture
def researcher():
    return Actor("res-li", Role.RESEARCHER, Purpose.RESEARCH)


@pytest.fixture
def ops():
    return Actor("ops-zhao", Role.ADMIN, Purpose.OPERATIONS)


def log_case(case: str, *, inputs: dict, parties: list[str], rationale: str):
    """单测日志：打印输入、参与方与判定依据。"""
    log.info("CASE %s | inputs=%s | parties=%s | rationale=%s",
             case, inputs, parties, rationale)
