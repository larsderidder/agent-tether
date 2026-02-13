"""Tests for TextCommandBridge shared logic."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from agent_tether.base import ApprovalRequest, BridgeCallbacks, BridgeConfig
from agent_tether.text_command_bridge import TextCommandBridge


class FakeTextBridge(TextCommandBridge):
    """Concrete implementation for testing TextCommandBridge."""

    def __init__(self, tmp_path: Path, **kwargs):
        super().__init__(
            thread_name_path=tmp_path / "threads.json",
            **kwargs,
        )
        self.outputs: list[tuple[str, str]] = []

    async def on_output(self, session_id, text, metadata=None):
        self.outputs.append((session_id, text))

    async def on_approval_request(self, session_id, request):
        pass

    async def on_status_change(self, session_id, status, metadata=None):
        pass

    async def create_thread(self, session_id, session_name):
        return {"thread_id": f"thread_{session_id}"}


# ========== Thread naming ==========


def test_pick_unique_thread_name_first(tmp_path):
    """Test first name is returned as-is."""
    bridge = FakeTextBridge(tmp_path)
    assert bridge._pick_unique_thread_name("MyProject") == "MyProject"


def test_pick_unique_thread_name_dedup(tmp_path):
    """Test duplicate names get a suffix."""
    bridge = FakeTextBridge(tmp_path)
    bridge._used_thread_names.add("MyProject")
    assert bridge._pick_unique_thread_name("MyProject") == "MyProject 2"


def test_pick_unique_thread_name_multiple_dupes(tmp_path):
    """Test multiple duplicates get incrementing suffixes."""
    bridge = FakeTextBridge(tmp_path)
    bridge._used_thread_names.update({"MyProject", "MyProject 2", "MyProject 3"})
    assert bridge._pick_unique_thread_name("MyProject") == "MyProject 4"


def test_make_external_thread_name(tmp_path):
    """Test thread name is derived from directory."""
    bridge = FakeTextBridge(tmp_path)
    name = bridge._make_external_thread_name(directory="/home/user/my-repo", session_id="s1")
    assert name == "My-repo"


def test_make_external_thread_name_dedup(tmp_path):
    """Test duplicate directory names get suffixed."""
    bridge = FakeTextBridge(tmp_path)
    bridge._used_thread_names.add("My-repo")
    name = bridge._make_external_thread_name(directory="/home/user/my-repo", session_id="s1")
    assert name == "My-repo 2"


# ========== Thread name persistence ==========


def test_reserve_and_release_thread_name(tmp_path):
    """Test reserving and releasing thread names persists to disk."""
    bridge = FakeTextBridge(tmp_path)
    bridge._reserve_thread_name("sess_1", "MyProject")

    assert "MyProject" in bridge._used_thread_names
    assert bridge._thread_names["sess_1"] == "MyProject"
    assert (tmp_path / "threads.json").exists()

    bridge._release_thread_name("sess_1")
    assert "MyProject" not in bridge._used_thread_names
    assert "sess_1" not in bridge._thread_names


@pytest.mark.asyncio
async def test_on_session_removed_releases_name(tmp_path):
    """Test on_session_removed cleans up thread names."""
    bridge = FakeTextBridge(tmp_path)
    bridge._reserve_thread_name("sess_1", "MyProject")
    bridge.set_allow_all("sess_1")

    await bridge.on_session_removed("sess_1")

    assert "MyProject" not in bridge._used_thread_names
    assert bridge.check_auto_approve("sess_1", "Bash") is None


# ========== _parse_list_args ==========


def test_parse_list_args_empty(tmp_path):
    """Test empty args returns page 1, no query."""
    bridge = FakeTextBridge(tmp_path)
    assert bridge._parse_list_args("") == (1, None)


def test_parse_list_args_page_number(tmp_path):
    """Test numeric arg is parsed as page number."""
    bridge = FakeTextBridge(tmp_path)
    assert bridge._parse_list_args("3") == (3, None)


def test_parse_list_args_search_query(tmp_path):
    """Test non-numeric arg is parsed as search query."""
    bridge = FakeTextBridge(tmp_path)
    assert bridge._parse_list_args("my-repo") == (1, "my-repo")


# ========== _parse_new_args ==========


@pytest.mark.asyncio
async def test_parse_new_args_no_context_no_args(tmp_path):
    """Test no args and no base session raises ValueError."""
    bridge = FakeTextBridge(tmp_path)
    with pytest.raises(ValueError, match="Usage"):
        await bridge._parse_new_args("", base_session_id=None)


@pytest.mark.asyncio
async def test_parse_new_args_unknown_agent(tmp_path):
    """Test unknown agent name raises ValueError."""
    bridge = FakeTextBridge(tmp_path)
    with pytest.raises(ValueError, match="Unknown agent"):
        await bridge._parse_new_args("badagent /tmp", base_session_id=None)


@pytest.mark.asyncio
async def test_parse_new_args_agent_without_dir(tmp_path):
    """Test agent name without directory (outside session) raises ValueError."""
    bridge = FakeTextBridge(tmp_path)
    with pytest.raises(ValueError, match="Usage"):
        await bridge._parse_new_args("claude", base_session_id=None)


@pytest.mark.asyncio
async def test_parse_new_args_directory_only(tmp_path):
    """Test bare directory resolves correctly."""
    check_dir = AsyncMock(return_value={"exists": True, "path": "/tmp"})
    callbacks = AsyncMock(spec=BridgeCallbacks)
    callbacks.check_directory = check_dir

    bridge = FakeTextBridge(tmp_path, callbacks=callbacks)
    adapter, directory = await bridge._parse_new_args("/tmp", base_session_id=None)

    assert adapter is None
    assert directory == "/tmp"


# ========== _handle_approval_text_response ==========


@pytest.mark.asyncio
async def test_handle_approval_text_response_allow(tmp_path):
    """Test approval allow response."""
    respond = AsyncMock(return_value=True)
    callbacks = AsyncMock(spec=BridgeCallbacks)
    callbacks.respond_to_permission = respond

    bridge = FakeTextBridge(tmp_path, callbacks=callbacks)
    request = ApprovalRequest(
        request_id="req_1", title="Bash", description="ls", options=[]
    )
    bridge.set_pending_permission("s1", request)

    ok, message = await bridge._handle_approval_text_response(
        "s1", request, {"allow": True, "reason": None, "timer": None}
    )
    assert ok is True
    assert message == "Approved"


@pytest.mark.asyncio
async def test_handle_approval_text_response_deny_with_reason(tmp_path):
    """Test approval deny with reason."""
    respond = AsyncMock(return_value=True)
    callbacks = AsyncMock(spec=BridgeCallbacks)
    callbacks.respond_to_permission = respond

    bridge = FakeTextBridge(tmp_path, callbacks=callbacks)
    request = ApprovalRequest(
        request_id="req_1", title="Bash", description="rm -rf", options=[]
    )
    bridge.set_pending_permission("s1", request)

    ok, message = await bridge._handle_approval_text_response(
        "s1", request, {"allow": False, "reason": "too dangerous", "timer": None}
    )
    assert ok is True
    assert message == "Denied: too dangerous"


@pytest.mark.asyncio
async def test_handle_approval_text_response_allow_all_timer(tmp_path):
    """Test approval with allow-all timer sets the timer."""
    respond = AsyncMock(return_value=True)
    callbacks = AsyncMock(spec=BridgeCallbacks)
    callbacks.respond_to_permission = respond

    bridge = FakeTextBridge(tmp_path, callbacks=callbacks)
    request = ApprovalRequest(
        request_id="req_1", title="Bash", description="ls", options=[]
    )

    ok, message = await bridge._handle_approval_text_response(
        "s1", request, {"allow": True, "reason": None, "timer": "all"}
    )
    assert ok is True
    assert message == "Allow All (30m)"
    assert bridge.check_auto_approve("s1", "Bash") == "Allow All"


# ========== _format_external_replay ==========


@pytest.mark.asyncio
async def test_format_external_replay_no_messages(tmp_path):
    """Test replay returns None when there are no messages."""
    get_history = AsyncMock(return_value={"messages": []})
    callbacks = AsyncMock(spec=BridgeCallbacks)
    callbacks.get_external_history = get_history

    bridge = FakeTextBridge(tmp_path, callbacks=callbacks)
    result = await bridge._format_external_replay("ext_1", "claude")
    assert result is None


@pytest.mark.asyncio
async def test_format_external_replay_with_messages(tmp_path):
    """Test replay formats messages correctly."""
    get_history = AsyncMock(
        return_value={
            "messages": [
                {"role": "user", "content": "fix the bug"},
                {"role": "assistant", "content": "I'll look at it."},
            ]
        }
    )
    callbacks = AsyncMock(spec=BridgeCallbacks)
    callbacks.get_external_history = get_history

    bridge = FakeTextBridge(tmp_path, callbacks=callbacks)
    result = await bridge._format_external_replay("ext_1", "claude")
    assert result is not None
    assert "fix the bug" in result
    assert "look at it" in result
    assert "👤" in result
    assert "🤖" in result
