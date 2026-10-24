"""Installed into every Python process of a collection run.

ComponentBench runs each task in a child process (``multiprocessing.Process``
with ``join(timeout=...)`` and ``kill()``, because Playwright's C-level
blocking calls defeat signal- and thread-based watchdogs). A monkeypatch
applied only in the parent therefore reaches the children under ``fork`` and
misses them under ``spawn``.

Python imports ``sitecustomize`` automatically at interpreter start-up if it is
importable, so putting this directory on ``PYTHONPATH`` installs the GLANCE
hook in the parent and in every child, under either start method. That is the
whole reason this file exists.

Activated by ``GLANCE_COLLECT=1``; otherwise it does nothing at all.
"""

from __future__ import annotations

import logging
import pathlib
import os
import sys

if os.environ.get("GLANCE_COLLECT") == "1":
    logger = logging.getLogger("glance.bootstrap")

    # The GLANCE repo root, so `import probe` / `import collect` resolve in a
    # child process whose working directory is the vendored benchmark.
    repo_root = os.environ.get("GLANCE_REPO_ROOT", "")
    if repo_root and repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    def _stub_tiktoken_counting() -> None:
        """Keep BrowserGym's token telemetry from killing the run.

        browsergym.experiments.utils.count_tokens calls
        tiktoken.encoding_for_model, which downloads a BPE file from
        openaipublic.blob.core.windows.net on first use. Behind TLS
        interception that fails with SSLCertVerificationError, and because it
        is called while recording step stats it takes the whole episode down
        before any action is dispatched.

        The count is telemetry, not behaviour: nothing the agent does depends
        on it. It is replaced with a characters/4 estimate so episodes run.
        The authoritative token numbers come from collect/budget.py, which
        records what the provider actually reports and bills.
        """
        try:
            import tiktoken

            tiktoken.encoding_for_model("gpt-4").encode("probe")
            return  # the real tokenizer works; leave it alone
        except Exception:
            pass

        # Patch tiktoken itself rather than a caller's module attribute:
        # browsergym.experiments.loop does `from .utils import count_tokens`,
        # so rebinding utils.count_tokens leaves loop's reference untouched.
        # Replacing the encoding factory covers every import style.
        class _EstimatingEncoding:
            def encode(self, text, *args, **kwargs):  # type: ignore[no-untyped-def]
                return [0] * max(1, len(str(text)) // 4)

            def decode(self, tokens, *args, **kwargs):  # type: ignore[no-untyped-def]
                return ""

        def _encoding_for_model(model=None, *args, **kwargs):  # type: ignore[no-untyped-def]
            return _EstimatingEncoding()

        try:
            import tiktoken

            tiktoken.encoding_for_model = _encoding_for_model
            tiktoken.get_encoding = _encoding_for_model
        except Exception:
            return

        logger.info("tiktoken unavailable; BrowserGym token stats are chars/4 estimates")

    def _force_reasoning_effort() -> None:
        """Pin the agent's reasoning effort.

        GPT-5 models spend completion tokens on internal reasoning before they
        answer. Measured on one realistic structure-only step, gpt-5-mini used
        576 reasoning tokens against 73 visible ones — 6.4x the cost of the
        same step at reasoning_effort="minimal" — and gpt-5-nano consumed its
        entire completion budget reasoning and returned no action at all,
        which would have recorded a dispatch error on every step and corrupted
        the label.

        ComponentBench's agent does not expose the parameter, so it is
        injected at the SDK call. Which value to use is a research decision,
        not just a cost one, and the calibration phase measures it: too little
        reasoning and the agent fails at planning rather than at grounding.
        """
        effort = os.environ.get("GLANCE_REASONING_EFFORT", "")
        if not effort:
            return
        try:
            from openai.resources.chat import completions as chat_completions
        except ImportError:
            return
        if getattr(chat_completions.Completions, "_glance_effort_patched", False):
            return

        original_create = chat_completions.Completions.create

        def patched_create(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            model = str(kwargs.get("model", ""))
            # Only OpenAI reasoning models take it; Anthropic's compatibility
            # layer ignores the field, and sending it elsewhere risks a 400.
            if model.startswith("gpt-5") or model.startswith("o"):
                kwargs.setdefault("reasoning_effort", effort)
            return original_create(self, *args, **kwargs)

        chat_completions.Completions.create = patched_create
        chat_completions.Completions._glance_effort_patched = True
        logger.info("reasoning_effort=%s pinned on the agent", effort)

    # A failing sitecustomize must stop the run, not be skipped.
    #
    # Python prints a traceback for an exception raised here and then carries
    # on without the module. When install_budget() raised BudgetExceeded, the
    # interpreter continued with no spend cap, no harness hook, no reasoning
    # effort pinned and no GLANCE_VERIFIED_OBS — so both arms of an A/B ran
    # identically and unmetered, and would have reported "no difference"
    # as a result. A guard that fails open is worse than no guard.
    try:
        from collect.budget import install as install_budget
        from collect.harness_hook import install_hook

        # The spend cap goes in before the hook, and before any agent is
        # constructed. ComponentBench runs each task in a child process, so
        # this is also what puts the cap in every child rather than only in
        # the parent.
        install_budget()
        _stub_tiktoken_counting()
        _force_reasoning_effort()
        install_hook()

        # Agent patches are applied by the harness hook on the first
        # env.reset(); see collect/agent_patches.py for why they cannot run
        # here.
        # A sentinel the caller can check. Without it there is no way to tell
        # a probed run from an unprobed one after the fact, and an unprobed
        # run looks exactly like a null result.
        marker = os.environ.get("GLANCE_BOOTSTRAP_MARKER")
        if marker:
            try:
                pathlib.Path(marker).write_text("ok", encoding="utf-8")
            except OSError:
                pass
    except Exception:
        # Deliberately fatal.
        #
        # This used to swallow the exception with "must never break the
        # benchmark run" and log "the benchmark will run unprobed". It then
        # did exactly that: install_budget() raised BudgetExceeded, the
        # interpreter carried on with no spend cap, no harness hook, no
        # reasoning effort pinned and no GLANCE_VERIFIED_OBS, and both arms of
        # an A/B ran identically and unmetered. The warning was written to a
        # subprocess's stderr that nobody read, and the run would have
        # reported "no difference between arms" as a finding.
        #
        # An unprobed run is not a degraded run, it is a different experiment.
        # Better to stop than to produce a number that looks like a result.
        logging.getLogger("glance.bootstrap").error(
            "GLANCE bootstrap failed; refusing to run unprobed", exc_info=True
        )
        sys.stderr.flush()
        os._exit(97)
