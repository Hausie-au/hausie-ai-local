from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .storage import Store


def context_signature(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "weekday": int(context.get("weekday", 0)),
        "hour_bucket": int(context.get("hour_bucket", 0)),
        "occupancy": str(context.get("occupancy", "unknown")),
    }


@dataclass(frozen=True)
class Candidate:
    action: dict[str, Any]
    total: int
    positive: int
    confidence: float
    average_reward: float


def current_context(states: list[dict[str, Any]], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now().astimezone()
    occupied = False
    for item in states:
        entity_id = str(item.get("entity_id", ""))
        state = str(item.get("state", "")).lower()
        attributes = item.get("attributes") or {}
        device_class = str(attributes.get("device_class", "")).lower()
        if entity_id.startswith(("person.", "device_tracker.")) and state == "home":
            occupied = True
        if entity_id.startswith("binary_sensor.") and device_class in {"motion", "occupancy", "presence"} and state == "on":
            occupied = True
    return {
        "weekday": now.weekday(),
        "hour_bucket": now.hour * 4 + now.minute // 15,
        "occupancy": "occupied" if occupied else "unknown",
    }


class Learner:
    def __init__(self, store: Store, min_observations: int, min_confidence: float):
        self.store = store
        self.min_observations = min_observations
        self.min_confidence = min_confidence

    def observe(self, context: dict[str, Any], action: dict[str, Any], source: str) -> int:
        return self.store.add_observation(context_signature(context), action, source)

    def recommend(self, context: dict[str, Any]) -> tuple[Candidate | None, str]:
        rows = self.store.candidate_rows(context_signature(context))
        if not rows:
            return None, "No learned action for this context."
        row = rows[0]
        total = int(row["total"])
        positive = int(row["positive"] or 0)
        confidence = positive / total if total else 0.0
        candidate = Candidate(
            action=json.loads(row["action_json"]),
            total=total,
            positive=positive,
            confidence=confidence,
            average_reward=float(row["average_reward"] or 0.0),
        )
        if total < self.min_observations:
            return None, f"Need {self.min_observations} observations; have {total}."
        if confidence < self.min_confidence:
            return None, f"Confidence {confidence:.2f} is below {self.min_confidence:.2f}."
        if candidate.average_reward <= 0:
            return None, "The learned feedback is negative for this action."
        return candidate, "Learned from repeated user observations."

