from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "hausie_ai.sqlite3"
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    action_json TEXT NOT NULL,
                    source TEXT NOT NULL,
                    reward REAL NOT NULL DEFAULT 1.0
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    action_json TEXT,
                    confidence REAL NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    executed INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS state_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    states_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decision_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    reward REAL NOT NULL,
                    source TEXT NOT NULL,
                    FOREIGN KEY(decision_id) REFERENCES decisions(id)
                );
                CREATE TABLE IF NOT EXISTS inventory_entities (
                    entity_id TEXT PRIMARY KEY,
                    updated_at TEXT NOT NULL,
                    profile_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS environmental_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    area_name TEXT,
                    variable_kind TEXT NOT NULL,
                    old_state TEXT,
                    new_state TEXT,
                    normalized_value TEXT,
                    used_in_context INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_environmental_events_created_at
                    ON environmental_events(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_environmental_events_entity_time
                    ON environmental_events(entity_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_observations_created_at
                    ON observations(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_observations_action_time
                    ON observations(action_json, created_at DESC);
                CREATE TABLE IF NOT EXISTS learning_experiences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    trigger_json TEXT NOT NULL,
                    action_json TEXT NOT NULL,
                    source TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_learning_experiences_created_at
                    ON learning_experiences(created_at DESC);
                CREATE TABLE IF NOT EXISTS shadow_opportunities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    trigger_json TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    actual_action_json TEXT
                );
                CREATE TABLE IF NOT EXISTS shadow_predictions (
                    opportunity_id INTEGER NOT NULL,
                    method TEXT NOT NULL,
                    action_json TEXT,
                    confidence REAL NOT NULL,
                    reason TEXT NOT NULL,
                    PRIMARY KEY (opportunity_id, method),
                    FOREIGN KEY(opportunity_id) REFERENCES shadow_opportunities(id)
                );
                CREATE INDEX IF NOT EXISTS idx_shadow_opportunities_created_at
                    ON shadow_opportunities(created_at DESC);
                CREATE TABLE IF NOT EXISTS action_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    area TEXT NOT NULL,
                    action_json TEXT NOT NULL,
                    before_json TEXT NOT NULL,
                    after_json TEXT,
                    other_user_actions INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_action_outcomes_pending
                    ON action_outcomes(completed_at, created_at);
                CREATE TABLE IF NOT EXISTS history_import_records (
                    entity_id TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    new_state TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    PRIMARY KEY (entity_id, changed_at, new_state, kind)
                );
                CREATE TABLE IF NOT EXISTS historical_action_events (
                    entity_id TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    old_state TEXT NOT NULL,
                    new_state TEXT NOT NULL,
                    action_json TEXT NOT NULL,
                    source TEXT NOT NULL,
                    PRIMARY KEY (entity_id, changed_at, new_state)
                );
                CREATE INDEX IF NOT EXISTS idx_historical_actions_time
                    ON historical_action_events(changed_at DESC);
                CREATE TABLE IF NOT EXISTS history_bootstrap (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    coverage_start TEXT NOT NULL,
                    coverage_end TEXT NOT NULL,
                    entity_signature TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    imported_actions INTEGER NOT NULL,
                    imported_environmental INTEGER NOT NULL
                );
                """
            )

    def history_bootstrap_state(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM history_bootstrap WHERE id = 1").fetchone()
        return dict(row) if row else None

    def history_record_exists(self, entity_id: str, changed_at: str, new_state: str) -> bool:
        with self._connect() as connection:
            return connection.execute(
                """SELECT 1 FROM history_import_records h
                   WHERE h.entity_id = ? AND h.changed_at = ? AND h.new_state = ?
                     AND (h.kind = 'environmental' OR EXISTS (
                         SELECT 1 FROM historical_action_events a
                         WHERE a.entity_id = h.entity_id AND a.changed_at = h.changed_at
                           AND a.new_state = h.new_state AND a.source = 'user'))""",
                (entity_id, changed_at, new_state),
            ).fetchone() is not None

    def recent_historical_actions(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT changed_at, entity_id, old_state, new_state, action_json, source
                   FROM historical_action_events ORDER BY changed_at DESC LIMIT ?""",
                (max(1, min(250, limit)),),
            ).fetchall()
        return [dict(row) | {"action": json.loads(row["action_json"])} for row in rows]

    def historical_action_count(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM historical_action_events").fetchone()[0])

    def mark_history_bootstrap(self, coverage_start: str, coverage_end: str, entity_signature: str,
                               actions: int, environmental: int) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO history_bootstrap(id, coverage_start, coverage_end, entity_signature, completed_at,
                   imported_actions, imported_environmental) VALUES (1, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET coverage_start=excluded.coverage_start,
                   coverage_end=excluded.coverage_end, entity_signature=excluded.entity_signature,
                   completed_at=excluded.completed_at,
                   imported_actions=history_bootstrap.imported_actions + excluded.imported_actions,
                   imported_environmental=history_bootstrap.imported_environmental + excluded.imported_environmental""",
                (coverage_start, coverage_end, entity_signature, utc_now(), actions, environmental),
            )

    def import_history_batch(self, records: list[dict[str, Any]]) -> dict[str, int]:
        """Atomically seed trusted observations, events and shadow training rows.

        A unique Recorder identity makes a failed/restarted import idempotent.
        Older locally observed events are also checked by timestamp to avoid
        counting the same user action twice on an add-on upgrade.
        """
        counts = {"actions": 0, "seen_actions": 0, "environmental": 0, "experiences": 0, "transitions": 0, "timings": 0}
        with self._lock, self._connect() as connection:
            for record in records:
                at = record["changed_at"]
                entity_id = record["entity_id"]
                state = record["new_state"]
                if not (record.get("environmental") or record.get("observed_action") or record.get("transition")):
                    continue
                kind = "action" if record.get("observed_action") else "environmental"
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO history_import_records(entity_id, changed_at, new_state, kind) VALUES (?, ?, ?, ?)",
                    (entity_id, at, state, kind),
                )
                already_seen = not bool(cursor.rowcount)
                if already_seen:
                    # A just-recorded Logbook entry may arrive after Recorder.
                    # Promote only unknown -> verified user on the overlap scan.
                    if not record.get("action") or kind != "action":
                        continue
                    prior_source = connection.execute(
                        """SELECT source FROM historical_action_events
                           WHERE entity_id = ? AND changed_at = ? AND new_state = ?""",
                        (entity_id, at, state),
                    ).fetchone()
                    if not prior_source or prior_source["source"] != "unknown":
                        continue
                    connection.execute(
                        """UPDATE historical_action_events SET source = 'user'
                           WHERE entity_id = ? AND changed_at = ? AND new_state = ?""",
                        (entity_id, at, state),
                    )
                profile = record["profile"]
                observed_action = record.get("observed_action")
                if observed_action and not already_seen:
                    connection.execute(
                        """INSERT OR IGNORE INTO historical_action_events
                           (entity_id, changed_at, old_state, new_state, action_json, source)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (entity_id, at, record["old_state"], state,
                         json.dumps(observed_action, sort_keys=True), record.get("observed_source") or "unknown"),
                    )
                    counts["seen_actions"] += 1
                if record.get("environmental") and not already_seen:
                    existing = connection.execute(
                        """SELECT 1 FROM environmental_events WHERE entity_id = ?
                           AND abs(strftime('%s', created_at) - strftime('%s', ?)) <= 5
                           AND new_state = ? LIMIT 1""", (entity_id, at, state),
                    ).fetchone()
                    if not existing:
                        connection.execute(
                            """INSERT INTO environmental_events
                               (created_at, entity_id, area_name, variable_kind, old_state,
                                new_state, normalized_value, used_in_context)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                            (at, entity_id, profile.get("area_name"),
                             profile.get("environmental_kind") or "context", record["old_state"],
                             state, record.get("normalized_value"), int(bool(profile.get("is_environmental")))),
                        )
                        counts["environmental"] += 1
                        if "numeric_value" in record:
                            connection.execute(
                                """INSERT OR IGNORE INTO lab_historical_values
                                   (entity_id, unit, changed_at, local_hour, value) VALUES (?, ?, ?, ?, ?)""",
                                (entity_id, str(profile.get("unit") or ""), at,
                                 int(record["local_hour"]), float(record["numeric_value"])),
                            )
                transition = record.get("transition")
                if transition and not already_seen:
                    connection.execute(
                        """INSERT INTO lab_event_transitions
                           (created_at, area, previous_signal, current_signal, next_signal)
                           VALUES (?, ?, ?, ?, ?)""",
                        (at, transition["area"], transition.get("previous_signal"),
                         transition["current_signal"], transition["next_signal"]),
                    )
                    counts["transitions"] += 1
                action = record.get("action")
                if not action:
                    continue
                action_json = json.dumps(action, sort_keys=True)
                existing = connection.execute(
                    """SELECT 1 FROM observations WHERE action_json = ?
                       AND abs(strftime('%s', created_at) - strftime('%s', ?)) <= 5 LIMIT 1""",
                    (action_json, at),
                ).fetchone()
                if existing:
                    continue
                from .learning import context_signature
                context = record["context"]
                connection.execute(
                    "INSERT INTO observations(created_at, context_json, action_json, source) VALUES (?, ?, ?, 'historical_user')",
                    (at, json.dumps(context_signature(context), sort_keys=True), action_json),
                )
                counts["actions"] += 1
                area = record.get("area")
                if area:
                    previous = connection.execute(
                        """SELECT created_at, action_json FROM lab_manual_actions
                           WHERE area = ? AND created_at < ? ORDER BY created_at DESC LIMIT 1""",
                        (area, at),
                    ).fetchone()
                    previous_json = (previous["action_json"] if previous and
                                     datetime.fromisoformat(at) - datetime.fromisoformat(previous["created_at"]) <= timedelta(hours=2)
                                     else None)
                    connection.execute(
                        """INSERT INTO lab_manual_actions
                           (created_at, area, action_json, previous_action_json, context_json)
                           VALUES (?, ?, ?, ?, ?)""",
                        (at, area, action_json, previous_json, json.dumps(context, sort_keys=True)),
                    )
                trigger = record.get("trigger")
                if trigger:
                    connection.execute(
                        """INSERT INTO learning_experiences
                           (created_at, context_json, trigger_json, action_json, source)
                           VALUES (?, ?, ?, ?, 'historical_user')""",
                        (at, json.dumps(context, sort_keys=True),
                         json.dumps(trigger, sort_keys=True), action_json),
                    )
                    counts["experiences"] += 1
                    trigger_at = record.get("trigger_at")
                    if trigger_at:
                        seconds = (datetime.fromisoformat(at) - datetime.fromisoformat(trigger_at)).total_seconds()
                        connection.execute(
                            """INSERT OR IGNORE INTO lab_historical_timing
                               (entity_id, changed_at, area, kind, seconds) VALUES (?, ?, ?, ?, ?)""",
                            (entity_id, at, area, str(trigger.get("kind") or ""), seconds),
                        )
                        counts["timings"] += 1
        return counts

    def start_action_outcome(self, area: str, action: dict[str, Any], before: dict[str, Any]) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO action_outcomes(created_at, area, action_json, before_json) VALUES (?, ?, ?, ?)",
                (utc_now(), area, json.dumps(action, sort_keys=True), json.dumps(before, sort_keys=True)),
            )
            return int(cursor.lastrowid)

    def pending_action_outcomes(self, minimum_age_seconds: int = 1800) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, area, created_at FROM action_outcomes WHERE completed_at IS NULL AND datetime(created_at) <= datetime('now', ?)",
                (f"-{minimum_age_seconds} seconds",),
            ).fetchall()
        return [dict(row) for row in rows]

    def finish_action_outcome(self, outcome_id: int, after: dict[str, Any]) -> bool:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT area, created_at FROM action_outcomes WHERE id = ? AND completed_at IS NULL", (outcome_id,)).fetchone()
            if row is None:
                return False
            other_actions = connection.execute(
                "SELECT COUNT(*) FROM action_outcomes WHERE area = ? AND id != ? AND created_at BETWEEN ? AND ?",
                (row["area"], outcome_id, row["created_at"],
                 (datetime.fromisoformat(row["created_at"]) + timedelta(minutes=30)).isoformat()),
            ).fetchone()[0]
            connection.execute(
                "UPDATE action_outcomes SET completed_at = ?, after_json = ?, other_user_actions = ? WHERE id = ?",
                (utc_now(), json.dumps(after, sort_keys=True), other_actions, outcome_id),
            )
            return True

    def recent_action_outcomes(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, created_at, completed_at, area, action_json, before_json, after_json, other_user_actions "
                "FROM action_outcomes ORDER BY id DESC LIMIT ?", (max(1, min(100, limit)),)
            ).fetchall()
        return [{"id": row["id"], "created_at": row["created_at"], "completed_at": row["completed_at"],
                 "area": row["area"], "action": json.loads(row["action_json"]),
                 "before": json.loads(row["before_json"]), "after": json.loads(row["after_json"]) if row["after_json"] else None,
                 "other_user_actions": row["other_user_actions"]} for row in rows]

    def add_experience(self, context: dict[str, Any], trigger: dict[str, Any], action: dict[str, Any], source: str) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO learning_experiences(created_at, context_json, trigger_json, action_json, source) VALUES (?, ?, ?, ?, ?)",
                (utc_now(), json.dumps(context, sort_keys=True), json.dumps(trigger, sort_keys=True), json.dumps(action, sort_keys=True), source),
            )
            return int(cursor.lastrowid)

    def experiences(self, limit: int = 2000) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, created_at, context_json, trigger_json, action_json, source FROM learning_experiences ORDER BY created_at DESC, id DESC LIMIT ?",
                (max(1, min(10000, limit)),),
            ).fetchall()
        return [{"id": row["id"], "created_at": row["created_at"], "context": json.loads(row["context_json"]),
                 "trigger": json.loads(row["trigger_json"]), "action": json.loads(row["action_json"]), "source": row["source"]} for row in rows]

    def add_shadow_opportunity(self, trigger: dict[str, Any], context: dict[str, Any], predictions: dict[str, dict[str, Any]]) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO shadow_opportunities(created_at, trigger_json, context_json) VALUES (?, ?, ?)",
                (utc_now(), json.dumps(trigger, sort_keys=True), json.dumps(context, sort_keys=True)),
            )
            opportunity_id = int(cursor.lastrowid)
            connection.executemany(
                "INSERT INTO shadow_predictions(opportunity_id, method, action_json, confidence, reason) VALUES (?, ?, ?, ?, ?)",
                [(opportunity_id, method, json.dumps(value["action"], sort_keys=True) if value["action"] else None,
                  value["confidence"], value["reason"]) for method, value in predictions.items()],
            )
            return opportunity_id

    def label_shadow_opportunity(self, opportunity_id: int, action: dict[str, Any]) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE shadow_opportunities SET actual_action_json = ? WHERE id = ? AND actual_action_json IS NULL",
                (json.dumps(action, sort_keys=True), opportunity_id),
            )
            return cursor.rowcount == 1

    def shadow_report(self, limit: int = 30) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT p.method, COUNT(*) AS opportunities,
                   SUM(CASE WHEN p.action_json IS NOT NULL THEN 1 ELSE 0 END) AS predicted,
                   SUM(CASE WHEN o.actual_action_json IS NOT NULL THEN 1 ELSE 0 END) AS labelled,
                   SUM(CASE WHEN o.actual_action_json IS NOT NULL AND p.action_json = o.actual_action_json THEN 1 ELSE 0 END) AS matched,
                   SUM(CASE WHEN o.actual_action_json IS NOT NULL AND p.action_json IS NOT NULL AND p.action_json != o.actual_action_json THEN 1 ELSE 0 END) AS disagreed
                   FROM shadow_predictions p JOIN shadow_opportunities o ON o.id = p.opportunity_id
                   GROUP BY p.method ORDER BY p.method"""
            ).fetchall()
            recent = connection.execute(
                """SELECT o.id, o.created_at, o.trigger_json, o.actual_action_json,
                   p.method, p.action_json, p.confidence, p.reason
                   FROM shadow_opportunities o JOIN shadow_predictions p ON p.opportunity_id = o.id
                   WHERE o.id IN (SELECT id FROM shadow_opportunities ORDER BY id DESC LIMIT ?)
                   ORDER BY o.id DESC, p.method""",
                (max(1, min(100, limit)),),
            ).fetchall()
        return {"methods": [dict(row) for row in rows], "recent": [
            {"id": row["id"], "created_at": row["created_at"], "trigger": json.loads(row["trigger_json"]),
             "actual_action": json.loads(row["actual_action_json"]) if row["actual_action_json"] else None,
             "method": row["method"], "action": json.loads(row["action_json"]) if row["action_json"] else None,
             "confidence": row["confidence"], "reason": row["reason"]} for row in recent]}

    def replace_inventory(self, profiles: list[dict[str, Any]]) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM inventory_entities")
            connection.executemany(
                "INSERT INTO inventory_entities(entity_id, updated_at, profile_json) VALUES (?, ?, ?)",
                [(str(profile["entity_id"]), utc_now(), json.dumps(profile, sort_keys=True)) for profile in profiles],
            )

    def inventory(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT profile_json FROM inventory_entities ORDER BY entity_id").fetchall()
        return [json.loads(row["profile_json"]) for row in rows]

    def add_environmental_event(
        self,
        profile: dict[str, Any],
        old_state: Any,
        new_state: Any,
    ) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO environmental_events
                (created_at, entity_id, area_name, variable_kind, old_state, new_state, normalized_value, used_in_context)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    utc_now(),
                    profile["entity_id"],
                    profile.get("area_name"),
                    profile.get("environmental_kind") or "context",
                    None if old_state is None else str(old_state),
                    None if new_state is None else str(new_state),
                    profile.get("normalized_value"),
                    int(bool(profile.get("is_environmental"))),
                ),
            )
            return int(cursor.lastrowid)

    def recent_environmental_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id, created_at, entity_id, area_name, variable_kind, old_state, new_state,
                          normalized_value, used_in_context
                   FROM environmental_events ORDER BY created_at DESC, id DESC LIMIT ?""",
                (max(1, min(250, limit)),),
            ).fetchall()
        return [dict(row) | {"used_in_context": bool(row["used_in_context"])} for row in rows]

    def recent_decisions(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT d.id, d.created_at, d.context_json, d.action_json, d.confidence, d.decision, d.reason, d.executed,
                          EXISTS(SELECT 1 FROM decision_feedback f WHERE f.decision_id = d.id AND f.source = 'explicit_user_feedback') AS rated
                   FROM decisions d ORDER BY d.id DESC LIMIT ?""",
                (max(1, min(100, limit)),),
            ).fetchall()
        return [
            {
                "id": row["id"], "created_at": row["created_at"], "context": json.loads(row["context_json"]),
                "action": json.loads(row["action_json"]) if row["action_json"] else None,
                "confidence": row["confidence"], "decision": row["decision"], "reason": row["reason"],
                "executed": bool(row["executed"]), "rated": bool(row["rated"]),
            }
            for row in rows
        ]

    def add_observation(self, context: dict[str, Any], action: dict[str, Any], source: str) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO observations(created_at, context_json, action_json, source) VALUES (?, ?, ?, ?)",
                (utc_now(), json.dumps(context, sort_keys=True), json.dumps(action, sort_keys=True), source),
            )
            return int(cursor.lastrowid)

    def add_decision(
        self,
        context: dict[str, Any],
        action: dict[str, Any] | None,
        confidence: float,
        decision: str,
        reason: str,
        executed: bool,
    ) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO decisions
                (created_at, context_json, action_json, confidence, decision, reason, executed)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    utc_now(),
                    json.dumps(context, sort_keys=True),
                    json.dumps(action, sort_keys=True) if action else None,
                    confidence,
                    decision,
                    reason,
                    int(executed),
                ),
            )
            return int(cursor.lastrowid)

    def add_snapshot(self, states: list[dict[str, Any]]) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO state_snapshots(created_at, states_json) VALUES (?, ?)",
                (utc_now(), json.dumps(states, sort_keys=True)),
            )

    def feedback(self, observation_id: int, reward: float) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE observations SET reward = ? WHERE id = ?",
                (max(-1.0, min(1.0, reward)), observation_id),
            )
            return cursor.rowcount == 1

    def add_decision_feedback(self, decision_id: int, reward: float, source: str) -> bool:
        with self._lock, self._connect() as connection:
            decision = connection.execute(
                "SELECT id FROM decisions WHERE id = ? AND decision IN ('ACTION', 'SUGGEST_ACTION') AND action_json IS NOT NULL",
                (decision_id,),
            ).fetchone()
            if decision is None:
                return False
            if source == "explicit_user_feedback" and connection.execute(
                "SELECT 1 FROM decision_feedback WHERE decision_id = ? AND source = ?", (decision_id, source)
            ).fetchone():
                return False
            connection.execute(
                "INSERT INTO decision_feedback(decision_id, created_at, reward, source) VALUES (?, ?, ?, ?)",
                (decision_id, utc_now(), max(-1.0, min(1.0, reward)), source),
            )
            return True

    def candidate_rows(self, context: dict[str, Any]) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return list(
                connection.execute(
                    """WITH observation_candidates AS (
                        SELECT action_json, COUNT(*) AS total,
                        SUM(CASE WHEN reward > 0 THEN 1 ELSE 0 END) AS positive,
                        AVG(reward) AS average_reward
                        FROM observations
                        WHERE context_json = ?
                        GROUP BY action_json
                    ), feedback_candidates AS (
                        SELECT d.context_json, d.action_json, COUNT(*) AS feedback_count,
                        AVG(f.reward) AS feedback_average
                        FROM decision_feedback f
                        JOIN decisions d ON d.id = f.decision_id
                        GROUP BY d.context_json, d.action_json
                    )
                    SELECT o.action_json, o.total, o.positive, o.average_reward,
                           COALESCE(f.feedback_count, 0) AS feedback_count,
                           COALESCE(f.feedback_average, 0.0) AS feedback_average
                    FROM observation_candidates o
                    LEFT JOIN feedback_candidates f
                      ON f.context_json = ? AND f.action_json = o.action_json
                    ORDER BY o.positive DESC, o.total DESC""",
                    (json.dumps(context, sort_keys=True), json.dumps(context, sort_keys=True)),
                )
            )

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, created_at, context_json, action_json, source, reward FROM observations ORDER BY created_at DESC, id DESC LIMIT ?",
                (max(1, min(100, limit)),),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "context": json.loads(row["context_json"]),
                "action": json.loads(row["action_json"]),
                "source": row["source"],
                "reward": row["reward"],
            }
            for row in rows
        ]

    def stats(self) -> dict[str, int]:
        with self._connect() as connection:
            observations = connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
            decisions = connection.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
            snapshots = connection.execute("SELECT COUNT(*) FROM state_snapshots").fetchone()[0]
            feedback = connection.execute("SELECT COUNT(*) FROM decision_feedback").fetchone()[0]
            environmental_events = connection.execute("SELECT COUNT(*) FROM environmental_events").fetchone()[0]
            inventory_entities = connection.execute("SELECT COUNT(*) FROM inventory_entities").fetchone()[0]
            experiences = connection.execute("SELECT COUNT(*) FROM learning_experiences").fetchone()[0]
            shadow_opportunities = connection.execute("SELECT COUNT(*) FROM shadow_opportunities").fetchone()[0]
            action_outcomes = connection.execute("SELECT COUNT(*) FROM action_outcomes").fetchone()[0]
        return {
            "observations": observations, "decisions": decisions, "snapshots": snapshots,
            "decision_feedback": feedback, "environmental_events": environmental_events,
            "inventory_entities": inventory_entities,
            "experiences": experiences, "shadow_opportunities": shadow_opportunities,
            "action_outcomes": action_outcomes,
        }

