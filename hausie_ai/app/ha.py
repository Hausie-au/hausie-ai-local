from __future__ import annotations

from typing import Any

import requests
import websocket

from .events import websocket_url


class HomeAssistantClient:
    def __init__(self, base_url: str, token: str, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})

    def get_states(self) -> list[dict[str, Any]]:
        response = self.session.get(f"{self.base_url}/api/states", timeout=10)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []

    def get_time_zone(self) -> str:
        response = self.session.get(f"{self.base_url}/api/config", timeout=10)
        response.raise_for_status()
        name = response.json().get("time_zone")
        if not isinstance(name, str) or not name:
            raise ValueError("Home Assistant did not provide a valid time_zone.")
        return name

    def call_service(self, action: dict[str, Any]) -> list[dict[str, Any]]:
        domain = action["domain"]
        service = action["service"]
        data: dict[str, Any] = {"entity_id": action["entity_id"]}
        data.update(action.get("service_data") or {})
        response = self.session.post(f"{self.base_url}/api/services/{domain}/{service}", json=data, timeout=10)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []

    def get_inventory_registry(self) -> dict[str, list[dict[str, Any]]]:
        """Read Home Assistant's local registries used by the Hausie inventory.

        The Supervisor token is sufficient; no cloud request or mounted HA
        configuration directory is required.
        """
        socket = websocket.create_connection(websocket_url(self.base_url), timeout=15)
        try:
            required = socket.recv()
            if not required:
                raise ConnectionError("Home Assistant closed the registry connection.")
            import json

            message = json.loads(required)
            if message.get("type") != "auth_required":
                raise ConnectionError("Home Assistant did not request WebSocket authentication.")
            token = self.session.headers.get("Authorization", "").removeprefix("Bearer ")
            socket.send(json.dumps({"type": "auth", "access_token": token}))
            if json.loads(socket.recv()).get("type") != "auth_ok":
                raise PermissionError("Home Assistant rejected the Supervisor token.")
            result: dict[str, list[dict[str, Any]]] = {}
            for request_id, (name, command) in enumerate(
                (
                    ("areas", "config/area_registry/list"),
                    ("devices", "config/device_registry/list"),
                    ("entities", "config/entity_registry/list"),
                    ("labels", "config/label_registry/list"),
                ),
                start=10,
            ):
                socket.send(json.dumps({"id": request_id, "type": command}))
                while True:
                    response = json.loads(socket.recv())
                    if response.get("id") != request_id:
                        continue
                    if not response.get("success"):
                        result[name] = []
                    else:
                        payload = response.get("result")
                        result[name] = payload if isinstance(payload, list) else []
                    break
            return result
        finally:
            socket.close()

