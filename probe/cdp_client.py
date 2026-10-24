"""Raw Chrome DevTools Protocol client with per-command timeouts.

Why this exists rather than using Playwright's ``CDPSession``:

Playwright's sync ``CDPSession.send()`` has no per-command timeout and its
objects may not be touched from another thread, so there is no way to bound a
call that hangs. The hang is not hypothetical and it is part of the result the
project is measuring — ``Accessibility.getFullAXTree`` has been observed
failing at 180004 ms on an unsettled page while ``take_screenshot`` returned in
54 ms on the same page. A collector that blocks for three minutes per step
cannot gather 30,000 rows, and a probe that silently waits would also hide the
latency tail that the paper's p95 argument rests on.

This client talks to the browser's debugging port directly over a websocket, so
a command can be abandoned on a deadline and the step recorded with
``ax_timed_out: true``. It carries only what the probe needs; it is not a
general CDP library.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import Any, Optional
from urllib.request import urlopen

logger = logging.getLogger(__name__)


class CDPTimeout(TimeoutError):
    """A CDP command did not answer within its deadline."""


class CDPError(RuntimeError):
    """The browser answered with an error payload."""


@dataclass
class TargetInfo:
    target_id: str
    title: str
    url: str
    ws_url: str


def normalise_debug_url(debug_url: str) -> str:
    """Chrome's DevTools HTTP endpoint insists on a hostname, not an IP.

    ``http://127.0.0.1:9222/json/list`` returns 404 while
    ``http://localhost:9222/json/list`` returns 200 on the same browser —
    Chrome validates the Host header and rejects the literal address. Passing
    the IP is the natural thing to do, so the substitution happens here rather
    than in every caller.
    """
    return debug_url.rstrip("/").replace("//127.0.0.1:", "//localhost:").replace("//[::1]:", "//localhost:")


def list_targets(debug_url: str, timeout: float = 5.0) -> list[TargetInfo]:
    """Page targets from ``/json/list`` on the browser's debugging port."""
    base = normalise_debug_url(debug_url)
    with urlopen(f"{base}/json/list", timeout=timeout) as response:  # noqa: S310
        payload = json.loads(response.read().decode())
    targets = []
    for entry in payload:
        if entry.get("type") != "page":
            continue
        ws_url = entry.get("webSocketDebuggerUrl")
        if not ws_url:
            continue
        targets.append(
            TargetInfo(
                target_id=entry.get("id", ""),
                title=entry.get("title", ""),
                url=entry.get("url", ""),
                ws_url=ws_url,
            )
        )
    return targets


def find_target(debug_url: str, url_contains: str = "", timeout: float = 5.0) -> Optional[TargetInfo]:
    targets = list_targets(debug_url, timeout=timeout)
    if url_contains:
        for target in targets:
            if url_contains in target.url:
                return target
    return targets[0] if targets else None


