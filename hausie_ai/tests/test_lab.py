"""The lab predicts before labels arrive and never holds a Home Assistant client."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hausie_ai.app.experiments import ShadowLearners
from hausie_ai.app.lab import ACTION_IDS, CATALOG, ModelLab
from hausie_ai.app.lab_storage import LabStore
from hausie_ai.app.learning import Learner
from hausie_ai.app.storage import Store


ACTION = {"domain": "light", "service": "turn_on", "entity_id": "light.room"}
CONTEXT = {"month": 10, "weekday": 2, "hour_bucket": 40, "occupancy": "occupied", "environment": {}}
TRIGGER = {"area": "room", "entity_id": "sensor.room_light", "kind": "illuminance",
           "direction": "falling", "old_band": "bright", "new_band": "dim",
           "old_value": "100", "new_value": "50", "sequence": ["temperature:falling:cool", "illuminance:falling:dim"]}
PROFILE = {"entity_id": "sensor.room_temperature", "area_id": "room", "environmental_kind": "temperature",
           "unit": "°C", "state": "22", "normalized_value": "comfortable", "is_environmental": True}


def lab_at(path: Path, minimum: int = 2) -> tuple[Store, LabStore, ModelLab]:
    store = Store(path)
    lab_store = LabStore(store)
    learner = Learner(store, minimum, 0.8)
    return store, lab_store, ModelLab(lab_store, ShadowLearners(store, learner, minimum, 0.8), minimum, 0.8)


def age_row(store: Store, table: str, row_id: int, minutes: int) -> None:
    timestamp = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    with store._lock, store._connect() as connection:
        connection.execute(f"UPDATE {table} SET created_at = ? WHERE id = ?", (timestamp, row_id))


def test_fifteen_distinct_models_and_action_predictions_are_pre_action(tmp_path: Path):
    store, _, lab = lab_at(tmp_path)
    assert len(CATALOG) == len({item["id"] for item in CATALOG}) == 15
    assert len(ACTION_IDS) == 8
    for _ in range(3):
        store.add_experience(CONTEXT, TRIGGER, ACTION, "user")
        lab.observe_manual_action("room", ACTION, CONTEXT)
    future_context = dict(CONTEXT, weekday=4, hour_bucket=45)
    predictions = lab.predict_actions(future_context, TRIGGER)
    assert set(predictions) == set(ACTION_IDS)
    for name in ["no_context", "hierarchical", "nearest_case", "markov_action", "event_sequence"]:
        assert predictions[name]["action"] == ACTION
    assert predictions["exact"]["action"] is None
    assert lab.predict_actions(future_context, dict(TRIGGER, sequence=["different", "sequence"]))["event_sequence"]["action"] is None


def test_sensor_forecasts_are_scored_only_against_later_readings(tmp_path: Path):
    store, lab_store, lab = lab_at(tmp_path)
    event_id = store.add_environmental_event(PROFILE, "20", "21")
    age_row(store, "environmental_events", event_id, 30)
    first = lab.forecast_sensor(PROFILE)
    assert first["sensor_persistence"] == 22
    assert first["sensor_trend"] == pytest.approx(23, abs=0.01)
    assert lab_store.forecast_report()[0]["evaluated"] == 0
    with store._lock, store._connect() as connection:
        connection.execute("UPDATE lab_sensor_forecasts SET created_at = ?", ((datetime.now(timezone.utc) - timedelta(minutes=35)).isoformat(),))
    assert lab.finish_forecasts([dict(PROFILE, state="23")]) == 2
    assert all(row["evaluated"] == 1 for row in lab_store.forecast_report())
    target_hour = (datetime.now().astimezone() + timedelta(minutes=30)).hour
    assert lab_store.completed_forecast_values(PROFILE["entity_id"], "°C", target_hour) == [23.0]
    lab._last_forecast_at.clear()
    assert "sensor_hourly" not in lab.forecast_sensor(dict(PROFILE, state="23"))
    with store._lock, store._connect() as connection:
        connection.execute("UPDATE lab_sensor_forecasts SET created_at = ? WHERE completed_at IS NULL", ((datetime.now(timezone.utc) - timedelta(minutes=35)).isoformat(),))
    assert lab.finish_forecasts([dict(PROFILE, state="25")]) == 2
    assert sorted(lab_store.completed_forecast_values(PROFILE["entity_id"], "°C", target_hour)) == [23.0, 25.0]
    lab._last_forecast_at.clear()
    assert lab.forecast_sensor(dict(PROFILE, state="24"))["sensor_hourly"] == 24


def test_response_predictions_require_prior_clean_outcomes(tmp_path: Path):
    store, lab_store, lab = lab_at(tmp_path)
    before = {"sensor.room_temperature": {"value": "20"}}
    after = {"sensor.room_temperature": {"value": "21"}}
    for days in (3, 2):
        past = store.start_action_outcome("room", ACTION, before)
        age_row(store, "action_outcomes", past, days * 1440)
        assert store.finish_action_outcome(past, after)
    current = store.start_action_outcome("room", ACTION, before)
    assert lab.predict_response(current, "room", ACTION, before) == 2
    assert all(row["evaluated"] == 0 for row in lab_store.outcome_report())
    assert store.finish_action_outcome(current, {"sensor.room_temperature": {"value": "22"}})
    lab_store.label_outcome_predictions(current)
    report = lab_store.outcome_report()
    assert len(report) == 2
    assert all(row["evaluated"] == 1 and row["mean_absolute_error"] == 1 for row in report)


def test_preference_uses_only_explicit_feedback_and_anomaly_needs_history(tmp_path: Path):
    store, lab_store, lab = lab_at(tmp_path)
    for reward in (1, -1):
        decision = store.add_decision(CONTEXT, ACTION, 0.9, "SUGGEST_ACTION", "test", False)
        assert store.add_decision_feedback(decision, reward, "explicit_user_feedback")
    current = store.add_decision(CONTEXT, ACTION, 0.9, "SUGGEST_ACTION", "test", False)
    assert lab.predict_preference(current, ACTION) == 0.5
    assert lab_store.preference_report()["evaluated"] == 0
    lab_store.label_preference_prediction(current, True)
    assert lab_store.preference_report()["brier_score"] == 0.25
    anomaly = dict(TRIGGER, entity_id="sensor.room_temperature", kind="temperature", old_value="20", new_value="21")
    first = lab.detect_anomaly(anomaly)
    assert not lab_store.rate_anomaly(first, True)  # No score without earlier changes.
    for i in range(5):
        store.add_environmental_event(PROFILE, str(i), str(i + 1))
    flagged = lab.detect_anomaly(dict(anomaly, new_value="50"))
    report = lab_store.anomaly_report()
    assert report["summary"]["flagged"] == 1
    assert lab_store.rate_anomaly(flagged, True)
    assert not lab_store.rate_anomaly(flagged, False)
    assert lab_store.anomaly_report()["summary"]["agreed"] == 1


def test_schema_is_additive_and_history_survives_reopen(tmp_path: Path):
    store, lab_store, lab = lab_at(tmp_path)
    lab.observe_manual_action("room", ACTION, CONTEXT)
    store.add_observation(CONTEXT, ACTION, "user")
    reopened = LabStore(Store(tmp_path))
    assert reopened.manual_actions("room")[0]["action"] == ACTION
    assert reopened.store.stats()["observations"] == 1
