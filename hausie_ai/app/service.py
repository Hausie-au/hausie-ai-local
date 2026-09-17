from __future__ import annotations

import asyncio
import logging
from typing import Any

from .cloud import CloudClient
from .ha import HomeAssistantClient
from .learning import Learner, current_context
from .safety import SafetyPolicy
from .settings import Settings
from .storage import Store

LOGGER = logging.getLogger(__name__)


def changed_low_risk_actions(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    before = {item.get("entity_id"): item for item in previous}
    actions: list[dict[str, Any]] = []
    for item in current:
        entity_id = str(item.get("entity_id", ""))
        old_state = str((before.get(entity_id) or {}).get("state", ""))
        new_state = str(item.get("state", ""))
        domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
        action: dict[str, Any] | None = None
        if domain == "light" and old_state != new_state:
            action = {"domain": "light", "service": "turn_on" if new_state == "on" else "turn_off", "entity_id": entity_id}
        elif domain == "cover" and old_state != new_state:
            if new_state in {"open", "opening"}:
                service = "open_cover"
            elif new_state in {"closed", "closing"}:
                service = "close_cover"
            else:
                service = "stop_cover"
            action = {"domain": "cover", "service": service, "entity_id": entity_id}
        elif domain == "media_player" and old_state != new_state:
            service_by_state = {"playing": "media_play", "paused": "media_pause", "off": "turn_off", "idle": "turn_on"}
            if new_state in service_by_state:
                action = {"domain": "media_player", "service": service_by_state[new_state], "entity_id": entity_id}
        if action:
            actions.append(action)
    return actions


class HausieAIService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings.data_dir)
        self.learner = Learner(self.store, settings.min_observations, settings.min_confidence)
        self.safety = SafetyPolicy()
        self.ha = HomeAssistantClient(settings.ha_url, settings.ha_token)
        self.cloud = CloudClient(settings.cloud_url, settings.cloud_token, settings.device_id)
        self.last_states: list[dict[str, Any]] = []
        self.last_cloud_error: str | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self._stop.clear()
        asyncio.create_task(self._collector_loop(), name="hausie-ai-collector")

    async def stop(self) -> None:
        self._stop.set()

    async def _collector_loop(self) -> None:
        while not self._stop.is_set():
            try:
                states = await asyncio.to_thread(self.ha.get_states)
                result = await asyncio.to_thread(self.collect_states, states)
                if self.settings.cloud_url and self.settings.device_id:
                    await asyncio.to_thread(self._send_cloud_heartbeat, result["stats"])
            except Exception as exc:
                LOGGER.warning("Home Assistant collection failed: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.poll_interval_seconds)
            except asyncio.TimeoutError:
                pass

    def collect_states(self, states: list[dict[str, Any]]) -> dict[str, Any]:
        if self.last_states:
            for action in changed_low_risk_actions(self.last_states, states):
                if self.settings.learn_from_unknown:
                    self.learner.observe(current_context(self.last_states), action, "unknown")
        self.last_states = states
        self.store.add_snapshot(states)
        return {"states": len(states), "stats": self.store.stats()}

    def _send_cloud_heartbeat(self, stats: dict[str, int]) -> None:
        try:
            self.cloud.heartbeat(
                {
                    "addon_version": "0.1.0",
                    "status": "healthy",
                    "capabilities": ["state-collector", "local-learner", "safety-policy"],
                    "aggregate": stats,
                }
            )
            self.last_cloud_error = None
        except Exception as exc:
            self.last_cloud_error = str(exc)
            LOGGER.warning("Hausie AI Cloud heartbeat failed: %s", exc)

    def observe(self, context: dict[str, Any], action: dict[str, Any], source: str) -> int:
        if source != "user" and not self.settings.learn_from_unknown:
            raise ValueError("Only explicit user observations are learned by default.")
        safety = self.safety.evaluate(action)
        if not safety.allowed:
            raise ValueError(safety.reason)
        return self.learner.observe(context, action, source)

    def decide(self, context: dict[str, Any], execute: bool = False) -> dict[str, Any]:
        candidate, reason = self.learner.recommend(context)
        if candidate is None:
            decision = "DO_NOTHING"
            self.store.add_decision(context, None, 0.0, decision, reason, False)
            return {"decision": decision, "action": None, "confidence": 0.0, "reason": reason, "executed": False}

        safety = self.safety.evaluate(candidate.action)
        if not safety.allowed:
            reason = f"Safety layer blocked the recommendation: {safety.reason}"
            self.store.add_decision(context, candidate.action, candidate.confidence, "DO_NOTHING", reason, False)
            return {"decision": "DO_NOTHING", "action": candidate.action, "confidence": candidate.confidence, "reason": reason, "executed": False}

        should_execute = execute and self.settings.auto_act and not self.settings.dry_run
        if should_execute:
            self.ha.call_service(candidate.action)
        decision = "ACTION" if should_execute else "SUGGEST_ACTION"
        reason = f"{reason} {safety.reason}"
        self.store.add_decision(context, candidate.action, candidate.confidence, decision, reason, should_execute)
        return {
            "decision": decision,
            "action": candidate.action,
            "confidence": candidate.confidence,
            "reason": reason,
            "executed": should_execute,
        }

    def status(self) -> dict[str, Any]:
        return {
            "service": "hausie-ai-local",
            "mode": "auto-act" if self.settings.auto_act and not self.settings.dry_run else "observe-and-suggest",
            "home_assistant_connected": bool(self.last_states),
            "settings": {
                "poll_interval_seconds": self.settings.poll_interval_seconds,
                "min_observations": self.settings.min_observations,
                "min_confidence": self.settings.min_confidence,
                "learn_from_unknown": self.settings.learn_from_unknown,
            },
            "stats": self.store.stats(),
            "last_cloud_error": self.last_cloud_error,
        }
