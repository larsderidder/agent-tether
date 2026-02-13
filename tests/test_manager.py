"""Tests for BridgeManager."""

import pytest

from agent_tether.base import ApprovalRequest, BridgeInterface, BridgeConfig
from agent_tether.manager import BridgeManager


class MockBridge(BridgeInterface):
    """Mock bridge for testing."""

    def __init__(self, name: str):
        super().__init__(BridgeConfig())
        self.name = name
        self.outputs = []
        self.approvals = []
        self.statuses = []
        self.threads = []

    async def on_output(self, session_id: str, text: str, metadata=None):
        self.outputs.append((session_id, text, metadata))

    async def on_approval_request(self, session_id: str, request: ApprovalRequest):
        self.approvals.append((session_id, request))

    async def on_status_change(self, session_id: str, status: str, metadata=None):
        self.statuses.append((session_id, status, metadata))

    async def create_thread(self, session_id: str, session_name: str) -> dict:
        thread_id = f"{self.name}_thread_{len(self.threads)}"
        self.threads.append((session_id, session_name, thread_id))
        return {"thread_id": thread_id, "platform": self.name}


def test_register_and_get_bridge():
    """Test registering and retrieving bridges."""
    manager = BridgeManager()
    bridge = MockBridge("telegram")

    manager.register_bridge("telegram", bridge)

    retrieved = manager.get_bridge("telegram")
    assert retrieved is bridge


def test_get_bridge_unknown():
    """Test get_bridge returns None for unknown platform."""
    manager = BridgeManager()
    assert manager.get_bridge("unknown") is None


def test_list_bridges_empty():
    """Test list_bridges with no bridges."""
    manager = BridgeManager()
    assert manager.list_bridges() == []


def test_list_bridges():
    """Test list_bridges returns registered platforms."""
    manager = BridgeManager()
    manager.register_bridge("telegram", MockBridge("telegram"))
    manager.register_bridge("slack", MockBridge("slack"))
    manager.register_bridge("discord", MockBridge("discord"))

    platforms = manager.list_bridges()
    assert set(platforms) == {"telegram", "slack", "discord"}


def test_register_multiple_bridges():
    """Test registering multiple bridges."""
    manager = BridgeManager()
    telegram = MockBridge("telegram")
    slack = MockBridge("slack")

    manager.register_bridge("telegram", telegram)
    manager.register_bridge("slack", slack)

    assert manager.get_bridge("telegram") is telegram
    assert manager.get_bridge("slack") is slack


def test_register_overwrites():
    """Test registering a bridge twice overwrites the first."""
    manager = BridgeManager()
    bridge1 = MockBridge("telegram_v1")
    bridge2 = MockBridge("telegram_v2")

    manager.register_bridge("telegram", bridge1)
    manager.register_bridge("telegram", bridge2)

    assert manager.get_bridge("telegram") is bridge2


@pytest.mark.asyncio
async def test_route_output():
    """Test routing output to the correct bridge."""
    manager = BridgeManager()
    telegram = MockBridge("telegram")
    slack = MockBridge("slack")

    manager.register_bridge("telegram", telegram)
    manager.register_bridge("slack", slack)

    await manager.route_output("sess_1", "Hello from Telegram", "telegram")
    await manager.route_output("sess_2", "Hello from Slack", "slack")

    assert len(telegram.outputs) == 1
    assert telegram.outputs[0] == ("sess_1", "Hello from Telegram", None)

    assert len(slack.outputs) == 1
    assert slack.outputs[0] == ("sess_2", "Hello from Slack", None)


@pytest.mark.asyncio
async def test_route_output_with_metadata():
    """Test routing output with metadata."""
    manager = BridgeManager()
    bridge = MockBridge("telegram")
    manager.register_bridge("telegram", bridge)

    metadata = {"stream": "stdout", "final": True}
    await manager.route_output("sess_1", "Output text", "telegram", metadata)

    assert len(bridge.outputs) == 1
    assert bridge.outputs[0] == ("sess_1", "Output text", metadata)


@pytest.mark.asyncio
async def test_route_output_unknown_platform():
    """Test routing output to unknown platform doesn't raise."""
    manager = BridgeManager()

    # Should not raise, just log warning
    await manager.route_output("sess_1", "Message", "unknown")


@pytest.mark.asyncio
async def test_route_approval():
    """Test routing approval request to the correct bridge."""
    manager = BridgeManager()
    telegram = MockBridge("telegram")
    manager.register_bridge("telegram", telegram)

    request = ApprovalRequest(
        request_id="req_1",
        title="Bash",
        description="ls -la",
        options=[],
    )

    await manager.route_approval("sess_1", request, "telegram")

    assert len(telegram.approvals) == 1
    assert telegram.approvals[0] == ("sess_1", request)


@pytest.mark.asyncio
async def test_route_approval_unknown_platform():
    """Test routing approval to unknown platform doesn't raise."""
    manager = BridgeManager()

    request = ApprovalRequest(
        request_id="req_1",
        title="Bash",
        description="ls -la",
        options=[],
    )

    # Should not raise, just log warning
    await manager.route_approval("sess_1", request, "unknown")


@pytest.mark.asyncio
async def test_route_status():
    """Test routing status change to the correct bridge."""
    manager = BridgeManager()
    bridge = MockBridge("telegram")
    manager.register_bridge("telegram", bridge)

    await manager.route_status("sess_1", "running", "telegram")

    assert len(bridge.statuses) == 1
    assert bridge.statuses[0] == ("sess_1", "running", None)


@pytest.mark.asyncio
async def test_route_status_with_metadata():
    """Test routing status with metadata."""
    manager = BridgeManager()
    bridge = MockBridge("telegram")
    manager.register_bridge("telegram", bridge)

    metadata = {"error": "Connection failed"}
    await manager.route_status("sess_1", "error", "telegram", metadata)

    assert len(bridge.statuses) == 1
    assert bridge.statuses[0] == ("sess_1", "error", metadata)


@pytest.mark.asyncio
async def test_route_status_unknown_platform():
    """Test routing status to unknown platform doesn't raise."""
    manager = BridgeManager()

    # Should not raise, just log warning
    await manager.route_status("sess_1", "running", "unknown")


@pytest.mark.asyncio
async def test_create_thread():
    """Test creating a thread on a platform."""
    manager = BridgeManager()
    bridge = MockBridge("telegram")
    manager.register_bridge("telegram", bridge)

    result = await manager.create_thread("sess_1", "My Session", "telegram")

    assert result == {"thread_id": "telegram_thread_0", "platform": "telegram"}
    assert len(bridge.threads) == 1
    assert bridge.threads[0] == ("sess_1", "My Session", "telegram_thread_0")


@pytest.mark.asyncio
async def test_create_thread_unknown_platform():
    """Test creating thread on unknown platform raises ValueError."""
    manager = BridgeManager()

    with pytest.raises(ValueError, match="No bridge registered for platform: unknown"):
        await manager.create_thread("sess_1", "My Session", "unknown")
