"""Tests for BridgeInterface base class."""

import asyncio
import os
import time
from unittest.mock import AsyncMock

import pytest

from agent_tether.base import ApprovalRequest, BridgeConfig, BridgeInterface


class FakeBridge(BridgeInterface):
    """Concrete implementation for testing BridgeInterface."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.outputs = []
        self.approvals = []
        self.statuses = []
        self.created_threads = []

    async def on_output(self, session_id: str, text: str, **kwargs):
        """Record output messages."""
        self.outputs.append((session_id, text))

    async def on_approval_request(self, session_id: str, request: ApprovalRequest, **kwargs):
        """Record approval requests."""
        self.approvals.append((session_id, request))

    async def on_status_change(self, session_id: str, status: str, **kwargs):
        """Record status changes."""
        self.statuses.append((session_id, status))

    async def on_typing(self, session_id: str):
        """Record typing events."""
        pass

    async def on_typing_stopped(self, session_id: str):
        """Record typing stopped events."""
        pass

    async def create_thread(self, title: str, **kwargs) -> str:
        """Create a mock thread."""
        thread_id = f"thread_{len(self.created_threads)}"
        self.created_threads.append((thread_id, title))
        return thread_id


# ========== Formatting helpers ==========


def test_humanize_key_no_underscores():
    """Test humanize_key with keys without underscores."""
    assert BridgeInterface._humanize_key("command") == "command"
    assert BridgeInterface._humanize_key("path") == "path"
    assert BridgeInterface._humanize_key("-C") == "-C"


def test_humanize_key_with_underscores():
    """Test humanize_key with snake_case keys."""
    assert BridgeInterface._humanize_key("file_path") == "File path"
    assert BridgeInterface._humanize_key("output_mode") == "Output mode"


def test_humanize_key_acronyms():
    """Test humanize_key handles acronyms correctly."""
    assert BridgeInterface._humanize_key("session_id") == "Session ID"
    assert BridgeInterface._humanize_key("api_key") == "API key"
    assert BridgeInterface._humanize_key("http_url") == "HTTP URL"
    assert BridgeInterface._humanize_key("json_data") == "JSON data"


def test_humanize_enum_value_no_underscores():
    """Test humanize_enum_value with simple values."""
    assert BridgeInterface._humanize_enum_value("running") == "running"
    assert BridgeInterface._humanize_enum_value("active") == "active"


def test_humanize_enum_value_with_underscores():
    """Test humanize_enum_value with snake_case enum values."""
    assert BridgeInterface._humanize_enum_value("files_with_matches") == "Files with matches"
    assert BridgeInterface._humanize_enum_value("user_id") == "User ID"
    assert BridgeInterface._humanize_enum_value("awaiting_input") == "Awaiting input"


def test_humanize_enum_value_preserves_paths():
    """Test humanize_enum_value doesn't mangle paths or commands."""
    # Paths and commands should be left alone
    assert BridgeInterface._humanize_enum_value("/path/to_file") == "/path/to_file"
    assert BridgeInterface._humanize_enum_value("ls -la") == "ls -la"


def test_format_tool_input_markdown_string():
    """Test format_tool_input_markdown with plain text."""
    bridge = FakeBridge()
    result = bridge.format_tool_input_markdown("plain text")
    assert "plain text" in result


def test_format_tool_input_markdown_json():
    """Test format_tool_input_markdown with JSON dictionary."""
    bridge = FakeBridge()
    result = bridge.format_tool_input_markdown('{"command": "ls -la", "path": "/tmp"}')
    assert "command" in result or "Command" in result
    assert "ls -la" in result
    assert "/tmp" in result


def test_format_tool_input_markdown_truncate():
    """Test format_tool_input_markdown truncates long values."""
    bridge = FakeBridge()
    long_value = "x" * 500
    result = bridge.format_tool_input_markdown(f'{{"data": "{long_value}"}}', truncate=100)
    assert "..." in result
    assert len(result) < 600  # Should be truncated


# ========== Approval text parsing ==========


def test_parse_approval_allow():
    """Test parsing basic allow commands."""
    bridge = FakeBridge()
    assert bridge.parse_approval_text("allow") == {
        "allow": True,
        "reason": None,
        "timer": None,
    }
    assert bridge.parse_approval_text("yes") == {
        "allow": True,
        "reason": None,
        "timer": None,
    }
    assert bridge.parse_approval_text("approve") == {
        "allow": True,
        "reason": None,
        "timer": None,
    }


