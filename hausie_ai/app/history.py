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

from .buttons import (BUTTON_EVENT_IDS, BUTTON_HELPER_IDS, DESTINATIONS,
                      attribute_effect, matching_press, resolve_press)
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
        available = {str(item["entity_id"]) for item in profiles}
        self.button_event_ids = sorted(BUTTON_EVENT_IDS & available)
        self.button_helper_ids = sorted(BUTTON_HELPER_IDS & available)
        self.zone = zone
        self.days = days
        self.progress = progress or (lambda _: None)
        self._states: dict[str, str] = {}
        self._attributes: dict[str, dict[str, Any]] = {}
        self._recent_triggers: dict[str, list[tuple[datetime, dict[str, Any]]]] = defaultdict(list)
        self._recent_signals: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
        self._recent_presses: list[dict[str, Any]] = []
        self._seen_presses: set[tuple[str, str]] = set()

    def run(self, cutoff: datetime | None = None) -> dict[str, Any]:
        self._states.clear()
        self._attributes.clear()
        self._recent_triggers.clear()
        self._recent_signals.clear()
        self._recent_presses.clear()
        self._seen_presses.clear()
        cutoff = (cutoff or datetime.now(timezone.utc)).astimezone(timezone.utc)
        start = cutoff - timedelta(days=self.days)
        ids = sorted(self.profiles)
        signature = sha256(json.dumps(["button-learning-v1", self.button_event_ids, self.button_helper_ids, [
            (entity_id, self.profiles[entity_id].get("area_id"),
             self.profiles[entity_id].get("environmental_kind"),
             self.profiles[entity_id].get("device_class")) for entity_id in ids
        ]], sort_keys=True).encode()).hexdigest()
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

        totals = {"actions": 0, "seen_actions": 0, "environmental": 0,
                  "experiences": 0, "transitions": 0, "timings": 0,
                  "button_presses": 0, "button_effects": 0}
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

        button_changes: list[tuple[datetime, str, str, dict[str, Any]]] = []
        for group in _chunks(self.button_event_ids):
            for series in self.ha.get_history(start, end, group, include_attributes=True):
                if not isinstance(series, list):
                    continue
                for item in series:
                    if isinstance(item, dict):
                        at = parse_time(item.get("last_changed") or item.get("last_updated"))
                        if at and item.get("entity_id") in self.button_event_ids and item.get("state") is not None:
                            button_changes.append((at, str(item["entity_id"]), str(item["state"]),
                                                   item.get("attributes") or {}))
        has_press = (any(at >= start and attributes.get("event_type")
                         for at, _, _, attributes in button_changes) or
                     any(0 <= (start - datetime.fromisoformat(press["pressed_at"])).total_seconds() <= 8
                         for press in self._recent_presses))
        changes: list[tuple[datetime, str, str, dict[str, Any]]] = []
        attribute_targets = {entity_id for targets in DESTINATIONS.values() for entity_id in targets}
        for group in _chunks([entity_id for entity_id in ids if not has_press or entity_id not in attribute_targets]) + _chunks(
                [entity_id for entity_id in ids if has_press and entity_id in attribute_targets]):
            with_attributes = has_press and group[0] in attribute_targets
            for series in self.ha.get_history(start, end, group, include_attributes=with_attributes):
                if not isinstance(series, list):
                    continue
                fallback = str(series[0].get("entity_id") or "") if series and isinstance(series[0], dict) else ""
                for item in series:
                    if not isinstance(item, dict):
                        continue
                    entity_id = str(item.get("entity_id") or fallback)
                    at = parse_time((item.get("last_updated") or item.get("last_changed")) if with_attributes
                                    else (item.get("last_changed") or item.get("last_updated")))
                    if entity_id in self.profiles and at and item.get("state") is not None and at < end:
                        changes.append((at, entity_id, str(item["state"]),
                                        item.get("attributes") or {} if with_attributes else {}))
            if len(changes) > 300_000:
                raise ValueError("Recorder returned over 300,000 state rows in one day; history import paused to protect this host.")
        for group in _chunks(self.button_helper_ids if has_press else []):
            for series in self.ha.get_history(start, end, group):
                if not isinstance(series, list):
                    continue
                for item in series:
                    if isinstance(item, dict):
                        at = parse_time(item.get("last_changed") or item.get("last_updated"))
                        if at and item.get("entity_id") in self.button_helper_ids and item.get("state") is not None:
                            changes.append((at, str(item["entity_id"]), str(item["state"]), {}))
        changes.extend(button_changes)
        if len(changes) > 300_000:
            raise ValueError("Recorder returned over 300,000 state rows in one day; history import paused to protect this host.")
        changes.sort(key=lambda row: (row[0], 0 if row[1] in self.button_helper_ids else
                                      1 if row[1] in self.button_event_ids else 2, row[1]))
        records: list[dict[str, Any]] = []
        helper_states: dict[str, str] = {}
        for at, entity_id, state, attributes in changes:
            if entity_id in self.button_helper_ids:
                helper_states[entity_id] = state
                continue
            if entity_id in self.button_event_ids:
                if at < start:
                    continue
                press = resolve_press(entity_id, str(attributes.get("event_type") or ""), attributes,
                                      helper_states, at, self._context(at, "", ""))
                if press and (entity_id, press["pressed_at"]) not in self._seen_presses:
                    self._seen_presses.add((entity_id, press["pressed_at"]))
                    records.append({"button_press": press})
                    self._recent_presses.append(press)
                continue
            self._recent_presses = [press for press in self._recent_presses
                                    if 0 <= (at - datetime.fromisoformat(press["pressed_at"])).total_seconds() <= 8]
            old = self._states.get(entity_id)
            old_attributes = self._attributes.get(entity_id, {})
            self._states[entity_id] = state
            self._attributes[entity_id] = attributes
            button_attribute_action = attribute_effect(
                {"entity_id": entity_id, "state": old, "attributes": old_attributes},
                {"entity_id": entity_id, "state": state, "attributes": attributes},
            ) if old is not None else None
            if at < start or old is None or (old == state and not button_attribute_action):
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
                action = action_from_state_change(old_state, new_state) or button_attribute_action
                if (old.lower() not in {"unknown", "unavailable", "none"} and action and
                        self.safety.evaluate(action).allowed):
                    source = (self._source_match(logbook[entity_id], at, state)
                              if action is not button_attribute_action else "unknown")
                    button_press = (matching_press(self._recent_presses, at, action,
                                                   {"entity_id": entity_id, "state": old,
                                                    "attributes": old_attributes},
                                                   {"entity_id": entity_id, "state": state,
                                                    "attributes": attributes})
                                    if source in {"automation", "unknown"} else None)
                    if action is button_attribute_action and not button_press:
                        continue  # attribute-only changes are action examples only with a verified press
                    if button_press:
                        source = "physical_button"
                        item["button_press_ref"] = {
                            "event_entity_id": button_press["event_entity_id"],
                            "pressed_at": button_press["pressed_at"],
                        }
                    item["observed_action"] = action
                    item["observed_source"] = source
                if item.get("observed_source") in {"user", "physical_button"}:
                    item["action"] = action
                    item["area"] = area
                    item["context"] = button_press["context"] if button_press else self._context(at, entity_id, old)
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
