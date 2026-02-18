"""Tests for BridgeSubscriber event routing."""

import asyncio
import json

import pytest

from agent_tether.base import ApprovalRequest, BridgeConfig, BridgeInterface
from agent_tether.manager import BridgeManager
from agent_tether.subscriber import BridgeSubscriber, _OUTPUT_FLUSH_DELAY_S


class FakeBridge(BridgeInterface):
    """Fake bridge that records all calls."""

    def __init__(self):
        super().__init__(BridgeConfig())
        self.outputs: list[tuple[str, str]] = []
        self.approvals: list[tuple[str, ApprovalRequest]] = []
        self.statuses: list[tuple[str, str, dict | None]] = []
        self.typing_started: list[str] = []
        self.typing_stopped: list[str] = []
        self.sessions_removed: list[str] = []

    async def on_output(self, session_id, text, metadata=None):
        self.outputs.append((session_id, text))

    async def on_approval_request(self, session_id, request):
        self.approvals.append((session_id, request))

    async def on_status_change(self, session_id, status, metadata=None):
        self.statuses.append((session_id, status, metadata))

    async def on_typing(self, session_id):
        self.typing_started.append(session_id)

    async def on_typing_stopped(self, session_id):
        self.typing_stopped.append(session_id)

    async def on_session_removed(self, session_id):
        self.sessions_removed.append(session_id)
        await super().on_session_removed(session_id)

    async def create_thread(self, session_id, session_name):
        return {"thread_id": f"thread_{session_id}"}


