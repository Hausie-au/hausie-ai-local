# Hausie AI

Hausie AI is a local-first Home Assistant app that observes household context,
learns from repeated **attributed user actions and confirmed physical-button effects**, and produces an explainable
decision: suggest one low-risk action or do nothing.

It is an experimental proof of concept, not a replacement for Home Assistant
automations and not a chatbot. Home Assistant remains the device platform;
Hausie AI is the local decision layer above it.

## What it does today

- Subscribes to Home Assistant `state_changed` events over the Supervisor API.
- Before live decisions, backfills retained Home Assistant Recorder history (30
  days of requested lookback by default) in one-day windows. Logbook provides
  user attribution. Physical-button events can also confirm a safe
  device effect; unrelated automated changes do not become preferences.
- Resynchronises the full Home Assistant state regularly as a safe fallback.
- Builds a local inventory from Home Assistant's state, area, device, entity
  and label registries. This is the same source data that Hausie uses to build
  its detailed household inventory; it does not require a cloud round trip.
- Builds context from weekday, a 15-minute time bucket, occupancy and
  recognised environmental inputs. Temperature, humidity, illuminance,
  pressure, air-quality and opening sensors are converted into explainable
  bands such as `comfortable`, `dark`, `elevated` or `open`.
- Persists environmental and occupancy state changes in local SQLite as they
  happen. A sensor does not need to wait for the next 15-minute bucket to be
  recorded.
- Learns low-risk actions for lights, covers and media players from repeated
  attributed user actions, plus confirmed physical-button effects for known
  TEST_HAUSIE controls or newly discovered controls with a direct Home Assistant
  context link. A button press is never assigned to a named person.
- Uses a transparent frequency model with minimum-observation and confidence
  thresholds.
- Registers 31 local methods across action prediction, sensor forecasting,
  observed action responses, explicit preference feedback, anomaly detection,
  action timing and next-event sequences.
  The original exact-context learner is the only method that can feed decisions;
  the other 30 methods never call Home Assistant services.
- Associates a user action with the latest relevant, same-area stimulus within
  10 minutes. Predictions are saved before the later action trains the models.
- Records area sensor values before a manual action and approximately 30
  minutes later, and scores prior predictions against the later readings.
  This is observational evidence, never proof of causation.
- Chooses `DO_NOTHING`, `SUGGEST_ACTION`, or an opt-in `ACTION`.
- Keeps its SQLite database, snapshots, observations and feedback in the
  app's persistent `/data` volume.
- Logs every relevant step to the Home Assistant app log so the behaviour is
  visible before any automatic action is enabled.
- Provides an Ingress panel that shows the current context, the full local
  inventory, safety classification, environmental history and decisions.
- Optionally sends an aggregate heartbeat to Hausie AI Cloud. It never sends
  raw states, entity IDs, device names or household timelines to the cloud.

## How the learning loop works

On first startup after installing 0.8.0 or newer, Hausie AI first reads its current
inventory to know which entities are relevant, then imports the available
historical changes. The live event stream is buffered during this import;
suggestions and automatic actions wait for completion. The Overview page and
terminal show progress and counts. On error, live learning resumes and the
import retries on the next app start. Successful imports are remembered in
SQLite and repeated restarts do not duplicate training rows. Increasing
`history_import_days` or changing the inventory causes an idempotent rescan on
the next app start. Ordinary restarts fetch the interval missed while the app
was stopped, with a small overlap for Recorder commit delays. A final short
catch-up covers the time spent doing the initial import itself.

The import uses Home Assistant's `/api/history/period` for state transitions
and `/api/logbook` for user attribution; it never opens or writes Home
Assistant's Recorder database directly. The requested lookback cannot recover
data older than Home Assistant actually retained, including entities excluded
from Recorder or Logbook. Its first state for an entity is a baseline, not a
trainable action. Physical button presses or integrations with no attributable
user ID remain unlabelled unless a known physical button press, its helper
selection at that time and a confirmed safe device effect can be linked, or a
newly discovered button has a direct live Home Assistant context link to the
effect. The
Environmental activity page audits imported action-like changes as `user`,
`physical_button`, `automation` or `unknown`. Historical readings
seed sensor histories, including same-local-hour numeric baselines; verified actions
seed the main learner and action shadow models. Where a relevant same-area
sensor change preceded an action within ten minutes, the historical episode
also seeds stimulus/action and action-delay methods. It does not fabricate historic feedback,
causal response labels or retrospective prediction accuracy; those still need
future observations. No historical action is replayed on a device.

Attribution is a conservative match on entity, state and timestamp within two
seconds; it is not proof of causality. For larger Recorder databases, set
`history_import_days` between 0 (disable)
and 365. The default is 30; the UI reports how many rows were actually
imported. Imports are bounded to 300,000 state rows per day and pause with a
visible error if that limit is exceeded rather than exhausting the Pi's RAM.

