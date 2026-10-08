#!/bin/sh
set -eu

OPTIONS_FILE="/data/options.json"

if [ -f "$OPTIONS_FILE" ]; then
  eval "$(python - <<'PY'
import json
import shlex

with open('/data/options.json', 'r', encoding='utf-8') as handle:
    options = json.load(handle) or {}

mapping = {
    'cloud_url': 'HAUSIE_AI_CLOUD_URL',
    'cloud_token': 'HAUSIE_AI_CLOUD_TOKEN',
    'device_id': 'HAUSIE_AI_DEVICE_ID',
    'poll_interval_seconds': 'HAUSIE_AI_POLL_INTERVAL_SECONDS',
    'auto_act': 'HAUSIE_AI_AUTO_ACT',
    'dry_run': 'HAUSIE_AI_DRY_RUN',
    'min_observations': 'HAUSIE_AI_MIN_OBSERVATIONS',
    'min_confidence': 'HAUSIE_AI_MIN_CONFIDENCE',
    'learn_from_unknown': 'HAUSIE_AI_LEARN_FROM_UNKNOWN',
    'event_stream_enabled': 'HAUSIE_AI_EVENT_STREAM_ENABLED',
    'history_import_days': 'HAUSIE_AI_HISTORY_IMPORT_DAYS',
    'decision_interval_seconds': 'HAUSIE_AI_DECISION_INTERVAL_SECONDS',
    'action_cooldown_seconds': 'HAUSIE_AI_ACTION_COOLDOWN_SECONDS',
    'reversal_window_seconds': 'HAUSIE_AI_REVERSAL_WINDOW_SECONDS',
    'log_level': 'HAUSIE_AI_LOG_LEVEL',
}

for key, env_name in mapping.items():
    value = options.get(key)
    if value is None or value == '':
        continue
    if isinstance(value, bool):
        value = 'true' if value else 'false'
    print(f'export {env_name}={shlex.quote(str(value))}')
PY
)"
fi

export HAUSIE_AI_DATA_DIR="${HAUSIE_AI_DATA_DIR:-/data}"
export HA_URL="${HA_URL:-http://supervisor/core}"
export HA_TOKEN="${HA_TOKEN:-${SUPERVISOR_TOKEN:-}}"

echo "Hausie AI: starting local runtime (logs below show the learning flow)."

exec python -m hausie_ai.app.main

