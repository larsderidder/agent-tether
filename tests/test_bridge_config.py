"""Tests for BridgeConfig and type aliases."""

from agent_tether.base import BridgeConfig


def test_default_values():
    """Test BridgeConfig default values."""
    config = BridgeConfig()
    assert config.data_dir == ""
    assert config.error_debounce_seconds == 0
    assert config.default_adapter is None


def test_custom_values():
    """Test BridgeConfig with custom values."""
    config = BridgeConfig(
        data_dir="/custom/path",
        error_debounce_seconds=5,
        default_adapter="claude_auto",
    )
    assert config.data_dir == "/custom/path"
    assert config.error_debounce_seconds == 5
    assert config.default_adapter == "claude_auto"


def test_round_trip():
    """Test custom values round-trip correctly."""
    original = BridgeConfig(
        data_dir="/tmp/data",
        error_debounce_seconds=10,
        default_adapter="codex_sdk_sidecar",
    )

    copy = BridgeConfig(
        data_dir=original.data_dir,
        error_debounce_seconds=original.error_debounce_seconds,
        default_adapter=original.default_adapter,
    )

    assert copy.data_dir == original.data_dir
    assert copy.error_debounce_seconds == original.error_debounce_seconds
    assert copy.default_adapter == original.default_adapter
