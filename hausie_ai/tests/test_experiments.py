from pathlib import Path

from hausie_ai.app.experiments import ShadowLearners, season
from hausie_ai.app.learning import Learner
from hausie_ai.app.service import HausieAIService
from hausie_ai.app.settings import Settings
from hausie_ai.app.storage import Store


ACTION = {"domain": "light", "service": "turn_on", "entity_id": "light.living_room"}
TRIGGER = {"area": "living_room", "area_name": "Living Room", "entity_id": "sensor.living_room_light",
           "kind": "illuminance", "direction": "falling", "old_band": "bright", "new_band": "dim"}
REGISTRY = {
    "areas": [{"id": "living_room", "name": "Living Room"}],
    "entities": [{"entity_id": name, "area_id": "living_room"} for name in
                 ["sensor.living_room_light", "light.living_room"]],
    "devices": [], "labels": [],
}


def sensor_event(old: str, new: str) -> dict:
    return {"data": {"old_state": {"entity_id": "sensor.living_room_light", "state": old},
                     "new_state": {"entity_id": "sensor.living_room_light", "state": new,
                                   "attributes": {"device_class": "illuminance"}}}}


def user_light_event() -> dict:
    return {"data": {"old_state": {"entity_id": "light.living_room", "state": "off"},
                     "new_state": {"entity_id": "light.living_room", "state": "on", "attributes": {},
                                   "context": {"user_id": "user-1"}}}}


def test_event_learner_generalizes_over_weekday_and_temperature(tmp_path: Path):
    store = Store(tmp_path)
    model = ShadowLearners(store, Learner(store, 3, 0.8), 3, 0.8)
    for month, weekday, temperature in [(6, 0, "cool"), (7, 2, "cold"), (8, 5, "comfortable")]:
        store.add_experience({"month": month, "weekday": weekday, "occupancy": "occupied",
                              "environment": {"living_room:temperature": temperature}}, TRIGGER, ACTION, "user")
    context = {"month": 9, "weekday": 6, "occupancy": "occupied",
               "environment": {"living_room:temperature": "warm"}}
    predictions = model.predict(context, TRIGGER)
    assert predictions["exact"]["action"] is None
    assert predictions["event"]["action"] == ACTION
    assert predictions["adaptive_seasonal"]["action"] == ACTION
    assert season(1) == "summer" and season(7) == "winter"


def test_boolean_triggers_do_not_reverse_and_conflicting_actions_abstain(tmp_path: Path):
    store = Store(tmp_path)
    model = ShadowLearners(store, Learner(store, 3, 0.8), 3, 0.8)
    motion = dict(TRIGGER, kind="motion", direction="changed", old_band="inactive", new_band="active")
    for _ in range(3):
        store.add_experience({"occupancy": "occupied", "month": 7}, motion, ACTION, "user")
    assert model.predict({"occupancy": "occupied", "month": 7}, dict(motion, new_band="inactive"))["event"]["action"] is None
    opposite = dict(ACTION, service="turn_off")
    for _ in range(3):
        store.add_experience({"occupancy": "occupied", "month": 7}, motion, opposite, "user")
    assert model.predict({"occupancy": "occupied", "month": 7}, motion)["event"]["action"] is None


def test_seasonal_method_can_adapt_when_recent_summer_preference_changes(tmp_path: Path):
    store = Store(tmp_path)
    model = ShadowLearners(store, Learner(store, 3, 0.8), 3, 0.8)
    for _ in range(3):
        store.add_experience({"occupancy": "occupied", "month": 7}, TRIGGER, ACTION, "user")
    summer_action = dict(ACTION, service="turn_off")
    for _ in range(9):
        store.add_experience({"occupancy": "occupied", "month": 1}, TRIGGER, summer_action, "user")
    predictions = model.predict({"occupancy": "occupied", "month": 1}, TRIGGER)
    assert predictions["event"]["action"] is None  # 9/12 is below the 0.8 agreement threshold
    assert predictions["adaptive_seasonal"]["action"] == summer_action


