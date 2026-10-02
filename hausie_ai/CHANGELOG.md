# Changelog

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
