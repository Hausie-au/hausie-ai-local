from pathlib import Path

from hausie_ai.app.service import HausieAIService
from hausie_ai.app.settings import Settings


def _user_light_event(state: str) -> dict:
    return {
        "data": {
            "entity_id": "light.living_room",
            "old_state": {"entity_id": "light.living_room", "state": "off"},
            "new_state": {
                "entity_id": "light.living_room",
                "state": state,
                "attributes": {},
                "context": {"user_id": "person-1", "parent_id": None},
            },
        },
        "context": {"user_id": "person-1", "parent_id": None},
    }


def test_event_source_classification(tmp_path: Path):
    service = HausieAIService(Settings.from_environment().with_data_dir(tmp_path))
    state = {"entity_id": "light.kitchen", "state": "on"}
    assert service._classify_source({"context": {"user_id": "u"}}, state) == "user"
    assert service._classify_source({"context": {"parent_id": "automation"}}, state) == "automation"
    assert service._classify_source({}, state) == "unknown"


def test_user_state_events_train_and_produce_a_safe_suggestion(tmp_path: Path):
    service = HausieAIService(Settings.from_environment().with_data_dir(tmp_path))
    service.collect_states(
        [
            {"entity_id": "person.mateo", "state": "home", "attributes": {}},
            {"entity_id": "light.living_room", "state": "off", "attributes": {}},
        ]
    )
    for _ in range(3):
        service.handle_state_changed(_user_light_event("on"))
        service.state_index["light.living_room"] = {"entity_id": "light.living_room", "state": "off", "attributes": {}}

    learned_context = service.store.recent(limit=1)[0]["context"]
    result = service.decide(learned_context)
    assert result["decision"] == "SUGGEST_ACTION"
    assert result["action"]["entity_id"] == "light.living_room"
    assert service.store.stats()["observations"] == 3
