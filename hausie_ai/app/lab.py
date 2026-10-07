"""Thirty-one bounded experiments across seven learning tasks.

The lab produces predictions and scores only. It has no Home Assistant client.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean, median
from time import monotonic
from typing import Any

from .experiments import ShadowLearners
from .lab_storage import LabStore


CATALOG = [
    {"id": "exact", "family": "actions", "name": "Exact context", "status": "legacy"},
    {"id": "event", "family": "actions", "name": "Event matching", "status": "shadow"},
    {"id": "adaptive_seasonal", "family": "actions", "name": "Adaptive seasonal", "status": "shadow"},
    {"id": "no_context", "family": "actions", "name": "Area frequency (no context)", "status": "shadow"},
    {"id": "hierarchical", "family": "actions", "name": "Hierarchical backoff", "status": "shadow"},
    {"id": "nearest_case", "family": "actions", "name": "Nearest experiences", "status": "shadow"},
    {"id": "markov_action", "family": "actions", "name": "Previous action", "status": "shadow"},
    {"id": "event_sequence", "family": "actions", "name": "Sensor event sequence", "status": "shadow"},
    {"id": "clock_prior", "family": "actions", "name": "Time-of-day action", "status": "shadow"},
    {"id": "recency_prior", "family": "actions", "name": "Recent action frequency", "status": "shadow"},
    {"id": "naive_bayes", "family": "actions", "name": "Probabilistic event features", "status": "shadow"},
    {"id": "sensor_persistence", "family": "sensors", "name": "Last value", "status": "shadow"},
    {"id": "sensor_trend", "family": "sensors", "name": "Recent trend", "status": "shadow"},
    {"id": "sensor_hourly", "family": "sensors", "name": "Same-hour history", "status": "shadow"},
    {"id": "sensor_rolling_mean", "family": "sensors", "name": "Rolling average", "status": "shadow"},
    {"id": "sensor_rolling_median", "family": "sensors", "name": "Rolling median", "status": "shadow"},
    {"id": "sensor_ewma", "family": "sensors", "name": "Recent-value weighted average", "status": "shadow"},
    {"id": "sensor_multivariate", "family": "sensors", "name": "Nearby multi-sensor situations", "status": "shadow"},
    {"id": "response_mean", "family": "responses", "name": "Mean observed change", "status": "shadow"},
    {"id": "response_nearest", "family": "responses", "name": "Similar starting value", "status": "shadow"},
    {"id": "response_median", "family": "responses", "name": "Median observed change", "status": "shadow"},
    {"id": "preference_bayes", "family": "preferences", "name": "Explicit feedback", "status": "shadow"},
    {"id": "preference_recent", "family": "preferences", "name": "Recent explicit feedback", "status": "shadow"},
    {"id": "preference_context", "family": "preferences", "name": "Occupancy-specific feedback", "status": "shadow"},
    {"id": "anomaly_robust", "family": "anomalies", "name": "Unusual sensor change", "status": "shadow"},
    {"id": "anomaly_value", "family": "anomalies", "name": "Unusual sensor value", "status": "shadow"},
    {"id": "anomaly_drift", "family": "anomalies", "name": "Recent-vs-earlier shift", "status": "shadow"},
    {"id": "timing_area_median", "family": "timing", "name": "Typical action delay in area", "status": "shadow"},
    {"id": "timing_kind_median", "family": "timing", "name": "Event-specific action delay", "status": "shadow"},
    {"id": "routine_transition", "family": "routines", "name": "Next sensor-event transition", "status": "shadow"},
    {"id": "routine_bigram", "family": "routines", "name": "Two-event sequence", "status": "shadow"},
]
ACTION_IDS = tuple(item["id"] for item in CATALOG if item["family"] == "actions")


def action_key(action: dict[str, Any]) -> str:
    return json.dumps(action, sort_keys=True)


def numeric(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", "."))
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def abstain(reason: str) -> dict[str, Any]:
    return {"action": None, "confidence": 0.0, "reason": reason}


def vote(samples: list[tuple[dict[str, Any], float]], minimum: int, threshold: float, explanation: str) -> dict[str, Any]:
    if not samples:
        return abstain("No comparable examples yet.")
    weights: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    actions: dict[str, dict[str, Any]] = {}
    for action, weight in samples:
        key = action_key(action)
        weights[key] += weight
        counts[key] += 1
        actions[key] = action
    winner = max(weights, key=lambda key: (weights[key], counts[key], key))
    if counts[winner] < minimum:
        return abstain(f"Need {minimum} matching user actions; have {counts[winner]}.")
    share = weights[winner] / sum(weights.values())
    if share < threshold:
        return abstain(f"Conflicting actions: top share {share:.2f} below {threshold:.2f}.")
    return {"action": actions[winner], "confidence": round(share, 3),
            "reason": f"{explanation}; {counts[winner]} supporting user actions."}


def signal_token(trigger: dict[str, Any]) -> str:
    return ":".join((str(trigger.get("kind", "")), str(trigger.get("direction", "")), str(trigger.get("new_band", ""))))


class ModelLab:
    def __init__(self, store: LabStore, original: ShadowLearners, minimum: int, confidence: float):
        self.store = store
        self.original = original
        self.minimum = minimum
        self.confidence = confidence
        self._last_forecast_at: dict[str, float] = {}
        self.local_zone = datetime.now().astimezone().tzinfo

    def enrich_trigger(self, trigger: dict[str, Any]) -> dict[str, Any]:
        earlier = self.store.recent_triggers(str(trigger["area"]), 2)
        return dict(trigger, sequence=[signal_token(item) for item in earlier] + [signal_token(trigger)])

    def predict_actions(self, context: dict[str, Any], trigger: dict[str, Any]) -> dict[str, dict[str, Any]]:
        result = self.original.predict(context, trigger)
        area = str(trigger["area"])
        manual = self.store.manual_actions(area)
        experiences = [row for row in self.original.store.experiences() if row["trigger"].get("area") == area]
        result["no_context"] = vote([(row["action"], 1.0) for row in manual], self.minimum,
                                     self.confidence, "Same-area action frequency without sensor or time context")
        tiers = [
            ("same area, occupancy and event kind", lambda row: row["context"].get("occupancy") == context.get("occupancy") and row["trigger"].get("kind") == trigger.get("kind")),
            ("same area and event kind", lambda row: row["trigger"].get("kind") == trigger.get("kind")),
            ("same area", lambda row: True),
        ]
        result["hierarchical"] = abstain("No backoff tier has enough consistent observations.")
        for name, predicate in tiers:
            proposal = vote([(row["action"], 1.0) for row in experiences if predicate(row)], self.minimum,
                            self.confidence, name)
            if proposal["action"]:
                result["hierarchical"] = proposal
                break

        nearest: list[tuple[float, dict[str, Any]]] = []
        for row in experiences:
            past = row["trigger"]
            past_context = row["context"]
            score = 1.0
            score += 3.0 if past.get("kind") == trigger.get("kind") else 0.0
            score += 2.0 if past.get("direction") == trigger.get("direction") else 0.0
            score += 1.5 if past.get("new_band") == trigger.get("new_band") else 0.0
            score += 1.0 if past_context.get("occupancy") == context.get("occupancy") else 0.0
            try:
                distance = abs(int(past_context["hour_bucket"]) - int(context["hour_bucket"]))
                score += max(0.0, 1.0 - min(distance, 96 - distance) / 24)
            except (KeyError, TypeError, ValueError):
                pass
            nearest.append((score, row["action"]))
        nearest.sort(key=lambda item: item[0], reverse=True)
        result["nearest_case"] = vote([(action, score) for score, action in nearest[:9]], self.minimum,
                                      self.confidence, "Weighted nearby examples in this area")

        previous = manual[0]["action"] if manual and datetime.now(timezone.utc) - datetime.fromisoformat(manual[0]["created_at"]) <= timedelta(hours=2) else None
        if previous:
            result["markov_action"] = vote(
                [(row["action"], 1.0) for row in manual if row["previous_action"] == previous],
                self.minimum, self.confidence, "Same previous manual action within two hours")
        else:
            result["markov_action"] = abstain("No recent previous manual action in this area.")
        sequence = trigger.get("sequence") or []
        if len(sequence) >= 2:
            suffix = sequence[-2:]
            result["event_sequence"] = vote(
                [(row["action"], 1.0) for row in experiences if (row["trigger"].get("sequence") or [])[-2:] == suffix],
                self.minimum, self.confidence, "Same last two sensor changes in this area")
        else:
            result["event_sequence"] = abstain("Need two recent sensor changes in this area.")
        try:
            hour = int(context["hour_bucket"])
            clock_rows = [(row["action"], 1.0) for row in manual
                          if "hour_bucket" in row["context"] and
                          min(abs(int(row["context"]["hour_bucket"]) - hour),
                              96 - abs(int(row["context"]["hour_bucket"]) - hour)) <= 4]
            result["clock_prior"] = vote(clock_rows, self.minimum, self.confidence,
                                          "Manual actions in this area within one clock hour")
        except (KeyError, TypeError, ValueError):
            result["clock_prior"] = abstain("No valid time bucket to compare.")
        now = datetime.now(timezone.utc)
        result["recency_prior"] = vote(
            [(row["action"], 0.5 ** (max(0, (now - datetime.fromisoformat(row["created_at"])).total_seconds()) / (14 * 86400)))
             for row in manual], self.minimum, self.confidence,
            "Same-area actions weighted by a 14-day half-life")
        result["naive_bayes"] = self._naive_bayes(experiences, context, trigger)
        return result

    def _naive_bayes(self, experiences: list[dict[str, Any]], context: dict[str, Any],
                     trigger: dict[str, Any]) -> dict[str, Any]:
        if len(experiences) < self.minimum:
            return abstain("Need more labelled area events for probabilistic features.")
        def features(past_context: dict[str, Any], past_trigger: dict[str, Any]) -> tuple[str, ...]:
            return (str(past_trigger.get("kind", "")), str(past_trigger.get("direction", "")),
                    str(past_trigger.get("new_band", "")), str(past_context.get("occupancy", "")),
                    str(past_context.get("month", "")), str(int(past_context.get("hour_bucket", 0)) // 16))
        target = features(context, trigger)
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in experiences:
            groups[action_key(row["action"])].append(row)
        scores: dict[str, float] = {}
        for key, rows in groups.items():
            if len(rows) < self.minimum:
                continue
            score = math.log((len(rows) + 1) / (len(experiences) + len(groups)))
            for index, value in enumerate(target):
                match_count = sum(features(row["context"], row["trigger"])[index] == value for row in rows)
                score += math.log((match_count + 1) / (len(rows) + 3))
            scores[key] = score
        if not scores:
            return abstain("No action class has enough examples.")
        best = max(scores, key=scores.get)
        scale = max(scores.values())
        denominator = sum(math.exp(value - scale) for value in scores.values())
        probability = math.exp(scores[best] - scale) / denominator
        if probability < self.confidence:
            return abstain(f"Probabilistic action share {probability:.2f} below {self.confidence:.2f}.")
        return {"action": groups[best][0]["action"], "confidence": round(probability, 3),
                "reason": f"Smoothed event/time/occupancy features from {len(experiences)} area examples."}

    def observe_manual_action(self, area: str, action: dict[str, Any], context: dict[str, Any]) -> int:
        return self.store.add_manual_action(area, action, context)

    def predict_timing(self, opportunity_id: int, trigger: dict[str, Any]) -> dict[str, float | None]:
        area, kind = str(trigger["area"]), str(trigger["kind"])
        area_times = self.store.timing_examples(area)
        kind_times = self.store.timing_examples(area, kind)
        predictions = {
            "timing_area_median": median(area_times) if len(area_times) >= self.minimum else None,
            "timing_kind_median": median(kind_times) if len(kind_times) >= self.minimum else None,
        }
        self.store.add_timing_predictions(opportunity_id, area, kind, predictions)
        return predictions

    def predict_next_event(self, trigger: dict[str, Any]) -> dict[str, str | None]:
        area = str(trigger["area"])
        current = signal_token(trigger)
        previous = self.store.previous_opportunity(area)
        if previous:
            self.store.observe_next_event(previous, current)
        history = self.store.event_transitions(area)
        sequence = trigger.get("sequence") or []
        prior = sequence[-2] if len(sequence) >= 2 else None

        def strongest(rows: list[dict[str, Any]]) -> str | None:
            counts: dict[str, int] = defaultdict(int)
            for row in rows:
                counts[row["next_signal"]] += 1
            if not counts:
                return None
            winner = max(counts, key=lambda key: (counts[key], key))
            return winner if counts[winner] >= self.minimum and counts[winner] / len(rows) >= self.confidence else None

        return {
            "routine_transition": strongest([row for row in history if row["current_signal"] == current]),
            "routine_bigram": strongest([row for row in history if row["current_signal"] == current and
                                           prior is not None and row["previous_signal"] == prior]),
        }

    def save_next_event_predictions(self, opportunity_id: int, trigger: dict[str, Any],
                                    predictions: dict[str, str | None]) -> None:
        self.store.add_routine_predictions(opportunity_id, str(trigger["area"]), predictions)

    def forecast_sensor(self, profile: dict[str, Any], profiles: list[dict[str, Any]] | None = None) -> dict[str, float]:
        entity = str(profile["entity_id"])
        kind = str(profile.get("environmental_kind") or "")
        if kind not in {"temperature", "humidity", "illuminance"}:
            return {}
        current = numeric(profile.get("state"))
        if current is None or monotonic() - self._last_forecast_at.get(entity, -1e10) < 1800:
            return {}
        self._last_forecast_at[entity] = monotonic()
        target_hour = (datetime.now(self.local_zone) + timedelta(minutes=30)).hour
        predictions = {"sensor_persistence": current}
        for previous in self.store.previous_sensor_events(entity):
            previous_value = numeric(previous["new_state"])
            if previous_value is None:
                continue
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(previous["created_at"])).total_seconds() / 60
            if 2 <= elapsed <= 180:
                change = (current - previous_value) * 30 / elapsed
                cap = {"temperature": 5.0, "humidity": 25.0, "illuminance": 500.0}[kind]
                predictions["sensor_trend"] = current + max(-cap, min(cap, change))
                break
        historical = self.store.completed_forecast_values(entity, str(profile.get("unit") or ""), target_hour)
        if len(historical) >= self.minimum:
            predictions["sensor_hourly"] = mean(historical)
        values = [current] + self.store.sensor_values(entity, 20)
        if len(values) >= self.minimum + 1:
            recent = values[:6]
            predictions["sensor_rolling_mean"] = mean(recent)
            predictions["sensor_rolling_median"] = median(recent)
            weights = [0.6 ** index for index in range(len(recent))]
            predictions["sensor_ewma"] = sum(value * weight for value, weight in zip(recent, weights)) / sum(weights)
        area = str(profile.get("area_id") or profile.get("area_name") or "")
        features = {entity: current}
        for other in profiles or []:
            other_area = str(other.get("area_id") or other.get("area_name") or "")
            if other_area != area or other.get("entity_id") == entity:
                continue
            value = numeric(other.get("state"))
            if value is not None and (other.get("is_environmental") or other.get("is_context_input")):
                features[str(other["entity_id"])] = value
            elif str(other.get("state", "")).lower() in {"on", "off"}:
                features[str(other["entity_id"])] = float(str(other["state"]).lower() == "on")
        cases = []
        if len(features) >= 2:
            for case in self.store.sensor_vectors(entity, str(profile.get("unit") or "")):
                shared = features.keys() & case["features"].keys()
                if len(shared) < 2:
                    continue
                distance = sum(abs(features[key] - case["features"][key]) /
                               max(1.0, abs(features[key]), abs(case["features"][key])) for key in shared) / len(shared)
                cases.append((distance, case["actual"]))
            cases.sort(key=lambda item: item[0])
            if len(cases) >= self.minimum:
                predictions["sensor_multivariate"] = mean(value for _, value in cases[:7])
        self.store.add_forecasts(profile, target_hour, current, predictions, features)
        return predictions

    def finish_forecasts(self, profiles: list[dict[str, Any]]) -> int:
        by_entity = {str(row["entity_id"]): row for row in profiles}
        completed = 0
        for pending in self.store.pending_forecasts():
            profile = by_entity.get(pending["entity_id"])
            value = numeric(profile.get("state")) if profile and str(profile.get("unit") or "") == pending["unit"] else None
            completed += self.store.finish_forecasts(pending["entity_id"], pending["unit"], value)
        return completed

    def predict_response(self, outcome_id: int, area: str, action: dict[str, Any], before: dict[str, Any]) -> int:
        historical = self.store.outcome_history(area, action)
        predictions: list[tuple[str, str, float]] = []
        for entity_id, current in before.items():
            value = numeric(current.get("value"))
            if value is None:
                continue
            cases = []
            for case in historical:
                old = numeric(case["before"].get(entity_id, {}).get("value"))
                new = numeric(case["after"].get(entity_id, {}).get("value"))
                if old is not None and new is not None:
                    cases.append((old, new - old))
            if len(cases) < self.minimum:
                continue
            predictions.append((entity_id, "response_mean", mean(delta for _, delta in cases)))
            predictions.append((entity_id, "response_median", median(delta for _, delta in cases)))
            nearest = sorted(cases, key=lambda item: abs(item[0] - value))[:7]
            predictions.append((entity_id, "response_nearest", mean(delta for _, delta in nearest)))
        self.store.add_outcome_predictions(outcome_id, predictions)
        return len(predictions)

    def predict_preference(self, decision_id: int, action: dict[str, Any]) -> float | None:
        feedback = self.store.feedback_history(action)
        if len(feedback) < 2:
            return None
        probability = (sum(feedback) + 1) / (len(feedback) + 2)
        self.store.add_preference_prediction(decision_id, probability)
        examples = self.store.feedback_examples(action)
        weights = [0.8 ** index for index in range(min(30, len(examples)))]
        predictions = {"preference_recent":
                       (1 + sum(row["helpful"] * weight for row, weight in zip(examples, weights))) / (2 + sum(weights))}
        current = self.store.decision_context(decision_id)
        comparable = [row["helpful"] for row in examples
                      if row["context"].get("occupancy") == current.get("occupancy")]
        if len(comparable) >= self.minimum:
            predictions["preference_context"] = (sum(comparable) + 1) / (len(comparable) + 2)
        self.store.add_preference_extended(decision_id, predictions)
        return probability

    def detect_anomaly(self, trigger: dict[str, Any]) -> int:
        old, new = numeric(trigger.get("old_value")), numeric(trigger.get("new_value"))
        deltas = []
        for previous in self.store.previous_sensor_events(str(trigger["entity_id"]), 50):
            a, b = numeric(previous["old_state"]), numeric(previous["new_state"])
            if a is not None and b is not None:
                deltas.append(abs(b - a))
        score = None
        flagged = False
        reason = "Need five earlier numeric changes from this sensor."
        if old is not None and new is not None and len(deltas) >= 5:
            centre = median(deltas)
            spread = median(abs(item - centre) for item in deltas)
            score = abs(abs(new - old) - centre) / max(0.1, 1.4826 * spread, centre * 0.1)
            flagged = score >= 3.5
            reason = f"Change size compared with {len(deltas)} earlier changes; robust score {score:.2f}."
        anomaly_id = self.store.add_anomaly(trigger, score, flagged, reason)
        if new is not None:
            values = self.store.sensor_values(str(trigger["entity_id"]), 30)
            value_score = None
            value_reason = "Need five earlier numeric readings from this sensor."
            if len(values) >= 5:
                centre = median(values)
                spread = median(abs(item - centre) for item in values)
                value_score = abs(new - centre) / max(0.1, 1.4826 * spread, abs(centre) * 0.01)
                value_reason = f"Current value versus {len(values)} earlier values; robust score {value_score:.2f}."
            self.store.add_anomaly(trigger, value_score, value_score is not None and value_score >= 3.5,
                                   value_reason, "anomaly_value")
            drift_score = None
            drift_reason = "Need ten earlier numeric readings to compare recent and earlier windows."
            if len(values) >= 10:
                recent = [new] + values[:4]
                previous = values[4:9]
                spread = median(abs(item - median(previous)) for item in previous)
                drift_score = abs(median(recent) - median(previous)) / max(0.1, 1.4826 * spread, abs(median(previous)) * 0.01)
                drift_reason = f"Recent five readings versus preceding five; shift score {drift_score:.2f}."
            self.store.add_anomaly(trigger, drift_score, drift_score is not None and drift_score >= 3.0,
                                   drift_reason, "anomaly_drift")
        return anomaly_id

    def report(self) -> dict[str, Any]:
        return {"catalog": CATALOG, "actions": self.original.store.shadow_report(),
                "sensors": self.store.forecast_report(), "responses": self.store.outcome_report(),
                "preferences": self.store.preference_report(), "anomalies": self.store.anomaly_report(),
                "timing": self.store.timing_report(), "routines": self.store.routine_report()}
