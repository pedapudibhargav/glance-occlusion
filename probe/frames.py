"""Observation across frames, not just the top document.

On BBC, the Guardian, Spiegel, the FT, Sky Sports, heise and Le Figaro the
consent dialog that blocks the page renders its controls inside a cross-origin
iframe. A top-document accessibility snapshot contains neither those controls
nor any sign that the page is blocked, so the agent is stuck with no way to
act and no way to diagnose it.

This cannot be fixed from inside the page: script in the top document cannot
reach a cross-origin frame. It has to be done from the driver, which can
evaluate in every frame. Playwright can; an accessibility snapshot of one
document cannot.

Element ids are frame-qualified — `f2e17` for the 17th element of frame 2 —
following the convention Playwright MCP already uses, so an action can be
routed back to the frame it came from. Without that the ids collide and the
agent acts on the wrong element.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

STRUCTURE_JS = Path(__file__).with_name("structure.js")

# A deliberately small collector for child frames. The full structure builder
# assumes one document and computes layering within it; inside a consent
# dialog's frame there is nothing to be occluded by, so the extra work would
# only cost time.
FRAME_COLLECT_JS = """
() => {
  const clean = t => (t || '').replace(/\\s+/g, ' ').trim();
  const name = el => {
    const aria = el.getAttribute('aria-label');
    if (clean(aria)) return clean(aria);
    if (el.labels && el.labels.length) {
      const l = clean(el.labels[0].innerText); if (l) return l;
    }
    const text = clean(el.innerText);
    if (text) return text.split('\\n')[0].slice(0, 60);
    return clean(el.getAttribute('title') || el.getAttribute('placeholder') ||
                 el.value || '').slice(0, 60);
  };
  const role = el => {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'button' || tag === 'summary') return 'button';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      const t = (el.getAttribute('type') || 'text').toLowerCase();
      return ({checkbox:'checkbox',radio:'radio',submit:'button',button:'button'})[t] || 'textbox';
    }
    return tag;
  };
  const sel = 'a[href],button,input,select,textarea,summary,[role=button],[role=link],[role=checkbox],[tabindex],[onclick]';
  const ACTIONABLE_TAGS = new Set(['A','BUTTON','INPUT','SELECT','TEXTAREA','SUMMARY']);
  const ACTIONABLE_ROLES = new Set(['button','link','checkbox','radio','tab','menuitem','switch','option']);
  const out = [];
  let i = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;

    // A tabindex on a paragraph does not make it a control.
    //
    // Consent dialogs put tabindex on their body text for focus management,
    // so the first version listed <p> elements as controls. On BBC that made
    // a paragraph beginning "Terms of Use & Privacy" outrank the actual
    // "Accept and Continue" button, and the click did nothing.
    const elRole = el.getAttribute('role');
    if (!ACTIONABLE_TAGS.has(el.tagName) && !(elRole && ACTIONABLE_ROLES.has(elRole))) {
      if (!el.hasAttribute('onclick')) continue;
    }
    i += 1;
    el.setAttribute('glance-fid', String(i));
    out.push({ fid: i, role: role(el), name: name(el) });
  }
  return out;
}
"""


@dataclass
class FrameElement:
    qualified_id: str
    frame_index: int
    frame_id: int
    role: str
    name: str


@dataclass
class CrossFrameObservation:
    text: str
    top_text: str
    frames_scanned: int
    frames_with_controls: int
    frame_elements: list[FrameElement] = field(default_factory=list)
    unreadable_frames: int = 0

    def locate(self, qualified_id: str) -> Optional[FrameElement]:
        for element in self.frame_elements:
            if element.qualified_id == qualified_id:
                return element
        return None


def build(page: Any, include_frames: bool = True) -> CrossFrameObservation:
    """Build an observation covering the top document and its child frames.

    Child frames are only included when the top document reports a blocking
    overlay. Enumerating every frame on every step would add the advertising
    and analytics iframes that litter a news page — hundreds of irrelevant
    controls — for no benefit. Frames earn their tokens exactly when the page
    is blocked and the way out may be inside one.
    """
    source = STRUCTURE_JS.read_text(encoding="utf-8")
    page.evaluate(source)
    top = page.evaluate("() => window.__glanceStructure.build()")

    observation = CrossFrameObservation(
        text=top["text"], top_text=top["text"], frames_scanned=0, frames_with_controls=0
    )
    if not include_frames or not top.get("overlay_present"):
        return observation

    sections: list[str] = [top["text"]]
    for index, frame in enumerate(page.frames):
        if frame == page.main_frame:
            continue
        observation.frames_scanned += 1
        try:
            elements = frame.evaluate(FRAME_COLLECT_JS)
        except Exception:
            # Cross-origin frames the driver cannot reach are counted, not
            # hidden: "there is a frame here I could not read" is itself
            # something the agent should know.
            observation.unreadable_frames += 1
            continue
        named = [e for e in elements if e["name"]]
        if not named:
            continue
        observation.frames_with_controls += 1

        lines = [f'FRAME {index} ({frame.url.split("?")[0][:60]}) — controls inside the overlay:']
        for element in named[:25]:
            qualified = f"f{index}e{element['fid']}"
            observation.frame_elements.append(
                FrameElement(qualified, index, element["fid"], element["role"], element["name"])
            )
            lines.append(f'  [{qualified}] {element["role"]} "{element["name"]}"')
        sections.append("\n".join(lines))

    if observation.unreadable_frames:
        sections.append(
            f"({observation.unreadable_frames} further frame(s) could not be read.)"
        )

    observation.text = "\n\n".join(sections)
    return observation


def click(page: Any, qualified_id: str, observation: CrossFrameObservation,
          timeout: int = 4000) -> str:
    """Click an element by its frame-qualified id.

    A bare id is ambiguous once frames are in play — every document numbers its
    own elements from one — so the frame has to be recovered from the id before
    the click is dispatched.
    """
    element = observation.locate(qualified_id)
    if element is None:
        return f"unknown id {qualified_id}"
    try:
        frame = page.frames[element.frame_index]
    except IndexError:
        return f"frame {element.frame_index} is gone"
    try:
        frame.locator(f'[glance-fid="{element.frame_id}"]').first.click(timeout=timeout)
        return f"click({qualified_id})"
    except Exception as exc:
        return f"err:{type(exc).__name__}"
