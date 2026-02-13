"""Tests for Discord pairing state."""

import json

import pytest

from agent_tether.discord.pairing_state import (
    DiscordPairingState,
    generate_pairing_code,
    load_or_create,
    save,
)


def test_generate_pairing_code_format():
    """Test pairing code is 8 digits."""
    code = generate_pairing_code()
    assert len(code) == 8
    assert code.isdigit()


def test_generate_pairing_code_uniqueness():
    """Test consecutive codes are different (probabilistic)."""
    codes = {generate_pairing_code() for _ in range(10)}
    # With 1e8 space, 10 codes should all be unique
    assert len(codes) == 10


def test_load_or_create_no_file(tmp_path):
    """Test creating state when no file exists."""
    path = tmp_path / "pairing.json"

    state = load_or_create(path=path)

    assert len(state.pairing_code) == 8
    assert state.pairing_code.isdigit()
    assert state.paired_user_ids == set()
    assert state.control_channel_id is None
    assert state.created_at != ""

    # File should have been created
    assert path.exists()


def test_load_or_create_existing_file(tmp_path):
    """Test loading state from existing file."""
    path = tmp_path / "pairing.json"

    # Write state manually
    data = {
        "pairing_code": "12345678",
        "paired_user_ids": [111, 222],
        "control_channel_id": 999,
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    path.write_text(json.dumps(data))

    state = load_or_create(path=path)

    assert state.pairing_code == "12345678"
    assert state.paired_user_ids == {111, 222}
    assert state.control_channel_id == 999


def test_load_or_create_fixed_code(tmp_path):
    """Test fixed_code overrides saved code."""
    path = tmp_path / "pairing.json"

    # Create initial state
    state1 = load_or_create(path=path)
    original_code = state1.pairing_code

    # Load with fixed_code
    state2 = load_or_create(path=path, fixed_code="99999999")

    assert state2.pairing_code == "99999999"

    # Verify it was persisted
    state3 = load_or_create(path=path)
    assert state3.pairing_code == "99999999"


def test_load_or_create_fixed_code_same_as_saved(tmp_path):
    """Test fixed_code matching saved code is a no-op."""
    path = tmp_path / "pairing.json"

    state1 = load_or_create(path=path, fixed_code="11111111")
    state2 = load_or_create(path=path, fixed_code="11111111")

    assert state2.pairing_code == "11111111"


def test_save_and_reload(tmp_path):
    """Test save and reload round-trip."""
    path = tmp_path / "pairing.json"

    state = DiscordPairingState(
        pairing_code="87654321",
        paired_user_ids={100, 200, 300},
        control_channel_id=555,
        created_at="2026-01-01T00:00:00+00:00",
    )
    save(path=path, state=state)

    loaded = load_or_create(path=path)

    assert loaded.pairing_code == "87654321"
    assert loaded.paired_user_ids == {100, 200, 300}
    assert loaded.control_channel_id == 555


def test_load_or_create_corrupt_file(tmp_path):
    """Test corrupt file results in fresh state."""
    path = tmp_path / "pairing.json"
    path.write_text("not valid json {{{")

    state = load_or_create(path=path)

    assert len(state.pairing_code) == 8
    assert state.paired_user_ids == set()


def test_load_or_create_creates_parent_dirs(tmp_path):
    """Test load_or_create creates parent directories."""
    path = tmp_path / "nested" / "deep" / "pairing.json"

    state = load_or_create(path=path)

    assert path.exists()
    assert len(state.pairing_code) == 8


def test_to_json():
    """Test DiscordPairingState serialization."""
    state = DiscordPairingState(
        pairing_code="12345678",
        paired_user_ids={300, 100, 200},
        control_channel_id=999,
        created_at="2026-01-01T00:00:00+00:00",
    )

    data = state.to_json()

    assert data["pairing_code"] == "12345678"
    assert data["paired_user_ids"] == [100, 200, 300]  # Sorted
    assert data["control_channel_id"] == 999
    assert data["created_at"] == "2026-01-01T00:00:00+00:00"
