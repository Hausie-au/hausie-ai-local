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
                """
            )

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
        return {"observations": observations, "decisions": decisions, "snapshots": snapshots, "decision_feedback": feedback}

