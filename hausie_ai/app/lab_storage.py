"""Additive SQLite tables for read-only model experiments."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from .storage import Store, utc_now


class LabStore:
    def __init__(self, store: Store):
        self.store = store
        with store._lock, store._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS lab_manual_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    area TEXT NOT NULL,
                    action_json TEXT NOT NULL,
                    previous_action_json TEXT,
                    context_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_lab_manual_area ON lab_manual_actions(area, id DESC);
                CREATE TABLE IF NOT EXISTS lab_sensor_forecasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    entity_id TEXT NOT NULL,
                    area TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    unit TEXT NOT NULL,
                    target_hour INTEGER NOT NULL,
                    method TEXT NOT NULL,
                    current_value REAL NOT NULL,
                    prediction REAL NOT NULL,
                    actual_value REAL
                );
                CREATE INDEX IF NOT EXISTS idx_lab_forecasts_pending ON lab_sensor_forecasts(completed_at, created_at);
                CREATE INDEX IF NOT EXISTS idx_lab_forecasts_history ON lab_sensor_forecasts(entity_id, unit, target_hour, completed_at);
                CREATE TABLE IF NOT EXISTS lab_outcome_predictions (
                    outcome_id INTEGER NOT NULL,
                    entity_id TEXT NOT NULL,
                    method TEXT NOT NULL,
                    predicted_delta REAL NOT NULL,
                    actual_delta REAL,
                    PRIMARY KEY (outcome_id, entity_id, method)
                );
                CREATE TABLE IF NOT EXISTS lab_preference_predictions (
                    decision_id INTEGER PRIMARY KEY,
                    predicted_helpful REAL NOT NULL,
                    actual_helpful INTEGER
                );
                CREATE TABLE IF NOT EXISTS lab_anomalies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    area TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    score REAL,
                    flagged INTEGER NOT NULL,
                    explanation TEXT NOT NULL,
                    reviewed INTEGER
                );
                """
            )

    def manual_actions(self, area: str, limit: int = 2000) -> list[dict[str, Any]]:
        with self.store._connect() as connection:
            rows = connection.execute(
                "SELECT created_at, action_json, previous_action_json, context_json FROM lab_manual_actions WHERE area = ? ORDER BY id DESC LIMIT ?",
                (area, max(1, min(5000, limit))),
            ).fetchall()
        return [{"created_at": row["created_at"], "action": json.loads(row["action_json"]),
                 "previous_action": json.loads(row["previous_action_json"]) if row["previous_action_json"] else None,
                 "context": json.loads(row["context_json"])} for row in rows]

    def add_manual_action(self, area: str, action: dict[str, Any], context: dict[str, Any]) -> int:
        with self.store._lock, self.store._connect() as connection:
            previous = connection.execute(
                "SELECT created_at, action_json FROM lab_manual_actions WHERE area = ? ORDER BY id DESC LIMIT 1", (area,)
            ).fetchone()
            previous_json = None
            if previous and datetime.now(timezone.utc) - datetime.fromisoformat(previous["created_at"]) <= timedelta(hours=2):
                previous_json = previous["action_json"]
            cursor = connection.execute(
                "INSERT INTO lab_manual_actions(created_at, area, action_json, previous_action_json, context_json) VALUES (?, ?, ?, ?, ?)",
                (utc_now(), area, json.dumps(action, sort_keys=True), previous_json, json.dumps(context, sort_keys=True)),
            )
            return int(cursor.lastrowid)

    def recent_triggers(self, area: str, limit: int = 2) -> list[dict[str, Any]]:
        with self.store._connect() as connection:
            rows = connection.execute(
                "SELECT created_at, trigger_json FROM shadow_opportunities ORDER BY id DESC LIMIT 300"
            ).fetchall()
        result = []
        for row in rows:
            trigger = json.loads(row["trigger_json"])
            if trigger.get("area") == area and datetime.now(timezone.utc) - datetime.fromisoformat(row["created_at"]) <= timedelta(hours=2):
                result.append(trigger)
                if len(result) >= limit:
                    break
        return list(reversed(result))

    def previous_sensor_events(self, entity_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.store._connect() as connection:
            rows = connection.execute(
                "SELECT created_at, old_state, new_state FROM environmental_events WHERE entity_id = ? ORDER BY id DESC LIMIT ?",
                (entity_id, max(1, min(100, limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    def completed_forecast_values(self, entity_id: str, unit: str, target_hour: int, limit: int = 60) -> list[float]:
        with self.store._connect() as connection:
            rows = connection.execute(
                """SELECT DISTINCT created_at, actual_value FROM lab_sensor_forecasts
                   WHERE entity_id = ? AND unit = ? AND target_hour = ? AND actual_value IS NOT NULL
                   ORDER BY created_at DESC LIMIT ?""", (entity_id, unit, target_hour, limit)
            ).fetchall()
        return [float(row["actual_value"]) for row in rows]

    def add_forecasts(self, profile: dict[str, Any], target_hour: int, current: float, predictions: dict[str, float]) -> None:
        area = str(profile.get("area_id") or profile.get("area_name") or "")
        created_at = utc_now()
        with self.store._lock, self.store._connect() as connection:
            connection.executemany(
                """INSERT INTO lab_sensor_forecasts(created_at, entity_id, area, kind, unit, target_hour, method, current_value, prediction)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(created_at, profile["entity_id"], area, str(profile.get("environmental_kind") or ""),
                  str(profile.get("unit") or ""), target_hour, method, current, value)
                 for method, value in predictions.items()],
            )

    def pending_forecasts(self) -> list[dict[str, Any]]:
        with self.store._connect() as connection:
            rows = connection.execute(
                """SELECT DISTINCT entity_id, unit FROM lab_sensor_forecasts
                   WHERE completed_at IS NULL AND datetime(created_at) <= datetime('now', '-30 minutes') LIMIT 500"""
            ).fetchall()
        return [dict(row) for row in rows]

    def finish_forecasts(self, entity_id: str, unit: str, actual: float | None) -> int:
        with self.store._lock, self.store._connect() as connection:
            cursor = connection.execute(
                """UPDATE lab_sensor_forecasts SET completed_at = ?,
                   actual_value = CASE WHEN datetime(created_at) >= datetime('now', '-45 minutes') THEN ? ELSE NULL END
                   WHERE entity_id = ? AND unit = ? AND completed_at IS NULL
                   AND datetime(created_at) <= datetime('now', '-30 minutes')""",
                (utc_now(), actual, entity_id, unit),
            )
            return cursor.rowcount

    def forecast_report(self) -> list[dict[str, Any]]:
        with self.store._connect() as connection:
            rows = connection.execute(
                """SELECT method, kind, unit, COUNT(*) AS opportunities, COUNT(actual_value) AS evaluated,
                   AVG(ABS(prediction - actual_value)) AS mean_absolute_error
                   FROM lab_sensor_forecasts GROUP BY method, kind, unit ORDER BY kind, unit, method"""
            ).fetchall()
        return [dict(row) for row in rows]

    def outcome_history(self, area: str, action: dict[str, Any], limit: int = 500) -> list[dict[str, Any]]:
        with self.store._connect() as connection:
            rows = connection.execute(
                """SELECT before_json, after_json FROM action_outcomes WHERE area = ? AND action_json = ?
                   AND completed_at IS NOT NULL AND other_user_actions = 0 AND after_json IS NOT NULL
                   ORDER BY id DESC LIMIT ?""", (area, json.dumps(action, sort_keys=True), limit)
            ).fetchall()
        return [{"before": json.loads(row["before_json"]), "after": json.loads(row["after_json"])} for row in rows]

    def add_outcome_predictions(self, outcome_id: int, predictions: list[tuple[str, str, float]]) -> None:
        if not predictions:
            return
        with self.store._lock, self.store._connect() as connection:
            connection.executemany(
                "INSERT INTO lab_outcome_predictions(outcome_id, entity_id, method, predicted_delta) VALUES (?, ?, ?, ?)",
                [(outcome_id, entity_id, method, delta) for entity_id, method, delta in predictions],
            )

    def label_outcome_predictions(self, outcome_id: int) -> None:
        with self.store._lock, self.store._connect() as connection:
            outcome = connection.execute(
                "SELECT before_json, after_json, other_user_actions FROM action_outcomes WHERE id = ?", (outcome_id,)
            ).fetchone()
            if not outcome or not outcome["after_json"] or outcome["other_user_actions"]:
                return
            before, after = json.loads(outcome["before_json"]), json.loads(outcome["after_json"])
            for entity_id in before.keys() & after.keys():
                try:
                    delta = float(after[entity_id]["value"]) - float(before[entity_id]["value"])
                except (TypeError, ValueError, KeyError):
                    continue
                connection.execute(
                    "UPDATE lab_outcome_predictions SET actual_delta = ? WHERE outcome_id = ? AND entity_id = ?",
                    (delta, outcome_id, entity_id),
                )

    def outcome_report(self) -> list[dict[str, Any]]:
        with self.store._connect() as connection:
            rows = connection.execute(
                """SELECT method, entity_id, COUNT(*) AS predictions, COUNT(actual_delta) AS evaluated,
                   AVG(ABS(predicted_delta - actual_delta)) AS mean_absolute_error
                   FROM lab_outcome_predictions GROUP BY method, entity_id ORDER BY entity_id, method"""
            ).fetchall()
        return [dict(row) for row in rows]

    def feedback_history(self, action: dict[str, Any]) -> list[int]:
        with self.store._connect() as connection:
            rows = connection.execute(
                """SELECT f.reward FROM decision_feedback f JOIN decisions d ON d.id = f.decision_id
                   WHERE f.source = 'explicit_user_feedback' AND d.action_json = ? ORDER BY f.id DESC LIMIT 500""",
                (json.dumps(action, sort_keys=True),),
            ).fetchall()
        return [1 if row["reward"] > 0 else 0 for row in rows]

    def add_preference_prediction(self, decision_id: int, probability: float) -> None:
        with self.store._lock, self.store._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO lab_preference_predictions(decision_id, predicted_helpful) VALUES (?, ?)",
                (decision_id, probability),
            )

    def label_preference_prediction(self, decision_id: int, helpful: bool) -> None:
        with self.store._lock, self.store._connect() as connection:
            connection.execute(
                "UPDATE lab_preference_predictions SET actual_helpful = ? WHERE decision_id = ? AND actual_helpful IS NULL",
                (int(helpful), decision_id),
            )

    def preference_report(self) -> dict[str, Any]:
        with self.store._connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS predictions, COUNT(actual_helpful) AS evaluated,
                   AVG((predicted_helpful - actual_helpful) * (predicted_helpful - actual_helpful)) AS brier_score
                   FROM lab_preference_predictions"""
            ).fetchone()
        return dict(row)

    def add_anomaly(self, trigger: dict[str, Any], score: float | None, flagged: bool, explanation: str) -> int:
        with self.store._lock, self.store._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO lab_anomalies(created_at, entity_id, area, kind, score, flagged, explanation)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (utc_now(), trigger["entity_id"], trigger["area"], trigger["kind"], score, int(flagged), explanation),
            )
            return int(cursor.lastrowid)

    def rate_anomaly(self, anomaly_id: int, surprising: bool) -> bool:
        with self.store._lock, self.store._connect() as connection:
            cursor = connection.execute(
                "UPDATE lab_anomalies SET reviewed = ? WHERE id = ? AND reviewed IS NULL AND score IS NOT NULL",
                (int(surprising), anomaly_id),
            )
            return cursor.rowcount == 1

    def anomaly_report(self, limit: int = 30) -> dict[str, Any]:
        with self.store._connect() as connection:
            summary = connection.execute(
                """SELECT COUNT(*) AS opportunities, COUNT(score) AS scored,
                   SUM(flagged) AS flagged, COUNT(reviewed) AS reviewed,
                   SUM(CASE WHEN reviewed IS NOT NULL AND flagged = reviewed THEN 1 ELSE 0 END) AS agreed
                   FROM lab_anomalies"""
            ).fetchone()
            recent = connection.execute(
                "SELECT * FROM lab_anomalies WHERE flagged = 1 ORDER BY id DESC LIMIT ?", (max(1, min(100, limit)),)
            ).fetchall()
        return {"summary": dict(summary), "recent": [dict(row) for row in recent]}
