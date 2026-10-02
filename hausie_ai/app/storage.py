from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
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
                """
            )

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
                   FROM environmental_events ORDER BY id DESC LIMIT ?""",
                (max(1, min(250, limit)),),
            ).fetchall()
        return [dict(row) | {"used_in_context": bool(row["used_in_context"])} for row in rows]

    def recent_decisions(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id, created_at, context_json, action_json, confidence, decision, reason, executed
                   FROM decisions ORDER BY id DESC LIMIT ?""",
                (max(1, min(100, limit)),),
            ).fetchall()
        return [
            {
                "id": row["id"], "created_at": row["created_at"], "context": json.loads(row["context_json"]),
                "action": json.loads(row["action_json"]) if row["action_json"] else None,
                "confidence": row["confidence"], "decision": row["decision"], "reason": row["reason"],
                "executed": bool(row["executed"]),
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
                "SELECT id FROM decisions WHERE id = ? AND action_json IS NOT NULL",
                (decision_id,),
            ).fetchone()
            if decision is None:
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
                "SELECT id, created_at, context_json, action_json, source, reward FROM observations ORDER BY id DESC LIMIT ?",
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
        return {
            "observations": observations, "decisions": decisions, "snapshots": snapshots,
            "decision_feedback": feedback, "environmental_events": environmental_events,
            "inventory_entities": inventory_entities,
        }

