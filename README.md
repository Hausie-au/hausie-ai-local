# Hausie AI Local

Hausie AI Local is the first proof of concept for the Hausie AI brain. It is
packaged as a Home Assistant add-on and keeps the household's learning data on
the Home Assistant installation.

The add-on is intentionally small and interpretable:

- collects Home Assistant state snapshots through the Supervisor API;
- accepts explicit user observations for the first learning loop;
- groups observations by weekday, time bucket and occupancy;
- recommends one low-risk action or `DO_NOTHING`;
- requires a minimum number of observations and a confidence threshold;
- applies an independent safety policy before any service call;
- stores observations and feedback in SQLite under `/data`;
- can optionally send only health and aggregate counters to Hausie AI Cloud.

Automatic actions are disabled by default. The safe first run is:

```yaml
auto_act: false
dry_run: true
```

## Install in Home Assistant

Add the repository URL to the Home Assistant add-on store:

```text
https://github.com/Hausie-au/hausie-ai-local
```

Install **Hausie AI** and configure at least the Supervisor-provided Home
Assistant access. The add-on declares `homeassistant_api: true`, so the normal
Home Assistant add-on token is available through `SUPERVISOR_TOKEN`. A manual
`ha_token` is supported for local development only.

## Local development

```powershell
cd hausie-ai-local
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r hausie_ai\requirements.txt
pytest -q
uvicorn hausie_ai.app.main:app --reload --port 8099
```

The local server can run without Home Assistant for API and learner tests. To
seed the model, post explicit user observations:

```powershell
Invoke-RestMethod http://localhost:8099/api/v1/observe -Method Post -ContentType 'application/json' -Body (@{
  context = @{ weekday = 1; hour_bucket = 80; occupancy = 'occupied' }
  action = @{ domain = 'light'; service = 'turn_on'; entity_id = 'light.living_room' }
  source = 'user'
} | ConvertTo-Json)
```

Repeat the observation three times, then ask for a decision:

```powershell
Invoke-RestMethod http://localhost:8099/api/v1/decide -Method Post -ContentType 'application/json' -Body (@{
  context = @{ weekday = 1; hour_bucket = 80; occupancy = 'occupied' }
} | ConvertTo-Json)
```

The response contains the selected action, confidence, safety decision and
the reason. The decision endpoint never executes an action unless both
`auto_act=true` and `dry_run=false` are configured.

## Scope boundary

This repository is the local runtime, not a replacement for Home Assistant and
not a voice assistant. The existing Hausie App continues to own dashboards,
installation workflows, support and generated configuration. Hausie AI only
observes, learns and proposes low-risk contextual actions.

The companion cloud repository is [hausie-ai-cloud](https://github.com/Hausie-au/hausie-ai-cloud).
It receives health and aggregate metrics only; raw state history stays local.

