import secrets
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .complaints import ComplaintStore
from .config import Settings, get_settings
from .ledger import Ledger
from .registry import AshaRegistry
from .status import StatusStore


templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
router = APIRouter()
REASON_CODES = ("missing_payment", "wrong_amount", "wrong_activity", "wrong_month", "other")


def officer_token(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> str:
    supplied = request.headers.get("x-officer-token") or request.query_params.get("token")
    if not supplied or not secrets.compare_digest(
        supplied.encode("utf-8"), settings.officer_token.encode("utf-8")
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="officer token required")
    return supplied


def page_data(settings: Settings, token: str) -> dict:
    complaint_store = ComplaintStore(settings.database_path)
    ledger = Ledger(settings.database_path, settings.receipt_prefix)
    statuses = StatusStore(settings.database_path).all()
    registry_ids = AshaRegistry(
        settings.database_path, settings.session_salt
    ).ids()
    status_by_asha = {row["asha_id"]: row for row in statuses if row["asha_id"]}
    merged_rows = []
    for asha_id in sorted(registry_ids | set(status_by_asha)):
        row = status_by_asha.get(asha_id, {})
        merged_rows.append(
            {
                "asha_id": asha_id,
                "registered": asha_id in registry_ids,
                "month": row.get("month", ""),
                "head": row.get("head", ""),
                "claimed_amount": row.get("claimed_amount", ""),
                "approved_amount": row.get("approved_amount", ""),
                "stage": row.get("stage", ""),
                "stage_date": row.get("stage_date", ""),
            }
        )
    return {
        "token": token,
        "complaints": complaint_store.confirmed(),
        "entries": ledger.entries(),
        "gaps": ledger.gaps(),
        "statuses": statuses,
        "registry_ids": registry_ids,
        "merged_rows": merged_rows,
        "has_uploaded_data": bool(statuses or registry_ids),
        "reason_codes": REASON_CODES,
        "message": None,
    }


async def dashboard_page(
    request: Request,
    token: Annotated[str, Depends(officer_token)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "dashboard.html", page_data(settings, token)
    )


async def save_reason(
    complaint_id: str,
    reason_code: Annotated[str, Form()],
    token: Annotated[str, Depends(officer_token)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RedirectResponse:
    if reason_code not in REASON_CODES:
        raise HTTPException(status_code=422, detail="invalid reason code")
    try:
        ComplaintStore(settings.database_path).set_reason(complaint_id, reason_code)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return RedirectResponse(f"/dashboard?token={token}", status_code=303)


async def upload_status(
    request: Request,
    file: UploadFile = File(...),
    token: str = Depends(officer_token),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    try:
        count = StatusStore(settings.database_path).import_csv(await file.read())
        data = page_data(settings, token)
        data["message"] = f"{count} status rows stored"
    except ValueError as error:
        data = page_data(settings, token)
        data["message"] = f"CSV error: {error}"
    return templates.TemplateResponse(request, "dashboard.html", data)


async def upload_registry(
    request: Request,
    file: UploadFile = File(...),
    token: str = Depends(officer_token),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    try:
        count = AshaRegistry(
            settings.database_path, settings.session_salt
        ).import_csv(await file.read())
        data = page_data(settings, token)
        data["message"] = f"{count} ASHA registrations stored"
    except ValueError as error:
        data = page_data(settings, token)
        data["message"] = f"Registration CSV error: {error}"
    return templates.TemplateResponse(request, "dashboard.html", data)


async def upload_dashboard_data(
    request: Request,
    registry_file: UploadFile = File(...),
    status_file: UploadFile = File(...),
    token: str = Depends(officer_token),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    registry_content = await registry_file.read()
    status_content = await status_file.read()
    try:
        with tempfile.TemporaryDirectory() as directory:
            validation_path = str(Path(directory) / "validation.sqlite3")
            AshaRegistry(validation_path, settings.session_salt).import_csv(registry_content)
            StatusStore(validation_path).import_csv(status_content)
        registry_count = AshaRegistry(
            settings.database_path, settings.session_salt
        ).import_csv(registry_content, replace=True)
        status_count = StatusStore(settings.database_path).import_csv(
            status_content, replace=True
        )
        message = (
            f"Uploaded {registry_count} ASHA registrations and "
            f"{status_count} officer status rows"
        )
    except ValueError as error:
        message = f"CSV error: {error}"
    data = page_data(settings, token)
    data["message"] = message
    return templates.TemplateResponse(request, "dashboard.html", data)


async def reset_registry(
    asha_id: Annotated[str, Form()],
    token: str = Depends(officer_token),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    try:
        AshaRegistry(settings.database_path, settings.session_salt).reset(asha_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return RedirectResponse(f"/dashboard?token={token}", status_code=303)


router.add_api_route("/dashboard", dashboard_page, methods=["GET"], response_class=HTMLResponse)
router.add_api_route(
    "/dashboard/complaints/{complaint_id}/reason",
    save_reason,
    methods=["POST"],
)
router.add_api_route("/dashboard/status", upload_status, methods=["POST"])
router.add_api_route("/dashboard/registry", upload_registry, methods=["POST"])
router.add_api_route("/dashboard/data", upload_dashboard_data, methods=["POST"])
router.add_api_route(
    "/dashboard/registry/reset", reset_registry, methods=["POST"]
)