class CDPClient:
    """One websocket to one page target.

    Commands are synchronous from the caller's point of view but each carries
    its own deadline. Events are counted in a background reader thread, which
    is what ``pending_network_requests`` and ``ms_since_navigation`` are built
    from.
    """

    def __init__(self, ws_url: str, open_timeout: float = 10.0) -> None:
        try:
            import websocket  # type: ignore
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "websocket-client is required for the raw CDP probe path: "
                "pip install websocket-client"
            ) from exc

        self._ws = websocket.create_connection(
            ws_url,
            timeout=open_timeout,
            max_size=64 * 1024 * 1024,  # full AX trees and DOM snapshots are large
            enable_multithread=True,
            # Chrome rejects a DevTools WebSocket that carries an Origin it was
            # not told to allow:
            #
            #   Rejected an incoming WebSocket connection from the
            #   http://localhost:9222 origin. Use the command line flag
            #   --remote-allow-origins=... to allow connections from this origin
            #
            # A connection with no Origin header at all is accepted, so
            # suppressing it keeps the probe working against a browser started
            # by someone else — Playwright, a container, or the user — without
            # requiring an extra launch flag.
            suppress_origin=True,
        )
        self._next_id = 0
        self._lock = threading.Lock()
        self._pending: dict[int, threading.Event] = {}
        self._results: dict[int, dict[str, Any]] = {}
        self._closed = False

        # Event-derived counters. Guarded by _counter_lock.
        self._counter_lock = threading.Lock()
        self._inflight_requests: set[str] = set()
        self._last_navigation_monotonic: Optional[float] = None
        self._navigation_count = 0

        self._reader = threading.Thread(target=self._read_loop, name="cdp-reader", daemon=True)
        self._reader.start()

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._closed = True
        try:
            self._ws.close()
        except Exception:  # pragma: no cover - best effort
            pass

    def __enter__(self) -> "CDPClient":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    # -- plumbing ----------------------------------------------------------

    def _read_loop(self) -> None:
        import time

        while not self._closed:
            try:
                raw = self._ws.recv()
            except Exception:
                if not self._closed:
                    logger.debug("CDP reader stopped", exc_info=True)
                return
            if not raw:
                continue
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_id = message.get("id")
            if msg_id is not None:
                with self._lock:
                    self._results[msg_id] = message
                    event = self._pending.pop(msg_id, None)
                if event is not None:
                    event.set()
                continue

            method = message.get("method", "")
            params = message.get("params", {})
            if method == "Network.requestWillBeSent":
                with self._counter_lock:
                    self._inflight_requests.add(params.get("requestId", ""))
            elif method in ("Network.loadingFinished", "Network.loadingFailed"):
                with self._counter_lock:
                    self._inflight_requests.discard(params.get("requestId", ""))
            elif method == "Page.frameNavigated":
                # Only the main frame resets the navigation clock; subframe
                # navigations are routine on component pages.
                frame = params.get("frame", {})
                if not frame.get("parentId"):
                    with self._counter_lock:
                        self._last_navigation_monotonic = time.monotonic()
                        self._navigation_count += 1
            elif method == "Page.frameStartedLoading":
                pass

    def send(self, method: str, params: Optional[dict[str, Any]] = None, timeout: float = 5.0) -> dict[str, Any]:
        """Issue a command and wait at most ``timeout`` seconds for its reply.

        On timeout the command is abandoned — the browser may still answer
        later, and that reply is discarded by the reader. The caller gets
        ``CDPTimeout`` and can record the step and move on.
        """
        if self._closed:
            raise CDPError("CDP client is closed")

        with self._lock:
            self._next_id += 1
            msg_id = self._next_id
            event = threading.Event()
            self._pending[msg_id] = event

        payload = json.dumps({"id": msg_id, "method": method, "params": params or {}})
        try:
            self._ws.send(payload)
        except Exception as exc:
            with self._lock:
                self._pending.pop(msg_id, None)
            raise CDPError(f"failed to send {method}: {exc}") from exc

        if not event.wait(timeout):
            with self._lock:
                self._pending.pop(msg_id, None)
                self._results.pop(msg_id, None)
            raise CDPTimeout(f"{method} exceeded {timeout * 1000:.0f} ms")

        with self._lock:
            message = self._results.pop(msg_id, {})

        if "error" in message:
            raise CDPError(f"{method}: {message['error']}")
        return message.get("result", {})

    # -- counters ----------------------------------------------------------

    def enable_domains(self, network: bool = True, page: bool = True, timeout: float = 5.0) -> None:
        """Enable only the domains whose events the probe counts.

        The Accessibility domain is deliberately not enabled here. Chrome's own
        documentation warns that enabling it turns on accessibility for the
        page and can affect performance until it is disabled, which would
        contaminate the latency measurements this project reports.
        """
        if network:
            self.send("Network.enable", timeout=timeout)
        if page:
            self.send("Page.enable", timeout=timeout)

    def pending_network_requests(self) -> int:
        with self._counter_lock:
            return len(self._inflight_requests)

    def ms_since_navigation(self) -> Optional[float]:
        import time

        with self._counter_lock:
            last = self._last_navigation_monotonic
        if last is None:
            return None
        return (time.monotonic() - last) * 1000.0

    def navigation_count(self) -> int:
        with self._counter_lock:
            return self._navigation_count

    def reset_navigation_clock(self) -> None:
        import time

        with self._counter_lock:
            self._last_navigation_monotonic = time.monotonic()
