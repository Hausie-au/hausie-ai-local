"""One-time, bounded Recorder backfill before online learning begins.

History REST supplies states but not actor attribution. Only matching Logbook
state entries with a user ID become preference observations. Nothing here calls
Home Assistant services or scores retrospective predictions as if they were
made before the outcome.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from typing import Any, Callable

from .experiments import RELEVANT_KINDS
from .ha import HomeAssistantClient
from .inventory import normalize_environmental_value
from .learning import current_context
from .safety import SafetyPolicy
from .service import action_from_state_change
from .storage import Store


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _chunks(items: list[str], size: int = 40) -> list[list[str]]:
    return [items[index:index + size] for index in range(0, len(items), size)]


def _signal(trigger: dict[str, Any]) -> str:
    return ":".join(str(trigger[key]) for key in ("kind", "direction", "new_band"))


class HistoryBootstrap:
    def __init__(self, ha: HomeAssistantClient, store: Store, safety: SafetyPolicy,
                 profiles: list[dict[str, Any]], zone: Any, days: int,
                 progress: Callable[[dict[str, Any]], None] | None = None):
        self.ha = ha
        self.store = store
        self.safety = safety
        self.profiles = {str(item["entity_id"]): item for item in profiles
                         if item.get("is_environmental") or item.get("is_context_input") or
                         item.get("safety", {}).get("classification") == "safe_action_target"}
        self.zone = zone
        self.days = days
        self.progress = progress or (lambda _: None)
        self._states: dict[str, str] = {}
        self._recent_triggers: dict[str, list[tuple[datetime, dict[str, Any]]]] = defaultdict(list)
        self._recent_signals: dict[str, list[tuple[datetime, str]]] = defaultdict(list)

    def run(self, cutoff: datetime | None = None) -> dict[str, Any]:
        self._states.clear()
        self._recent_triggers.clear()
        self._recent_signals.clear()
        cutoff = (cutoff or datetime.now(timezone.utc)).astimezone(timezone.utc)
        start = cutoff - timedelta(days=self.days)
        ids = sorted(self.profiles)
        signature = sha256(json.dumps([
            (entity_id, self.profiles[entity_id].get("area_id"),
             self.profiles[entity_id].get("environmental_kind"),
             self.profiles[entity_id].get("device_class")) for entity_id in ids
        ], sort_keys=True).encode()).hexdigest()
        prior = self.store.history_bootstrap_state()
        same_coverage = bool(prior and prior["entity_signature"] == signature and
                             prior["coverage_start"] <= start.isoformat())
        if same_coverage and prior["coverage_end"] >= cutoff.isoformat():
            result = {"state": "complete", "already_imported": True, "days": self.days,
                      "actions": prior["imported_actions"], "environmental": prior["imported_environmental"],
                      "seen_actions": self.store.historical_action_count(),
                      "completed_at": prior["completed_at"]}
            self.progress(result)
            return result

        # Recover events missed while the app was stopped. Overlap the previous
        # endpoint slightly because Recorder and Logbook can commit later than
        # the live event; the unique import keys make this safe to repeat.
        if same_coverage:
            previous_end = parse_time(prior["coverage_end"])
            if previous_end:
                start = max(start, previous_end - timedelta(minutes=5))

        totals = {"actions": 0, "seen_actions": 0, "environmental": 0, "experiences": 0, "transitions": 0, "timings": 0}
        window_start = start
        processed_days = 0
        while window_start < cutoff:
            window_end = min(window_start + timedelta(days=1), cutoff)
            self.progress({"state": "running", "days": self.days,
                           "processed_days": processed_days, **totals})
            records = self._load_window(window_start, window_end)
            for offset in range(0, len(records), 1000):
                counts = self.store.import_history_batch(records[offset:offset + 1000])
                for key in totals:
                    totals[key] += counts[key]
            processed_days += 1
            window_start = window_end
        stored_start = min(prior["coverage_start"], start.isoformat()) if same_coverage else start.isoformat()
        self.store.mark_history_bootstrap(stored_start, cutoff.isoformat(), signature,
                                          totals["actions"], totals["environmental"])
        persisted = self.store.history_bootstrap_state() or {}
        result = {"state": "complete", "already_imported": False, "days": self.days,
                  "processed_days": processed_days, **totals,
                  "new_actions": totals["actions"], "new_environmental": totals["environmental"],
                  "actions": persisted.get("imported_actions", totals["actions"]),
                  "seen_actions": self.store.historical_action_count(),
                  "environmental": persisted.get("imported_environmental", totals["environmental"]),
                  "completed_at": datetime.now(timezone.utc).isoformat()}
        self.progress(result)
        return result

    def _load_window(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        ids = sorted(self.profiles)
        action_ids = [entity_id for entity_id in ids if
                      self.profiles[entity_id].get("safety", {}).get("classification") == "safe_action_target"]
        logbook: dict[str, list[tuple[datetime, str, str]]] = defaultdict(list)
        for group in _chunks(action_ids):
            for entry in self.ha.get_logbook(start, end, group):
                entity_id = str(entry.get("entity_id") or "")
                at = parse_time(entry.get("when"))
                if (entity_id not in self.profiles or not at or not start <= at < end or
                        entry.get("state") is None):
                    continue
                domain = str(entry.get("context_domain") or "")
                if domain in {"automation", "script"} or entry.get("context_event_type") in {"automation_triggered", "script_started"}:
                    source = "automation"
                elif entry.get("context_user_id") and entry.get("context_event_type") in (None, "call_service"):
                    source = "user"
                else:
                    source = "unknown"
                logbook[entity_id].append((at, str(entry["state"]).lower(), source))

        changes: list[tuple[datetime, str, str]] = []
        for group in _chunks(ids):
            for series in self.ha.get_history(start, end, group):
                if not isinstance(series, list):
                    continue
                fallback = str(series[0].get("entity_id") or "") if series and isinstance(series[0], dict) else ""
                for item in series:
                    if not isinstance(item, dict):
                        continue
                    entity_id = str(item.get("entity_id") or fallback)
                    at = parse_time(item.get("last_changed") or item.get("last_updated"))
                    if entity_id in self.profiles and at and item.get("state") is not None and at < end:
                        changes.append((at, entity_id, str(item["state"])))
            if len(changes) > 300_000:
                raise ValueError("Recorder returned over 300,000 state rows in one day; history import paused to protect this host.")
        changes.sort(key=lambda row: (row[0], row[1]))
        records: list[dict[str, Any]] = []
        for at, entity_id, state in changes:
            old = self._states.get(entity_id)
            self._states[entity_id] = state
            if at < start or old is None or old == state:
                continue  # start-of-window baseline, not an observed transition
            profile = self.profiles[entity_id]
            kind = str(profile.get("environmental_kind") or "")
            area = str(profile.get("area_id") or profile.get("area_name") or "")
            item: dict[str, Any] = {"changed_at": at.isoformat(), "entity_id": entity_id,
                                    "old_state": old, "new_state": state, "profile": profile}
            if profile.get("is_environmental") or profile.get("is_context_input"):
                item["environmental"] = True
                item["normalized_value"] = normalize_environmental_value(kind, state)
                try:
                    numeric = float(state)
                    if math.isfinite(numeric):
                        item["numeric_value"] = numeric
                        item["local_hour"] = at.astimezone(self.zone).hour
                except (TypeError, ValueError):
                    pass
                trigger = self._trigger(profile, old, state)
                if trigger:
                    signal = _signal(trigger)
                    previous = self._recent_signals[area]
                    previous[:] = [(time, value) for time, value in previous if at - time <= timedelta(hours=2)]
                    trigger["sequence"] = [previous[-1][1], signal] if previous else [signal]
                    if previous:
                        item["transition"] = {"area": area, "previous_signal": previous[-2][1] if len(previous) > 1 else None,
                                              "current_signal": previous[-1][1], "next_signal": signal}
                    previous.append((at, signal))
                    self._recent_triggers[area].append((at, trigger))
                    self._recent_triggers[area] = [(time, value) for time, value in self._recent_triggers[area]
                                                    if at - time <= timedelta(minutes=10)]
            if profile.get("safety", {}).get("classification") == "safe_action_target":
                old_state = {"entity_id": entity_id, "state": old}
                new_state = {"entity_id": entity_id, "state": state}
                action = action_from_state_change(old_state, new_state)
                if (old.lower() not in {"unknown", "unavailable", "none"} and action and
                        self.safety.evaluate(action).allowed):
                    source = self._source_match(logbook[entity_id], at, state)
                    item["observed_action"] = action
                    item["observed_source"] = source
                if item.get("observed_source") == "user":
                    item["action"] = action
                    item["area"] = area
                    item["context"] = self._context(at, entity_id, old)
                    if area:
                        for index in range(len(self._recent_triggers[area]) - 1, -1, -1):
                            time, trigger = self._recent_triggers[area][index]
                            if (at - time <= timedelta(minutes=10) and
                                    trigger["kind"] in RELEVANT_KINDS.get(action["domain"], set())):
                                item["trigger"] = trigger
                                item["trigger_at"] = time.isoformat()
                                del self._recent_triggers[area][index]
                                break
            records.append(item)
        return records

    @staticmethod
    def _source_match(entries: list[tuple[datetime, str, str]], at: datetime, state: str) -> str:
        matches = [(abs((when - at).total_seconds()), source) for when, value, source in entries
                   if value == state.lower() and abs((when - at).total_seconds()) <= 2]
        if not matches:
            return "unknown"
        return min(matches)[1]

    def _context(self, at: datetime, action_entity: str, old_state: str) -> dict[str, Any]:
        states = []
        profiles = []
        for entity_id, state in self._states.items():
            if entity_id == action_entity:
                state = old_state
            profile = self.profiles[entity_id]
            states.append({"entity_id": entity_id, "state": state,
                           "attributes": {"device_class": profile.get("device_class")}})
            if profile.get("is_environmental"):
                historical = dict(profile, state=state,
                                  normalized_value=normalize_environmental_value(str(profile.get("environmental_kind") or ""), state))
                profiles.append(historical)
        return current_context(states, now=at.astimezone(self.zone), profiles=profiles)

    @staticmethod
    def _trigger(profile: dict[str, Any], old: str, new: str) -> dict[str, Any] | None:
        kind = str(profile.get("environmental_kind") or "")
        area = str(profile.get("area_id") or profile.get("area_name") or "")
        if not kind or not area or old.lower() in {"unknown", "unavailable"} or new.lower() in {"unknown", "unavailable"}:
            return None
        old_band = normalize_environmental_value(kind, old)
        new_band = normalize_environmental_value(kind, new)
        delta = None
        try:
            delta = float(new) - float(old)
        except (TypeError, ValueError):
            pass
        thresholds = {"temperature": 0.5, "humidity": 5.0, "moisture": 5.0, "illuminance": 20.0}
        if old_band == new_band and (delta is None or abs(delta) < thresholds.get(kind, float("inf"))):
            return None
        direction = "rising" if delta is not None and delta > 0 else "falling" if delta is not None and delta < 0 else "changed"
        return {"entity_id": profile["entity_id"], "area": area,
                "area_name": profile.get("area_name") or area, "kind": kind,
                "direction": direction, "old_band": old_band, "new_band": new_band,
                "old_value": old, "new_value": new}
