from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from time import monotonic
from typing import Any

from .cloud import CloudClient
from .events import HomeAssistantEventStream
from .ha import HomeAssistantClient
from .inventory import InventoryBuilder
from .learning import Learner, current_context
from .safety import SafetyPolicy
from .settings import Settings
from .storage import Store

ADDON_VERSION = "0.4.0"
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecutedAction:
    decision_id: int
    action: dict[str, Any]
    expected_state: str
    executed_at: float


def action_from_state_change(old_state: dict[str, Any] | None, new_state: dict[str, Any] | None) -> dict[str, Any] | None:
    """Translate a meaningful low-risk state transition to a HA service action."""
    if not isinstance(old_state, dict) or not isinstance(new_state, dict):
        return None
    entity_id = str(new_state.get("entity_id", ""))
    old_value = str(old_state.get("state", "")).lower()
    new_value = str(new_state.get("state", "")).lower()
    if not entity_id or old_value == new_value or "." not in entity_id:
        return None
    domain = entity_id.split(".", 1)[0]

    if domain == "light" and new_value in {"on", "off"}:
        return {"domain": domain, "service": "turn_on" if new_value == "on" else "turn_off", "entity_id": entity_id}
    if domain == "cover" and new_value in {"open", "opening"}:
        return {"domain": domain, "service": "open_cover", "entity_id": entity_id}
    if domain == "cover" and new_value in {"closed", "closing"}:
        return {"domain": domain, "service": "close_cover", "entity_id": entity_id}
    if domain == "media_player":
        service_by_state = {
            "on": "turn_on",
            "off": "turn_off",
            "playing": "media_play",
            "paused": "media_pause",
        }
        service = service_by_state.get(new_value)
        if service:
            return {"domain": domain, "service": service, "entity_id": entity_id}
    return None