class FakeStore:
    """Fake store that provides subscriber queues."""

    def __init__(self):
        self.queues: dict[str, list[asyncio.Queue]] = {}

    def new_subscriber(self, session_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.queues.setdefault(session_id, []).append(q)
        return q

    def remove_subscriber(self, session_id: str, queue: asyncio.Queue) -> None:
        if session_id in self.queues:
            try:
                self.queues[session_id].remove(queue)
            except ValueError:
                pass


def _make_subscriber():
    """Create a BridgeSubscriber with a fake bridge and store."""
    bridge = FakeBridge()
    manager = BridgeManager()
    manager.register_bridge("test", bridge)
    store = FakeStore()
    subscriber = BridgeSubscriber(manager, store.new_subscriber, store.remove_subscriber)
    return subscriber, bridge, store


# ========== Lifecycle ==========


@pytest.mark.asyncio
async def test_subscribe_creates_task():
    """Test subscribe creates a background task."""
    subscriber, bridge, store = _make_subscriber()

    subscriber.subscribe("sess_1", "test")

    assert "sess_1" in subscriber._tasks
    assert "sess_1" in subscriber._queues

    await subscriber.unsubscribe("sess_1", platform="test")


@pytest.mark.asyncio
async def test_subscribe_idempotent():
    """Test subscribing twice is a no-op."""
    subscriber, bridge, store = _make_subscriber()

    subscriber.subscribe("sess_1", "test")
    task1 = subscriber._tasks["sess_1"]

    subscriber.subscribe("sess_1", "test")
    task2 = subscriber._tasks["sess_1"]

    assert task1 is task2

    await subscriber.unsubscribe("sess_1", platform="test")


@pytest.mark.asyncio
async def test_unsubscribe_cancels_and_cleans():
    """Test unsubscribe cancels task and calls on_session_removed."""
    subscriber, bridge, store = _make_subscriber()

    subscriber.subscribe("sess_1", "test")
    assert "sess_1" in subscriber._tasks

    await subscriber.unsubscribe("sess_1", platform="test")

    assert "sess_1" not in subscriber._tasks
    assert "sess_1" not in subscriber._queues
    assert "sess_1" in bridge.sessions_removed


@pytest.mark.asyncio
async def test_unsubscribe_without_platform():
    """Test unsubscribe without platform skips bridge cleanup."""
    subscriber, bridge, store = _make_subscriber()

    subscriber.subscribe("sess_1", "test")
    await subscriber.unsubscribe("sess_1")

    assert "sess_1" not in subscriber._tasks
    assert bridge.sessions_removed == []


# ========== Event routing ==========


@pytest.mark.asyncio
async def test_output_final_true():
    """Test output event with final=True is forwarded immediately."""
    subscriber, bridge, store = _make_subscriber()

    subscriber.subscribe("sess_1", "test")
    queue = subscriber._queues["sess_1"]

    queue.put_nowait({"type": "output", "data": {"text": "Hello!", "final": True}})
    await asyncio.sleep(0.05)

    assert ("sess_1", "Hello!") in bridge.outputs

    await subscriber.unsubscribe("sess_1", platform="test")


@pytest.mark.asyncio
async def test_output_step_buffered_and_flushed_on_timer():
    """Test step output is buffered and flushed after the delay."""
    subscriber, bridge, store = _make_subscriber()

    subscriber.subscribe("sess_1", "test")
    queue = subscriber._queues["sess_1"]

    queue.put_nowait({"type": "output", "data": {"text": "[tool: bash]\n", "final": False}})
    queue.put_nowait({"type": "output", "data": {"text": "$ ls -la\n", "final": False}})
    await asyncio.sleep(0.05)

    # Not yet flushed (timer hasn't fired)
    assert bridge.outputs == []

    # Wait for flush timer
    await asyncio.sleep(_OUTPUT_FLUSH_DELAY_S + 0.2)

    # Now should be flushed as a single concatenated message
    assert len(bridge.outputs) == 1
    assert bridge.outputs[0] == ("sess_1", "[tool: bash]\n$ ls -la\n")

    await subscriber.unsubscribe("sess_1", platform="test")


@pytest.mark.asyncio
async def test_output_step_flushed_on_state_change():
    """Test buffered step output is flushed when state changes to AWAITING_INPUT."""
    subscriber, bridge, store = _make_subscriber()

    subscriber.subscribe("sess_1", "test")
    queue = subscriber._queues["sess_1"]

    queue.put_nowait({"type": "output", "data": {"text": "Step output\n", "final": False}})
    await asyncio.sleep(0.05)

    # Not yet flushed
    assert bridge.outputs == []

    # State change flushes buffer
    queue.put_nowait({"type": "session_state", "data": {"state": "AWAITING_INPUT"}})
    await asyncio.sleep(0.05)

    assert len(bridge.outputs) == 1
    assert bridge.outputs[0] == ("sess_1", "Step output\n")

    await subscriber.unsubscribe("sess_1", platform="test")


@pytest.mark.asyncio
async def test_output_step_flushed_before_final():
    """Test buffered step output is flushed before final output is sent."""
    subscriber, bridge, store = _make_subscriber()

    subscriber.subscribe("sess_1", "test")
    queue = subscriber._queues["sess_1"]

    queue.put_nowait({"type": "output", "data": {"text": "[tool: bash]\n", "final": False}})
    await asyncio.sleep(0.05)
    queue.put_nowait({"type": "output", "data": {"text": "Final answer", "final": True}})
    await asyncio.sleep(0.05)

    # Both step buffer and final should be delivered, in order
    assert len(bridge.outputs) == 2
    assert bridge.outputs[0] == ("sess_1", "[tool: bash]\n")
    assert bridge.outputs[1] == ("sess_1", "Final answer")

    await subscriber.unsubscribe("sess_1", platform="test")


@pytest.mark.asyncio
async def test_output_step_flushed_on_permission_request():
    """Test buffered step output is flushed before a permission request."""
    subscriber, bridge, store = _make_subscriber()

    subscriber.subscribe("sess_1", "test")
    queue = subscriber._queues["sess_1"]

    queue.put_nowait({"type": "output", "data": {"text": "[tool: write]\n", "final": False}})
    await asyncio.sleep(0.05)
    queue.put_nowait(
        {
            "type": "permission_request",
            "data": {
                "request_id": "req_1",
                "tool_name": "Write",
                "tool_input": {"path": "/tmp/test"},
            },
        }
    )
    await asyncio.sleep(0.05)

    # Step output flushed, then approval request delivered
    assert len(bridge.outputs) == 1
    assert bridge.outputs[0] == ("sess_1", "[tool: write]\n")
    assert len(bridge.approvals) == 1

    await subscriber.unsubscribe("sess_1", platform="test")


@pytest.mark.asyncio
async def test_output_step_flushed_on_error():
    """Test buffered step output is flushed on error events."""
    subscriber, bridge, store = _make_subscriber()

    subscriber.subscribe("sess_1", "test")
    queue = subscriber._queues["sess_1"]

    queue.put_nowait({"type": "output", "data": {"text": "Working...\n", "final": False}})
    await asyncio.sleep(0.05)
    queue.put_nowait({"type": "error", "data": {"message": "Connection lost"}})
    await asyncio.sleep(0.05)

    assert len(bridge.outputs) == 1
    assert bridge.outputs[0] == ("sess_1", "Working...\n")
    assert len(bridge.statuses) == 1

    await subscriber.unsubscribe("sess_1", platform="test")


@pytest.mark.asyncio
async def test_output_step_flushed_on_unsubscribe():
    """Test buffered output is flushed when unsubscribing."""
    subscriber, bridge, store = _make_subscriber()

    subscriber.subscribe("sess_1", "test")
    queue = subscriber._queues["sess_1"]

    queue.put_nowait({"type": "output", "data": {"text": "Buffered text\n", "final": False}})
    await asyncio.sleep(0.05)

    assert bridge.outputs == []

    await subscriber.unsubscribe("sess_1", platform="test")

    # Unsubscribe should flush remaining buffer
    assert len(bridge.outputs) == 1
    assert bridge.outputs[0] == ("sess_1", "Buffered text\n")

    await subscriber.unsubscribe("sess_1", platform="test")


@pytest.mark.asyncio
async def test_output_final_event_skipped():
    """Test output_final event type is skipped (we use per-step events)."""
    subscriber, bridge, store = _make_subscriber()

    subscriber.subscribe("sess_1", "test")
    queue = subscriber._queues["sess_1"]

    queue.put_nowait({"type": "output_final", "data": {"text": "Accumulated blob"}})
    await asyncio.sleep(0.05)

    assert bridge.outputs == []

    await subscriber.unsubscribe("sess_1", platform="test")


@pytest.mark.asyncio
async def test_empty_output_text_skipped():
    """Test output with empty text is skipped."""
    subscriber, bridge, store = _make_subscriber()

    subscriber.subscribe("sess_1", "test")
    queue = subscriber._queues["sess_1"]

    queue.put_nowait({"type": "output", "data": {"text": "", "final": True}})
    await asyncio.sleep(0.05)

    assert bridge.outputs == []

    await subscriber.unsubscribe("sess_1", platform="test")


@pytest.mark.asyncio
async def test_permission_request():
    """Test permission_request creates correct ApprovalRequest."""
    subscriber, bridge, store = _make_subscriber()

    subscriber.subscribe("sess_1", "test")
    queue = subscriber._queues["sess_1"]

    queue.put_nowait(
        {
            "type": "permission_request",
            "data": {
                "request_id": "req_1",
                "tool_name": "Bash",
                "tool_input": {"command": "ls -la"},
            },
        }
    )
    await asyncio.sleep(0.05)

    assert len(bridge.approvals) == 1
    session_id, request = bridge.approvals[0]
    assert session_id == "sess_1"
    assert request.kind == "permission"
    assert request.request_id == "req_1"
    assert request.title == "Bash"
    assert "ls -la" in request.description
    assert request.options == ["Allow", "Deny"]

    await subscriber.unsubscribe("sess_1", platform="test")


@pytest.mark.asyncio
async def test_permission_request_choice():
    """Test AskUserQuestion creates a choice ApprovalRequest."""
    subscriber, bridge, store = _make_subscriber()

    subscriber.subscribe("sess_1", "test")
    queue = subscriber._queues["sess_1"]

    queue.put_nowait(
        {
            "type": "permission_request",
            "data": {
                "request_id": "req_2",
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "header": "Select env",
                            "question": "Where to deploy?",
                            "options": [
                                {"label": "staging", "description": "Test env"},
                                {"label": "production", "description": "Live env"},
                            ],
                        }
                    ]
                },
            },
        }
    )
    await asyncio.sleep(0.05)

    assert len(bridge.approvals) == 1
    session_id, request = bridge.approvals[0]
    assert session_id == "sess_1"
    assert request.kind == "choice"
    assert request.title == "Select env"
    assert request.options == ["staging", "production"]
    assert "staging" in request.description
    assert "production" in request.description

    await subscriber.unsubscribe("sess_1", platform="test")


