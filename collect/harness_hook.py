"""Wrap ComponentBench's dispatch path so every action emits a probe row.

The benchmark's logic is not forked. ``BrowserEnv.step`` is wrapped in place,
so ComponentBench and BrowserGym upgrades stay clean and the only thing GLANCE
owns is the before/after bracket around the dispatch:

    mark observation  ──►  agent chooses action
                             │
             probe + preflight + state snapshot (before)
                             │
                      original env.step(action)
                             │
                   state snapshot (after) ──► label
                             │
                     append one JSONL row

The probe runs twice per step in collection mode — once to capture features,
once after dispatch to determine the outcome. In deployment it runs once, and
the router consumes the features without the second half.

Install with ``install_hook()`` before the benchmark runner builds its
environment, or set ``GLANCE_COLLECT=1`` and import this module.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

from probe.probe import GlanceProbe, ProbeConfig
from probe.schema import schema_hash

from .outcome import evaluate_outcome, is_element_action, parse_action

logger = logging.getLogger(__name__)

_installed = False
_lock = threading.Lock()


class RowWriter:
    """Append-only JSONL writer, one file per run.

    Raw traces are committed, not just aggregates: a failure you cannot replay
    is a failure you cannot explain in the paper.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._count = 0

    def write(self, row: dict[str, Any]) -> None:
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, default=str) + "\n")
            self._count += 1

    @property
    def count(self) -> int:
        return self._count


class EpisodeState:
    """Per-episode counters the context features need."""

    def __init__(self) -> None:
        self.step_index = 0
        self.prior_failures = 0

    def advance(self, failed: bool) -> None:
        self.step_index += 1
        if failed:
            self.prior_failures += 1


