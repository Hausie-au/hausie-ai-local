# Hausie AI Local PoC architecture

```text
Home Assistant REST API
        |
        v
  state collector -----> SQLite under /data
        |
        v
 compact context (weekday, 15-minute bucket, occupancy)
        |
        v
 frequency learner -----> candidate or DO_NOTHING
        |
        v
 deterministic safety policy
        |
        +----> suggestion
        +----> Home Assistant service call (opt-in only)
```

The first model is intentionally a contextual frequency table. A row is
learned only from an explicit user observation unless `learn_from_unknown` is
enabled for controlled experiments. The cloud integration sends heartbeat and
aggregate counters only; it is not in the inference path.