@pytest.mark.asyncio
async def test_session_state_running():
    """Test RUNNING state triggers on_typing."""
    subscriber, bridge, store = _make_subscriber()

    subscriber.subscribe("sess_1", "test")
    queue = subscriber._queues["sess_1"]

    queue.put_nowait({"type": "session_state", "data": {"state": "RUNNING"}})
    await asyncio.sleep(0.05)

    assert "sess_1" in bridge.typing_started

    await subscriber.unsubscribe("sess_1", platform="test")


@pytest.mark.asyncio
async def test_session_state_awaiting_input():
    """Test AWAITING_INPUT state triggers on_typing_stopped."""
    subscriber, bridge, store = _make_subscriber()

    subscriber.subscribe("sess_1", "test")
    queue = subscriber._queues["sess_1"]

    queue.put_nowait({"type": "session_state", "data": {"state": "AWAITING_INPUT"}})
    await asyncio.sleep(0.05)

    assert "sess_1" in bridge.typing_stopped

    await subscriber.unsubscribe("sess_1", platform="test")


@pytest.mark.asyncio
async def test_session_state_error():
    """Test ERROR state triggers typing_stopped and status_change."""
    subscriber, bridge, store = _make_subscriber()

    subscriber.subscribe("sess_1", "test")
    queue = subscriber._queues["sess_1"]

    queue.put_nowait({"type": "session_state", "data": {"state": "ERROR"}})
    await asyncio.sleep(0.05)

    assert "sess_1" in bridge.typing_stopped
    assert any(s == "error" for _, s, _ in bridge.statuses)

    await subscriber.unsubscribe("sess_1", platform="test")


