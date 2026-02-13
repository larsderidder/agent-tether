"""Tests for Telegram StateManager."""

import json

import pytest

from agent_tether.telegram.state import StateManager


@pytest.fixture
def state_path(tmp_path):
    """Return a temporary state file path."""
    return str(tmp_path / "telegram_state.json")


def test_set_and_get_topic(state_path):
    """Test setting and getting a topic mapping."""
    sm = StateManager(state_path)

    sm.set_topic_for_session("sess_1", 12345, "My Session")

    assert sm.get_topic_for_session("sess_1") == 12345


def test_get_session_for_topic(state_path):
    """Test reverse lookup from topic to session."""
    sm = StateManager(state_path)

    sm.set_topic_for_session("sess_1", 12345, "My Session")

    assert sm.get_session_for_topic(12345) == "sess_1"


def test_get_topic_unknown_session(state_path):
    """Test getting topic for unknown session returns None."""
    sm = StateManager(state_path)
    assert sm.get_topic_for_session("unknown") is None


def test_get_session_unknown_topic(state_path):
    """Test getting session for unknown topic returns None."""
    sm = StateManager(state_path)
    assert sm.get_session_for_topic(99999) is None


def test_remove_session(state_path):
    """Test removing a session clears both mappings."""
    sm = StateManager(state_path)

    sm.set_topic_for_session("sess_1", 12345, "My Session")
    sm.remove_session("sess_1")

    assert sm.get_topic_for_session("sess_1") is None
    assert sm.get_session_for_topic(12345) is None


def test_remove_nonexistent_session(state_path):
    """Test removing a nonexistent session is a no-op."""
    sm = StateManager(state_path)
    sm.remove_session("nonexistent")  # Should not raise


def test_save_and_load(state_path):
    """Test state persists to disk and can be reloaded."""
    sm1 = StateManager(state_path)
    sm1.set_topic_for_session("sess_1", 111, "First")
    sm1.set_topic_for_session("sess_2", 222, "Second")

    # Create a new StateManager and load from disk
    sm2 = StateManager(state_path)
    sm2.load()

    assert sm2.get_topic_for_session("sess_1") == 111
    assert sm2.get_topic_for_session("sess_2") == 222
    assert sm2.get_session_for_topic(111) == "sess_1"
    assert sm2.get_session_for_topic(222) == "sess_2"


def test_load_missing_file(state_path):
    """Test loading from missing file results in empty state."""
    sm = StateManager(state_path)
    sm.load()  # Should not raise

    assert sm.get_topic_for_session("anything") is None


def test_load_corrupt_file(state_path):
    """Test loading from corrupt file results in empty state."""
    with open(state_path, "w") as f:
        f.write("not valid json {{{")

    sm = StateManager(state_path)
    sm.load()  # Should not raise

    assert sm.get_topic_for_session("anything") is None


def test_multiple_sessions(state_path):
    """Test managing multiple sessions."""
    sm = StateManager(state_path)

    sm.set_topic_for_session("sess_1", 100, "Session 1")
    sm.set_topic_for_session("sess_2", 200, "Session 2")
    sm.set_topic_for_session("sess_3", 300, "Session 3")

    assert sm.get_topic_for_session("sess_1") == 100
    assert sm.get_topic_for_session("sess_2") == 200
    assert sm.get_topic_for_session("sess_3") == 300

    sm.remove_session("sess_2")

    assert sm.get_topic_for_session("sess_1") == 100
    assert sm.get_topic_for_session("sess_2") is None
    assert sm.get_topic_for_session("sess_3") == 300


def test_overwrite_session_topic(state_path):
    """Test overwriting a session's topic mapping."""
    sm = StateManager(state_path)

    sm.set_topic_for_session("sess_1", 100, "Original")
    sm.set_topic_for_session("sess_1", 200, "Updated")

    assert sm.get_topic_for_session("sess_1") == 200
    assert sm.get_session_for_topic(200) == "sess_1"
