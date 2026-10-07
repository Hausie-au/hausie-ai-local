# Hausie AI

Hausie AI is a local-first Home Assistant app. It uses the Supervisor API to
observe context, learn repeated explicit user actions and make explainable
low-risk suggestions.

Start in observation mode:

```yaml
auto_act: false
dry_run: true
log_level: info
```

Open the app **Log** tab to see the state event, source classification,
learning observation and decision flow. The full installation, safety and
privacy documentation is in the repository root `README.md`.

The **Learning methods** page groups 31 local methods into Actions, Sensors,
Responses, Preferences, Anomalies, Timing and Event sequences. The original exact-context learner is
the only one that can feed suggestions; all other methods are observational
and cannot control devices. Assign sensors and action targets to Home
Assistant areas to associate changes with user actions in the same room.
There is no automatic model promotion or causal claim from before/after
sensor readings. Home Assistant's configured time zone is used after sync.

