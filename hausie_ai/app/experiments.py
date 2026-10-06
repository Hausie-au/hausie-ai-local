"""Small, explainable shadow learners. Predictions never call Home Assistant services."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from .learning import Learner
from .storage import Store


METHODS = ("exact", "event", "adaptive_seasonal")
RELEVANT_KINDS = {
    "light": {"illuminance", "motion", "occupancy", "presence", "opening"},
    "cover": {"illuminance", "temperature", "opening"},
    "media_player": {"motion", "occupancy", "presence"},
}


def season(month: int) -> str:
    """Australian meteorological seasons, based on Home Assistant local time."""
    return ("summer", "autumn", "winter", "spring")[(month % 12) // 3]


def _empty(reason: str) -> dict[str, Any]:
    return {"action": None, "confidence": 0.0, "reason": reason}


class ShadowLearners:
    def __init__(self, store: Store, baseline: Learner, min_observations: int, min_confidence: float):
        self.store = store
        self.baseline = baseline
        self.min_observations = min_observations
        self.min_confidence = min_confidence

    def predict(self, context: dict[str, Any], trigger: dict[str, Any]) -> dict[str, dict[str, Any]]:
        baseline, baseline_reason = self.baseline.recommend(context)
        result = {
            "exact": {"action": baseline.action if baseline else None,
                      "confidence": baseline.confidence if baseline else 0.0, "reason": baseline_reason}
        }
        experiences = self.store.experiences()
        for method in METHODS[1:]:
            result[method] = self._predict_event(context, trigger, experiences, adaptive=method == "adaptive_seasonal")
        return result

    def _predict_event(
        self, context: dict[str, Any], trigger: dict[str, Any], experiences: list[dict[str, Any]], *, adaptive: bool
    ) -> dict[str, Any]:
        area = trigger.get("area")
        kind = trigger.get("kind")
        direction = trigger.get("direction")
        if not area or not kind or not direction:
            return _empty("No area-specific context change to compare.")
        scores: dict[str, float] = defaultdict(float)
        counts: dict[str, int] = defaultdict(int)
        actions: dict[str, dict[str, Any]] = {}
        now = datetime.now(timezone.utc)
        for example in experiences:
            past = example["trigger"]
            action = example["action"]
            domain = str(action.get("domain", ""))
            if kind not in RELEVANT_KINDS.get(domain, set()):
                continue
            if past.get("area") != area or past.get("kind") != kind or past.get("direction") != direction:
                continue
            if kind in {"motion", "occupancy", "presence", "opening"} and past.get("new_band") != trigger.get("new_band"):
                continue
            old_occupancy = example["context"].get("occupancy", "unknown")
            new_occupancy = context.get("occupancy", "unknown")
            if old_occupancy != new_occupancy and "unknown" not in {old_occupancy, new_occupancy}:
                continue
            # The trigger's direction matters; exact raw sensor values do not.
            similarity = 1.0 if past.get("new_band") == trigger.get("new_band") else 0.6
            if adaptive:
                age_days = max(0.0, (now - datetime.fromisoformat(example["created_at"])).total_seconds() / 86400)
                recency = 0.5 ** (age_days / 120.0)
                past_month = int(example["context"].get("month", 0))
                current_month = int(context.get("month", 0))
                seasonal = 1.0 if past_month and current_month and season(past_month) == season(current_month) else 0.6
                similarity *= (0.25 + 0.75 * recency) * seasonal
            key = str(sorted(action.items()))
            scores[key] += similarity
            counts[key] += 1
            actions[key] = action
        if not scores:
            return _empty("No relevant user actions after this kind of change.")
        winner = max(scores, key=lambda key: (scores[key], counts[key], key))
        support = counts[winner]
        if support < self.min_observations:
            return _empty(f"Need {self.min_observations} comparable events; have {support}.")
        share = scores[winner] / sum(scores.values())
        if share < self.min_confidence:
            return _empty(f"Competing actions: strongest has {share:.2f} share, below {self.min_confidence:.2f}.")
        return {"action": actions[winner], "confidence": round(share, 3),
                "reason": f"{support} matching user actions; {kind} moved {direction} in {area}; "
                          + ("recent and same-season examples weighted higher." if adaptive else "irrelevant sensor bands ignored.")}
