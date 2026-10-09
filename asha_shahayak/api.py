import logging
import json
import secrets
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from .config import Settings, get_settings
from .complaints import ComplaintStore
from .data_handler import CsvDataHandler
from .dashboard import router as dashboard_router
from .reconciliation import reconcile_saved_text
from .storage import IntakeStore
from .whatsapp import router as whatsapp_router


class IntakeRequest(BaseModel):
    source: str = Field(pattern=r"^(whatsapp|ivr|sms)$")
    external_message_id: str = Field(min_length=1, max_length=255)
    payload: dict[str, Any]


class IntakeResponse(BaseModel):
    receipt_id: str
    received_at: datetime
    duplicate: bool
    status: str = "queued"


class ReconcileRequest(BaseModel):
    external_message_id: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1)


class ComplaintDecision(BaseModel):
    confirm: bool


def get_store(settings: Annotated[Settings, Depends(get_settings)]) -> IntakeStore:
    return IntakeStore(settings.database_path, settings.receipt_prefix)


def get_settings_value(settings: Annotated[Settings, Depends(get_settings)]) -> Settings:
    return settings


def require_api_key(
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header(alias="x-api-key")] = None,
) -> None:
    if (
        not settings.api_key
        or not x_api_key
        or not secrets.compare_digest(
            x_api_key.encode("utf-8"), settings.api_key.encode("utf-8")
        )
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="api key required")


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_settings.cache_clear()
    settings = get_settings()
    if Path(settings.registry_csv_path).exists() and Path(settings.status_csv_path).exists():
        handler = CsvDataHandler(settings.registry_csv_path, settings.status_csv_path)
        handler.load()
        app.state.csv_data = handler
    yield


app = FastAPI(
    title="ASHA Shahayak",
    description="Durable, confirmation-first payment claim intake.",
    lifespan=lifespan,
)
app.include_router(dashboard_router)
app.include_router(whatsapp_router)


@app.post(
    "/v1/intake",
    response_model=IntakeResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def intake(
    request: IntakeRequest,
    store: Annotated[IntakeStore, Depends(get_store)],
    _: Annotated[None, Depends(require_api_key)],
) -> IntakeResponse:
    receipt_id, duplicate, received_at = store.save_or_get(
        source=request.source,
        external_message_id=request.external_message_id,
        payload_json=json.dumps(request.payload, ensure_ascii=False, sort_keys=True),
    )
    return IntakeResponse(
        receipt_id=receipt_id,
        received_at=received_at,
        duplicate=duplicate,
    )


@app.post("/v1/reconcile", status_code=status.HTTP_202_ACCEPTED)
async def reconcile(
    request: ReconcileRequest,
    settings: Annotated[Settings, Depends(get_settings_value)],
    _: Annotated[None, Depends(require_api_key)],
) -> dict[str, Any]:
    intake_store = IntakeStore(settings.database_path, settings.receipt_prefix)
    intake_receipt, duplicate, received_at = intake_store.save_or_get(
        source="text",
        external_message_id=request.external_message_id,
        payload_json=json.dumps({"text": request.text}, ensure_ascii=False),
    )
    if duplicate and intake_store.status_for(request.external_message_id) == "processed":
        return {
            "receipt_id": intake_receipt,
            "received_at": received_at,
            "duplicate": True,
            "status": "already_received",
        }
    try:
        result = reconcile_saved_text(request.text, intake_receipt, settings)
    except ValueError as error:
        intake_store.mark_status(intake_receipt, "failed")
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception:
        intake_store.mark_status(intake_receipt, "failed")
        raise
    intake_store.mark_status(intake_receipt, "processed")
    response: dict[str, Any] = {**result, "duplicate": duplicate}
    if result["status"] == "medical_emergency":
        response["received_at"] = received_at
        response["message"] = "Payment claim nahi bana. Emergency help ke liye 108/112 verify karke call karein."
    return response


@app.post("/v1/complaints/{complaint_id}/decision")
async def complaint_decision(
    complaint_id: str,
    decision: ComplaintDecision,
    settings: Annotated[Settings, Depends(get_settings_value)],
    _: Annotated[None, Depends(require_api_key)],
) -> dict[str, Any]:
    try:
        return ComplaintStore(settings.database_path).decide(complaint_id, decision.confirm)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.middleware("http")
async def _log_wa_webhook(request, call_next):
    response = await call_next(request)
    if request.url.path == "/webhooks/whatsapp" and request.method == "POST":
        logging.getLogger(__name__).info("WA_WEBHOOK status=%s", response.status_code)
    return response
