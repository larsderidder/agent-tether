"""Helpers for external thread and topic naming."""

from __future__ import annotations

RUNNER_DISPLAY_NAMES: dict[str, str] = {
    "claude-subprocess": "Claude",
    "claude-local": "Claude",
    "claude": "Claude",
    "codex": "Codex",
    "pi": "Pi",
    "litellm": "LiteLLM",
    "opencode": "OpenCode",
}

ADAPTER_TO_RUNNER: dict[str, str] = {
    "claude_auto": "claude",
    "claude_subprocess": "claude",
    "codex_sdk_sidecar": "codex",
    "litellm": "litellm",
    "pi_rpc": "pi",
    "opencode": "opencode",
}


def adapter_to_runner(adapter: str | None) -> str:
    """Map an adapter name to a runner type string."""
    return ADAPTER_TO_RUNNER.get(adapter or "", "")


def runner_display_name(runner_type: str | None) -> str:
    """Return a human-friendly name for a runner type."""
    return RUNNER_DISPLAY_NAMES.get(runner_type or "", "")


def format_thread_name(
    *,
    directory: str | None,
    runner_type: str | None = None,
    adapter: str | None = None,
    max_len: int = 64,
) -> str:
    """Format a thread or topic name from directory and runner info."""
    dir_short = (directory or "").rstrip("/").rsplit("/", 1)[-1] or "Session"
    dir_label = dir_short[:1].upper() + dir_short[1:]

    rt = runner_type or adapter_to_runner(adapter)
    runner_label = runner_display_name(rt)

    if runner_label:
        base_name = f"{runner_label} / {dir_label}"
    else:
        base_name = dir_label

    return base_name[:max_len]
