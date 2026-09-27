"""FastAPI surface for the clinical interop service.

Rejection reasons map to distinct HTTP statuses so clients can classify
failures without parsing message text:

===============================  ======
reason                           status
===============================  ======
missing_field / invalid_*        422
unsupported_resource             422
version_conflict                 409
not_found                        404
forbidden                        403
===============================  ======
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .audit import AuditLog
from .errors import Rejected, RejectReason
from .security import Actor, Purpose, Role
from .service import ClinicalInteropService
from .store import Store

_STATUS = {
    RejectReason.MISSING_FIELD: 422,
    RejectReason.INVALID_CODING: 422,
    RejectReason.INVALID_TIME: 422,
    RejectReason.UNSUPPORTED_RESOURCE: 422,
    RejectReason.VERSION_CONFLICT: 409,
    RejectReason.NOT_FOUND: 404,
    RejectReason.FORBIDDEN: 403,
}


class WriteRequest(BaseModel):
    payload: dict[str, Any]
    base_version: int | None = None
    idempotency_key: str


def _actor(x_subject: str, x_role: str, x_purpose: str) -> Actor:
    try:
        return Actor(x_subject, Role(x_role), Purpose(x_purpose))
    except ValueError as exc:
        raise Rejected(RejectReason.FORBIDDEN, f"unknown role or purpose: {exc}") from exc


def create_app() -> FastAPI:
    app = FastAPI(title="clinical-fhir-interop")
    app.state.service = ClinicalInteropService(Store(), AuditLog())

    @app.exception_handler(Rejected)
    async def on_rejected(_req: Request, exc: Rejected) -> JSONResponse:
        return JSONResponse(
            status_code=_STATUS[exc.reason],
            content={"error": exc.reason.value, "detail": exc.detail},
        )

    def service(req: Request) -> ClinicalInteropService:
        return req.app.state.service

    @app.put("/resources/{resource_type}/{resource_id}")
    def write(resource_type: str, body: WriteRequest, req: Request,
              x_subject: str = Header(), x_role: str = Header(), x_purpose: str = Header()):
        actor = _actor(x_subject, x_role, x_purpose)
        return service(req).write_resource(
            actor, resource_type, body.payload,
            base_version=body.base_version, idempotency_key=body.idempotency_key,
        )

    @app.get("/resources/{resource_type}/{resource_id}")
    def read(resource_type: str, resource_id: str, req: Request,
             x_subject: str = Header(), x_role: str = Header(), x_purpose: str = Header()):
        return service(req).read_resource(_actor(x_subject, x_role, x_purpose), resource_type, resource_id)

    @app.get("/resources/{resource_type}/{resource_id}/export")
    def export(resource_type: str, resource_id: str, req: Request,
               x_subject: str = Header(), x_role: str = Header(), x_purpose: str = Header()):
        return service(req).export_resource(_actor(x_subject, x_role, x_purpose), resource_type, resource_id)

    @app.get("/resources/{resource_type}/{resource_id}/history")
    def history(resource_type: str, resource_id: str, req: Request,
                x_subject: str = Header(), x_role: str = Header(), x_purpose: str = Header()):
        return service(req).resource_history(_actor(x_subject, x_role, x_purpose), resource_type, resource_id)

    @app.get("/audit")
    def audit(req: Request, x_subject: str = Header(), x_role: str = Header(), x_purpose: str = Header()):
        actor = _actor(x_subject, x_role, x_purpose)
        if actor.role != Role.ADMIN:
            raise Rejected(RejectReason.FORBIDDEN, "only admin may read the audit log")
        svc = service(req)
        return [e.__dict__ | {"masked_fields": list(e.masked_fields)} for e in svc._audit.events()]

    return app
