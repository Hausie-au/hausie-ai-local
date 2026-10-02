from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import websocket

LOGGER = logging.getLogger(__name__)


def websocket_url(home_assistant_url: str) -> str:
    """Convert the Supervisor REST proxy URL into the HA WebSocket URL."""
    parsed = urlsplit(home_assistant_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    base_path = parsed.path.rstrip("/")
    return urlunsplit((scheme, parsed.netloc, f"{base_path}/api/websocket", "", ""))


class HomeAssistantEventStream:
    """Reconnectable subscription to Home Assistant state_changed events."""

    def __init__(
        self,
        home_assistant_url: str,
        token: str,
        connection_factory: Callable[..., Any] = websocket.create_connection,
    ):
        self.url = websocket_url(home_assistant_url)
        self.token = token
        self.connection_factory = connection_factory

    def run(
        self,
        on_state_changed: Callable[[dict[str, Any]], None],
        should_stop: Callable[[], bool],
    ) -> None:
        retry_seconds = 1
        while not should_stop():
            socket = None
            try:
                LOGGER.info("EVENT_STREAM connecting url=%s", self.url)
                socket = self.connection_factory(self.url, timeout=15)
                self._authenticate(socket)
                self._subscribe(socket)
                retry_seconds = 1
                LOGGER.info("EVENT_STREAM connected subscription=state_changed")
                socket.settimeout(5)
                while not should_stop():
                    try:
                        raw_message = socket.recv()
                    except websocket.WebSocketTimeoutException:
                        continue
                    if not raw_message:
                        raise ConnectionError("Home Assistant closed the WebSocket connection.")
                    message = json.loads(raw_message)
                    event = message.get("event") if message.get("type") == "event" else None
                    if isinstance(event, dict) and event.get("event_type") == "state_changed":
                        on_state_changed(event)
            except Exception as exc:
                if not should_stop():
                    LOGGER.warning("EVENT_STREAM disconnected error=%s retry_in=%ss", exc, retry_seconds)
                    time.sleep(retry_seconds)
                    retry_seconds = min(retry_seconds * 2, 30)
            finally:
                if socket is not None:
                    try:
                        socket.close()
                    except Exception:
                        pass

    def _authenticate(self, socket: Any) -> None:
        message = json.loads(socket.recv())
        if message.get("type") != "auth_required":
            raise ConnectionError(f"Expected auth_required, got {message.get('type')!r}.")
        socket.send(json.dumps({"type": "auth", "access_token": self.token}))
        result = json.loads(socket.recv())
        if result.get("type") != "auth_ok":
            raise PermissionError(result.get("message", "Home Assistant WebSocket authentication failed."))

    @staticmethod
    def _subscribe(socket: Any) -> None:
        subscription_id = 1
        socket.send(json.dumps({"id": subscription_id, "type": "subscribe_events", "event_type": "state_changed"}))
        result = json.loads(socket.recv())
        if result.get("type") != "result" or result.get("id") != subscription_id or not result.get("success"):
            raise ConnectionError("Home Assistant rejected the state_changed subscription.")

