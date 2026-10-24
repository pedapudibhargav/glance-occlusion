"""The GLANCE probe: one feature vector per attempted action.

Two halves are merged here.

The in-page half (``inject.js``) owns hit testing, live geometry, the mutation
log and the cheap semantic reads. The CDP half owns the accessibility node,
paint order from ``DOMSnapshot.captureSnapshot``, the in-flight request count
and the navigation clock. ``StepFeatures.from_probe`` joins them.

Everything is read from the page as it already is. No screenshot is taken and
no model is called, because the router's entire job is to decide whether to pay
for those.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .cdp_client import CDPClient, CDPError, CDPTimeout, find_target
from .schema import StepFeatures, bucket_ax_role, is_generic_name, schema_hash

logger = logging.getLogger(__name__)

INJECT_JS_PATH = Path(__file__).with_name("inject.js")

# The hard timeout on the accessibility read. 2,000 ms is the spec's
# suggestion: long enough that a settled page always answers, short enough that
# a hung page costs one step rather than three minutes.
AX_TIMEOUT_S = 2.0
SNAPSHOT_TIMEOUT_S = 2.0
EVAL_TIMEOUT_S = 2.0

SETTLE_MS = 250.0


def inject_source() -> str:
    return INJECT_JS_PATH.read_text(encoding="utf-8")


@dataclass
class ProbeConfig:
    ax_timeout_s: float = AX_TIMEOUT_S
    snapshot_timeout_s: float = SNAPSHOT_TIMEOUT_S
    # "partial" reads only the target's accessibility node and is what
    # per-step collection should use. "full" calls getFullAXTree, which is the
    # call with the pathological tail; keep it for the latency experiment and
    # for reproducing the hang, not for collecting 30,000 rows.
    ax_mode: str = "partial"
    include_paint_order: bool = True
    # Enabling the Accessibility domain turns accessibility on for the page and
    # can affect its performance until it is disabled, per Chrome's own
    # protocol docs. The probe therefore never enables it persistently.
    url_hint: str = ""


class GlanceProbe:
    """Probe bound to one page, over an independent CDP connection.

    ``debug_url`` is the browser's debugging endpoint (``--remote-debugging-port``).
    A separate connection is used rather than Playwright's CDP session so that
    a command can be abandoned on a deadline — see ``cdp_client``.
    """

    def __init__(
        self,
        page: Any,
        debug_url: str = "",
        config: Optional[ProbeConfig] = None,
    ) -> None:
        self.page = page
        self.config = config or ProbeConfig()
        self.cdp: Optional[CDPClient] = None
        self._schema_hash = schema_hash()
        self._last_observation_wall: Optional[float] = None

        self.install_inject_script()

        if debug_url:
            try:
                target = find_target(debug_url, url_contains=self.config.url_hint)
                if target is None:
                    logger.warning("no CDP page target found at %s", debug_url)
                else:
                    self.cdp = CDPClient(target.ws_url)
                    self.cdp.enable_domains(network=True, page=True)
                    self.cdp.send("DOM.enable", timeout=EVAL_TIMEOUT_S)
                    self.cdp.reset_navigation_clock()
            except Exception:
                logger.warning(
                    "independent CDP connection unavailable; falling back to "
                    "Playwright's CDP session",
                    exc_info=True,
                )
                self.cdp = None

        if self.cdp is None:
            self._attach_playwright_cdp()

    def _attach_playwright_cdp(self) -> None:
        """Use Playwright's own CDP session when no debugging port is available.

        The independent socket exists so a hung ``getFullAXTree`` can be
        abandoned on a deadline, and it needs ``--remote-debugging-port``. That
        flag cannot simply be added to a Playwright-launched browser: Playwright
        drives Chromium over a pipe, and forcing a port changes the debugging
        transport so its handshake never completes — the launch hangs until it
        times out.

        Rather than lose the CDP half of the feature vector, the probe falls
        back to ``new_cdp_session``. What is lost is only the hard timeout: a
        Playwright CDP command cannot be bounded, so ``ax_timed_out`` can no
        longer fire and ``ax_mode="full"`` is unsafe on this path. Targeted
        ``getPartialAXTree`` calls, which collection uses, are cheap and do not
        exhibit the pathological tail.
        """
        page = self.page
        try:
            context = page.context
            session = context.new_cdp_session(page)
        except Exception:
            logger.warning("no CDP available at all; CDP-sourced features will be null")
            self._pw_cdp = None
            return

        self._pw_cdp = session
        for domain in ("DOM", "Network", "Page"):
            try:
                session.send(f"{domain}.enable")
            except Exception:
                logger.debug("could not enable %s over the Playwright session", domain)

        # Network and navigation counters come from events, which the raw
        # client tracks itself; here they are counted off the session.
        self._inflight: set[str] = set()
        self._last_navigation: Optional[float] = time.monotonic()

        def on_request(params: dict) -> None:
            self._inflight.add(params.get("requestId", ""))

        def on_done(params: dict) -> None:
            self._inflight.discard(params.get("requestId", ""))

        def on_nav(params: dict) -> None:
            if not (params.get("frame") or {}).get("parentId"):
                self._last_navigation = time.monotonic()

        try:
            session.on("Network.requestWillBeSent", on_request)
            session.on("Network.loadingFinished", on_done)
            session.on("Network.loadingFailed", on_done)
            session.on("Page.frameNavigated", on_nav)
        except Exception:
            logger.debug("could not subscribe to CDP events", exc_info=True)
        logger.info("probe attached via Playwright CDP session (no timeout guard)")

    def _cdp_send(self, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
        """Issue a CDP command over whichever transport is available."""
        if self.cdp is not None:
            return self.cdp.send(method, params, timeout=timeout)
        session = getattr(self, "_pw_cdp", None)
        if session is None:
            raise CDPError("no CDP transport")
        try:
            return session.send(method, params or {})
        except Exception as exc:
            raise CDPError(f"{method}: {exc}") from exc

    def _has_cdp(self) -> bool:
        return self.cdp is not None or getattr(self, "_pw_cdp", None) is not None

    def _pending_requests(self) -> Optional[int]:
        if self.cdp is not None:
            return self.cdp.pending_network_requests()
        if getattr(self, "_pw_cdp", None) is not None:
            return len(getattr(self, "_inflight", ()))
        return None

    def _ms_since_navigation(self) -> Optional[float]:
        if self.cdp is not None:
            return self.cdp.ms_since_navigation()
        last = getattr(self, "_last_navigation", None)
        return (time.monotonic() - last) * 1000.0 if last is not None else None

    # -- setup -------------------------------------------------------------

    def install_inject_script(self) -> None:
        """Install the content script at ``document_start`` and in this document.

        ``add_init_script`` covers every future navigation and every frame;
        the immediate ``evaluate`` covers the document already loaded. The
        script is idempotent, so doing both is safe.
        """
        source = inject_source()
        try:
            self.page.add_init_script(source)
        except Exception:
            logger.warning("add_init_script failed", exc_info=True)
        try:
            self.page.evaluate(source)
        except Exception:
            # A page mid-navigation will pick the script up from the init hook.
            logger.debug("immediate inject failed; relying on init script", exc_info=True)

    def mark_observation(self) -> None:
        """Call this when the agent's observation is taken.

        ``snapshot_age_ms`` and ``scroll_offset_delta`` are both measured
        against this mark, so the harness must call it at snapshot time rather
        than at dispatch time.
        """
        self._last_observation_wall = time.monotonic()
        try:
            self.page.evaluate("() => window.__glance && window.__glance.markObservation()")
        except Exception:
            logger.debug("markObservation failed", exc_info=True)

    def close(self) -> None:
        if self.cdp is not None:
            self.cdp.close()
            self.cdp = None
        session = getattr(self, "_pw_cdp", None)
        if session is not None:
            try:
                session.detach()
            except Exception:
                pass
            self._pw_cdp = None

    # -- the probe ---------------------------------------------------------

    def probe(
        self,
        selector: str,
        task_id: str,
        step_index: int,
        action_type: str = "other",
        **meta: Any,
    ) -> StepFeatures:
        started = time.monotonic()
        errors: list[str] = []

        in_page = self._run_in_page(selector, errors)
        cdp_part = self._run_cdp(selector, in_page, errors)

        merged_in_page = self._derive_in_page(in_page)
        merged_cdp = self._derive_cdp(cdp_part, in_page)

        features = StepFeatures.from_probe(
            task_id=task_id,
            step_index=step_index,
            in_page=merged_in_page,
            cdp=merged_cdp,
            action_type=action_type,
            selector=selector,
            schema_hash=self._schema_hash,
            ts_dispatch=time.time(),
            **meta,
        )
        features.probe_errors = errors + list(in_page.get("probe_errors") or [])
        features.probe_wall_ms = (time.monotonic() - started) * 1000.0

        # page_settled is a derived conjunction and is computed here, where both
        # halves are known: no mutations for 250 ms AND no pending requests.
        mutations_quiet = in_page.get("mutations_quiet")
        pending = features.pending_network_requests
        if mutations_quiet is None or pending is None:
            features.page_settled = None
        else:
            features.page_settled = bool(mutations_quiet) and pending == 0

        if self._last_observation_wall is not None:
            features.snapshot_age_ms = (time.monotonic() - self._last_observation_wall) * 1000.0
        elif in_page.get("observation_age_ms") is not None:
            features.snapshot_age_ms = in_page["observation_age_ms"]

        return features

    # -- halves ------------------------------------------------------------

    def _run_in_page(self, selector: str, errors: list[str]) -> dict[str, Any]:
        expression = """
        (sel) => {
          if (!window.__glance) return { probe_ok: false, probe_errors: ['probe_not_installed'] };
          return window.__glance.probe(sel);
        }
        """
        try:
            result = self.page.evaluate(expression, selector)
        except Exception as exc:
            errors.append(f"in_page_probe_failed:{type(exc).__name__}")
            return {}
        if not isinstance(result, dict):
            errors.append("in_page_probe_bad_shape")
            return {}
        if not result.get("probe_ok", False):
            errors.extend(result.get("probe_errors") or ["in_page_probe_not_ok"])
        return result

    def _run_cdp(self, selector: str, in_page: dict[str, Any], errors: list[str]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "pending_network_requests": None,
            "ms_since_navigation": None,
            "ax_timed_out": False,
        }
        if not self._has_cdp():
            errors.append("cdp_unavailable")
            return out

        out["pending_network_requests"] = self._pending_requests()
        out["ms_since_navigation"] = self._ms_since_navigation()

        backend_node_id = self._backend_node_id(selector, errors)
        if backend_node_id is None:
            return out

        out.update(self._read_ax(backend_node_id, errors))
        if self.config.include_paint_order:
            out.update(self._read_paint_order(backend_node_id, in_page, errors))
        return out

    def _backend_node_id(self, selector: str, errors: list[str]) -> Optional[int]:
        """Resolve the selector to a backendNodeId via the page's own resolver.

        ``window.__glance.resolve`` already handles selectors, BrowserGym bids
        and bare ids, so the CDP side does not need its own matching rules.
        """
        try:
            evaluated = self._cdp_send(
                "Runtime.evaluate",
                {
                    "expression": f"window.__glance.resolve({json.dumps(selector)})",
                    "returnByValue": False,
                },
                timeout=EVAL_TIMEOUT_S,
            )
        except (CDPTimeout, CDPError) as exc:
            errors.append(f"resolve_failed:{type(exc).__name__}")
            return None

        object_id = (evaluated.get("result") or {}).get("objectId")
        if not object_id:
            errors.append("resolve_returned_null")
            return None

        try:
            described = self._cdp_send(
                "DOM.describeNode", {"objectId": object_id}, timeout=EVAL_TIMEOUT_S
            )
        except (CDPTimeout, CDPError) as exc:
            errors.append(f"describe_node_failed:{type(exc).__name__}")
            return None
        finally:
            try:
                self._cdp_send("Runtime.releaseObject", {"objectId": object_id}, timeout=1.0)
            except Exception:
                pass

        node = described.get("node") or {}
        return node.get("backendNodeId")

    def _read_ax(self, backend_node_id: int, errors: list[str]) -> dict[str, Any]:
        """Read the accessibility node, bounded by a hard timeout.

        A timeout is recorded as ``ax_timed_out`` and the step still produces a
        row. That is both the robustness fix and a finding: the tail is part of
        the result.
        """
        if self.config.ax_mode == "full":
            method, params = "Accessibility.getFullAXTree", {}
        else:
            method, params = (
                "Accessibility.getPartialAXTree",
                {"backendNodeId": backend_node_id, "fetchRelatives": False},
            )

        started = time.monotonic()
        try:
            result = self._cdp_send(method, params, timeout=self.config.ax_timeout_s)
        except CDPTimeout:
            elapsed = (time.monotonic() - started) * 1000.0
            logger.warning("%s timed out after %.0f ms", method, elapsed)
            return {"ax_timed_out": True, "ax_wall_ms": elapsed}
        except CDPError as exc:
            errors.append(f"ax_error:{exc}"[:120])
            return {"ax_timed_out": False}

        nodes = result.get("nodes") or []
        node = self._pick_ax_node(nodes, backend_node_id)
        if node is None:
            errors.append("ax_node_not_found")
            return {"ax_timed_out": False, "ax_wall_ms": (time.monotonic() - started) * 1000.0}

        return self._ax_node_features(node) | {
            "ax_timed_out": False,
            "ax_wall_ms": (time.monotonic() - started) * 1000.0,
        }

    @staticmethod
    def _pick_ax_node(nodes: list[dict[str, Any]], backend_node_id: int) -> Optional[dict[str, Any]]:
        for node in nodes:
            if node.get("backendDOMNodeId") == backend_node_id:
                return node
        # getPartialAXTree with fetchRelatives=False still returns ancestors in
        # some Chrome builds; the target is the last entry when the id is absent.
        return nodes[-1] if nodes else None

    @staticmethod
    def _ax_node_features(node: dict[str, Any]) -> dict[str, Any]:
        def value_of(container: Any) -> Any:
            if isinstance(container, dict):
                return container.get("value")
            return container

        role = value_of(node.get("role")) or None
        name = value_of(node.get("name"))
        name = name.strip() if isinstance(name, str) else None

        properties: dict[str, Any] = {}
        for prop in node.get("properties") or []:
            properties[prop.get("name", "")] = value_of(prop.get("value"))

        def flag(key: str) -> Optional[bool]:
            if key in properties:
                raw = properties[key]
                if isinstance(raw, bool):
                    return raw
                if isinstance(raw, str):
                    return raw.lower() == "true"
                return bool(raw)
            return None

        return {
            "ax_role": bucket_ax_role(role),
            "ax_name": name,
            "ax_name_present": bool(name),
            "ax_name_len": len(name) if name else 0,
            "ax_name_is_generic": is_generic_name(name),
            "ax_state_disabled": flag("disabled"),
            "ax_state_focusable": flag("focusable"),
            "ax_state_focused": flag("focused"),
            "ax_state_expanded": flag("expanded"),
            "ax_state_selected": flag("selected"),
            "ax_state_checked": flag("checked"),
            "ax_state_hidden": (
                flag("hidden") if flag("hidden") is not None else bool(node.get("ignored"))
            ),
            "ax_state_inert": flag("inert"),
        }

    def _read_paint_order(
        self, backend_node_id: int, in_page: dict[str, Any], errors: list[str]
    ) -> dict[str, Any]:
        """Paint order and snapshot-time geometry from ``DOMSnapshot``.

        ``rect_iou`` compares the rect the *snapshot* reports with the rect the
        element has live at dispatch time. That difference is exactly the
        staleness the router needs to see: a structured observation whose
        geometry no longer matches the page is one the agent should not act on.
        """
        try:
            snapshot = self._cdp_send(
                "DOMSnapshot.captureSnapshot",
                {
                    "computedStyles": [],
                    "includePaintOrder": True,
                    "includeDOMRects": True,
                },
                timeout=self.config.snapshot_timeout_s,
            )
        except CDPTimeout:
            errors.append("dom_snapshot_timed_out")
            return {}
        except CDPError as exc:
            errors.append(f"dom_snapshot_error:{exc}"[:120])
            return {}

        documents = snapshot.get("documents") or []
        if not documents:
            return {}

        for doc in documents:
            nodes = doc.get("nodes") or {}
            layout = doc.get("layout") or {}
            backend_ids = nodes.get("backendNodeId") or []
            node_indices = layout.get("nodeIndex") or []
            bounds = layout.get("bounds") or []
            paint_orders = layout.get("paintOrders") or []

            for position, node_index in enumerate(node_indices):
                if node_index >= len(backend_ids):
                    continue
                if backend_ids[node_index] != backend_node_id:
                    continue

                out: dict[str, Any] = {}
                if position < len(paint_orders):
                    out["paint_order_rank"] = int(paint_orders[position])
                if position < len(bounds):
                    out["rect_iou"] = self._rect_iou(
                        snapshot_rect=bounds[position],
                        client_rect=in_page.get("client_rect"),
                        scroll_x=doc.get("scrollOffsetX", 0.0),
                        scroll_y=doc.get("scrollOffsetY", 0.0),
                    )
                return out

        errors.append("node_not_in_dom_snapshot")
        return {}

    @staticmethod
    def _rect_iou(
        snapshot_rect: Any,
        client_rect: Any,
        scroll_x: float,
        scroll_y: float,
    ) -> Optional[float]:
        """IoU between the snapshot's rect and the live client rect.

        ``DOMSnapshot`` bounds are document-relative; ``getBoundingClientRect``
        is viewport-relative. The client rect is shifted into document space by
        the snapshot's own scroll offsets before comparison.
        """
        if not snapshot_rect or not client_rect or len(snapshot_rect) < 4:
            return None

        sx, sy, sw, sh = (float(v) for v in snapshot_rect[:4])
        cx = float(client_rect.get("x", 0.0)) + float(scroll_x or 0.0)
        cy = float(client_rect.get("y", 0.0)) + float(scroll_y or 0.0)
        cw = float(client_rect.get("width", 0.0))
        ch = float(client_rect.get("height", 0.0))

        if sw <= 0 or sh <= 0 or cw <= 0 or ch <= 0:
            # A zero-area rect on either side means disagreement, not an
            # undefined comparison: one source sees a box, the other does not.
            return 0.0 if (sw * sh) != (cw * ch) else 1.0

        ix = max(0.0, min(sx + sw, cx + cw) - max(sx, cx))
        iy = max(0.0, min(sy + sh, cy + ch) - max(sy, cy))
        intersection = ix * iy
        union = sw * sh + cw * ch - intersection
        return intersection / union if union > 0 else None

    # -- derivations -------------------------------------------------------

    @staticmethod
    def _derive_in_page(in_page: dict[str, Any]) -> dict[str, Any]:
        if not in_page:
            return {}
        out = dict(in_page)
        # ms_since_navigation from CDP wins when present; the in-page epoch is
        # the fallback for real-world pages probed without a CDP connection.
        out.pop("ms_since_navigation_in_page", None)
        out.pop("client_rect", None)
        out.pop("probe_ok", None)
        out.pop("probe_errors", None)
        return out

    @staticmethod
    def _derive_cdp(cdp_part: dict[str, Any], in_page: dict[str, Any]) -> dict[str, Any]:
        out = dict(cdp_part)
        # ax_name_matches_text needs both halves: the authoritative AX name and
        # the element's rendered text.
        ax_name = out.get("ax_name")
        element_text = in_page.get("element_text")
        out["ax_name_matches_text"] = _edit_similarity(ax_name, element_text)
        if out.get("ms_since_navigation") is None:
            out["ms_since_navigation"] = in_page.get("ms_since_navigation_in_page")
        if out.get("ax_role") is None and in_page.get("dom_role"):
            # No AX node (timed out, or an ignored node): fall back to the DOM
            # role so the row is still usable, and let ax_timed_out mark why.
            out["ax_role"] = bucket_ax_role(in_page.get("dom_role"))
        return out


def _edit_similarity(a: Optional[str], b: Optional[str]) -> Optional[float]:
    """Normalised edit similarity, matching ``inject.js``'s definition."""
    if a is None or b is None:
        return None
    a_norm = " ".join(a.lower().split())[:200]
    b_norm = " ".join(b.lower().split())[:200]
    if not a_norm and not b_norm:
        return 1.0
    if not a_norm or not b_norm:
        return 0.0
    if a_norm == b_norm:
        return 1.0

    previous = list(range(len(b_norm) + 1))
    for i, ch_a in enumerate(a_norm, start=1):
        current = [i]
        for j, ch_b in enumerate(b_norm, start=1):
            cost = 0 if ch_a == ch_b else 1
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost))
        previous = current
    return 1.0 - previous[-1] / max(len(a_norm), len(b_norm))
