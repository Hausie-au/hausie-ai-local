from __future__ import annotations

from contextlib import asynccontextmanager
from html import escape
import logging
import re
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

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


@app.get("/api/v1/decisions")
def decisions(limit: int = 30) -> list[dict[str, Any]]:
    return service.store.recent_decisions(limit)


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
        raise HTTPException(status_code=404, detail="Decision with an action was not found.")
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
@app.get("/ui", response_class=HTMLResponse)
@app.get("/ui/", response_class=HTMLResponse)
def index(request: Request) -> str:
    ingress_path = request.headers.get("X-Ingress-Path", "").rstrip("/")
    if re.fullmatch(r"/api/hassio_ingress/[A-Za-z0-9_-]+", ingress_path):
        base_href = f"{ingress_path}/"
    else:
        base_href = "../" if request.url.path.endswith("/ui/") else "./"
    html = """<!doctype html>
<html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<base href='__BASE_HREF__'>
<title>Hausie AI</title><style>
:root{color-scheme:light}body{font:15px system-ui,sans-serif;max-width:1200px;margin:28px auto;padding:0 18px;background:#f6f5ef;color:#173b3f}main{background:#fff;border-radius:18px;padding:26px;box-shadow:0 8px 30px #173b3f18}.top{display:flex;justify-content:space-between;gap:20px;align-items:start}.pill{display:inline-block;padding:6px 10px;border-radius:999px;background:#d8f2ec}.warn{background:#fff0c7}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:12px}.card{background:#f4faf8;padding:14px;border-radius:12px}.num{font-size:25px;font-weight:700}section{margin-top:26px}pre{white-space:pre-wrap;background:#f4f4f0;padding:14px;border-radius:12px;overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;border-bottom:1px solid #e5e7e6;padding:8px;vertical-align:top}.scroll{overflow:auto}.tag{display:inline-block;background:#e8eeee;padding:2px 6px;border-radius:8px;margin:1px}a{color:#0b7770}
</style></head><body><main><div class='top'><div><h1>Hausie AI</h1><p>Local inventory, environmental context and explainable learning.</p></div><p id='mode' class='pill'>Loading...</p></div>
<div class='grid' id='summary'></div>
<section><h2>Context used now</h2><p>Time and occupancy are always included. Environmental readings below are normalised into stable bands, so the learner does not treat every decimal change as a new situation.</p><pre id='context'>Waiting for first Home Assistant snapshot...</pre></section>
<section><h2>What Hausie AI can see</h2><p><span class='tag'>Environmental input</span> contributes to context. <span class='tag'>Context input</span> detects occupancy or presence. <span class='tag'>Safe action target</span> is eligible only for a learned suggestion; <span class='tag'>Blocked action target</span> remains visible but cannot be controlled.</p><div class='scroll'><table><thead><tr><th>Entity</th><th>Area</th><th>Current value</th><th>Classification</th><th>Safety</th></tr></thead><tbody id='inventory'></tbody></table></div></section>
<section><h2>Environmental changes recorded locally</h2><div class='scroll'><table><thead><tr><th>When</th><th>Entity</th><th>Area</th><th>Change</th><th>Context band</th></tr></thead><tbody id='events'></tbody></table></div></section>
<section><h2>Recent decisions</h2><div class='scroll'><table><thead><tr><th>When</th><th>Decision</th><th>Action</th><th>Why</th></tr></thead><tbody id='decisions'></tbody></table></div></section>
<p><a href='docs'>Open API documentation</a> · <a href='api/v1/status'>Status JSON</a></p></main>
<script>
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const tags=roles=>(roles||[]).map(role=>`<span class='tag'>${esc(role.replaceAll('_',' '))}</span>`).join('');
async function load(){try{const [status,inventory,events,decisions]=await Promise.all([fetch('api/v1/status').then(r=>r.json()),fetch('api/v1/inventory').then(r=>r.json()),fetch('api/v1/environment/events?limit=25').then(r=>r.json()),fetch('api/v1/decisions?limit=15').then(r=>r.json())]);document.querySelector('#mode').textContent=`Mode: ${status.mode}`;document.querySelector('#mode').className=`pill ${status.mode==='auto-act'?'warn':''}`;const summary={...inventory.summary,observations:status.stats.observations,environmental_events:status.stats.environmental_events};document.querySelector('#summary').innerHTML=Object.entries(summary).map(([key,value])=>`<div class='card'><div class='num'>${esc(value)}</div><div>${esc(key.replaceAll('_',' '))}</div></div>`).join('');document.querySelector('#context').textContent=JSON.stringify(status.current_context||{status:'Waiting for first snapshot'},null,2);document.querySelector('#inventory').innerHTML=inventory.entities.map(item=>`<tr><td><strong>${esc(item.name)}</strong><br><small>${esc(item.entity_id)}</small></td><td>${esc(item.area_name||'Unassigned')}</td><td>${esc(item.state)} ${esc(item.unit||'')}${item.normalized_value?`<br><small>band: ${esc(item.normalized_value)}</small>`:''}</td><td>${tags(item.roles)}</td><td>${esc(item.safety.reason)}</td></tr>`).join('');document.querySelector('#events').innerHTML=events.map(item=>`<tr><td>${esc(item.created_at)}</td><td>${esc(item.entity_id)}</td><td>${esc(item.area_name||'Unassigned')}</td><td>${esc(item.old_state)} → ${esc(item.new_state)}</td><td>${esc(item.normalized_value||'')}</td></tr>`).join('')||'<tr><td colspan="5">No environmental or occupancy change recorded yet.</td></tr>';document.querySelector('#decisions').innerHTML=decisions.map(item=>`<tr><td>${esc(item.created_at)}</td><td>${esc(item.decision)} (${esc(Number(item.confidence).toFixed(2))})</td><td>${esc(item.action?`${item.action.domain}.${item.action.service} ${item.action.entity_id}`:'')}</td><td>${esc(item.reason)}</td></tr>`).join('')||'<tr><td colspan="4">No decision recorded yet.</td></tr>';}catch(error){document.querySelector('#mode').textContent=`Panel error: ${error}`;}}
load();setInterval(load,5000);
</script></body></html>"""
    return html.replace("__BASE_HREF__", escape(base_href, quote=True))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("hausie_ai.app.main:app", host="0.0.0.0", port=8099)