### Learning from physical controls

Version 0.10.0 still supports the TEST_HAUSIE Cube, Ali button, IKEA dual button and
BILRESA wheel event entities configured in this home. It also discovers any
Home Assistant `event.*` entity carrying the `button` label (on the entity or
device) or the `button` device class. See the **Physical buttons** inventory
tab and **Physical button learning** activity table. Home Assistant `button.*`
entities are virtual controls, not evidence that a physical button was pressed;
integrations that emit only raw bus events without an event entity need a
separate adapter. The `input_select`
helpers are **mappings**, not button-press evidence. When one of those event
entities changes, Hausie records its gesture, current helper selection,
operation, timestamp and pre-press context with `actor=unknown`. It checks the
enabled/type/mode helpers so that an inactive mapping is not mistaken for an
executed control. A mapped, safe light/cover destination must actually change
within eight seconds before the action trains the frequency learner and
applicable shadow action methods. For dials, a brightness or cover-position
attribute change can confirm the effect even when the entity remains `on` or
`open`. These become absolute light brightness / cover position actions; they
are still subject to the same safety layer and default dry-run behaviour.
For a newly discovered, unmapped button, timing alone is insufficient: a safe
effect must occur within eight seconds and its live Home Assistant context ID
or direct parent ID must match the press context ID. Otherwise the press is
audited but does not train an action. Some integrations do not propagate such
a context link, so their presses can be seen without producing confirmed
effects until an explicit mapping/adapter is provided.

The historic import now reads retained button event attributes and the helper
values that existed at each press time. It rechecks existing 0.8.0 history
idempotently so an earlier `automation`/`unknown` row may be upgraded to
`physical_button` when the causal chain is sufficiently specific. It never
replays an old press. Newly discovered unmapped historical presses are audited,
but not attributed to actions: Recorder REST history does not carry the live
context chain required for that match. Unmapped selections, disabled controls, unsupported
domains, missing Recorder events, or changes that cannot be confidently tied
to a known destination are audited as presses but do not train an executable
action. Attribution is a bounded temporal match, not proof of causality; the
Activity page exposes each press and its confirmed effect count for review.

