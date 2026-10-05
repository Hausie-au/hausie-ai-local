# Hausie AI app

Hausie AI stores its local SQLite database in `/data/hausie_ai.sqlite3`. The
database is persistent across app restarts and is included in standard Home
Assistant backups.

The app has two Home Assistant inputs:

- A periodic REST state snapshot through `http://supervisor/core/api`.
- An optional real-time `state_changed` WebSocket subscription through the same
  Supervisor proxy.

The Ingress UI has separate Overview, Inventory, Environmental activity and
Decisions pages. The Inventory groups entities by devices, sensors and
presence, automations, action targets and other entities. Its filters can be
combined by area, domain, state, device class, Hausie AI role, safety
classification and Home Assistant label. It shows only data already available
to the local app; the UI does not change the learning or action safety rules.

No Home Assistant configuration files are mapped into the container and no
additional privileged capabilities are requested.

Keep `auto_act: false` and `dry_run: true` while reviewing the app log. Only
actions identified by Home Assistant as user actions become training data by
default. Existing automations and unknown sources are visible in the log but
are skipped unless `learn_from_unknown` is enabled for an experiment.

Cloud settings are optional. When configured, the app sends only an aggregate
heartbeat: app version, capabilities and counters. Raw state snapshots, entity
IDs and household timelines never leave the local app.

