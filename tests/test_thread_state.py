"""Tests for thread_state load/save."""

import json

import pytest

from agent_tether.thread_state import load_mapping, save_mapping


def test_save_and_load(tmp_path):
    """Test round-trip save and load."""
    path = tmp_path / "threads.json"
    mapping = {"sess_1": "Thread 1", "sess_2": "Thread 2"}

    save_mapping(path=path, mapping=mapping)
    loaded = load_mapping(path=path)

    assert loaded == mapping


def test_load_missing_file(tmp_path):
    """Test loading from missing file returns empty dict."""
    path = tmp_path / "nonexistent.json"
    assert load_mapping(path=path) == {}


def test_load_corrupt_json(tmp_path):
    """Test loading corrupt JSON returns empty dict."""
    path = tmp_path / "corrupt.json"
    path.write_text("not valid json {{{")

    assert load_mapping(path=path) == {}


def test_load_non_dict(tmp_path):
    """Test loading non-dict JSON returns empty dict."""
    path = tmp_path / "list.json"
    path.write_text(json.dumps(["a", "b", "c"]))

    assert load_mapping(path=path) == {}


def test_empty_keys_filtered(tmp_path):
    """Test empty or whitespace keys are filtered out."""
    path = tmp_path / "threads.json"
    path.write_text(json.dumps({"": "Empty", "  ": "Spaces", "valid": "OK"}))

    loaded = load_mapping(path=path)

    assert "valid" in loaded
    assert loaded["valid"] == "OK"
    assert "" not in loaded
    assert "  " not in loaded


def test_empty_values_filtered(tmp_path):
    """Test empty or whitespace values are filtered out."""
    path = tmp_path / "threads.json"
    path.write_text(json.dumps({"a": "", "b": "  ", "c": "Valid"}))

    loaded = load_mapping(path=path)

    assert "c" in loaded
    assert loaded["c"] == "Valid"
    assert "a" not in loaded
    assert "b" not in loaded


def test_save_creates_parent_dirs(tmp_path):
    """Test save creates parent directories."""
    path = tmp_path / "nested" / "deep" / "threads.json"
    mapping = {"sess_1": "Thread 1"}

    save_mapping(path=path, mapping=mapping)
    loaded = load_mapping(path=path)

    assert loaded == mapping


def test_save_overwrites(tmp_path):
    """Test save overwrites existing file."""
    path = tmp_path / "threads.json"

    save_mapping(path=path, mapping={"a": "1"})
    save_mapping(path=path, mapping={"b": "2"})

    loaded = load_mapping(path=path)
    assert loaded == {"b": "2"}


def test_save_sorted(tmp_path):
    """Test saved JSON has sorted keys."""
    path = tmp_path / "threads.json"
    mapping = {"z_session": "Z", "a_session": "A", "m_session": "M"}

    save_mapping(path=path, mapping=mapping)

    raw = path.read_text()
    data = json.loads(raw)
    keys = list(data.keys())
    assert keys == sorted(keys)