```text
Home Assistant event
        |
        v
Context before the action
(weekday, 15-minute bucket, occupancy,
 environmental bands)
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
while the house is occupied and the room is `dark` create evidence for that
action in that context. When the same context occurs again, Hausie AI can
suggest it. A single event is never enough.

### What the 15-minute bucket means

The 15-minute bucket is a calendar feature, not a sensor polling or recording
interval. `19:00` through `19:14` are one part of the day; `19:15` through
`19:29` are the next. It lets a pattern distinguish morning from evening
without requiring an exact minute match. The real-time event stream records
environmental changes immediately, while the periodic snapshot is a fallback
and refreshes the inventory.

### Inventory and environmental inputs

At startup, and then periodically, the app reads the live state snapshot plus
the Home Assistant area, device, entity and label registries. Each entity is
displayed in the panel as one or more of the following:

- **Environmental input** — a recognised ambient variable used in the current
  context.
- **Context input** — person, tracker, motion, occupancy, presence or opening
  information used to infer household presence.
- **Safe action target** — a light, cover or media player that may be proposed
  after learning enough evidence. It is not automatically controlled by
  default.
- **Blocked action target** — for example climate, switch, lock, alarm or
  camera. It remains visible for transparency, but the safety layer blocks it.
- **Observed only** — an entity that is currently neither a supported input nor
  an eligible action target.

Hausie AI intentionally uses normalized bands rather than raw changing values
in its first learner. For example, `23.8 C` and `24.2 C` both become
`comfortable`. This prevents a new model context being created for every small
sensor fluctuation while still making the environmental condition visible.

### Event and seasonal learners (shadow mode)

The original exact-context learner remains the only method feeding the
Decisions page and the opt-in automatic-action path. It is also the reference
against which the new methods are compared. No new method is promoted silently.

When an area sensor changes band or crosses a meaningful cumulative threshold
(temperature 0.5 C, humidity/moisture 5 points, illuminance 20 lux), or an
area presence/opening input changes, the add-on creates a local opportunity.
All action methods predict at that point, before any later user action is
recorded. If an identifiable user changes a low-risk device in the same area
within 10 minutes, the latest compatible opportunity is labelled with that
action. Automation-origin and unknown-origin actions do not label shadow
opportunities; `learn_from_unknown` affects only the original learner.

The event learner compares stimulus kind, direction and area without requiring
identical weekdays or unrelated environmental readings. The adaptive/seasonal
learner additionally weights recent examples more and gives a modest
preference to the same Australian meteorological season. Neither requires
waiting a full year. Both require enough comparable actions and sufficient
agreement among alternatives. Predictions pass the shared safety check and
cannot execute devices. There is no automatic winner selection.

The comparison page reports opportunities, predictions, opportunities with a
user action, matches and different predicted actions. These are not acceptance
or causal-effect metrics: an unlabelled opportunity is not counted as a failure,
and a match only means Hausie predicted what the user later did. Only visible
suggestions on the Decisions page can receive explicit helpful/not-helpful
feedback. Shadow predictions cannot be rated because they were never shown.

The observed after-effects section compares same-area environmental sensors
before a user action and roughly 30 minutes later. Other user actions in that
area are counted, but weather, automations and unobserved factors may still
explain the difference. This is inspection-only and does not train an action
policy. Episodes created before installing 0.5.0 are not backfilled; existing
SQLite observations remain intact.

### The 31-method local lab (0.7.0)

The Learning methods page has seven tabs. Forecasts are saved *before* the
later observation or explicit feedback arrives; anomaly methods score a
change after it is observed. No new method is
allowed to execute a Home Assistant service or silently replace the original
decision policy. The lab is intentionally small and interpretable.

| Task | Method | What it tries |
| --- | --- | --- |
| Action | Exact context | Original weekday/time/occupancy/environment frequency reference; only method that can feed suggestions. |
| Action | Event matching | Match the changed variable, direction and area. |
| Action | Adaptive seasonal | Event matching with recent and same-season examples weighted more. |
| Action | Area frequency (no context) | Count manual actions in the same area, ignoring time and ambient readings. Area is retained to avoid mixing rooms. |
| Action | Hierarchical backoff | Try area + occupancy + event kind, then area + kind, then area. |
| Action | Nearest experiences | Weight similar past events and contexts instead of requiring an exact match. |
| Action | Previous action | Estimate the next action from the last manual action in the area (within two hours). |
| Action | Sensor event sequence | Match the two most recent significant sensor changes in the area. |
| Action | Time-of-day action | Use same-area manual actions within one local clock hour. |
| Action | Recent action frequency | Weight same-area manual actions by a 14-day half-life. |
| Action | Probabilistic event features | Use smoothed conditional frequencies for event, occupancy, month and time features. |
| Sensor | Last value | Assume a temperature, humidity or illuminance sensor stays where it is for 30 minutes. |
| Sensor | Recent trend | Extrapolate a bounded recent change over 30 minutes. |
| Sensor | Same-hour history | Use completed readings from the same local clock hour, once enough exist. |
| Sensor | Rolling average | Average the latest numeric readings, including the current one. |
| Sensor | Rolling median | Use a robust median of those readings. |
| Sensor | Recent-value weighted average | Weight the most recent readings more heavily. |
| Sensor | Nearby multi-sensor situations | Match prior completed windows using this sensor and other same-area readings. |
| Response | Mean observed change | Predict a 30-minute sensor delta from previous clean windows after the same manual action. |
| Response | Similar starting value | Predict that delta from windows with similar initial readings. |
| Response | Median observed change | Use the median delta after the same manual action. |
| Preference | Explicit feedback | Smooth Helpful / Not helpful ratings into a probability for the same suggested action. |
| Preference | Recent explicit feedback | Weight newer explicit ratings more heavily. |
| Preference | Occupancy-specific feedback | Use explicit ratings for the same action and occupancy state. |
| Anomaly | Unusual sensor change | Compare change size with a robust median and median absolute deviation from earlier changes. |
| Anomaly | Unusual sensor value | Compare the current value with earlier readings. |
| Anomaly | Recent-vs-earlier shift | Compare the last five values with the preceding five; this alone does not prove a durable regime change. |
| Timing | Typical action delay in area | Estimate seconds to an identifiable manual action from past labelled area events. |
| Timing | Event-specific action delay | Estimate that delay for the same area and stimulus type. |
| Next event | Next sensor-event transition | Predict the next significant event from the current event type. |
| Next event | Two-event sequence | Predict from the previous and current significant events. |

Action methods share the same event opportunities. The first identifiable
user action in the same area within 10 minutes supplies a label; an unlabelled
opportunity is *not* scored as a wrong prediction. Predictions from unsafe or
already-satisfied targets are filtered before saving. Action agreement and
coverage should be inspected separately; neither is an acceptance rate.

Sensor forecasts are sampled at most once per entity per 30 minutes and are
evaluated at the first periodic snapshot 30–45 minutes later; stale or missing
readings remain unevaluated. Mean absolute error is shown separately by sensor
kind and unit, so temperature and lux are never averaged together. Response
predictions are evaluated only on completed windows without another known
user action in the area; their error is shown per sensor. Other automations,
weather or occupancy may still confound the reading. The preference model
needs at least two explicit ratings of the same action before it predicts;
its Brier score is computed only when a later suggestion is explicitly rated.
The anomaly model needs five earlier numeric changes, then lets you review
flagged readings as Expected or Surprising. No review is inferred from silence.
Timing models are scored only when an identifiable user acts within ten
minutes; no action is censored, not a wrong timing prediction. Next-event
models are scored when another significant same-area event arrives within
two hours; they do not infer named activities such as cooking or sleeping.
The add-on now obtains the configured Home Assistant time zone from
`GET /api/config` for clock and seasonal features, falling back to the
container zone only if that request fails. Historical 0.6.0 same-hour sensor
records are preserved but cannot be reinterpreted reliably if the container
previously used UTC.

All records live in additive SQLite tables under the existing persistent
`/data` volume; upgrades preserve earlier history. Models that lack evidence
abstain instead of inventing a result. The lab does not yet rank methods or
promote a winner; compare methods within the *same* task only.

This breadth is bounded by available evidence and Raspberry Pi resources. It
does **not** include a causal intervention learner, online exploration or
reinforcement learning, per-person identification, named activity recognition,
or a calibrated physical HVAC controller. Those require additional reliable
labels, consent and/or safe controls; storing a before/after sensor reading is
not sufficient. The larger lab retains detailed local histories indefinitely,
so its SQLite file will grow with sensor count and runtime. Back up the add-on
data before major upgrades and monitor available disk space; the add-on does
not silently delete observations.

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
  Version 0.8.0 also imports retained Home Assistant history before live
  decisions; watch **Initial Home Assistant history import** on Overview.
5. Open **Hausie AI** from the sidebar or use **Open Web UI** on its app page.
   The panel uses Home Assistant Ingress and has no separate network port to
   open. The Overview links to separate Inventory, Environmental activity and
   Decisions pages.

**What Hausie AI can see** is a dedicated Inventory page.
Browse Home Assistant devices (expand each device for its entities), sensors
and presence, automations, action targets, other entities or all entities.
Search and paginate the results, or combine filters for area, domain, state,
device class, Hausie AI role, safety classification and Home Assistant label.
Device grouping requires Home Assistant's device registry; entities remain
visible in the other categories while that registry is unavailable. The new
Learning methods page also needs area assignments to associate a room sensor
with an action target. Without an assigned area, the original learner still
works but the event cannot become a room-specific episode.

If the panel shows `{"detail":"Not Found"}`, refresh the App Store repository,
update Hausie AI to version `0.3.3` or newer, and reopen the panel. Earlier
versions configured the Ingress entry as `/ui`; Supervisor prepends a slash,
which can send `//ui` to the app. Version 0.3.3 uses the relative entry `ui`
and also accepts the old doubled-slash path. Restarting an older installed
version alone will not apply the fix. Do not uninstall the add-on to update:
uninstalling removes its local SQLite learning history.

