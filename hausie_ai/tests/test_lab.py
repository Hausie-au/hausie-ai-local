"""The lab predicts before labels arrive and never holds a Home Assistant client."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hausie_ai.app.experiments import ShadowLearners
from hausie_ai.app.ha import HomeAssistantClient
from hausie_ai.app.lab import ACTION_IDS, CATALOG, ModelLab
from hausie_ai.app.lab_storage import LabStore
from hausie_ai.app.learning import Learner
from hausie_ai.app.storage import Store
from hausie_ai.app.service import HausieAIService
from hausie_ai.app.settings import Settings


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


def test_thirty_one_distinct_models_and_action_predictions_are_pre_action(tmp_path: Path):
    store, _, lab = lab_at(tmp_path)
    assert len(CATALOG) == len({item["id"] for item in CATALOG}) == 31
    assert len(ACTION_IDS) == 11
    for _ in range(3):
        store.add_experience(CONTEXT, TRIGGER, ACTION, "user")
        lab.observe_manual_action("room", ACTION, CONTEXT)
    future_context = dict(CONTEXT, weekday=4, hour_bucket=44)
    predictions = lab.predict_actions(future_context, TRIGGER)
    assert set(predictions) == set(ACTION_IDS)
    for name in ["no_context", "hierarchical", "nearest_case", "markov_action", "event_sequence"]:
        assert predictions[name]["action"] == ACTION
    for name in ["clock_prior", "recency_prior", "naive_bayes"]:
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
    assert lab.predict_response(current, "room", ACTION, before) == 3
    assert all(row["evaluated"] == 0 for row in lab_store.outcome_report())
    assert store.finish_action_outcome(current, {"sensor.room_temperature": {"value": "22"}})
    lab_store.label_outcome_predictions(current)
    report = lab_store.outcome_report()
    assert len(report) == 3
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
    preference_methods = {row["method"]: row for row in lab_store.preference_report()["methods"]}
    assert set(preference_methods) == {"preference_bayes", "preference_recent", "preference_context"}
    assert all(row["evaluated"] == 1 for row in preference_methods.values())
    anomaly = dict(TRIGGER, entity_id="sensor.room_temperature", kind="temperature", old_value="20", new_value="21")
    first = lab.detect_anomaly(anomaly)
    assert not lab_store.rate_anomaly(first, True)  # No score without earlier changes.
    for i in range(5):
        store.add_environmental_event(PROFILE, str(i), str(i + 1))
    flagged = lab.detect_anomaly(dict(anomaly, new_value="50"))
    report = lab_store.anomaly_report()
    assert report["summary"]["flagged"] >= 1
    assert lab_store.rate_anomaly(flagged, True)
    assert not lab_store.rate_anomaly(flagged, False)
    assert lab_store.anomaly_report()["summary"]["agreed"] == 1


def test_multivariate_sensor_forecast_uses_only_completed_past_windows(tmp_path: Path):
    store, lab_store, lab = lab_at(tmp_path)
    peer = {"entity_id": "sensor.room_humidity", "area_id": "room", "state": "50",
            "is_environmental": True, "environmental_kind": "humidity", "unit": "%"}
    profiles = [PROFILE, peer]
    for current, actual in [(20, 21), (22, 23)]:
        lab._last_forecast_at.clear()
        prediction = lab.forecast_sensor(dict(PROFILE, state=str(current)), profiles)
        assert "sensor_multivariate" not in prediction
        with store._lock, store._connect() as connection:
            connection.execute("UPDATE lab_sensor_forecasts SET created_at = ? WHERE completed_at IS NULL",
                               ((datetime.now(timezone.utc) - timedelta(minutes=35)).isoformat(),))
            connection.execute("UPDATE lab_sensor_vectors SET created_at = ? WHERE completed_at IS NULL",
                               ((datetime.now(timezone.utc) - timedelta(minutes=35)).isoformat(),))
        lab.finish_forecasts([dict(PROFILE, state=str(actual)), peer])
    lab._last_forecast_at.clear()
    prediction = lab.forecast_sensor(dict(PROFILE, state="21"), profiles)
    assert prediction["sensor_multivariate"] == 22
    assert len(lab_store.sensor_vectors(PROFILE["entity_id"], "°C")) == 2


def test_rolling_forecasts_and_shift_detection_use_prior_values(tmp_path: Path):
    store, lab_store, lab = lab_at(tmp_path)
    for value in (20, 21, 22):
        store.add_environmental_event(PROFILE, str(value - 1), str(value))
    forecasts = lab.forecast_sensor(dict(PROFILE, state="23"))
    assert forecasts["sensor_rolling_mean"] == pytest.approx(21.5)
    assert forecasts["sensor_rolling_median"] == pytest.approx(21.5)
    assert forecasts["sensor_ewma"] > forecasts["sensor_rolling_mean"]
    for value in [20] * 5 + [30] * 5:
        store.add_environmental_event(PROFILE, str(value), str(value))
    lab.detect_anomaly(dict(TRIGGER, entity_id=PROFILE["entity_id"], kind="temperature",
                            old_value="29", new_value="30"))
    methods = {row["method"]: row for row in lab_store.anomaly_report()["methods"]}
    assert set(methods) == {"anomaly_robust", "anomaly_value", "anomaly_drift"}
    assert methods["anomaly_drift"]["scored"] == 1
    assert methods["anomaly_drift"]["flagged"] == 1


def test_timing_and_event_sequence_predictions_are_saved_before_labels(tmp_path: Path):
    store, lab_store, lab = lab_at(tmp_path)
    other = dict(TRIGGER, entity_id="sensor.room_motion", kind="motion", direction="changed",
                 old_band="inactive", new_band="active", sequence=["illuminance:falling:dim", "motion:changed:active"])
    for trigger in [TRIGGER, other, TRIGGER, other]:
        next_prediction = lab.predict_next_event(trigger)
        opportunity = store.add_shadow_opportunity(trigger, CONTEXT, {})
        lab.save_next_event_predictions(opportunity, trigger, next_prediction)
        lab.predict_timing(opportunity, trigger)
        if trigger["kind"] == "illuminance":
            assert store.label_shadow_opportunity(opportunity, ACTION)
            lab_store.label_timing(opportunity)
    future = lab.predict_next_event(TRIGGER)
    assert future["routine_transition"] == "motion:changed:active"
    assert future["routine_bigram"] == "motion:changed:active"
    timing_opportunity = store.add_shadow_opportunity(TRIGGER, CONTEXT, {})
    timing = lab.predict_timing(timing_opportunity, TRIGGER)
    assert timing["timing_area_median"] is not None
    assert timing["timing_kind_median"] is not None
    assert next(row for row in lab_store.timing_report() if row["method"] == "timing_kind_median")["evaluated"] == 0
    lab.save_next_event_predictions(timing_opportunity, TRIGGER, future)
    assert store.label_shadow_opportunity(timing_opportunity, ACTION)
    lab_store.label_timing(timing_opportunity)
    assert next(row for row in lab_store.timing_report() if row["method"] == "timing_kind_median")["evaluated"] == 1


def test_anomaly_schema_upgrade_preserves_legacy_rows(tmp_path: Path):
    store = Store(tmp_path)
    with store._lock, store._connect() as connection:
        connection.execute("""CREATE TABLE lab_anomalies (
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, entity_id TEXT NOT NULL,
            area TEXT NOT NULL, kind TEXT NOT NULL, score REAL, flagged INTEGER NOT NULL,
            explanation TEXT NOT NULL, reviewed INTEGER)""")
        connection.execute("""INSERT INTO lab_anomalies(created_at, entity_id, area, kind, score, flagged, explanation)
            VALUES (?, 'sensor.room_temperature', 'room', 'temperature', 4, 1, 'legacy')""", (datetime.now(timezone.utc).isoformat(),))
    lab_store = LabStore(store)
    assert lab_store.anomaly_report()["recent"][0]["method"] == "anomaly_robust"


def test_home_assistant_configured_time_zone_is_available_to_learning(tmp_path: Path):
    client = HomeAssistantClient("http://supervisor/core", "test-token")
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"time_zone": "Australia/Sydney"}
    client.session.get = lambda url, timeout: Response()
    assert client.get_time_zone() == "Australia/Sydney"
    service = HausieAIService(Settings.from_environment().with_data_dir(tmp_path))
    service.home_zone = timezone(timedelta(hours=11))
    before = datetime.now(service.home_zone)
    context = service.current_context()
    after = datetime.now(service.home_zone)
    assert context["hour_bucket"] in {before.hour * 4 + before.minute // 15,
                                      after.hour * 4 + after.minute // 15}
    assert context["month"] in {before.month, after.month}


def test_schema_is_additive_and_history_survives_reopen(tmp_path: Path):
    store, lab_store, lab = lab_at(tmp_path)
    lab.observe_manual_action("room", ACTION, CONTEXT)
    store.add_observation(CONTEXT, ACTION, "user")
    reopened = LabStore(Store(tmp_path))
    assert reopened.manual_actions("room")[0]["action"] == ACTION
    assert reopened.store.stats()["observations"] == 1