def test_shadow_predictions_are_scored_before_user_action_and_never_execute(tmp_path: Path):
    service = HausieAIService(Settings.from_environment().with_data_dir(tmp_path))
    service.collect_states([
        {"entity_id": "sensor.living_room_light", "state": "200", "attributes": {"device_class": "illuminance"}},
        {"entity_id": "light.living_room", "state": "off", "attributes": {}},
    ], REGISTRY)
    service.ha.call_service = lambda _: (_ for _ in ()).throw(AssertionError("shadow learner called Home Assistant"))
    for old, new in [("200", "100"), ("100", "200"), ("200", "100"), ("100", "200"), ("200", "100")]:
        service.handle_state_changed(sensor_event(old, new))
        if new == "100":
            service.handle_state_changed(user_light_event())
            service.state_index["light.living_room"] = {"entity_id": "light.living_room", "state": "off", "attributes": {}}
    report = service.store.shadow_report()
    assert service.store.stats()["observations"] == 3
    assert len(service.store.experiences()) == 3
    assert all(row["matched"] == 0 for row in report["methods"])  # predictions preceded training
    service.handle_state_changed(sensor_event("100", "200"))
    service.handle_state_changed(sensor_event("200", "100"))
    recent = service.store.shadow_report()["recent"]
    latest = [row for row in recent if row["id"] == recent[0]["id"]]
    assert len(latest) == 8
    assert next(row for row in latest if row["method"] == "event")["action"] == ACTION
    assert next(row for row in latest if row["method"] == "adaptive_seasonal")["action"] == ACTION
    assert all(not row["actual_action"] for row in latest)
    service.handle_state_changed(user_light_event())
    after = service.store.shadow_report()
    assert next(row for row in after["methods"] if row["method"] == "event")["matched"] == 1
    assert next(row for row in after["methods"] if row["method"] == "event")["labelled"] == 4


def test_automation_action_does_not_label_or_train_shadow(tmp_path: Path):
    service = HausieAIService(Settings.from_environment().with_data_dir(tmp_path))
    service.collect_states([
        {"entity_id": "sensor.living_room_light", "state": "200", "attributes": {"device_class": "illuminance"}},
        {"entity_id": "light.living_room", "state": "off", "attributes": {}},
    ], REGISTRY)
    service.handle_state_changed(sensor_event("200", "100"))
    event = user_light_event()
    event["data"]["new_state"]["context"] = {"parent_id": "automation-1"}
    service.handle_state_changed(event)
    assert not service.store.experiences()
    assert all(row["labelled"] == 0 for row in service.store.shadow_report()["methods"])


def test_observational_outcomes_record_before_and_after_without_claiming_causation(tmp_path: Path):
    service = HausieAIService(Settings.from_environment().with_data_dir(tmp_path))
    service.collect_states([
        {"entity_id": "sensor.living_room_light", "state": "100", "attributes": {"device_class": "illuminance"}},
        {"entity_id": "light.living_room", "state": "off", "attributes": {}},
    ], REGISTRY)
    service.handle_state_changed(user_light_event())
    outcome = service.store.recent_action_outcomes()[0]
    assert outcome["before"]["sensor.living_room_light"]["value"] == "100"
    assert outcome["after"] is None
    assert service.store.pending_action_outcomes() == []
    assert service.store.pending_action_outcomes(minimum_age_seconds=0)
    assert service.store.finish_action_outcome(outcome["id"], {"sensor.living_room_light": {"value": "120", "band": "dim"}})
    assert service.store.recent_action_outcomes()[0]["after"]["sensor.living_room_light"]["value"] == "120"
    assert not service.store.finish_action_outcome(outcome["id"], {})


def test_additive_schema_keeps_existing_learning_history(tmp_path: Path):
    old = Store(tmp_path)
    observation_id = old.add_observation({"weekday": 1}, ACTION, "user")
    reopened = Store(tmp_path)
    assert reopened.recent()[0]["id"] == observation_id
    assert reopened.stats()["observations"] == 1
    assert reopened.shadow_report() == {"methods": [], "recent": []}
