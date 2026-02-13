"""Tests for BridgeConfig and type aliases."""

import pytest

from agent_tether.base import BridgeConfig


def test_default_values():
    """Test BridgeConfig default values."""
    config = BridgeConfig()
    assert config.api_port == 8787
    assert config.data_dir == ""
    assert config.error_debounce_seconds == 0


def test_custom_values():
    """Test BridgeConfig with custom values."""
    config = BridgeConfig(
        api_port=9000,
        data_dir="/custom/path",
        error_debounce_seconds=5,
    )
    assert config.api_port == 9000
    assert config.data_dir == "/custom/path"
    assert config.error_debounce_seconds == 5


def test_round_trip():
    """Test custom values round-trip correctly."""
    original = BridgeConfig(
        api_port=8080,
        data_dir="/tmp/data",
        error_debounce_seconds=10,
    )

    # Create a new instance with same values
    copy = BridgeConfig(
        api_port=original.api_port,
        data_dir=original.data_dir,
        error_debounce_seconds=original.error_debounce_seconds,
    )

    assert copy.api_port == original.api_port
    assert copy.data_dir == original.data_dir
    assert copy.error_debounce_seconds == original.error_debounce_seconds
