"""Shared base for text-command bridges (Slack, Discord).

Both Slack and Discord use the same command set (!help, !status, !list, etc.)
and identical business logic for thread naming, approval handling, and
argument parsing. This module extracts that shared logic so the platform
bridges only implement the transport layer.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from agent_tether.base import (
    ApprovalRequest,
    BridgeCallbacks,
    BridgeConfig,
    BridgeInterface,
    GetSessionDirectory,
    GetSessionInfo,
    OnSessionBound,
    _EXTERNAL_MAX_FETCH,
    _EXTERNAL_REPLAY_LIMIT,
    _EXTERNAL_REPLAY_MAX_CHARS,
)
from agent_tether.thread_state import load_mapping, save_mapping

logger = structlog.get_logger(__name__)

_THREAD_NAME_MAX_LEN = 64


class TextCommandBridge(BridgeInterface):
    """Base class for text-command bridges (Slack, Discord).

    Provides shared logic for:
    - Thread name allocation and persistence
    - ``!new`` / ``!list`` / ``!status`` argument parsing
    - Approval text response handling
    - External session replay formatting
    - Session-to-thread reverse lookups

    Subclasses implement the abstract ``_send_reply`` / ``_get_thread_session_id``
    methods and the platform-specific transport.
    """

    def __init__(
        self,
        *,
        config: BridgeConfig | None = None,
        callbacks: BridgeCallbacks | None = None,
        get_session_directory: GetSessionDirectory | None = None,
        get_session_info: GetSessionInfo | None = None,
        on_session_bound: OnSessionBound | None = None,
        thread_name_path: Path,
    ) -> None:
        super().__init__(
            config=config,
            callbacks=callbacks,
            get_session_directory=get_session_directory,
            get_session_info=get_session_info,
            on_session_bound=on_session_bound,
        )
        self._thread_name_path = thread_name_path
        self._thread_names: dict[str, str] = load_mapping(path=self._thread_name_path)
        self._used_thread_names: set[str] = set(self._thread_names.values())

    # ------------------------------------------------------------------
    # Thread naming
    # ------------------------------------------------------------------

    def _pick_unique_thread_name(self, base_name: str) -> str:
        """Pick a unique thread/channel name, appending a number if needed."""
        base_name = (base_name or "Session").strip() or "Session"
        base_name = base_name[:_THREAD_NAME_MAX_LEN]
        if base_name not in self._used_thread_names:
            return base_name

        for i in range(2, 100):
            suffix = f" {i}"
            avail = max(1, _THREAD_NAME_MAX_LEN - len(suffix))
            candidate = (base_name[:avail] + suffix)[:_THREAD_NAME_MAX_LEN]
            if candidate not in self._used_thread_names:
                return candidate

        return base_name

    def _make_external_thread_name(self, *, directory: str, session_id: str) -> str:
        """Generate a unique thread name from a directory path.

        Uses the last path component, upper-cased, and appends a number
        if a thread with the same name already exists.
        """
        dir_short = (directory or "").rstrip("/").rsplit("/", 1)[-1] or "Session"
        base_name = (dir_short[:1].upper() + dir_short[1:])[:_THREAD_NAME_MAX_LEN]
        return self._pick_unique_thread_name(base_name)

    def _reserve_thread_name(self, session_id: str, name: str) -> None:
        """Persist a session-to-name mapping and mark the name as used."""
        self._thread_names[session_id] = name
        self._used_thread_names.add(name)
        save_mapping(path=self._thread_name_path, mapping=self._thread_names)

    def _release_thread_name(self, session_id: str) -> None:
        """Release a previously reserved thread name."""
        name = self._thread_names.pop(session_id, None)
        if name:
            self._used_thread_names.discard(name)
            save_mapping(path=self._thread_name_path, mapping=self._thread_names)

    # ------------------------------------------------------------------
    # Argument parsing
    # ------------------------------------------------------------------

    async def _parse_new_args(
        self,
        args: str,
        *,
        base_session_id: str | None,
    ) -> tuple[str | None, str]:
        """Parse ``!new`` arguments into (adapter, directory).

        Returns (adapter, resolved_directory).
        Raises ``ValueError`` with a user-facing message on bad input.
        """
        parts = (args or "").split()

        base_directory: str | None = None
        base_adapter: str | None = None
        if base_session_id and self._get_session_info:
            s = self._get_session_info(base_session_id)
            if s:
                base_directory = s.get("directory")
                base_adapter = s.get("adapter")

        adapter: str | None = None
        directory_raw: str | None = None

        if not parts:
            if not base_directory:
                raise ValueError(
                    "Usage: !new <agent> <directory>\n"
                    "Or, inside a session thread: !new or !new <agent>"
                )
            adapter = base_adapter
            directory_raw = base_directory
        elif len(parts) == 1:
            token = parts[0]
            maybe_adapter = self._agent_to_adapter(token)
            if base_directory:
                if maybe_adapter:
                    adapter = maybe_adapter
                    directory_raw = base_directory
                else:
                    adapter = base_adapter
                    directory_raw = token
            else:
                if maybe_adapter:
                    raise ValueError("Usage: !new <agent> <directory>")
                directory_raw = token
        else:
            adapter = self._agent_to_adapter(parts[0])
            if not adapter:
                raise ValueError(
                    "Unknown agent. Use: claude, codex, claude_auto, "
                    "claude_subprocess, claude_api, codex_sdk_sidecar"
                )
            directory_raw = " ".join(parts[1:]).strip()

        assert directory_raw is not None
        directory = await self._resolve_directory_arg(
            directory_raw, base_directory=base_directory
        )
        return adapter, directory

    def _parse_list_args(self, args: str) -> tuple[int, str | None]:
        """Parse ``!list`` arguments into (page, query).

        Returns (page_number, search_query_or_None).
        """
        if not args:
            return 1, None
        first = args.split()[0]
        try:
            return int(first), self._external_query
        except ValueError:
            return 1, args.strip()

    # ------------------------------------------------------------------
    # Approval text handling
    # ------------------------------------------------------------------

    async def _handle_approval_text_response(
        self,
        session_id: str,
        request: ApprovalRequest,
        parsed: dict,
    ) -> tuple[bool, str]:
        """Process a parsed approval response, apply timers, and send the API call.

        Returns (success, display_message).
        """
        allow = parsed["allow"]
        reason = parsed.get("reason")
        timer = parsed.get("timer")

        if allow and timer == "all":
            self.set_allow_all(session_id)
        elif allow and timer == "dir":
            _dir = (
                self._get_session_directory(session_id)
                if self._get_session_directory
                else None
            )
            if _dir:
                self.set_allow_directory(_dir)
            else:
                self.set_allow_all(session_id)
        elif allow and timer:
            self.set_allow_tool(session_id, timer)

        if allow:
            message = "Approved"
            if timer == "all":
                message = "Allow All (30m)"
            elif timer == "dir":
                message = "Allow dir (30m)"
            elif timer:
                message = f"Allow {timer} (30m)"
        else:
            message = f"Denied: {reason}" if reason else "Denied"

        ok = await self._respond_to_permission(
            session_id,
            request.request_id,
            allow=allow,
            message=message,
        )
        return ok, message

    # ------------------------------------------------------------------
    # External session replay
    # ------------------------------------------------------------------

    async def _format_external_replay(
        self,
        external_id: str,
        runner_type: str,
        *,
        content_limit: int = 800,
        thinking_limit: int = 400,
        total_limit: int = _EXTERNAL_REPLAY_MAX_CHARS,
    ) -> str | None:
        """Fetch and format external session history as plain text.

        Returns the formatted text, or None if there are no messages.
        """
        try:
            payload = await self._callbacks.get_external_history(
                external_id, runner_type, _EXTERNAL_REPLAY_LIMIT
            )
            if not payload:
                return None
        except Exception:
            logger.exception(
                "Failed to fetch external session history for replay",
                external_id=external_id,
                runner_type=runner_type,
            )
            return None

        messages = payload.get("messages") or []
        if not messages:
            return None

        lines: list[str] = [
            f"Recent history (last {min(_EXTERNAL_REPLAY_LIMIT, len(messages))} messages):\n"
        ]
        for i, msg in enumerate(messages, 1):
            role = str(msg.get("role") or "").lower()
            prefix = (
                "👤"
                if role == "user"
                else ("🤖" if role == "assistant" else role[:1].upper() or "?")
            )
            content = (msg.get("content") or "").strip()
            thinking = (msg.get("thinking") or "").strip()
            if content and len(content) > content_limit:
                content = content[:content_limit] + "..."
            if thinking and len(thinking) > thinking_limit:
                thinking = thinking[:thinking_limit] + "..."
            if content:
                lines.append(f"{i}. {prefix}: {content}")
            if thinking:
                lines.append(f"   {prefix} (thinking): {thinking}")

        text = "\n".join(lines)
        if len(text) > total_limit:
            text = text[: total_limit - 3] + "..."
        return text

    # ------------------------------------------------------------------
    # Session cleanup
    # ------------------------------------------------------------------

    async def on_session_removed(self, session_id: str) -> None:
        """Clean up thread name tracking and base state."""
        self._release_thread_name(session_id)
        await super().on_session_removed(session_id)
