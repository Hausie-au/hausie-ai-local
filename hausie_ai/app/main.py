from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import re
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

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
    level = getattr(logging, settings.log_level, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
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


@app.get("/api/v1/decisions")
def decisions(limit: int = 30) -> list[dict[str, Any]]:
    return service.store.recent_decisions(limit)


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
    if page not in {"inventory", "activity", "decisions", "learning"}:
        raise HTTPException(status_code=404, detail="Page not found.")
    return render_page(page, request)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("hausie_ai.app.main:app", host="0.0.0.0", port=8099)
