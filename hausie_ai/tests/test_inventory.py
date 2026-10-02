from pathlib import Path

from hausie_ai.app.inventory import InventoryBuilder
from hausie_ai.app.safety import SafetyPolicy
from hausie_ai.app.service import HausieAIService
from hausie_ai.app.settings import Settings


def _registry() -> dict:
    return {
        "areas": [{"area_id": "living_room", "name": "Living Room"}],
        "devices": [{"id": "device-1", "name": "Climate sensor", "area_id": "living_room"}],
        "entities": [
            {"entity_id": "sensor.living_temperature", "device_id": "device-1"},
            {"entity_id": "sensor.living_humidity", "device_id": "device-1"},
            {"entity_id": "light.living_room", "device_id": "device-1"},
            {"entity_id": "climate.living_room", "device_id": "device-1"},
        ],
        "labels": [],
    }


def _states(temperature: str = "24.2") -> list[dict]:
    return [
        {
            "entity_id": "sensor.living_temperature", "state": temperature,
            "attributes": {"friendly_name": "Living temperature", "device_class": "temperature", "unit_of_measurement": "°C"},
        },
        {
            "entity_id": "sensor.living_humidity", "state": "52",
            "attributes": {"device_class": "humidity", "unit_of_measurement": "%"},
        },
        {"entity_id": "light.living_room", "state": "off", "attributes": {}},
        {"entity_id": "climate.living_room", "state": "heat", "attributes": {}},
    ]


def test_inventory_uses_registry_area_and_classifies_environment_and_safety():
    profiles = InventoryBuilder(SafetyPolicy()).build(_states(), _registry())
    indexed = {item["entity_id"]: item for item in profiles}

    temperature = indexed["sensor.living_temperature"]
    assert temperature["area_name"] == "Living Room"
    assert temperature["roles"] == ["environmental_input"]
    assert temperature["normalized_value"] == "comfortable"
    assert indexed["light.living_room"]["safety"]["classification"] == "safe_action_target"
    assert indexed["climate.living_room"]["safety"]["classification"] == "blocked_action_target"


def test_environmental_change_is_persisted_and_added_to_context(tmp_path: Path):
    service = HausieAIService(Settings.from_environment().with_data_dir(tmp_path))
    service.collect_states(_states(), _registry())
    changed = _states("28.1")[0]
    service.handle_state_changed({
        "data": {
            "entity_id": changed["entity_id"],
            "old_state": _states("24.2")[0],
            "new_state": changed,
        }
    })

    events = service.store.recent_environmental_events()
    assert events[0]["entity_id"] == "sensor.living_temperature"
    assert events[0]["normalized_value"] == "warm"
    assert service.current_context()["environment"]["living_room:temperature"] == "warm"
