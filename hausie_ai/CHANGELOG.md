# Changelog

## 0.7.0

- Expand the shadow lab from 15 to 31 implemented methods across seven tasks:
  add probabilistic/time-weighted action baselines, rolling and multi-sensor
  forecasts, robust response deltas, personalized feedback variants, value and
  shift anomaly detectors, action timing and next-event sequence prediction.
- Persist timing, transitions, multi-sensor features and model-specific
  feedback in additive SQLite tables; preserve prior observations and ratings.
- Compare every new method in the Ingress UI with task-appropriate coverage
  and metrics; unlabelled opportunities remain unscored.
- Sync Home Assistant's configured time zone for clock-based and seasonal
  features. All new methods remain observational; safe defaults are unchanged.

## 0.6.0

- Add 12 observational methods to the existing three, for 15 local models
  across action, sensor, response, preference and anomaly tasks.
- Add additive persistent SQLite histories and pre-outcome evaluation for
  numeric 30-minute sensor forecasts, observed response deltas and explicit
  feedback probabilities.
- Add five Learning methods tabs with separate, unit-aware metrics and manual
  anomaly reviews. Keep the original decision path and safe defaults unchanged.
- Keep all new methods in shadow mode; no automatic model promotion.

## 0.5.1

- Align the Dockerfile's default build version with the add-on version.
- Validate an ARM64 image build in CI for Raspberry Pi installations.

## 0.5.0

- Add event-based and adaptive/seasonal shadow learners alongside the exact
  baseline, with pre-action predictions and local comparison metrics.
- Capture same-area user-action episodes after significant environmental or
  presence changes; automation-origin actions are not preference labels.
- Add a Learning methods Ingress page, explicit suggestion feedback controls,
  and observational 30-minute before/after sensor readings.
- Preserve the original baseline action path and safe default settings.

## 0.4.0

- Split the Ingress UI into Overview, Inventory, Environmental activity and
  Decisions pages.
- Group the inventory by Home Assistant devices, sensors and presence,
  automations, action targets, other entities and all entities.
- Add inventory search, pagination and expandable device details, plus
  combinable filters for area, domain, state, device class, role, safety and label.

## 0.3.3

- Fix Ingress 404 caused by a leading slash in `ingress_entry`, which
  Supervisor concatenates after its own slash.
- Accept doubled leading slashes from already-open Ingress URLs.

## 0.3.2

- Matched the existing Hausie add-on's tolerant Ingress entrypoint handling,
  including repeated trailing slashes and a forwarded Ingress prefix.
- Log the requested path when an Ingress request still returns 404.

## 0.3.1

- Fixed the Home Assistant Ingress entrypoint by serving the panel at `/ui`.
- Made panel links and API requests resolve through the Ingress base path.

## 0.3.0

- Added a local Home Assistant inventory built from state, area, device, entity
  and label registries already used by Hausie.
- Added environmental context for recognised temperature, humidity, light,
  air-quality, pressure and opening sensors, using stable explainable bands.
- Persisted environmental and occupancy changes in SQLite.
- Added an Ingress observability panel and local API endpoints that show the
  inventory, current context, environmental events, safety classification and
  decisions.
- Kept `auto_act: false` and `dry_run: true` as the default configuration.

## 0.2.0

- Added the Home Assistant `state_changed` WebSocket stream with reconnects.
- Added terminal logs for events, context, learning, decisions, feedback and
  cloud heartbeats.
- Added origin-aware learning: explicit user actions train by default;
  automation and unknown events remain visible but do not train the model.
- Added scheduled decision checks, entity cooldowns and reversal feedback.
- Updated the container metadata and Supervisor configuration for current Home
  Assistant app requirements.

## 0.1.0

- Initial local-first Hausie AI proof of concept.
- Added Home Assistant state collector, SQLite learner, safety policy and
  ingress API.
- Added optional aggregate heartbeat to Hausie AI Cloud.