@pytest.mark.asyncio
async def test_error_event():
    """Test error event triggers on_status_change with message."""
    subscriber, bridge, store = _make_subscriber()

    subscriber.subscribe("sess_1", "test")
    queue = subscriber._queues["sess_1"]

    queue.put_nowait({"type": "error", "data": {"message": "Connection lost"}})
    await asyncio.sleep(0.05)

    assert len(bridge.statuses) == 1
    assert bridge.statuses[0][0] == "sess_1"
    assert bridge.statuses[0][1] == "error"
    assert bridge.statuses[0][2] == {"message": "Connection lost"}

    await subscriber.unsubscribe("sess_1", platform="test")


@pytest.mark.asyncio
async def test_history_events_skipped():
    """Test events with is_history=True are skipped."""
    subscriber, bridge, store = _make_subscriber()

    subscriber.subscribe("sess_1", "test")
    queue = subscriber._queues["sess_1"]

    queue.put_nowait(
        {
            "type": "output",
            "data": {"text": "Old message", "final": True, "is_history": True},
        }
    )
    queue.put_nowait({"type": "output", "data": {"text": "New message", "final": True}})
    await asyncio.sleep(0.05)

    assert len(bridge.outputs) == 1
    assert bridge.outputs[0] == ("sess_1", "New message")

    await subscriber.unsubscribe("sess_1", platform="test")


@pytest.mark.asyncio
async def test_bridge_error_doesnt_crash_consumer():
    """Test that an error in bridge handling doesn't crash the consumer."""

    class ExplodingBridge(FakeBridge):
        async def on_output(self, session_id, text, metadata=None):
            raise RuntimeError("Boom!")

    bridge = ExplodingBridge()
    manager = BridgeManager()
    manager.register_bridge("test", bridge)
    store = FakeStore()
    subscriber = BridgeSubscriber(manager, store.new_subscriber, store.remove_subscriber)

    subscriber.subscribe("sess_1", "test")
    queue = subscriber._queues["sess_1"]

    # This should not crash the consumer
    queue.put_nowait({"type": "output", "data": {"text": "Boom!", "final": True}})
    await asyncio.sleep(0.05)

    # Consumer should still be alive and processing
    queue.put_nowait({"type": "session_state", "data": {"state": "AWAITING_INPUT"}})
    await asyncio.sleep(0.05)

    assert "sess_1" in bridge.typing_stopped

    await subscriber.unsubscribe("sess_1", platform="test")
