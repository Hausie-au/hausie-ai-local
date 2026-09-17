# Hausie AI add-on

The add-on observes Home Assistant state through the Supervisor API and stores
learning data in its `/data` directory. No cloud connection is needed for the
local learner.

The cloud settings are optional. If configured, only a heartbeat containing
the add-on version, capability names and aggregate counters is sent. Raw state
snapshots and entity IDs are never uploaded by this add-on.

