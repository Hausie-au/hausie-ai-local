import json

from hausie_ai.app.events import HomeAssistantEventStream, websocket_url


class FakeSocket:
    def __init__(self):
        self.sent: list[dict] = []
        self.closed = False
        self.timeout = None
        self.messages = [
            {"type": "auth_required"},
            {"type": "auth_ok"},
            {"id": 1, "type": "result", "success": True, "result": None},
            {
                "id": 1,
                "type": "event",
                "event": {
                    "event_type": "state_changed",
                    "data": {
                        "entity_id": "light.living_room",
                        "old_state": {"entity_id": "light.living_room", "state": "off"},
                        "new_state": {"entity_id": "light.living_room", "state": "on"},
                    },
                },
            },
        ]

    def recv(self):
        return json.dumps(self.messages.pop(0))

    def send(self, payload: str):
        self.sent.append(json.loads(payload))

    def settimeout(self, timeout: int):
        self.timeout = timeout

    def close(self):
        self.closed = True


def test_websocket_url_preserves_supervisor_core_path():
    assert websocket_url("http://supervisor/core") == "ws://supervisor/core/api/websocket"
    assert websocket_url("https://ha.example") == "wss://ha.example/api/websocket"


def test_event_stream_authenticates_subscribes_and_delivers_state_change():
    socket = FakeSocket()
    stopped = False
    received = []

    def on_event(event):
        nonlocal stopped
        received.append(event)
        stopped = True

    stream = HomeAssistantEventStream("http://supervisor/core", "test-token", lambda *_args, **_kwargs: socket)
    stream.run(on_event, lambda: stopped)

    assert received[0]["data"]["entity_id"] == "light.living_room"
    assert socket.sent == [
        {"type": "auth", "access_token": "test-token"},
        {"id": 1, "type": "subscribe_events", "event_type": "state_changed"},
    ]
    assert socket.closed

