from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
import re
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from .log_archive import LogArchive
from .service import ADDON_VERSION, HausieAIService
from .settings import Settings
from .ui import asset_path, render_page


class ObserveRequest(BaseModel):
    context: dict[str, Any] = Field(default_factory=dict)
    action: dict[str, Any]
    source: str = "user"


class DecideRequest(BaseModel):
    context: dict[str, Any] | None = None
    execute: bool = False


class FeedbackRequest(BaseModel):
    observation_id: int
    reward: float = Field(ge=-1, le=1)


class DecisionFeedbackRequest(BaseModel):
    reward: float = Field(ge=-1, le=1)
    source: str = Field(default="explicit_user_feedback", max_length=80)


class AnomalyFeedbackRequest(BaseModel):
    surprising: bool


settings = Settings.from_environment()
service = HausieAIService(settings)


def configure_logging() -> None:
    level = getattr(logging, service.settings.log_level, logging.INFO)
    archive = LogArchive(service.settings.data_dir)
    for name in ("uvicorn", "uvicorn.access"):
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            if getattr(handler, "hausie_archive", False):
                logger.removeHandler(handler)
                handler.close()
    stream = logging.StreamHandler()
    handlers: list[logging.Handler] = [stream]
    try:
        file_handler = archive.handler()
        file_handler.hausie_archive = True  # type: ignore[attr-defined]
        handlers.append(file_handler)
        for name in ("uvicorn", "uvicorn.access"):
            logger = logging.getLogger(name)
            logger.addHandler(file_handler)
            logger.propagate = False
            logger.setLevel(level)
    except OSError as exc:
        stream.handle(logging.LogRecord("hausie_ai", logging.ERROR, __file__, 0,
                                        "LOG_ARCHIVE unavailable error=%s", (exc,), None))
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        handlers=handlers,
        force=True,
    )
    logging.getLogger("websocket").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    await service.start()
    yield
    await service.stop()


app = FastAPI(title="Hausie AI Local", version=ADDON_VERSION, lifespan=lifespan)
LOGGER = logging.getLogger(__name__)


@app.middleware("http")
async def normalize_ingress_entry(request: Request, call_next):
    """Accept the path forms forwarded by Home Assistant Ingress."""
    original_path = request.scope["path"]
    ingress_path = request.headers.get("X-Ingress-Path", "").rstrip("/")
    forwarded_path = original_path
    if re.fullmatch(r"/api/hassio_ingress/[A-Za-z0-9_-]+", ingress_path):
        if original_path == ingress_path or original_path.startswith(f"{ingress_path}/"):
            forwarded_path = original_path[len(ingress_path):] or "/"
    forwarded_path = "/" + forwarded_path.lstrip("/")
    request.scope["path"] = forwarded_path
    normalized_path = forwarded_path.rstrip("/")
    if request.method == "GET" and normalized_path in {"", "/ui"}:
        request.scope["path"] = normalized_path or "/"
    response = await call_next(request)
    if response.status_code == 404 and ingress_path:
        LOGGER.warning("INGRESS_NOT_FOUND method=%s path=%s", request.method, forwarded_path)
    return response


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "hausie-ai-local", "version": ADDON_VERSION, "mode": service.status()["mode"]}


@app.get("/api/v1/status")
def status() -> dict[str, Any]:
    return service.status()


@app.get("/api/v1/observations")
def observations(limit: int = 20) -> list[dict[str, Any]]:
    return service.store.recent(limit)


@app.get("/api/v1/inventory")
def inventory() -> dict[str, Any]:
    """Everything Hausie AI can see, classified from local HA registries."""
    return service.inventory_view()


@app.get("/api/v1/context")
def context() -> dict[str, Any]:
    if not service.last_states:
        raise HTTPException(status_code=503, detail="No Home Assistant state snapshot is available yet.")
    return {"context": service.current_context()}


@app.get("/api/v1/environment/events")
def environment_events(limit: int = 50) -> list[dict[str, Any]]:
    return service.store.recent_environmental_events(limit)


@app.get("/api/v1/history/actions")
def history_actions(limit: int = 50) -> list[dict[str, Any]]:
    """Read-only audit of action-like Recorder changes and attribution."""
    return service.store.recent_historical_actions(limit)


@app.get("/api/v1/buttons/presses")
def button_presses(limit: int = 50) -> list[dict[str, Any]]:
    """Read-only audit of physical gestures and confirmed low-risk effects."""
    return service.store.recent_button_presses(limit)


@app.get("/api/v1/decisions")
def decisions(limit: int = 30) -> list[dict[str, Any]]:
    return service.store.recent_decisions(limit)


