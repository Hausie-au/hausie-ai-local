from __future__ import annotations

from contextlib import asynccontextmanager
from html import escape
import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .learning import current_context
from .service import ADDON_VERSION, HausieAIService
from .settings import Settings


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


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "hausie-ai-local", "version": ADDON_VERSION, "mode": service.status()["mode"]}


@app.get("/api/v1/status")
def status() -> dict[str, Any]:
    return service.status()


@app.get("/api/v1/observations")
def observations(limit: int = 20) -> list[dict[str, Any]]:
    return service.store.recent(limit)


@app.post("/api/v1/observe")
def observe(request: ObserveRequest) -> dict[str, Any]:
    try:
        observation_id = service.observe(request.context, request.action, request.source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "observation_id": observation_id}


@app.post("/api/v1/decide")
def decide(request: DecideRequest) -> dict[str, Any]:
    context = request.context
    if context is None:
        if not service.last_states:
            raise HTTPException(status_code=503, detail="No Home Assistant state snapshot is available yet.")
        context = current_context(service.last_states)
    return service.decide(context, execute=request.execute)


@app.post("/api/v1/feedback")
def feedback(request: FeedbackRequest) -> dict[str, Any]:
    if not service.store.feedback(request.observation_id, request.reward):
        raise HTTPException(status_code=404, detail="Observation not found.")
    return {"ok": True}


@app.post("/api/v1/decisions/{decision_id}/feedback")
def decision_feedback(decision_id: int, request: DecisionFeedbackRequest) -> dict[str, Any]:
    if not service.add_decision_feedback(decision_id, request.reward, request.source):
        raise HTTPException(status_code=404, detail="Decision with an action was not found.")
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    current = service.status()
    stats = current["stats"]
    return f"""<!doctype html>
<html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Hausie AI</title><style>
body{{font:16px system-ui,sans-serif;max-width:900px;margin:40px auto;padding:0 20px;background:#f6f5ef;color:#173b3f}}
main{{background:white;border-radius:18px;padding:28px;box-shadow:0 8px 30px #173b3f18}}
.pill{{display:inline-block;padding:6px 10px;border-radius:999px;background:#d8f2ec}}
a{{color:#0b7770}}
</style></head><body><main><h1>Hausie AI</h1>
<p class='pill'>Mode: {escape(str(current['mode']))}</p>
<p>Local-first contextual learning for Home Assistant. Raw household state stays on this installation.</p>
<h2>Runtime</h2><ul><li>Home Assistant snapshot: {escape(str(current['home_assistant_connected']))}</li>
<li>Observations: {stats['observations']}</li><li>Decisions: {stats['decisions']}</li><li>Snapshots: {stats['snapshots']}</li></ul>
<p><a href='/docs'>Open API documentation</a> · <a href='/api/v1/status'>Status JSON</a></p>
</main></body></html>"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("hausie_ai.app.main:app", host="0.0.0.0", port=8099)

