"""Bridge subscriber that routes store events to platform bridges."""

from __future__ import annotations

import asyncio
import json
import structlog
from typing import Callable

from agent_tether.base import ApprovalRequest
from agent_tether.manager import BridgeManager

logger = structlog.get_logger(__name__)

# Callback types for store integration
NewSubscriberFn = Callable[[str], asyncio.Queue]  # (session_id) -> Queue
RemoveSubscriberFn = Callable[[str, asyncio.Queue], None]  # (session_id, queue) -> None

# Output buffering constants
_OUTPUT_FLUSH_DELAY_S = 2.0  # seconds to wait before flushing buffered output
_OUTPUT_FLUSH_MAX_CHARS = 1800  # flush immediately when buffer exceeds this size


class BridgeSubscriber:
    """Subscribes to store events and routes them to platform bridges.

    For each session with a platform binding, a background task consumes
    events from a store subscriber queue and forwards them to the bridge.

    Output events are buffered and flushed after a short delay (or when
    the buffer is large enough, or when the session state changes) so
    that rapid-fire streaming deltas are collapsed into fewer messages.

    Args:
        bridge_manager: The bridge manager to route events through.
        new_subscriber: Callback to register a subscriber queue for a session.
        remove_subscriber: Callback to unregister a subscriber queue.
    """

    def __init__(
        self,
        bridge_manager: BridgeManager,
        new_subscriber: NewSubscriberFn,
        remove_subscriber: RemoveSubscriberFn,
    ) -> None:
        self._bridge_manager = bridge_manager
        self._new_subscriber = new_subscriber
        self._remove_subscriber = remove_subscriber
        self._tasks: dict[str, asyncio.Task] = {}
        self._queues: dict[str, asyncio.Queue] = {}
        # Output buffering state per session
        self._output_buffers: dict[str, list[str]] = {}
        self._output_flush_tasks: dict[str, asyncio.Task] = {}

    def subscribe(self, session_id: str, platform: str) -> None:
        """Start consuming store events for a session and routing to a bridge.

        The subscriber queue is registered synchronously so that events
        emitted between subscribe() and the first await in _consume()
        are not lost.
        """
        if session_id in self._tasks:
            return

        # Register the queue eagerly so no events are missed.
        queue = self._new_subscriber(session_id)
        self._queues[session_id] = queue

        task = asyncio.create_task(self._consume(session_id, platform, queue))
        self._tasks[session_id] = task
        logger.info(
            "Bridge subscriber started",
            extra={"session_id": session_id, "platform": platform},
        )

    async def unsubscribe(self, session_id: str, *, platform: str | None = None) -> None:
        """Stop consuming events for a session and clean up bridge state."""
        task = self._tasks.pop(session_id, None)
        self._queues.pop(session_id, None)
        if task:
            task.cancel()
            logger.info("Bridge subscriber stopped", extra={"session_id": session_id})

        # Flush any remaining buffered output before cleanup
        if platform:
            bridge = self._bridge_manager.get_bridge(platform)
            if bridge:
                await self._flush_output(session_id, bridge)
                await bridge.on_session_removed(session_id)

    # ------------------------------------------------------------------
    # Output buffering
    # ------------------------------------------------------------------

    def _buffer_output(self, session_id: str, text: str) -> None:
        """Add text to the output buffer for a session."""
        self._output_buffers.setdefault(session_id, []).append(text)

    def _buffer_size(self, session_id: str) -> int:
        """Return the total character count in the output buffer."""
        return sum(len(t) for t in self._output_buffers.get(session_id, []))

    async def _flush_output(self, session_id: str, bridge: object) -> None:
        """Send all buffered output for a session to the bridge."""
        # Cancel any pending flush timer
        task = self._output_flush_tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()

        buf = self._output_buffers.pop(session_id, [])
        if not buf:
            return

        text = "".join(buf)
        if not text.strip():
            return

        try:
            await bridge.on_output(session_id, text)
        except Exception:
            logger.exception(
                "Failed to flush output to bridge",
                extra={"session_id": session_id},
            )

    async def _schedule_flush(self, session_id: str, bridge: object) -> None:
        """Schedule a delayed flush of buffered output.

        Cancels any existing timer and starts a new one.  If the buffer
        is already large enough, flushes immediately instead.
        """
        # Cancel existing timer
        existing = self._output_flush_tasks.pop(session_id, None)
        if existing and not existing.done():
            existing.cancel()

        # Flush immediately if buffer is large
        if self._buffer_size(session_id) >= _OUTPUT_FLUSH_MAX_CHARS:
            await self._flush_output(session_id, bridge)
            return

        # Schedule delayed flush
        async def _delayed_flush() -> None:
            try:
                await asyncio.sleep(_OUTPUT_FLUSH_DELAY_S)
            except asyncio.CancelledError:
                return
            self._output_flush_tasks.pop(session_id, None)
            await self._flush_output(session_id, bridge)

        self._output_flush_tasks[session_id] = asyncio.create_task(_delayed_flush())

    # ------------------------------------------------------------------
    # Event consumer
    # ------------------------------------------------------------------

    async def _consume(self, session_id: str, platform: str, queue: asyncio.Queue) -> None:
        """Background task that reads from a store subscriber and routes events."""
        bridge = self._bridge_manager.get_bridge(platform)
        if not bridge:
            logger.warning(
                "No bridge for platform, subscriber exiting",
                extra={"session_id": session_id, "platform": platform},
            )
            return

        try:
            while True:
                event = await queue.get()
                event_type = event.get("type")
                data = event.get("data", {})

                # Skip history replay events
                if data.get("is_history"):
                    continue

                try:
                    if event_type == "output":
                        text = data.get("text", "")
                        if not text:
                            continue
                        is_final = bool(data.get("final"))

                        if is_final:
                            # Final output: flush any buffered step output first,
                            # then send the final text immediately.
                            await self._flush_output(session_id, bridge)
                            await bridge.on_output(
                                session_id,
                                text,
                                metadata={"final": True, "kind": "final"},
                            )
                        else:
                            # Step output: buffer and flush on a timer
                            self._buffer_output(session_id, text)
                            await self._schedule_flush(session_id, bridge)

                    elif event_type == "output_final":
                        # Accumulated blob -- skip, we forward per-step
                        # events above instead.
                        pass

                    elif event_type == "permission_request":
                        # Flush buffered output before showing approval request
                        await self._flush_output(session_id, bridge)

                        tool_input = data.get("tool_input", {})
                        tool_name = data.get("tool_name", "Permission request")

                        # Special-case multi-choice questions coming through as a "tool".
                        # Codex emits these as AskUserQuestion with a structured schema.
                        if (
                            isinstance(tool_input, dict)
                            and str(tool_name).startswith("AskUserQuestion")
                            and isinstance(tool_input.get("questions"), list)
                            and tool_input["questions"]
                            and isinstance(tool_input["questions"][0], dict)
                        ):
                            q = tool_input["questions"][0]
                            header = str(q.get("header") or "Question")
                            question = str(q.get("question") or "")
                            options = q.get("options") or []
                            labels: list[str] = []
                            lines: list[str] = [question.strip()] if question else []
                            for i, opt in enumerate(options, start=1):
                                if not isinstance(opt, dict):
                                    continue
                                label = str(opt.get("label") or "").strip()
                                desc = str(opt.get("description") or "").strip()
                                if not label:
                                    continue
                                labels.append(label)
                                if desc:
                                    lines.append(f"{i}. {label} - {desc}")
                                else:
                                    lines.append(f"{i}. {label}")

                            request = ApprovalRequest(
                                kind="choice",
                                request_id=data.get("request_id", ""),
                                title=header,
                                description="\n".join([l for l in lines if l]).strip(),
                                options=labels,
                            )
                        else:
                            description = (
                                json.dumps(tool_input)
                                if isinstance(tool_input, dict)
                                else str(tool_input)
                            )
                            request = ApprovalRequest(
                                kind="permission",
                                request_id=data.get("request_id", ""),
                                title=tool_name,
                                description=description,
                                options=["Allow", "Deny"],
                            )
                        await bridge.on_approval_request(session_id, request)

                    elif event_type == "session_state":
                        state = data.get("state", "")
                        if state == "RUNNING":
                            await bridge.on_typing(session_id)
                        elif state == "AWAITING_INPUT":
                            # Flush any remaining output before stopping typing
                            await self._flush_output(session_id, bridge)
                            await bridge.on_typing_stopped(session_id)
                        elif state == "ERROR":
                            await self._flush_output(session_id, bridge)
                            await bridge.on_typing_stopped(session_id)
                            await bridge.on_status_change(session_id, "error")

                    elif event_type == "error":
                        await self._flush_output(session_id, bridge)
                        msg = data.get("message", "Unknown error")
                        await bridge.on_status_change(session_id, "error", {"message": msg})

                except Exception:
                    logger.exception(
                        "Failed to route event to bridge",
                        extra={"session_id": session_id, "event_type": event_type},
                    )
        except asyncio.CancelledError:
            pass
        finally:
            self._remove_subscriber(session_id, queue)
