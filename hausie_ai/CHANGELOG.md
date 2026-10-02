# Changelog

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

