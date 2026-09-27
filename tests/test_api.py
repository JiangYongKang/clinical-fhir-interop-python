"""HTTP surface: rejection reasons map to distinct, classifiable statuses."""

import pytest
from fastapi.testclient import TestClient

from app.api import create_app

from .conftest import OBSERVATION_OK, PATIENT_OK

CLINICIAN = {"X-Subject": "dr.chen", "X-Role": "clinician", "X-Purpose": "treatment"}
RESEARCHER = {"X-Subject": "lab.li", "X-Role": "researcher", "X-Purpose": "research"}


@pytest.fixture()
def client():
    return TestClient(create_app())


def test_status_codes_distinguish_failures(client, log):
    r = client.put("/resources/Patient/p1", headers=CLINICIAN,
                   json={"payload": PATIENT_OK, "idempotency_key": "a1"})
    assert r.status_code == 200

    malformed = client.put("/resources/Patient/p2", headers=CLINICIAN,
                           json={"payload": {"id": "p2"}, "idempotency_key": "a2"})
    conflict = client.put("/resources/Patient/p1", headers=CLINICIAN,
                          json={"payload": PATIENT_OK, "base_version": 9, "idempotency_key": "a3"})
    missing = client.get("/resources/Patient/ghost", headers=CLINICIAN)
    forbidden = client.put("/resources/Patient/p3", headers=RESEARCHER,
                           json={"payload": PATIENT_OK, "idempotency_key": "a4"})
    log.info("malformed=%s conflict=%s missing=%s forbidden=%s",
             malformed.status_code, conflict.status_code, missing.status_code, forbidden.status_code)
    assert malformed.status_code == 422 and malformed.json()["error"] == "missing_field"
    assert conflict.status_code == 409 and conflict.json()["error"] == "version_conflict"
    assert missing.status_code == 404 and missing.json()["error"] == "not_found"
    assert forbidden.status_code == 403 and forbidden.json()["error"] == "forbidden"


def test_export_over_http_masks_for_researcher(client, log):
    client.put("/resources/Patient/p1", headers=CLINICIAN,
               json={"payload": PATIENT_OK, "idempotency_key": "b1"})
    r = client.get("/resources/Patient/p1/export", headers=RESEARCHER)
    log.info("export payload=%s", r.json()["payload"])
    assert r.status_code == 200
    assert r.json()["payload"]["mrn"] == "***"


def test_observation_ingestion_over_http(client, log):
    r = client.put("/resources/Observation/o1", headers=CLINICIAN,
                   json={"payload": OBSERVATION_OK, "idempotency_key": "c1"})
    log.info("normalized=%s", r.json()["payload"])
    assert r.status_code == 200
    assert r.json()["payload"]["code"]["code"] == "8480-6"
