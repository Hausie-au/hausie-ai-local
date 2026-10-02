# Hausie AI Local PoC architecture

```text
Home Assistant REST API + WebSocket registries
        |
        v
state collector + local inventory -----> SQLite under /data
        |
        v
context (weekday, 15-minute bucket, occupancy, environmental bands)
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

The inventory joins Home Assistant state with the area, device, entity and
label registries. It gives every entity a transparent local role:
environmental input, context input, safe action target, blocked action target
or observed only. Environmental state changes are persisted immediately; the
15-minute bucket is only a time feature used by the learner.