def test_parse_approval_deny():
    """Test parsing basic deny commands."""
    bridge = FakeBridge()
    assert bridge.parse_approval_text("deny") == {
        "allow": False,
        "reason": None,
        "timer": None,
    }
    assert bridge.parse_approval_text("no") == {
        "allow": False,
        "reason": None,
        "timer": None,
    }
    assert bridge.parse_approval_text("reject") == {
        "allow": False,
        "reason": None,
        "timer": None,
    }


def test_parse_approval_deny_with_reason():
    """Test parsing deny commands with reasons."""
    bridge = FakeBridge()
    result = bridge.parse_approval_text("deny: too risky")
    assert result == {"allow": False, "reason": "too risky", "timer": None}

    result = bridge.parse_approval_text("reject: not safe")
    assert result == {"allow": False, "reason": "not safe", "timer": None}

    result = bridge.parse_approval_text("deny testing purposes")
    assert result == {"allow": False, "reason": "testing purposes", "timer": None}


def test_parse_approval_allow_all():
    """Test parsing allow all timer command."""
    bridge = FakeBridge()
    assert bridge.parse_approval_text("allow all") == {
        "allow": True,
        "reason": None,
        "timer": "all",
    }


def test_parse_approval_allow_dir():
    """Test parsing allow dir timer command."""
    bridge = FakeBridge()
    assert bridge.parse_approval_text("allow dir") == {
        "allow": True,
        "reason": None,
        "timer": "dir",
    }


def test_parse_approval_allow_tool():
    """Test parsing allow tool timer command."""
    bridge = FakeBridge()
    result = bridge.parse_approval_text("allow Bash")
    assert result == {"allow": True, "reason": None, "timer": "Bash"}

    result = bridge.parse_approval_text("allow Write")
    assert result == {"allow": True, "reason": None, "timer": "Write"}


def test_parse_approval_synonyms():
    """Test parsing approval synonyms."""
    bridge = FakeBridge()
    assert bridge.parse_approval_text("proceed") == {
        "allow": True,
        "reason": None,
        "timer": None,
    }
    assert bridge.parse_approval_text("continue") == {
        "allow": True,
        "reason": None,
        "timer": None,
    }
    assert bridge.parse_approval_text("cancel") == {
        "allow": False,
        "reason": None,
        "timer": None,
    }


def test_parse_approval_unrecognized():
    """Test parsing unrecognized text returns None."""
    bridge = FakeBridge()
    assert bridge.parse_approval_text("random text") is None
    assert bridge.parse_approval_text("maybe") is None
    assert bridge.parse_approval_text("") is None


# ========== Choice text parsing ==========


def test_parse_choice_numeric():
    """Test parsing numeric choice selection (1-indexed)."""
    bridge = FakeBridge()
    request = ApprovalRequest(
        kind="choice",
        request_id="req_1",
        title="Select env",
        description="Where to deploy?",
        options=["staging", "production"],
    )
    bridge._pending_permissions["sess_1"] = request

    assert bridge.parse_choice_text("sess_1", "1") == "staging"
    assert bridge.parse_choice_text("sess_1", "2") == "production"


def test_parse_choice_label():
    """Test parsing choice by label (case-insensitive)."""
    bridge = FakeBridge()
    request = ApprovalRequest(
        kind="choice",
        request_id="req_1",
        title="Select env",
        description="Where to deploy?",
        options=["staging", "production"],
    )
    bridge._pending_permissions["sess_1"] = request

    assert bridge.parse_choice_text("sess_1", "staging") == "staging"
    assert bridge.parse_choice_text("sess_1", "PRODUCTION") == "production"
    assert bridge.parse_choice_text("sess_1", "Staging") == "staging"


def test_parse_choice_no_pending():
    """Test parsing choice with no pending choice request."""
    bridge = FakeBridge()
    assert bridge.parse_choice_text("sess_1", "1") is None


def test_parse_choice_wrong_kind():
    """Test parsing choice when pending request is not a choice."""
    bridge = FakeBridge()
    request = ApprovalRequest(
        kind="permission",
        request_id="req_1",
        title="Bash",
        description="ls -la",
        options=[],
    )
    bridge._pending_permissions["sess_1"] = request

    assert bridge.parse_choice_text("sess_1", "1") is None


