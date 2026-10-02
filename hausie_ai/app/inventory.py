from __future__ import annotations

"""Local Home Assistant inventory and context classification.

The inventory deliberately stays local.  It joins the live state snapshot with
Home Assistant's entity, device, area and label registries so the learner can
explain both what it sees and why a particular entity is (or is not) usable.
"""

from collections import Counter
from typing import Any

from .safety import SafetyPolicy


ENVIRONMENTAL_DEVICE_CLASSES = {
    "air_quality_index": "air_quality",
    "aqi": "air_quality",
    "atmospheric_pressure": "pressure",
    "carbon_dioxide": "carbon_dioxide",
    "carbon_monoxide": "carbon_monoxide",
    "humidity": "humidity",
    "illuminance": "illuminance",
    "moisture": "moisture",
    "nitrogen_dioxide": "nitrogen_dioxide",
    "ozone": "ozone",
    "pm1": "pm1",
    "pm10": "pm10",
    "pm25": "pm25",
    "pm2_5": "pm25",
    "pressure": "pressure",
    "sound_pressure": "sound_pressure",
    "temperature": "temperature",
    "volatile_organic_compounds": "voc",
    "volatile_organic_compounds_parts": "voc",
    "wind_speed": "wind_speed",
}
CONTEXT_DEVICE_CLASSES = {"motion", "occupancy", "presence", "opening"}
LABEL_KIND_HINTS = {
    "air_quality": "air_quality",
    "co2": "carbon_dioxide",
    "contact": "opening",
    "door": "opening",
    "humidity": "humidity",
    "illuminance": "illuminance",
    "light_level": "illuminance",
    "motion": "motion",
    "occupancy": "occupancy",
    "presence": "presence",
    "temperature": "temperature",
    "window": "opening",
}


def _slug(value: Any) -> str:
    return "_".join("".join(char.lower() if char.isalnum() else " " for char in str(value or "")).split())


