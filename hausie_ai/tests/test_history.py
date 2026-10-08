from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path

from hausie_ai.app.history import HistoryBootstrap
from hausie_ai.app.ha import HomeAssistantClient
from hausie_ai.app.inventory import InventoryBuilder
from hausie_ai.app.lab_storage import LabStore
from hausie_ai.app.safety import SafetyPolicy
from hausie_ai.app.service import HausieAIService
from hausie_ai.app.settings import Settings
from hausie_ai.app.storage import Store


class FakeHA:
    def __init__(self, rows, logbook):
        self.rows = rows
        self.logbook = logbook
        self.calls = []

    def get_history(self, start, end, entity_ids, include_attributes=False):
        self.calls.append(("history", start, end, tuple(entity_ids)))
        result = []
        for entity_id in entity_ids:
            rows = [row for row in self.rows if row["entity_id"] == entity_id and
                    datetime.fromisoformat(row["last_changed"]) < end]
            if rows:
                result.append(rows)
        return result

    def get_logbook(self, start, end, entity_ids):
        self.calls.append(("logbook", start, end, tuple(entity_ids)))
        return [row for row in self.logbook if row["entity_id"] in entity_ids]


def sample(at, entity_id, state):
    return {"entity_id": entity_id, "state": state, "last_changed": at.isoformat()}


def setup(tmp_path: Path):
    store = Store(tmp_path)
    LabStore(store)
    safety = SafetyPolicy()
    states = [
        {"entity_id": "sensor.room_lux", "state": "50", "attributes": {"device_class": "illuminance"}},
        {"entity_id": "light.room", "state": "off", "attributes": {}},
    ]
    registry = {"areas": [{"id": "room", "name": "Room"}],
                "entities": [{"entity_id": item["entity_id"], "area_id": "room"} for item in states]}
    return store, safety, InventoryBuilder(safety).build(states, registry)


def test_history_seeds_only_attributed_user_actions_and_is_idempotent(tmp_path: Path):
    store, safety, profiles = setup(tmp_path)
    cutoff = datetime(2026, 10, 8, tzinfo=timezone.utc)
    baseline = cutoff - timedelta(days=2)
    stimulus = cutoff - timedelta(hours=2)
    user_action = stimulus + timedelta(minutes=1)
    automatic = stimulus + timedelta(minutes=3)
    ha = FakeHA([
        sample(baseline, "sensor.room_lux", "200"),
        sample(stimulus, "sensor.room_lux", "10"),
        sample(baseline, "light.room", "off"),
        sample(user_action, "light.room", "on"),
        sample(automatic, "light.room", "off"),
    ], [
        {"entity_id": "light.room", "when": user_action.isoformat(), "state": "on", "context_user_id": "real-person"},
        {"entity_id": "light.room", "when": automatic.isoformat(), "state": "off", "context_user_id": None},
    ])
    result = HistoryBootstrap(ha, store, safety, profiles, timezone.utc, 1).run(cutoff)
    assert result["state"] == "complete"
    assert result["actions"] == 1
    assert result["seen_actions"] == 2
    assert result["environmental"] == 1
    assert result["experiences"] == 1
    assert result["timings"] == 1
    assert LabStore(store).timing_examples("room", "illuminance") == [60.0]
    assert LabStore(store).completed_forecast_values("sensor.room_lux", "", stimulus.hour) == [10.0]
    observation = store.recent()[0]
    assert observation["source"] == "historical_user"
    assert observation["action"] == {"domain": "light", "entity_id": "light.room", "service": "turn_on"}
    assert observation["context"]["environment"] == {"room:illuminance": "dark"}
    assert store.experiences()[0]["trigger"]["kind"] == "illuminance"
    assert store.recent_environmental_events()[0]["created_at"] == stimulus.isoformat()
    assert [row["source"] for row in store.recent_historical_actions()] == ["unknown", "user"]
    second = HistoryBootstrap(ha, store, safety, profiles, timezone.utc, 1).run(cutoff)
    assert second["already_imported"] is True
    assert store.stats()["observations"] == 1
    assert len(ha.calls) == 2
    later_stimulus = cutoff + timedelta(minutes=45)
    later_action = later_stimulus + timedelta(minutes=1)
    ha.rows.extend([sample(later_stimulus, "sensor.room_lux", "180"),
                    sample(later_action, "light.room", "on")])
    ha.logbook.append({"entity_id": "light.room", "when": later_action.isoformat(),
                       "state": "on", "context_user_id": "real-person"})
    catchup = HistoryBootstrap(ha, store, safety, profiles, timezone.utc, 1).run(cutoff + timedelta(hours=1))
    assert catchup["new_actions"] == 1
    assert catchup["actions"] == 2
    assert store.stats()["observations"] == 2


