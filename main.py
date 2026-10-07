"""FastAPI 入口：暴露接入/查询/历史/导出接口，错误码可区分。"""
import uvicorn
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from app.errors import InteropError, RejectReason
from app.models import Actor, Purpose, Role
from app.service import InteropService

app = FastAPI(title="clinical-fhir-interop")
service = InteropService()

_STATUS_BY_REASON = {
    RejectReason.MISSING_FIELD: 422,
    RejectReason.INVALID_CODE: 422,
    RejectReason.INVALID_TIME: 422,
    RejectReason.INVALID_VALUE: 422,
    RejectReason.UNSUPPORTED_FIELD: 422,
    RejectReason.UNSUPPORTED_RESOURCE: 422,
    RejectReason.VERSION_CONFLICT: 409,
    RejectReason.NOT_FOUND: 404,
    RejectReason.FORBIDDEN: 403,
}


@app.exception_handler(InteropError)
async def interop_error_handler(_: Request, exc: InteropError):
    return JSONResponse(
        status_code=_STATUS_BY_REASON.get(exc.reason, 400),
        content={"error": exc.reason.value, "message": exc.message, "details": exc.details},
    )


def _actor(x_subject: str, x_role: str, x_purpose: str) -> Actor:
    return Actor(subject=x_subject, role=Role(x_role), purpose=Purpose(x_purpose))


@app.get("/")
def read_root():
    return {"service": "clinical-fhir-interop", "status": "ok"}


@app.post("/ingest", status_code=201)
def ingest(body: dict,
           x_subject: str = Header(), x_role: str = Header(), x_purpose: str = Header(),
           x_source: str = Header(default="unknown"),
           x_expected_version: int | None = Header(default=None),
           x_idempotency_key: str | None = Header(default=None)):
    return service.ingest(_actor(x_subject, x_role, x_purpose), body, source=x_source,
                          expected_version=x_expected_version,
                          idempotency_key=x_idempotency_key)


@app.get("/resources/{resource_type}/{resource_id}")
def read(resource_type: str, resource_id: str,
         x_subject: str = Header(), x_role: str = Header(), x_purpose: str = Header()):
    return service.read(_actor(x_subject, x_role, x_purpose), resource_type, resource_id)


@app.get("/resources/{resource_type}/{resource_id}/history")
def history(resource_type: str, resource_id: str,
            x_subject: str = Header(), x_role: str = Header(), x_purpose: str = Header()):
    return service.history(_actor(x_subject, x_role, x_purpose), resource_type, resource_id)


@app.get("/export/{resource_type}/{resource_id}")
def export(resource_type: str, resource_id: str,
           x_subject: str = Header(), x_role: str = Header(), x_purpose: str = Header()):
    return service.export(_actor(x_subject, x_role, x_purpose), resource_type, resource_id)


@app.post("/export/{resource_type}/batch")
def export_batch(resource_type: str, body: dict,
                 x_subject: str = Header(), x_role: str = Header(), x_purpose: str = Header()):
    return service.export_batch(_actor(x_subject, x_role, x_purpose), resource_type,
                                list(body.get("resource_ids") or []))


@app.get("/audit")
def search_audit(subject: str | None = None, action: str | None = None,
                 outcome: str | None = None, since: str | None = None,
                 until: str | None = None,
                 x_subject: str = Header(), x_role: str = Header(),
                 x_purpose: str = Header()):
    return service.search_audit(_actor(x_subject, x_role, x_purpose),
                                subject=subject, action=action, outcome=outcome,
                                since=since, until=until)


@app.get("/audit/summary")
def audit_summary(subject: str | None = None, action: str | None = None,
                  outcome: str | None = None, since: str | None = None,
                  until: str | None = None,
                  x_subject: str = Header(), x_role: str = Header(),
                  x_purpose: str = Header()):
    return service.audit_summary(_actor(x_subject, x_role, x_purpose),
                                 subject=subject, action=action, outcome=outcome,
                                 since=since, until=until)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=0)
