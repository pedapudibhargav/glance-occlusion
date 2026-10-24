"""Patches applied to ComponentBench's agent, at the first episode.

Kept out of sitecustomize deliberately. At interpreter start-up the benchmark
package is not importable — for `python scripts/run_benchmark.py`, sys.path[0]
is the scripts directory — so patches attempted there failed silently, and a
run labelled structure-only was still giving the agent a screenshot. These are
applied from the harness hook on the first env.reset(), which is after every
import and before the first get_action.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _current_page():
    """The live page, found through the patched BrowserEnv."""
    try:
        from browsergym.core.env import BrowserEnv
    except Exception:
        return None
    return getattr(BrowserEnv, "_glance_current_page", None)

def _record_stats(path: str, before: str, built: dict) -> None:
    import json

    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "chars_before": len(before),
                        "chars_after": len(built.get("text", "")),
                        "lines_before": before.count("\n") + 1,
                        "lines_after": built.get("kept", 0),
                        "considered": built.get("considered", 0),
                        "dropped": built.get("dropped", {}),
                    }
                )
                + "\n"
            )
    except Exception:
        pass


def _force_structure_only(openai_agent) -> None:
    """Strip the screenshot from the structured observation.

    ComponentBench's ``ax_tree`` mode sends a screenshot *and* the
    accessibility tree — ``runner.py`` passes ``use_screenshot=True``
    literally, ignoring the ``use_screenshot`` key in its own ``OBS_MODES``
    table. That is correct for the benchmark's published numbers and wrong
    for GLANCE, whose label is defined on an action dispatched "using the
    structured layer only". With a screenshot in the prompt the agent is
    not acting on the structured layer alone and the label means something
    else.

    The agent constructor is wrapped rather than the runner edited, so the
    vendored checkout stays pristine.
    """
    agent_cls = getattr(openai_agent, "OpenAIAgent", None)
    if agent_cls is None or getattr(agent_cls, "_glance_structure_only", False):
        return

    original_init = agent_cls.__init__

    def patched_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["use_screenshot"] = False
        original_init(self, *args, **kwargs)
        self.use_screenshot = False

    agent_cls.__init__ = patched_init
    agent_cls._glance_structure_only = True
    logger.info("structure-only observation forced: screenshot removed from the prompt")

def _install_verified_observation(openai_agent) -> None:
    """Replace the agent's accessibility text with a verified element list.

    The A/B this exists for: same tasks, same model, same actions, and the
    only difference is what the model is shown. Arm A is the harness's
    standard accessibility tree. Arm B is the list in
    probe/verified_observation.js — elements that exist right now, are
    reachable by a click, and carry a name worth choosing by.

    The list reuses BrowserGym's own bid attribute, so actions work
    unchanged and nothing but the observation varies.
    """
    if os.environ.get("GLANCE_VERIFIED_OBS") != "1":
        return
    from probe.probe import INJECT_JS_PATH

    agent_cls = getattr(openai_agent, "OpenAIAgent", None)
    if agent_cls is None or getattr(agent_cls, "_glance_verified_obs", False):
        return

    source = (INJECT_JS_PATH.parent / "verified_observation.js").read_text(encoding="utf-8")
    original_get_action = agent_cls.get_action
    stats_path = os.environ.get("GLANCE_OBS_STATS", "data/observation_stats.jsonl")

    def patched_get_action(self, obs):  # type: ignore[no-untyped-def]
        page = getattr(self, "_glance_page", None) or _current_page()
        if page is not None and obs.get("axtree_txt"):
            try:
                page.evaluate(source)
                # GLANCE_KEEP_UNREACHABLE isolates the occlusion filter.
                #
                # The filtered arm bundles four filters — covered, unnamed,
                # zero-area and non-actionable — and at n = 40 it cost 6 tasks
                # (p = 0.03125) without saying which filter did it. Keeping
                # unreachable elements while leaving the other three filters on
                # is the one-at-a-time ablation: if the loss disappears, the
                # occlusion filter caused it.
                keep_unreachable = os.environ.get("GLANCE_KEEP_UNREACHABLE") == "1"
                built = page.evaluate(
                    "(o) => window.__glanceObs.build(o)",
                    {"includeUnreachable": keep_unreachable},
                )
                if built and built.get("text"):
                    before = obs["axtree_txt"]
                    obs = dict(obs)
                    obs["axtree_txt"] = built["text"]
                    _record_stats(stats_path, before, built)
            except Exception:
                logger.debug("verified observation failed; using the original", exc_info=True)
        return original_get_action(self, obs)

    agent_cls.get_action = patched_get_action
    agent_cls._glance_verified_obs = True
    logger.info("verified observation installed (arm B)")

# The benchmark exposes its agent under two different module paths.
#
# ComponentBench puts `benchmark/` on sys.path, so `agents.openai_agent` and
# `benchmark.agents.openai_agent` are two separate module objects holding two
# separate class objects. The runner imports the first
# (`from agents.openai_agent import OpenAIAgentArgs`); the obvious name to
# patch is the second.
#
# Patching only `benchmark.agents...` therefore did nothing at all, while
# still logging that it had succeeded — which is how runs labelled
# structure-only kept handing the agent a screenshot. Both are patched, and
# the applied count is returned so a caller can tell the difference between
# "patched" and "found nothing to patch".
AGENT_MODULES = ("agents.openai_agent", "benchmark.agents.openai_agent")


def apply_agent_patches(_unused=None) -> int:
    """Apply whichever agent patches this run asked for. Returns how many."""
    import importlib

    applied = 0
    structure_only = os.environ.get("GLANCE_STRUCTURE_ONLY") == "1"

    for name in AGENT_MODULES:
        try:
            module = importlib.import_module(name)
        except Exception:
            continue
        if getattr(module, "OpenAIAgent", None) is None:
            continue
        if structure_only:
            _force_structure_only(module)
        _install_verified_observation(module)
        applied += 1
        logger.info("agent patches applied to %s", name)

    if applied == 0:
        logger.warning(
            "no agent module could be patched; tried %s", ", ".join(AGENT_MODULES)
        )
    return applied