def test_history_rejects_automation_even_with_inherited_user_id(tmp_path: Path):
    store, safety, profiles = setup(tmp_path)
    cutoff = datetime(2026, 10, 8, tzinfo=timezone.utc)
    baseline = cutoff - timedelta(days=2)
    changed = cutoff - timedelta(hours=1)
    ha = FakeHA([sample(baseline, "light.room", "off"), sample(changed, "light.room", "on")],
                [{"entity_id": "light.room", "when": changed.isoformat(), "state": "on",
                  "context_user_id": "person", "context_domain": "automation"}])
    result = HistoryBootstrap(ha, store, safety, profiles, timezone.utc, 1).run(cutoff)
    assert result["actions"] == 0
    assert store.recent_historical_actions()[0]["source"] == "automation"
    assert store.stats()["observations"] == 0


def test_late_logbook_attribution_promotes_unknown_once(tmp_path: Path):
    store, safety, profiles = setup(tmp_path)
    cutoff = datetime(2026, 10, 8, tzinfo=timezone.utc)
    baseline = cutoff - timedelta(days=2)
    changed = cutoff - timedelta(minutes=2)
    ha = FakeHA([sample(baseline, "light.room", "off"), sample(changed, "light.room", "on")], [])
    first = HistoryBootstrap(ha, store, safety, profiles, timezone.utc, 1).run(cutoff)
    assert first["seen_actions"] == 1
    assert first["actions"] == 0
    ha.logbook.append({"entity_id": "light.room", "when": changed.isoformat(),
                       "state": "on", "context_user_id": "person"})
    second = HistoryBootstrap(ha, store, safety, profiles, timezone.utc, 1).run(cutoff + timedelta(minutes=1))
    assert second["new_actions"] == 1
    assert second["actions"] == 1
    assert store.recent_historical_actions()[0]["source"] == "user"
    assert store.stats()["observations"] == 1


def test_reimport_does_not_duplicate_live_action(tmp_path: Path):
    store, safety, profiles = setup(tmp_path)
    cutoff = datetime.now(timezone.utc)
    baseline = cutoff - timedelta(days=2)
    action = {"domain": "light", "service": "turn_on", "entity_id": "light.room"}
    store.add_observation({"weekday": 0, "hour_bucket": 0, "occupancy": "unknown"}, action, "user")
    live_at = datetime.fromisoformat(store.recent()[0]["created_at"])
    ha = FakeHA([sample(baseline, "light.room", "off"), sample(live_at, "light.room", "on")],
                [{"entity_id": "light.room", "when": live_at.isoformat(), "state": "on",
                  "context_user_id": "person"}])
    result = HistoryBootstrap(ha, store, safety, profiles, timezone.utc, 1).run(cutoff + timedelta(seconds=1))
    assert result["actions"] == 0
    assert store.stats()["observations"] == 1


def test_history_requests_are_bounded_and_filtered():
    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    class Session:
        def __init__(self):
            self.headers = {}
            self.calls = []

        def get(self, url, params=None, timeout=None):
            self.calls.append((url, params, timeout))
            return Response([])

    session = Session()
    client = HomeAssistantClient("http://supervisor/core", "test-token", session)
    start = datetime(2026, 10, 7, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    assert client.get_history(start, end, ["light.room", "sensor.room_lux"]) == []
    assert client.get_history(start, end, ["event.button"], include_attributes=True) == []
    assert client.get_logbook(start, end, ["light.room"]) == []
    assert session.calls[0][0].endswith("/api/history/period/2026-10-07T00:00:00+00:00")
    assert session.calls[0][1]["filter_entity_id"] == "light.room,sensor.room_lux"
    assert "no_attributes" in session.calls[0][1]
    assert session.calls[1][1]["filter_entity_id"] == "event.button"
    assert "no_attributes" not in session.calls[1][1]
    assert "significant_changes_only" not in session.calls[1][1]
    assert session.calls[2][0].endswith("/api/logbook/2026-10-07T00:00:00+00:00")
    assert session.calls[2][1]["entity"] == "light.room"


def test_service_pauses_decisions_until_history_is_loaded(tmp_path: Path):
    settings = replace(Settings.from_environment().with_data_dir(tmp_path), history_import_days=1)
    service = HausieAIService(settings)
    service._history_ready = False
    context = {"weekday": 1, "hour_bucket": 20, "occupancy": "unknown"}
    blocked = service.decide(context, execute=True)
    assert blocked["decision"] == "DO_NOTHING"
    assert "history import" in blocked["reason"].lower()
    service.ha = FakeHA([], [])
    service._bootstrap_history()
    assert service._history_ready is True
    assert service.status()["history_import"]["state"] == "complete"