The default configuration is safe for a real home:

```yaml
event_stream_enabled: true
history_import_days: 30
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
STARTUP version=0.10.0 mode=observe-and-suggest events=True ...
EVENT_STREAM connected subscription=state_changed
INVENTORY registry_sync areas=8 devices=74 entities=214 labels=12
HISTORY_BOOTSTRAP state=complete days=30 actions=42 environmental=1728 experiences=12 already_imported=False
SNAPSHOT entities=214 observations=0 environmental=18 context_inputs=7 safe_targets=21
ENVIRONMENT event_id=4 entity=sensor.living_room_temperature kind=temperature area=Living Room state=23.8->24.2 band=comfortable used_in_context=True
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

The read-only learning endpoints are `GET /api/v1/learning/lab` for all 31
models, `GET /api/v1/learning/comparison` for action predictions and
`GET /api/v1/learning/outcomes` for observational before/after sensor
readings. The anomaly-review endpoint accepts an explicit user judgement.
Existing observation, decision and feedback endpoints remain available.

The Ingress panel exposes FastAPI documentation at `/docs`. Useful endpoints:

- `GET /api/v1/status` — runtime status and counters.
- `GET /api/v1/inventory` — all visible entities with areas, labels, roles and
  safety status.
- `GET /api/v1/context` — the normalized context currently used for learning.
- `GET /api/v1/environment/events` — locally persisted ambient and occupancy
  changes.
- `GET /api/v1/decisions` — local decision history.
- `GET /api/v1/observations` — recent local learning observations.
- `POST /api/v1/observe` — seed a controlled explicit-user observation.
- `POST /api/v1/decide` — inspect a decision for a supplied context.
- `POST /api/v1/feedback` — attach feedback to an observation.
- `POST /api/v1/decisions/{id}/feedback` — attach feedback to a decision.
- `POST /api/v1/learning/anomalies/{id}/feedback` — review a scored anomaly
  with `{"surprising": true}` or `{"surprising": false}`.

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