def changed_low_risk_actions(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    before = {str(item.get("entity_id", "")): item for item in previous}
    return [
        action
        for state in current
        if (action := action_from_state_change(before.get(str(state.get("entity_id", ""))), state)) is not None
    ]


def expected_state_for(action: dict[str, Any]) -> str:
    return {
        ("light", "turn_on"): "on",
        ("light", "turn_off"): "off",
        ("cover", "open_cover"): "open",
        ("cover", "close_cover"): "closed",
        ("media_player", "turn_on"): "on",
        ("media_player", "turn_off"): "off",
        ("media_player", "media_play"): "playing",
        ("media_player", "media_pause"): "paused",
    }.get((str(action.get("domain")), str(action.get("service"))), "")


class HausieAIService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings.data_dir)
        self.learner = Learner(self.store, settings.min_observations, settings.min_confidence)
        self.safety = SafetyPolicy()
        self.inventory_builder = InventoryBuilder(self.safety)
        self.ha = HomeAssistantClient(settings.ha_url, settings.ha_token)
        self.cloud = CloudClient(settings.cloud_url, settings.cloud_token, settings.device_id)
        self.events = HomeAssistantEventStream(settings.ha_url, settings.ha_token)
        self.last_states: list[dict[str, Any]] = []
        self.state_index: dict[str, dict[str, Any]] = {}
        self.registry_snapshot: dict[str, list[dict[str, Any]]] = {}
        self.inventory_profiles: list[dict[str, Any]] = self.store.inventory()
        self.inventory_by_entity: dict[str, dict[str, Any]] = {
            str(profile["entity_id"]): profile for profile in self.inventory_profiles
        }
        self._last_registry_sync = 0.0
        self.last_inventory_error: str | None = None
        self.last_cloud_error: str | None = None
        self.last_event_at: float | None = None
        self.last_event_source: str | None = None
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._last_decision_attempt = 0.0
        self._last_action_attempt: dict[str, float] = {}
        self._executed_actions: dict[str, ExecutedAction] = {}

    async def start(self) -> None:
        self._stop.clear()
        LOGGER.info(
            "STARTUP version=%s mode=%s events=%s poll_interval=%ss decision_interval=%ss",
            ADDON_VERSION,
            "auto-act" if self.settings.auto_act and not self.settings.dry_run else "observe-and-suggest",
            self.settings.event_stream_enabled,
            self.settings.poll_interval_seconds,
            self.settings.decision_interval_seconds,
        )
        self._tasks = [asyncio.create_task(self._collector_loop(), name="hausie-ai-collector")]
        if self.settings.event_stream_enabled:
            self._tasks.append(asyncio.create_task(self._event_stream_loop(), name="hausie-ai-events"))

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        LOGGER.info("SHUTDOWN complete")

    async def _collector_loop(self) -> None:
        while not self._stop.is_set():
            try:
                states = await asyncio.to_thread(self.ha.get_states)
                registry: dict[str, list[dict[str, Any]]] | None = None
                if not self.registry_snapshot or monotonic() - self._last_registry_sync > 3600:
                    try:
                        registry = await asyncio.to_thread(self.ha.get_inventory_registry)
                        self.registry_snapshot = registry
                        self._last_registry_sync = monotonic()
                        self.last_inventory_error = None
                        LOGGER.info(
                            "INVENTORY registry_sync areas=%s devices=%s entities=%s labels=%s",
                            len(registry.get("areas", [])), len(registry.get("devices", [])),
                            len(registry.get("entities", [])), len(registry.get("labels", [])),
                        )
                    except Exception as exc:
                        self.last_inventory_error = str(exc)
                        LOGGER.warning("INVENTORY registry_sync_failed error=%s", exc)
                result = await asyncio.to_thread(self.collect_states, states, registry)
                if self.settings.cloud_url and self.settings.device_id:
                    await asyncio.to_thread(self._send_cloud_heartbeat, result["stats"])
                self._maybe_scheduled_decision()
            except Exception as exc:
                LOGGER.warning("COLLECTOR failed error=%s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.poll_interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def _event_stream_loop(self) -> None:
        try:
            await asyncio.to_thread(self.events.run, self.handle_state_changed, self._stop.is_set)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.warning("EVENT_STREAM stopped error=%s", exc)

    def collect_states(
        self,
        states: list[dict[str, Any]],
        registry: dict[str, list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        if self.last_states and self.settings.learn_from_unknown:
            context = self.current_context()
            for action in changed_low_risk_actions(self.last_states, states):
                observation_id = self.learner.observe(context, action, "unknown")
                LOGGER.info("LEARN observation_id=%s source=unknown action=%s context=%s", observation_id, action, context)
        self.last_states = states
        self.state_index = {str(state.get("entity_id", "")): state for state in states if state.get("entity_id")}
        if registry is not None:
            self.registry_snapshot = registry
        self.inventory_profiles = self.inventory_builder.build(states, self.registry_snapshot)
        self.inventory_by_entity = {str(profile["entity_id"]): profile for profile in self.inventory_profiles}
        self.store.replace_inventory(self.inventory_profiles)
        self.store.add_snapshot(states)
        inventory_summary = self.inventory_builder.summary(self.inventory_profiles)
        LOGGER.info(
            "SNAPSHOT entities=%s observations=%s environmental=%s context_inputs=%s safe_targets=%s",
            len(states), self.store.stats()["observations"], inventory_summary["environmental_inputs"],
            inventory_summary["context_inputs"], inventory_summary["safe_action_targets"],
        )
        return {"states": len(states), "stats": self.store.stats(), "inventory": inventory_summary}

    def handle_state_changed(self, event: dict[str, Any]) -> None:
        data = event.get("data") or {}
        old_state = data.get("old_state")
        new_state = data.get("new_state")
        if not isinstance(new_state, dict):
            return
        entity_id = str(new_state.get("entity_id", ""))
        if not entity_id:
            return

        context_before = self.current_context()
        source = self._classify_source(event, new_state)
        action = action_from_state_change(old_state, new_state)
        old_value = str((old_state or {}).get("state", ""))
        new_value = str(new_state.get("state", ""))
        self.last_event_at = monotonic()
        self.last_event_source = source
        LOGGER.info("EVENT source=%s entity=%s state=%s->%s", source, entity_id, old_value, new_value)

        self._record_reversal_if_needed(entity_id, new_value, source)
        if action:
            self._learn_from_event(context_before, action, source)

        self.state_index[entity_id] = new_state
        self.last_states = list(self.state_index.values())
        profile = self.inventory_builder.build([new_state], self.registry_snapshot)[0]
        self.inventory_by_entity[entity_id] = profile
        self.inventory_profiles = sorted(self.inventory_by_entity.values(), key=lambda item: item["entity_id"])
        if old_value != new_value and (profile["is_environmental"] or profile["is_context_input"]):
            event_id = self.store.add_environmental_event(profile, old_value, new_value)
            LOGGER.info(
                "ENVIRONMENT event_id=%s entity=%s kind=%s area=%s state=%s->%s band=%s used_in_context=%s",
                event_id, entity_id, profile.get("environmental_kind"), profile.get("area_name") or "unassigned",
                old_value, new_value, profile.get("normalized_value"), profile["is_environmental"],
            )

        if self._is_context_trigger(profile):
            context_after = self.current_context()
            result = self.decide(context_after, execute=self.settings.auto_act)
            self._log_decision("context-event", result)

    def _classify_source(self, event: dict[str, Any], new_state: dict[str, Any]) -> str:
        entity_id = str(new_state.get("entity_id", ""))
        action = self._executed_actions.get(entity_id)
        if action and expected_state_for(action.action) == str(new_state.get("state", "")).lower():
            return "hausie_ai"
        context = new_state.get("context") or event.get("context") or {}
        if context.get("user_id"):
            return "user"
        if context.get("parent_id"):
            return "automation"
        return "unknown"

    def _learn_from_event(self, context: dict[str, Any], action: dict[str, Any], source: str) -> None:
        if source == "user" or self.settings.learn_from_unknown:
            observation_id = self.learner.observe(context, action, source)
            LOGGER.info("LEARN observation_id=%s source=%s action=%s context=%s", observation_id, source, action, context)
        else:
            LOGGER.info("LEARN skipped source=%s action=%s reason=explicit-user-events-only", source, action)

    @staticmethod
    def _is_context_trigger(profile: dict[str, Any]) -> bool:
        """Presence changes can ask for a decision; environmental changes are persisted.

        A temperature or humidity event should not create a decision on every
        small update.  The regular decision interval evaluates those features.
        """
        return bool(profile.get("is_context_input"))

    def _maybe_scheduled_decision(self) -> None:
        now = monotonic()
        if now - self._last_decision_attempt < self.settings.decision_interval_seconds or not self.last_states:
            return
        self._last_decision_attempt = now
        result = self.decide(self.current_context(), execute=self.settings.auto_act)
        self._log_decision("scheduled", result)

    def _action_is_needed(self, action: dict[str, Any]) -> bool:
        entity_id = str(action.get("entity_id", ""))
        current = self.state_index.get(entity_id)
        if not current:
            return True
        state = str(current.get("state", "")).lower()
        expected = expected_state_for(action)
        if expected == "open":
            return state not in {"open", "opening"}
        if expected == "closed":
            return state not in {"closed", "closing"}
        return not expected or state != expected

    def _is_cooling_down(self, action: dict[str, Any]) -> bool:
        entity_id = str(action.get("entity_id", ""))
        last_attempt = self._last_action_attempt.get(entity_id, 0.0)
        return monotonic() - last_attempt < self.settings.action_cooldown_seconds

    def _remember_execution(self, decision_id: int, action: dict[str, Any]) -> None:
        entity_id = str(action["entity_id"])
        self._last_action_attempt[entity_id] = monotonic()
        self._executed_actions[entity_id] = ExecutedAction(
            decision_id=decision_id,
            action=action,
            expected_state=expected_state_for(action),
            executed_at=monotonic(),
        )

    def _record_reversal_if_needed(self, entity_id: str, state: str, source: str) -> None:
        execution = self._executed_actions.get(entity_id)
        if not execution or source != "user":
            return
        if monotonic() - execution.executed_at > self.settings.reversal_window_seconds:
            return
        if state.lower() == execution.expected_state:
            return
        if self.store.add_decision_feedback(execution.decision_id, -1.0, "user_reversal"):
            LOGGER.warning(
                "FEEDBACK decision_id=%s reward=-1 source=user_reversal entity=%s",
                execution.decision_id,
                entity_id,
            )
        self._executed_actions.pop(entity_id, None)

    def _send_cloud_heartbeat(self, stats: dict[str, int]) -> None:
        try:
            self.cloud.heartbeat(
                {
                    "addon_version": ADDON_VERSION,
                    "status": "healthy",
                    "capabilities": ["event-stream", "state-collector", "local-inventory", "environment-context", "local-learner", "safety-policy"],
                    "aggregate": stats,
                }
            )
            self.last_cloud_error = None
            LOGGER.info("CLOUD heartbeat=sent aggregate=%s", stats)
        except Exception as exc:
            self.last_cloud_error = str(exc)
            LOGGER.warning("CLOUD heartbeat=failed error=%s", exc)

    def observe(self, context: dict[str, Any], action: dict[str, Any], source: str) -> int:
        if source != "user" and not self.settings.learn_from_unknown:
            raise ValueError("Only explicit user observations are learned by default.")
        safety = self.safety.evaluate(action)
        if not safety.allowed:
            raise ValueError(safety.reason)
        observation_id = self.learner.observe(context, action, source)
        LOGGER.info("LEARN observation_id=%s source=%s action=%s context=%s", observation_id, source, action, context)
        return observation_id

    def decide(self, context: dict[str, Any], execute: bool = False) -> dict[str, Any]:
        candidate, reason = self.learner.recommend(context)
        if candidate is None:
            decision_id = self.store.add_decision(context, None, 0.0, "DO_NOTHING", reason, False)
            return {"decision_id": decision_id, "decision": "DO_NOTHING", "action": None, "confidence": 0.0, "reason": reason, "executed": False}

        safety = self.safety.evaluate(candidate.action)
        if not safety.allowed:
            reason = f"Safety layer blocked the recommendation: {safety.reason}"
            decision_id = self.store.add_decision(context, candidate.action, candidate.confidence, "DO_NOTHING", reason, False)
            return {"decision_id": decision_id, "decision": "DO_NOTHING", "action": candidate.action, "confidence": candidate.confidence, "reason": reason, "executed": False}

        if not self._action_is_needed(candidate.action):
            reason = "Candidate action is already satisfied by the current Home Assistant state."
            decision_id = self.store.add_decision(context, candidate.action, candidate.confidence, "DO_NOTHING", reason, False)
            return {"decision_id": decision_id, "decision": "DO_NOTHING", "action": candidate.action, "confidence": candidate.confidence, "reason": reason, "executed": False}

        should_execute = execute and self.settings.auto_act and not self.settings.dry_run
        if should_execute and self._is_cooling_down(candidate.action):
            reason = f"Action cooldown is active for {candidate.action['entity_id']}."
            decision_id = self.store.add_decision(context, candidate.action, candidate.confidence, "SUGGEST_ACTION", reason, False)
            return {"decision_id": decision_id, "decision": "SUGGEST_ACTION", "action": candidate.action, "confidence": candidate.confidence, "reason": reason, "executed": False}

        if should_execute:
            self.ha.call_service(candidate.action)
        decision = "ACTION" if should_execute else "SUGGEST_ACTION"
        reason = f"{reason} {safety.reason}"
        decision_id = self.store.add_decision(context, candidate.action, candidate.confidence, decision, reason, should_execute)
        if should_execute:
            self._remember_execution(decision_id, candidate.action)
        return {
            "decision_id": decision_id,
            "decision": decision,
            "action": candidate.action,
            "confidence": candidate.confidence,
            "reason": reason,
            "executed": should_execute,
        }
    @staticmethod
    def _log_decision(trigger: str, result: dict[str, Any]) -> None:
        level = logging.INFO if result["decision"] != "DO_NOTHING" else logging.DEBUG
        LOGGER.log(
            level,
            "DECISION trigger=%s decision=%s confidence=%.2f action=%s reason=%s",
            trigger,
            result["decision"],
            result["confidence"],
            result["action"],
            result["reason"],
        )

    def add_decision_feedback(self, decision_id: int, reward: float, source: str) -> bool:
        accepted = self.store.add_decision_feedback(decision_id, reward, source)
        if accepted:
            LOGGER.info("FEEDBACK decision_id=%s reward=%s source=%s", decision_id, reward, source)
        return accepted

    def current_context(self) -> dict[str, Any]:
        return current_context(self.last_states, profiles=self.inventory_profiles)

    def inventory_view(self) -> dict[str, Any]:
        return {
            "summary": self.inventory_builder.summary(self.inventory_profiles),
            "registry_available": bool(self.registry_snapshot),
            "entities": self.inventory_profiles,
        }

    def status(self) -> dict[str, Any]:
        return {
            "service": "hausie-ai-local",
            "version": ADDON_VERSION,
            "mode": "auto-act" if self.settings.auto_act and not self.settings.dry_run else "observe-and-suggest",
            "home_assistant_connected": bool(self.last_states),
            "event_stream_enabled": self.settings.event_stream_enabled,
            "last_event_source": self.last_event_source,
            "inventory_registry_available": bool(self.registry_snapshot),
            "last_inventory_error": self.last_inventory_error,
            "current_context": self.current_context() if self.last_states else None,
            "settings": {
                "poll_interval_seconds": self.settings.poll_interval_seconds,
                "decision_interval_seconds": self.settings.decision_interval_seconds,
                "action_cooldown_seconds": self.settings.action_cooldown_seconds,
                "min_observations": self.settings.min_observations,
                "min_confidence": self.settings.min_confidence,
                "learn_from_unknown": self.settings.learn_from_unknown,
                "log_level": self.settings.log_level,
            },
            "stats": self.store.stats(),
            "last_cloud_error": self.last_cloud_error,
        }
