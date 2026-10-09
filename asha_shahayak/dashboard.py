import secrets
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .complaints import ComplaintStore
from .config import Settings, get_settings
from .ledger import Ledger
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
    return {
        "token": token,
        "complaints": complaint_store.confirmed(),
        "entries": ledger.entries(),
        "gaps": ledger.gaps(),
        "statuses": StatusStore(settings.database_path).all(),
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


router.add_api_route("/dashboard", dashboard_page, methods=["GET"], response_class=HTMLResponse)
router.add_api_route(
    "/dashboard/complaints/{complaint_id}/reason",
    save_reason,
    methods=["POST"],
)
router.add_api_route("/dashboard/status", upload_status, methods=["POST"])