def test_parse_choice_out_of_range():
    """Test parsing numeric choice out of range."""
    bridge = FakeBridge()
    request = ApprovalRequest(
        kind="choice",
        request_id="req_1",
        title="Select env",
        description="Where to deploy?",
        options=["staging", "production"],
    )
    bridge._pending_permissions["sess_1"] = request

    assert bridge.parse_choice_text("sess_1", "0") is None
    assert bridge.parse_choice_text("sess_1", "3") is None


def test_parse_choice_invalid_label():
    """Test parsing choice with invalid label."""
    bridge = FakeBridge()
    request = ApprovalRequest(
        kind="choice",
        request_id="req_1",
        title="Select env",
        description="Where to deploy?",
        options=["staging", "production"],
    )
    bridge._pending_permissions["sess_1"] = request

    assert bridge.parse_choice_text("sess_1", "invalid") is None


# ========== Auto-approve logic ==========


def test_set_allow_all():
    """Test setting allow-all timer."""
    bridge = FakeBridge()
    bridge.set_allow_all("sess_1")

    result = bridge.check_auto_approve("sess_1", "Bash")
    assert result == "Allow All"

    result = bridge.check_auto_approve("sess_1", "Read")
    assert result == "Allow All"


def test_set_allow_tool():
    """Test setting tool-specific timer."""
    bridge = FakeBridge()
    bridge.set_allow_tool("sess_1", "Bash")

    assert bridge.check_auto_approve("sess_1", "Bash") == "Allow Bash"
    assert bridge.check_auto_approve("sess_1", "Read") is None


def test_allow_all_overrides_tool():
    """Test allow-all takes precedence over tool timer."""
    bridge = FakeBridge()
    bridge.set_allow_tool("sess_1", "Bash")
    bridge.set_allow_all("sess_1")

    # Allow All takes precedence
    assert bridge.check_auto_approve("sess_1", "Bash") == "Allow All"


def test_never_auto_approve_tools():
    """Test tools in _NEVER_AUTO_APPROVE are never auto-approved."""
    bridge = FakeBridge()
    bridge.set_allow_all("sess_1")

    assert bridge.check_auto_approve("sess_1", "task") is None
    assert bridge.check_auto_approve("sess_1", "Task") is None
    assert bridge.check_auto_approve("sess_1", "enterplanmode") is None
    assert bridge.check_auto_approve("sess_1", "exitplanmode") is None


def test_never_auto_approve_case_insensitive():
    """Test _NEVER_AUTO_APPROVE is case-insensitive."""
    bridge = FakeBridge()
    bridge.set_allow_all("sess_1")

    assert bridge.check_auto_approve("sess_1", "TASK") is None
    assert bridge.check_auto_approve("sess_1", "EnterPlanMode") is None


def test_set_allow_directory():
    """Test setting directory-scoped timer."""
    bridge = FakeBridge()

    def get_session_dir(session_id: str) -> str | None:
        if session_id == "sess_1":
            return "/home/user/repo"
        if session_id == "sess_2":
            return "/home/user/repo/subdir"
        if session_id == "sess_3":
            return "/home/user/other"
        return None

    bridge._get_session_directory = get_session_dir
    bridge.set_allow_directory("/home/user/repo")

    # Sessions in the directory or subdirectories should match
    result1 = bridge.check_auto_approve("sess_1", "Bash")
    assert result1 is not None
    assert "Allow dir" in result1
    assert "repo" in result1

    result2 = bridge.check_auto_approve("sess_2", "Bash")
    assert result2 is not None
    assert "Allow dir" in result2

    # Session in a different directory should not match
    assert bridge.check_auto_approve("sess_3", "Bash") is None


def test_auto_approve_expiry():
    """Test auto-approve timers expire."""
    bridge = FakeBridge()
    bridge._allow_all_until["sess_1"] = time.time() - 1  # Expired 1 second ago

    assert bridge.check_auto_approve("sess_1", "Bash") is None


def test_auto_approve_different_sessions():
    """Test auto-approve is session-scoped."""
    bridge = FakeBridge()
    bridge.set_allow_all("sess_1")

    assert bridge.check_auto_approve("sess_1", "Bash") == "Allow All"
    assert bridge.check_auto_approve("sess_2", "Bash") is None


