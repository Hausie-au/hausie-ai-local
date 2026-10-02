# Hausie AI

Hausie AI is a local-first Home Assistant app that observes household context,
learns from repeated **explicit user actions**, and produces an explainable
decision: suggest one low-risk action or do nothing.

It is an experimental proof of concept, not a replacement for Home Assistant
automations and not a chatbot. Home Assistant remains the device platform;
Hausie AI is the local decision layer above it.

## What it does today

- Subscribes to Home Assistant `state_changed` events over the Supervisor API.
- Resynchronises the full Home Assistant state regularly as a safe fallback.
- Builds a compact context: weekday, 15-minute time bucket and occupancy.
- Learns low-risk actions for lights, covers and media players from repeated
  actions made by an identifiable Home Assistant user.
- Uses a transparent frequency model with minimum-observation and confidence
  thresholds.
- Chooses `DO_NOTHING`, `SUGGEST_ACTION`, or an opt-in `ACTION`.
- Keeps its SQLite database, snapshots, observations and feedback in the
  app's persistent `/data` volume.
- Logs every relevant step to the Home Assistant app log so the behaviour is
  visible before any automatic action is enabled.
- Optionally sends an aggregate heartbeat to Hausie AI Cloud. It never sends
  raw states, entity IDs, device names or household timelines to the cloud.

## How the learning loop works

```text
Home Assistant event
        |
        v
Context before the action
(weekday, 15-minute bucket, occupancy)
        |
        v
Explicit user action, for example light.living_room: off -> on
        |
        v
Local SQLite observation
        |
        v
Frequency learner
        |
        +--> DO_NOTHING: insufficient evidence, low confidence or unsafe
        |
        +--> SUGGEST_ACTION: a learned low-risk action is eligible
        |
        +--> ACTION: only when auto_act=true and dry_run=false
```

Example: three Tuesday evening user actions that turn on the living-room light
while the house is occupied create evidence for that action in that context.
When the same context occurs again, Hausie AI can suggest it. A single event is
never enough.

## Safety and privacy

Safety is independent of the learner. The current allow-list is deliberately
small:

- `light`: on/off/toggle
- `cover`: open/close/stop/set position
- `media_player`: on/off/play/pause/stop

Locks, alarms, cameras, doors, garages, climate, switches and arbitrary
services are blocked. Automatic action is disabled by default.

The app uses the Home Assistant Supervisor proxy and its app token. It does not
need to expose Home Assistant to the internet, map the Home Assistant config
directory, or request privileged container access. Its database stays in the
app data volume and is included in standard Home Assistant backups.

Home Assistant event context can identify an action as coming from a user,
automation or an unknown source. By default, only `user` events train the
model. Existing automations and unknown events are shown in the log but are not
treated as household preferences. You can enable `learn_from_unknown` only for
controlled experiments.

## Install on a Home Assistant Pi

This repository is a standalone custom app repository. In Home Assistant:

1. Go to **Settings → Apps** (or **Add-ons**) → **App Store**.
2. Open the menu in the top right, choose **Repositories**, and add:

   ```text
   https://github.com/Hausie-au/hausie-ai-local
   ```

3. Refresh the store, find **Hausie AI**, install it and keep the default
   configuration.
4. Start it and open the **Log** tab. The first startup creates
   `/data/hausie_ai.sqlite3` automatically.

The default configuration is safe for a real home:

```yaml
event_stream_enabled: true
auto_act: false
dry_run: true
learn_from_unknown: false
log_level: info
```

Use `log_level: debug` when you also want to see `DO_NOTHING` decisions.

## What you will see in the terminal/log

The app writes structured messages to standard output, visible in the Home
Assistant Log tab. It never logs tokens.

```text
STARTUP version=0.2.0 mode=observe-and-suggest events=True ...
EVENT_STREAM connected subscription=state_changed
SNAPSHOT entities=214 observations=0
EVENT source=user entity=light.living_room state=off->on
LEARN observation_id=12 source=user action={'domain': 'light', ...}
DECISION trigger=scheduled decision=SUGGEST_ACTION confidence=1.00 ...
```

To view logs from the Home Assistant CLI, first find the full app slug with
`ha addons list`, then run `ha addons logs <full_app_slug>`. The UI Log tab is
the easiest and most portable option on Home Assistant OS.

## When to enable automatic actions

Do not enable this on a customer home until a pattern has been reviewed in the
log. When you are ready to test one controlled low-risk action:

1. Keep the minimum observations and confidence threshold conservative.
2. Confirm repeated `SUGGEST_ACTION` entries make sense.
3. Set `dry_run: false` while `auto_act` remains `false`; verify nothing acts.
4. Set `auto_act: true` only for the supervised test.
5. Use the app log to watch the service call and any quick user reversal.

An automatic action has a per-entity cooldown. If a user reverses it inside
the configured reversal window, Hausie AI records negative decision feedback.

## API for local inspection and controlled tests

The Ingress panel exposes FastAPI documentation at `/docs`. Useful endpoints:

- `GET /api/v1/status` — runtime status and counters.
- `GET /api/v1/observations` — recent local learning observations.
- `POST /api/v1/observe` — seed a controlled explicit-user observation.
- `POST /api/v1/decide` — inspect a decision for a supplied context.
- `POST /api/v1/feedback` — attach feedback to an observation.
- `POST /api/v1/decisions/{id}/feedback` — attach feedback to a decision.

The endpoints are intended for local development and Home Assistant Ingress,
not for exposing a public service.

## Develop locally

```powershell
git clone https://github.com/Hausie-au/hausie-ai-local.git
cd hausie-ai-local
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r hausie_ai\requirements.txt pytest httpx
pytest -q
$env:HAUSIE_AI_DATA_DIR = "data"
uvicorn hausie_ai.app.main:app --reload --port 8099
```

Without a Home Assistant token, the app still starts and the API/test suite is
usable; its collector and event stream will log connection failures until a
Home Assistant instance is available.

## Repository boundary

This repository contains only the local Home Assistant runtime. The companion
[Hausie AI Cloud](https://github.com/Hausie-au/hausie-ai-cloud) is an optional
control plane for aggregate health and policy. Existing Hausie repositories
continue to own customer accounts, billing, installation operations, support,
dashboards and generated Home Assistant configuration.

