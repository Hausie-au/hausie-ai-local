from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    ha_url: str
    ha_token: str
    cloud_url: str
    cloud_token: str
    device_id: str
    poll_interval_seconds: int
    auto_act: bool
    dry_run: bool
    min_observations: int
    min_confidence: float
    learn_from_unknown: bool
    event_stream_enabled: bool
    decision_interval_seconds: int
    action_cooldown_seconds: int
    reversal_window_seconds: int
    log_level: str

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            data_dir=Path(os.getenv("HAUSIE_AI_DATA_DIR", "data")),
            ha_url=os.getenv("HA_URL", "http://supervisor/core").rstrip("/"),
            ha_token=os.getenv("HA_TOKEN", "") or os.getenv("SUPERVISOR_TOKEN", ""),
            cloud_url=os.getenv("HAUSIE_AI_CLOUD_URL", "").rstrip("/"),
            cloud_token=os.getenv("HAUSIE_AI_CLOUD_TOKEN", ""),
            device_id=os.getenv("HAUSIE_AI_DEVICE_ID", ""),
            poll_interval_seconds=max(10, _env_int("HAUSIE_AI_POLL_INTERVAL_SECONDS", 30)),
            auto_act=_env_bool("HAUSIE_AI_AUTO_ACT", False),
            dry_run=_env_bool("HAUSIE_AI_DRY_RUN", True),
            min_observations=max(1, _env_int("HAUSIE_AI_MIN_OBSERVATIONS", 3)),
            min_confidence=min(1.0, max(0.0, _env_float("HAUSIE_AI_MIN_CONFIDENCE", 0.8))),
            learn_from_unknown=_env_bool("HAUSIE_AI_LEARN_FROM_UNKNOWN", False),
            event_stream_enabled=_env_bool("HAUSIE_AI_EVENT_STREAM_ENABLED", True),
            decision_interval_seconds=max(30, _env_int("HAUSIE_AI_DECISION_INTERVAL_SECONDS", 60)),
            action_cooldown_seconds=max(30, _env_int("HAUSIE_AI_ACTION_COOLDOWN_SECONDS", 900)),
            reversal_window_seconds=max(10, _env_int("HAUSIE_AI_REVERSAL_WINDOW_SECONDS", 120)),
            log_level=os.getenv("HAUSIE_AI_LOG_LEVEL", "INFO").upper(),
        )

    def with_data_dir(self, data_dir: Path) -> "Settings":
        return Settings(
            data_dir=data_dir,
            ha_url=self.ha_url,
            ha_token=self.ha_token,
            cloud_url=self.cloud_url,
            cloud_token=self.cloud_token,
            device_id=self.device_id,
            poll_interval_seconds=self.poll_interval_seconds,
            auto_act=self.auto_act,
            dry_run=self.dry_run,
            min_observations=self.min_observations,
            min_confidence=self.min_confidence,
            learn_from_unknown=self.learn_from_unknown,
            event_stream_enabled=self.event_stream_enabled,
            decision_interval_seconds=self.decision_interval_seconds,
            action_cooldown_seconds=self.action_cooldown_seconds,
            reversal_window_seconds=self.reversal_window_seconds,
            log_level=self.log_level,
        )


def load_options(path: Path = Path("/data/options.json")) -> dict[str, Any]:
    """Read add-on options for environments that do not use run.sh."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
