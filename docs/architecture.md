# Hausie AI Local PoC architecture

```text
Home Assistant Recorder history + Logbook (startup backfill)
        |                    Home Assistant live states + WebSocket registries
        v                                      |
conservative user attribution                v
        +------------> state collector + local inventory -----> SQLite under /data
        |                                                    |
        |                                                    +--> 30 shadow methods
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

On startup, the add-on imports bounded day-long Recorder windows before making
new decisions. The history endpoint supplies state transitions; the Logbook
supplies user attribution, so unverified/automated action-like changes are
audited but not trained as user preferences. Historic observations keep their
original timestamps. Restart catch-up overlaps the previous endpoint and uses
unique import keys to avoid duplicates. Live events buffer during backfill.
Historical data seeds the main frequency learner and relevant shadow histories
but does not manufacture feedback or score predictions retroactively. No
historical service call is replayed.

