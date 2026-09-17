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

exec python -m hausie_ai.app.main

