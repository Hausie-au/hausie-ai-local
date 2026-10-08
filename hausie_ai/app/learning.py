from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .storage import Store


def context_signature(context: dict[str, Any]) -> dict[str, Any]:
    signature = {
        "weekday": int(context.get("weekday", 0)),
        "hour_bucket": int(context.get("hour_bucket", 0)),
        "occupancy": str(context.get("occupancy", "unknown")),
    }
    environment = context.get("environment")
    if isinstance(environment, dict):
        signature["environment"] = {str(key): str(environment[key]) for key in sorted(environment)[:24]}
    return signature


@dataclass(frozen=True)
class Candidate:
    action: dict[str, Any]
    total: int
    positive: int
    confidence: float
    average_reward: float
    feedback_count: int
    feedback_average: float


def current_context(
    states: list[dict[str, Any]],
    now: datetime | None = None,
    profiles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
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
    context = {
        "weekday": now.weekday(),
        "month": now.month,
        "hour_bucket": now.hour * 4 + now.minute // 15,
        "occupancy": "occupied" if occupied else "unknown",
    }
    if profiles is not None:
        from .inventory import environmental_features

        context["environment"] = environmental_features(profiles)
    return context


class Learner:
    def __init__(self, store: Store, min_observations: int, min_confidence: float):
        self.store = store
        self.min_observations = min_observations
        self.min_confidence = min_confidence

    def observe(self, context: dict[str, Any], action: dict[str, Any], source: str,
                created_at: str | None = None) -> int:
        return self.store.add_observation(context_signature(context), action, source, created_at)

    def recommend(self, context: dict[str, Any]) -> tuple[Candidate | None, str]:
        rows = self.store.candidate_rows(context_signature(context))
        if not rows:
            return None, "No learned action for this context."
        row = rows[0]
        total = int(row["total"])
        positive = int(row["positive"] or 0)
        base_confidence = positive / total if total else 0.0
        feedback_count = int(row["feedback_count"] or 0)
        feedback_average = float(row["feedback_average"] or 0.0)
        confidence = min(1.0, max(0.0, base_confidence + (feedback_average * 0.2 if feedback_count else 0.0)))
        candidate = Candidate(
            action=json.loads(row["action_json"]),
            total=total,
            positive=positive,
            confidence=confidence,
            average_reward=float(row["average_reward"] or 0.0),
            feedback_count=feedback_count,
            feedback_average=feedback_average,
        )
        if total < self.min_observations:
            return None, f"Need {self.min_observations} observations; have {total}."
        if confidence < self.min_confidence:
            return None, f"Confidence {confidence:.2f} is below {self.min_confidence:.2f}."
        if candidate.average_reward <= 0:
            return None, "The learned feedback is negative for this action."
        if candidate.feedback_count and candidate.feedback_average <= -0.5:
            return None, "Recent feedback rejected this learned action."
        return candidate, "Learned from repeated user observations."