def _as_number(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def normalize_environmental_value(kind: str, state: Any) -> str:
    """Turn raw values into stable, explainable bands for the frequency model."""
    value = _as_number(state)
    if kind == "opening":
        return "open" if str(state).lower() in {"on", "open", "opening"} else "closed"
    if kind in {"motion", "occupancy", "presence"}:
        return "active" if str(state).lower() in {"on", "home", "detected"} else "inactive"
    if value is None:
        return _slug(state) or "unknown"
    if kind == "temperature":
        return "cold" if value < 18 else "cool" if value < 22 else "comfortable" if value < 26 else "warm" if value < 29 else "hot"
    if kind in {"humidity", "moisture"}:
        return "dry" if value < 35 else "comfortable" if value <= 60 else "humid"
    if kind == "illuminance":
        return "dark" if value < 20 else "dim" if value < 150 else "bright"
    if kind == "carbon_dioxide":
        return "low" if value < 800 else "elevated" if value < 1200 else "high"
    if kind in {"air_quality", "pm1", "pm10", "pm25", "voc", "nitrogen_dioxide", "ozone"}:
        return "good" if value < 50 else "moderate" if value < 100 else "poor"
    if kind == "pressure":
        return "low" if value < 1000 else "normal" if value <= 1025 else "high"
    return "low" if value < 25 else "normal" if value <= 75 else "high"


class InventoryBuilder:
    """Create a local, UI-ready entity inventory from Home Assistant data."""

    def __init__(self, safety: SafetyPolicy):
        self.safety = safety

    def build(self, states: list[dict[str, Any]], registry: dict[str, list[dict[str, Any]]] | None = None) -> list[dict[str, Any]]:
        registry = registry or {}
        areas = {str(item.get("area_id") or item.get("id")): item for item in registry.get("areas", []) if isinstance(item, dict)}
        devices = {str(item.get("id")): item for item in registry.get("devices", []) if isinstance(item, dict)}
        entity_registry = {
            str(item.get("entity_id")): item for item in registry.get("entities", []) if isinstance(item, dict) and item.get("entity_id")
        }
        label_names = {
            str(item.get("label_id") or item.get("id")): _slug(item.get("name") or item.get("label"))
            for item in registry.get("labels", []) if isinstance(item, dict)
        }
        profiles = [
            self._profile(state, entity_registry.get(str(state.get("entity_id")), {}), devices, areas, label_names)
            for state in states if isinstance(state, dict) and state.get("entity_id")
        ]
        return sorted(profiles, key=lambda item: item["entity_id"])

    def _profile(
        self,
        state: dict[str, Any],
        entity: dict[str, Any],
        devices: dict[str, dict[str, Any]],
        areas: dict[str, dict[str, Any]],
        label_names: dict[str, str],
    ) -> dict[str, Any]:
        entity_id = str(state["entity_id"])
        domain = entity_id.split(".", 1)[0]
        attributes = state.get("attributes") or {}
        device = devices.get(str(entity.get("device_id") or ""), {})
        area_id = str(entity.get("area_id") or device.get("area_id") or "") or None
        area = areas.get(area_id or "", {})
        raw_labels = list(entity.get("labels") or []) + list(device.get("labels") or [])
        labels = sorted({label_names.get(str(label), _slug(label)) for label in raw_labels if str(label)})
        device_class = _slug(attributes.get("device_class") or entity.get("device_class"))
        kind = ENVIRONMENTAL_DEVICE_CLASSES.get(device_class) or next((LABEL_KIND_HINTS[label] for label in labels if label in LABEL_KIND_HINTS), None)
        if domain == "binary_sensor" and device_class in CONTEXT_DEVICE_CLASSES:
            kind = device_class
        if domain == "weather":
            kind = kind or "weather"
        is_environmental = kind in ENVIRONMENTAL_DEVICE_CLASSES.values() or kind in {"weather", "opening"}
        is_context = domain in {"person", "device_tracker"} or kind in CONTEXT_DEVICE_CLASSES
        safety = self.safety.classify_entity(entity_id)
        roles: list[str] = []
        if is_environmental:
            roles.append("environmental_input")
        if is_context:
            roles.append("context_input")
        if safety["classification"] == "safe_action_target":
            roles.append("safe_action_target")
        elif safety["classification"] == "blocked_action_target":
            roles.append("blocked_action_target")
        if not roles:
            roles.append("observed_only")
        feature_key = ""
        normalized = ""
        if is_environmental:
            feature_key = f"{_slug(area.get('name') or area_id or 'home')}:{kind}"
            normalized = normalize_environmental_value(str(kind), state.get("state"))
        return {
            "entity_id": entity_id,
            "name": str(attributes.get("friendly_name") or entity.get("name") or entity_id),
            "domain": domain,
            "state": state.get("state"),
            "unit": attributes.get("unit_of_measurement"),
            "device_class": device_class or None,
            "area_id": area_id,
            "area_name": area.get("name") or None,
            "device_id": entity.get("device_id") or None,
            "device_name": device.get("name_by_user") or device.get("name") or None,
            "labels": labels,
            "roles": roles,
            "is_environmental": is_environmental,
            "is_context_input": is_context,
            "environmental_kind": kind if is_environmental or kind in CONTEXT_DEVICE_CLASSES else None,
            "feature_key": feature_key or None,
            "normalized_value": normalized or None,
            "safety": safety,
        }

    @staticmethod
    def summary(profiles: list[dict[str, Any]]) -> dict[str, int]:
        counts = Counter(role for profile in profiles for role in profile.get("roles", []))
        return {
            "entities": len(profiles),
            "environmental_inputs": counts["environmental_input"],
            "context_inputs": counts["context_input"],
            "safe_action_targets": counts["safe_action_target"],
            "blocked_action_targets": counts["blocked_action_target"],
            "observed_only": counts["observed_only"],
        }


def environmental_features(profiles: list[dict[str, Any]], max_features: int = 24) -> dict[str, str]:
    """Return bounded, deterministic environmental features for a context."""
    features: dict[str, str] = {}
    for profile in sorted(profiles, key=lambda item: str(item.get("feature_key") or "")):
        if str(profile.get("state", "")).lower() in {"unknown", "unavailable", "none"}:
            continue
        key = profile.get("feature_key")
        value = profile.get("normalized_value")
        if key and value and key not in features:
            features[str(key)] = str(value)
        if len(features) >= max_features:
            break
    return features