def _log_dates(since: datetime | None, until: datetime | None) -> tuple[datetime | None, datetime | None]:
    if (since and since.tzinfo is None) or (until and until.tzinfo is None):
        raise HTTPException(status_code=422, detail="Log date filters must include a time zone.")
    if since and until and since > until:
        raise HTTPException(status_code=422, detail="The 'since' date must be before 'until'.")
    return since, until


@app.get("/api/v1/logs")
def logs(limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0),
         level: str = Query("", pattern="^(|DEBUG|INFO|WARNING|ERROR|CRITICAL)$"),
         category: str = Query("", max_length=60),
         search: str = Query("", max_length=200),
         since: datetime | None = None, until: datetime | None = None) -> dict[str, Any]:
    """Paginated, read-only log lines retained by this app, newest first."""
    since, until = _log_dates(since, until)
    return LogArchive(service.settings.data_dir).query(
        limit=limit, offset=offset, level=level, category=category,
        search=search, since=since, until=until)


@app.get("/api/v1/logs/download")
def download_logs(level: str = Query("", pattern="^(|DEBUG|INFO|WARNING|ERROR|CRITICAL)$"),
                  category: str = Query("", max_length=60),
                  search: str = Query("", max_length=200),
                  since: datetime | None = None,
                  until: datetime | None = None) -> StreamingResponse:
    """Stream retained JSONL as gzip; no temporary file or full-memory buffer."""
    since, until = _log_dates(since, until)
    name = f"hausie-ai-logs-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.jsonl.gz"
    return StreamingResponse(
        LogArchive(service.settings.data_dir).download_gzip(
            level=level, category=category, search=search, since=since, until=until),
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{name}"',
                 "Cache-Control": "no-store"},
    )


@app.get("/api/v1/learning/comparison")
def learning_comparison(limit: int = 30) -> dict[str, Any]:
    """Read-only, local shadow predictions. Unlabelled rows are not failures."""
    return service.store.shadow_report(limit)


@app.get("/api/v1/learning/outcomes")
def learning_outcomes(limit: int = 30) -> list[dict[str, Any]]:
    """Observational before/after readings; no causal claims."""
    return service.store.recent_action_outcomes(limit)


@app.get("/api/v1/learning/lab")
def learning_lab() -> dict[str, Any]:
    """The complete local model catalog and family-specific evaluations."""
    return service.lab.report()


@app.post("/api/v1/learning/anomalies/{anomaly_id}/feedback")
def anomaly_feedback(anomaly_id: int, request: AnomalyFeedbackRequest) -> dict[str, Any]:
    if not service.lab_store.rate_anomaly(anomaly_id, request.surprising):
        raise HTTPException(status_code=404, detail="Anomaly not found, not scorable, or already reviewed.")
    return {"ok": True}


@app.post("/api/v1/observe")
def observe(request: ObserveRequest) -> dict[str, Any]:
    try:
        observation_id = service.observe(request.context, request.action, request.source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "observation_id": observation_id}


@app.post("/api/v1/decide")
def decide(request: DecideRequest) -> dict[str, Any]:
    context_value = request.context
    if context_value is None:
        if not service.last_states:
            raise HTTPException(status_code=503, detail="No Home Assistant state snapshot is available yet.")
        context_value = service.current_context()
    return service.decide(context_value, execute=request.execute)


@app.post("/api/v1/feedback")
def feedback(request: FeedbackRequest) -> dict[str, Any]:
    if not service.store.feedback(request.observation_id, request.reward):
        raise HTTPException(status_code=404, detail="Observation not found.")
    return {"ok": True}


@app.post("/api/v1/decisions/{decision_id}/feedback")
def decision_feedback(decision_id: int, request: DecisionFeedbackRequest) -> dict[str, Any]:
    if not service.add_decision_feedback(decision_id, request.reward, request.source):
        raise HTTPException(status_code=404, detail="Suggestion was not found or has already been rated.")
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
@app.get("/ui", response_class=HTMLResponse)
@app.get("/ui/", response_class=HTMLResponse)
def index(request: Request) -> str:
    return render_page("overview", request)


@app.get("/ui/assets/{name}")
def ui_asset(name: str) -> FileResponse:
    path, media_type = asset_path(name)
    return FileResponse(path, media_type=media_type)


@app.get("/ui/{page}", response_class=HTMLResponse)
@app.get("/ui/{page}/", response_class=HTMLResponse)
def ui_page(page: str, request: Request) -> str:
    if page not in {"inventory", "activity", "decisions", "learning", "logs"}:
        raise HTTPException(status_code=404, detail="Page not found.")
    return render_page(page, request)


if __name__ == "__main__":
    import uvicorn

    configure_logging()
    uvicorn.run("hausie_ai.app.main:app", host="0.0.0.0", port=8099)
