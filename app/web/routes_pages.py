"""HTML pages (Jinja2). Data is fetched client-side from the JSON API so the
pages never block on the FortiGate and can auto-refresh without a full reload.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.core.config import get_config, get_settings
from app.core.version import COLLECTOR_VERSION

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def _base_context(active: str) -> dict[str, object]:
    cfg = get_config()
    s = get_settings()
    return {
        "active": active,
        "app_name": cfg.get("app.display_name", "Predictive Network Lab"),
        "app_subtitle": cfg.get("app.dashboard_subtitle", "FortiGate 50F Telemetry Collector"),
        "app_full_subtitle": cfg.get("app.subtitle", ""),
        "mode": "MOCK" if s.mock_mode else "REAL",
        "collector_version": COLLECTOR_VERSION,
        "refresh_seconds": cfg.get("dashboard.refresh_interval_seconds", 30),
        "history_ranges": cfg.get("dashboard.history_ranges", ["1h", "6h", "24h", "7d"]),
        "timezone": s.timezone,
    }


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "dashboard.html", _base_context("dashboard"))


@router.get("/dataset", response_class=HTMLResponse)
def dataset(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "dataset.html", _base_context("dataset"))


@router.get("/variables", response_class=HTMLResponse)
def variables(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "variables.html", _base_context("variables"))


@router.get("/availability", response_class=HTMLResponse)
def availability(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "availability.html", _base_context("availability"))


@router.get("/model", response_class=HTMLResponse)
def model_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "model.html", _base_context("model"))


@router.get("/events", response_class=HTMLResponse)
def events(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "events.html", _base_context("events"))


@router.get("/speedtests", response_class=HTMLResponse)
def speedtests(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "speedtests.html", _base_context("speedtests"))


@router.get("/congestion", response_class=HTMLResponse)
def congestion(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "congestion.html", _base_context("congestion"))


@router.get("/wan-faults", response_class=HTMLResponse)
def wan_faults(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "wanfaults.html", _base_context("wanfaults"))


@router.get("/research", response_class=HTMLResponse)
def research(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "research.html", _base_context("research"))


@router.get("/system", response_class=HTMLResponse)
def system(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "system.html", _base_context("system"))