# ========== Error debounce ==========


def test_should_send_error_first_time():
    """Test first error is always sent."""
    bridge = FakeBridge(config=BridgeConfig(error_debounce_seconds=5))
    assert bridge._should_send_error_status("sess_1") is True


def test_should_send_error_debounce():
    """Test errors within debounce window are suppressed."""
    bridge = FakeBridge(config=BridgeConfig(error_debounce_seconds=5))

    # First error sent
    assert bridge._should_send_error_status("sess_1") is True
    bridge._last_error_status_sent_at["sess_1"] = time.time()

    # Immediate error suppressed
    assert bridge._should_send_error_status("sess_1") is False


def test_should_send_error_after_window():
    """Test errors after debounce window are sent."""
    bridge = FakeBridge(config=BridgeConfig(error_debounce_seconds=1))

    # First error sent
    assert bridge._should_send_error_status("sess_1") is True
    bridge._last_error_status_sent_at["sess_1"] = time.time() - 2  # 2 seconds ago

    # After window, error is sent
    assert bridge._should_send_error_status("sess_1") is True


def test_should_send_error_disabled():
    """Test debounce=0 always sends errors."""
    bridge = FakeBridge(config=BridgeConfig(error_debounce_seconds=0))

    assert bridge._should_send_error_status("sess_1") is True
    bridge._last_error_status_sent_at["sess_1"] = time.time()
    assert bridge._should_send_error_status("sess_1") is True


# ========== Pending permissions ==========


def test_set_and_get_pending_permission():
    """Test setting and getting pending permissions."""
    bridge = FakeBridge()
    request = ApprovalRequest(
        request_id="req_1",
        title="Bash",
        description="ls -la",
        options=[],
    )

    bridge.set_pending_permission("sess_1", request)
    retrieved = bridge.get_pending_permission("sess_1")

    assert retrieved is not None
    assert retrieved.request_id == "req_1"
    assert retrieved.title == "Bash"


def test_clear_pending_permission():
    """Test clearing pending permissions."""
    bridge = FakeBridge()
    request = ApprovalRequest(
        request_id="req_1",
        title="Bash",
        description="ls -la",
        options=[],
    )

    bridge.set_pending_permission("sess_1", request)
    bridge.clear_pending_permission("sess_1")

    assert bridge.get_pending_permission("sess_1") is None


@pytest.mark.asyncio
async def test_on_session_removed():
    """Test on_session_removed clears all session state."""
    bridge = FakeBridge()

    # Set up various state for sess_1
    bridge.set_allow_all("sess_1")
    bridge.set_allow_tool("sess_1", "Bash")
    request = ApprovalRequest(
        request_id="req_1",
        title="Test",
        description="test",
        options=[],
    )
    bridge.set_pending_permission("sess_1", request)
    bridge._last_error_status_sent_at["sess_1"] = time.time()

    # Remove session
    await bridge.on_session_removed("sess_1")

    # All state should be cleared
    assert bridge.check_auto_approve("sess_1", "Bash") is None
    assert bridge.get_pending_permission("sess_1") is None
    assert "sess_1" not in bridge._last_error_status_sent_at


# ========== Auto-approve batching ==========


@pytest.mark.asyncio
async def test_buffer_auto_approve_notification():
    """Test auto-approve notifications are buffered."""
    bridge = FakeBridge()

    # Buffer some notifications
    bridge.buffer_auto_approve_notification("sess_1", "Bash", "Allow All")
    bridge.buffer_auto_approve_notification("sess_1", "Read", "Allow All")

    # Check buffer was populated (before flush)
    assert len(bridge._auto_approve_buffer.get("sess_1", [])) == 2
    assert bridge._auto_approve_buffer["sess_1"] == [
        ("Bash", "Allow All"),
        ("Read", "Allow All"),
    ]


@pytest.mark.asyncio
async def test_buffer_auto_approve_single_item():
    """Test single auto-approve notification."""
    bridge = FakeBridge()

    bridge.buffer_auto_approve_notification("sess_1", "Bash", "Allow All")

    assert len(bridge._auto_approve_buffer.get("sess_1", [])) == 1
    assert bridge._auto_approve_buffer["sess_1"] == [("Bash", "Allow All")]
