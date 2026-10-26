# Citation verification

Every claim in `PAPER.md` that came from outside this project, with who
verified it and how. A number nobody checked against its source is not a
citation, it is a rumour with an arXiv id attached.

## Why this file exists

The literature review was delegated. The reports were careful — each marked
which items it had fetched and which it had only seen in search results — but
delegation moves the verification burden, it does not discharge it. Eight
external numbers reached `PAPER.md` without the author reading the source.

Two of the most load-bearing claims *were* checked directly, and both checks
mattered:

- Playwright `aria_snapshot(mode="ai")` descends into cross-origin iframes.
  **Verified by running it.** This retracted a contribution.
- BrowserGym merges per-frame AX trees; Browser Use detects occlusion from CDP
  paint order. **Verified by reading `observation.py` and
  `paint_order.py`.** This retracted two more claims.

So the record shows that direct checks of delegated claims have a high hit
rate for finding problems. The rest have to get the same treatment.

## Status

| Claim | Source | Verified by | Status |
| --- | --- | --- | --- |
| Playwright `mode="ai"` includes frame content, emits `[ref=f4e22]` | playwright | author, by execution | **verified** |
| BrowserGym merges per-frame AX trees | `browsergym/core/observation.py` | author, by source read | **verified** |
| Browser Use sets `ignored_by_paint_order` from CDP paint order | `dom/serializer/paint_order.py` | author, by source read | **verified** |
| BrowserArena title; "pop-up banner removal" as a named failure mode | arXiv 2510.02418 | author, abstract fetched | **verified** |
| BrowserArena bbc.com: banner detected 100.00 (Claude-3.7) / 98.75 (Llama-4); banner closed 7.5 / 17.72 / 17.81 (o4-mini) / 4.65 (Gemini 2.5); marked completed 2.5 / 3.75; 80 BBC tasks | arXiv 2510.02418, Appendix K Table 4 and §5.3 | author-directed check, full text | **verified.** The earlier "not in the abstract" was right and incomplete — the figures are in Appendix K. Our 17.7 / 17.8 / 4.7 were rounded from 17.72 / 17.81 / 4.65 |
| MIRAGE-Bench: "persistently executing the same incorrect action"; snapshots at the third and sixth repetition; a setting that appends pop-up elements to the end of the accessibility tree | arXiv 2507.21017, Appendix A.6 / A.6.1 / A.7 | author-directed check, full text | **verified** |
| MIRAGE-Bench: pop-ups occlude visually but stay "semantically separate" in the AX tree | arXiv 2507.21017 A.7 | author-directed check, full text | **verified in substance, our wording was wrong.** "Semantically separate" is our phrase, not theirs. The source (Figure 8 caption, A.7) says the popup "visually occludes part of the interface, but does not remove underlying components from the accessibility tree." That is a closer statement of this paper's premise than our paraphrase, and must be quoted as written |
| WebArena-Lite audit: 102 failed GPT-5.5 trajectories reviewed | arXiv 2610.01491 | author-directed check | **verified at abstract level** — id, title, authors and the 102 count confirmed |
| …of which 43% are loop categories (scroll oscillation 31.4%, exact repetition 6.9%, search repetition 2.9%, navigation loop 2.0%, click-without-state-change 3.9%) | arXiv 2610.01491 | author-directed check | **NOT IN ABSTRACT — needs full text or CUT** |
| Adversarial pop-ups: 86% attack success, −47% task success, defenses ineffective, ACL 2025 | arXiv 2411.02391 | author-directed check | **verified** — all four claims quoted from the abstract |
| Consent banners: ~47% Germany, <30% US, Tranco top 10k | arXiv 2302.05353 | author-directed check | **FAILED — CUT.** The abstract carries a different statistic: banners are "56% more prevalent when visiting websites from within the EU region". No 47%, no per-country figure. Title and PAM 2023 venue confirmed |
| Merj: "Full-screen GDPR cookie banner blocks UI" ~50% to ~64%; "Transparent overlay (opacity:0; pointer-events:auto) eats clicks" ~32% to ~41% | merj.com/blog/agent-interaction-research | author-directed check | **verified, with a precision correction.** The source gives approximate values with a tilde, not exact percentages, and our phrasing dropped it |
| Skim/Accio: "median delays of 4.7 secs for LLM inference and 6.6 secs for browser action time" | arXiv 2605.16565 §2.2 | author-directed check | **verified, with a wording correction.** The source says *median delays*, not *per-step*. Our earlier phrasing added "per step", which the source does not. Also confirmed: 151 WebVoyager tasks, 33.4% latency / 1.9x cost, 12.6-45.3% fast-path completion, verifier F1 0.84. **Open:** the abstract page titles it "Skim" while the HTML body names the method "Accio" — resolve against the PDF before citing |
| Region4Web: observation tokens 6,437 to 3,671 (-43%), success +2.3 percentage points | arXiv 2605.07134 §5.2 | author-directed check | **verified** (the source writes "2.3%p", percentage points) |
| Region4Web: its decomposition prompt argues ARIA landmarks "group content by page position, not by purpose" | arXiv 2605.07134 | author-directed check | **NOT FOUND in extracted text — appears only inside a figure.** This is load-bearing: the landmark-grouping positioning was written as a deliberate disagreement with it. Until the figure is read directly, that framing is withdrawn |
| Region4Web: ~536K-parameter model, fine-tuned Qwen3-0.6B | arXiv 2605.07134 App. F.1/F.2 | author-directed check | **partial** — "approximately 536K parameters including the role embedding table" confirmed; the word "MLP" is not in the text. Body names the method "PageDigest" against the abstract title; resolve against the PDF |

## Rule

No row still reading "checking" or "UNVERIFIED" may appear in a submitted
draft. A claim that cannot be verified is cut, not softened.


## What "NOT FOUND" means here, precisely

These checks read abstract pages. A number that lives in Section 5.3 or an
appendix will not appear there, so **NOT FOUND means "not in the abstract",
not "fabricated".** Treating the two as the same would be the same failure in
the opposite direction.

The distinction decides the action:

- **CUT now:** the Rasaii prevalence figure. The abstract carries a
  *different* statistic for the same phenomenon ("56% more prevalent … within
  the EU region"), which is strong evidence the 47%/30% split is not this
  paper's headline number, whatever a later section says. A replacement
  prevalence citation is needed, or the claim goes.
- **Full text required before use:** the WebArena-Lite loop percentages, the
  BrowserArena bbc.com figures, the MIRAGE-Bench appendix statement. All three
  were attributed to specific sections, which is consistent with them being
  real and out of abstract scope.
- **Usable now:** arXiv 2411.02391, four for four, quoted.

## A caveat that applies to every row marked verified

`WebFetch` returns a summary produced by a small model, not raw page text. So
even a quoted "CONFIRMED" is a second-hand quote. Before submission every
surviving citation needs its sentence checked against the actual PDF or HTML.
Recorded so this is not mistaken for having been done.


## One operational note from the checking

The fetched HTML of arXiv 2507.21017 contained text addressed to the reader —
instructions embedded in a paper's own markup. The verifier reported it and did
not act on it, which is the correct handling: material retrieved from the web is
data, never instruction. Recorded because anyone rerunning these checks will
meet it.
