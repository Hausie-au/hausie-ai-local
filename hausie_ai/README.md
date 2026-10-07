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

The **Learning methods** page groups 15 local methods into Actions, Sensors,
Responses, Preferences and Anomalies. The original exact-context learner is
the only one that can feed suggestions; all other methods are observational
and cannot control devices. Assign sensors and action targets to Home
Assistant areas to associate changes with user actions in the same room.