def install_hook(
    out_path: Optional[Path] = None,
    debug_url: str = "",
    ax_mode: str = "partial",
    run_id: str = "",
    mode: str = "",
) -> bool:
    """Patch ``BrowserEnv.step``. Returns False when BrowserGym is absent.

    Idempotent — a second call is a no-op, so importing this module from
    several entry points is safe.
    """
    global _installed
    with _lock:
        if _installed:
            return True

        try:
            from browsergym.core.env import BrowserEnv  # type: ignore
        except ImportError:
            logger.error(
                "browsergym is not importable; install ComponentBench "
                "(pip install -e vendor/ComponentBench) before collecting"
            )
            return False

        out_path = out_path or Path(
            os.environ.get("GLANCE_OUT", "data/raw/structure/rows.jsonl")
        )
        debug_url = debug_url or os.environ.get("GLANCE_CDP_URL", "")
        run_id = run_id or os.environ.get("GLANCE_RUN_ID", f"run-{int(time.time())}")
        mode = mode or os.environ.get("GLANCE_MODE", "")

        writer = RowWriter(out_path)
        original_step = BrowserEnv.step
        original_reset = BrowserEnv.reset
        original_env_init = BrowserEnv.__init__

        # Give the browser a CDP debugging port, through BrowserGym's own
        # pw_chromium_kwargs rather than by patching Playwright.
        #
        # The probe needs that port: it talks CDP over an independent
        # connection so a hung getFullAXTree can be abandoned on a deadline,
        # and without it ax_role, ax_name, rect_iou, paint_order_rank and
        # pending_network_requests are all null — most of two feature groups.
        #
        # An earlier version patched playwright.sync_api.BrowserType.launch
        # from sitecustomize. That runs at interpreter start-up, before
        # Playwright's driver is ready, and every browser launch then hung
        # until its 180s timeout. Using the documented parameter avoids
        # touching Playwright before it is safe to.
        cdp_port = os.environ.get("GLANCE_CDP_PORT", "9222").strip()

        def _patch_playwright_launch() -> None:
            """Append the debugging port to whatever args BrowserGym passes.

            It cannot go through ``pw_chromium_kwargs``: BrowserGym passes
            ``args`` to ``launch`` itself, so supplying it there raises
            ``got multiple values for keyword argument 'args'``. Merging
            inside a wrapper is the only place both sets are visible.

            Called from ``BrowserEnv.__init__`` rather than at interpreter
            start-up. Importing ``playwright.sync_api`` from sitecustomize is
            too early — it ran before Playwright's driver was ready and every
            launch hung until its 180s timeout.
            """
            try:
                from playwright.sync_api import BrowserType
            except Exception:
                return
            if getattr(BrowserType, "_glance_cdp_patched", False):
                return

            original_launch = BrowserType.launch

            def patched_launch(self, **launch_kwargs):  # type: ignore[no-untyped-def]
                launch_args = list(launch_kwargs.get("args") or [])
                if not any(str(a).startswith("--remote-debugging-port") for a in launch_args):
                    launch_args.append(f"--remote-debugging-port={cdp_port}")
                launch_kwargs["args"] = launch_args
                return original_launch(self, **launch_kwargs)

            BrowserType.launch = patched_launch
            BrowserType._glance_cdp_patched = True
            logger.info("CDP debugging port %s added to the browser launch", cdp_port)

        def patched_env_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            if cdp_port:
                _patch_playwright_launch()
            return original_env_init(self, *args, **kwargs)

        def _publish_page(env: Any) -> None:
            """Expose the live page so the observation arm can reach it.

            The agent is constructed separately from the environment and has no
            handle on the page, so the env publishes one.
            """
            page = getattr(env, "page", None)
            if page is not None:
                BrowserEnv._glance_current_page = page

        def _probe_for(env: Any) -> Optional[GlanceProbe]:
            probe = getattr(env, "_glance_probe", None)
            if probe is not None:
                return probe
            page = getattr(env, "page", None)
            if page is None:
                return None
            try:
                probe = GlanceProbe(
                    page,
                    debug_url=debug_url,
                    config=ProbeConfig(ax_mode=ax_mode),
                )
            except Exception:
                logger.warning("could not attach GLANCE probe", exc_info=True)
                return None
            env._glance_probe = probe
            return probe

        def _task_meta(env: Any) -> dict[str, Any]:
            task = getattr(env, "task", None)
            config = getattr(task, "task_config", {}) or {}
            task_id = getattr(task, "task_id", None) or config.get("task_id") or "unknown"
            return {
                "task_id": task_id,
                "library": getattr(task, "implementation_source", "")
                or config.get("implementation_source", ""),
                "canonical_type": getattr(task, "canonical_type", "")
                or config.get("canonical_type", ""),
                "benchmark_version": getattr(task, "benchmark_version", "v1"),
                "mode": mode or getattr(task, "mode", ""),
                "run_id": run_id,
            }

        def _patch_agent_class_once() -> None:
            """Patch the agent class on the first episode.

            These patches cannot be applied at interpreter start-up: for
            `python scripts/run_benchmark.py`, sys.path[0] is the scripts
            directory, so `import benchmark.agents.openai_agent` fails there
            and the patch silently did nothing — which meant runs labelled
            structure-only were still handing the agent a screenshot.

            reset() happens after every import and before the first
            get_action, and patching the class reaches instances already
            constructed, so this is the first reliable moment.
            """
            if getattr(BrowserEnv, "_glance_agent_patched", False):
                return
            BrowserEnv._glance_agent_patched = True
            try:
                from collect.agent_patches import apply_agent_patches

                if apply_agent_patches() == 0:
                    # Not fatal, but it means this run is not what it claims
                    # to be, so say so rather than carrying on quietly.
                    logger.warning(
                        "agent patches applied to nothing: this run is NOT "
                        "structure-only and any verified-observation arm is "
                        "running the standard observation"
                    )
            except Exception:
                logger.warning("agent patches failed", exc_info=True)

        def wrapped_reset(self: Any, *args: Any, **kwargs: Any) -> Any:
            _patch_agent_class_once()
            result = original_reset(self, *args, **kwargs)
            self._glance_episode = EpisodeState()
            _publish_page(self)
            probe = _probe_for(self)
            if probe is not None:
                # The agent's first action is chosen from the observation this
                # reset produced, so the observation clock starts here.
                probe.mark_observation()
            return result

        def wrapped_step(self: Any, action: Any) -> Any:
            # Also here, not only in reset: whichever of the two the loop
            # reaches first must install the agent patches, and the call is
            # idempotent.
            _patch_agent_class_once()
            episode: EpisodeState = getattr(self, "_glance_episode", None) or EpisodeState()
            self._glance_episode = episode
            _publish_page(self)

            action_str = action if isinstance(action, str) else str(action)
            probe = _probe_for(self)
            page = getattr(self, "page", None)

            features = None
            before = None
            preflight = None
            action_type, target, raw = parse_action(action_str)

            should_probe = (
                probe is not None
                and page is not None
                and is_element_action(action_str)
                and target is not None
            )

            if should_probe:
                meta = _task_meta(self)
                try:
                    preflight = page.evaluate(
                        "(sel) => window.__glance.preflight(sel)", target
                    )
                except Exception:
                    preflight = None
                try:
                    before = page.evaluate(
                        "(sel) => window.__glance.stateSnapshot(sel)", target
                    )
                except Exception:
                    before = None
                try:
                    features = probe.probe(
                        selector=target,
                        task_id=meta["task_id"],
                        step_index=episode.step_index,
                        action_type=action_type,
                        prior_failures_this_episode=episode.prior_failures,
                        action_raw=raw,
                        **{k: v for k, v in meta.items() if k != "task_id"},
                    )
                except Exception:
                    logger.warning("probe failed; step proceeds unprobed", exc_info=True)
                    features = None

            dispatch_error = ""
            dispatch_started = time.monotonic()
            try:
                result = original_step(self, action)
            except Exception as exc:
                dispatch_error = f"{type(exc).__name__}: {exc}"[:200]
                raise
            finally:
                dispatch_ms = (time.monotonic() - dispatch_started) * 1000.0

                if features is not None:
                    after = None
                    if page is not None and target is not None:
                        try:
                            after = page.evaluate(
                                "(sel) => window.__glance.stateSnapshot(sel)", target
                            )
                        except Exception:
                            after = None

                    outcome = evaluate_outcome(
                        before=before or {},
                        after=after or {},
                        preflight=preflight,
                        dispatch_error=dispatch_error,
                    )
                    row = features.to_row()
                    row.update(outcome.to_row())
                    row["dispatch_ms"] = dispatch_ms
                    row["schema_hash"] = schema_hash()
                    try:
                        writer.write(row)
                    except Exception:
                        logger.warning("failed to write probe row", exc_info=True)

                    episode.advance(failed=outcome.label is False)
                else:
                    episode.advance(failed=bool(dispatch_error))

                # The observation the agent will act on next is produced by the
                # step that just ran, so the clock restarts here.
                if probe is not None:
                    probe.mark_observation()

            return result

        BrowserEnv.__init__ = patched_env_init  # type: ignore[method-assign]
        BrowserEnv.step = wrapped_step  # type: ignore[method-assign]
        BrowserEnv.reset = wrapped_reset  # type: ignore[method-assign]
        _installed = True
        logger.info("GLANCE hook installed; rows → %s", out_path)
        return True


if os.environ.get("GLANCE_COLLECT") == "1":  # pragma: no cover - import side effect
    install_hook()
