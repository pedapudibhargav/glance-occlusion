// What the page knows about its own controls that no agent observation says.
//
// Occlusion was the first of these and is measured by probe/structure.js. This
// collects the rest of the family in one pass, so the question "how many
// expressive gaps are there, and how common is each" can be answered on live
// pages rather than argued from examples.
//
// Each gap has the same shape: a fact the DOM supplies for free, which changes
// whether or how an element can be acted on, and which is absent from both the
// CDP accessibility tree and Playwright's AI snapshot.
(() => {
  const SEL = 'a[href],button,input,select,textarea,summary,[role=button],' +
              '[role=link],[role=checkbox],[role=tab],[role=menuitem],[onclick]';

  // Collect across shadow roots as well as the light DOM. An agent that cannot
  // see into a shadow root cannot act on a web component's internals, and
  // component libraries put real controls there.
  function collect(root, depth, out) {
    if (depth > 6) return;
    let nodes;
    try { nodes = root.querySelectorAll(SEL); } catch (e) { return; }
    for (const el of nodes) out.push({ el, shadow: depth > 0 });
    let all;
    try { all = root.querySelectorAll('*'); } catch (e) { return; }
    for (const el of all) {
      if (el.shadowRoot) {
        out.shadowRoots += 1;
        collect(el.shadowRoot, depth + 1, out);
      }
    }
  }

  function visible(el) {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }

  window.__glanceGaps = {
    build() {
      const found = [];
      found.shadowRoots = 0;
      collect(document, 0, found);

      const rows = [];
      for (const { el, shadow } of found) {
        if (!visible(el)) continue;
        const style = getComputedStyle(el);

        // inert and aria-hidden: present in the DOM, excluded from
        // interaction. A modal sets inert on everything behind it, which is
        // the declarative twin of the occlusion we hit-test for.
        const inert = el.closest('[inert]') !== null;
        const ariaHidden = el.closest('[aria-hidden="true"]') !== null;

        // pointer-events:none is the quiet one. The element is painted,
        // named, and in every serialisation, and a click passes through it.
        const noPointer = style.pointerEvents === 'none';

        const disabled = el.disabled === true ||
          el.getAttribute('aria-disabled') === 'true';

        // Where a link goes. Free, in the DOM, in no agent observation.
        // Most web tasks are navigation, so this is the fact most often
        // needed and never supplied.
        let href = null;
        if (el.tagName === 'A' && el.getAttribute('href')) {
          try { href = new URL(el.href, location.href).pathname; }
          catch (e) { href = el.getAttribute('href'); }
        }

        // Whether reaching it needs a scroll, and of what.
        const r = el.getBoundingClientRect();
        const inViewport = r.bottom > 0 && r.top < innerHeight &&
                           r.right > 0 && r.left < innerWidth;

        rows.push({
          tag: el.tagName, shadow, inert, ariaHidden, noPointer, disabled,
          href, inViewport,
          name: (el.getAttribute('aria-label') || el.innerText ||
                 el.value || '').replace(/\s+/g, ' ').trim().slice(0, 40),
        });
      }

      const n = rows.length || 1;
      const count = (f) => rows.filter(f).length;
      const links = rows.filter((r) => r.href !== null);
      // Links whose accessible name is shared with another link but whose
      // destination differs: the case where the name cannot identify the
      // target and the href can.
      const byName = {};
      for (const l of links) (byName[l.name] = byName[l.name] || []).push(l);
      let nameCollides = 0;
      let hrefSeparates = 0;
      for (const k of Object.keys(byName)) {
        const g = byName[k];
        if (g.length < 2 || !k) continue;
        nameCollides += g.length;
        if (new Set(g.map((x) => x.href)).size > 1) hrefSeparates += g.length;
      }

      return {
        interactive: rows.length,
        shadow_roots: found.shadowRoots,
        in_shadow: count((r) => r.shadow),
        inert: count((r) => r.inert),
        aria_hidden: count((r) => r.ariaHidden),
        pointer_events_none: count((r) => r.noPointer),
        disabled: count((r) => r.disabled),
        links_with_href: links.length,
        offscreen: count((r) => !r.inViewport),
        link_name_collisions: nameCollides,
        collisions_href_separates: hrefSeparates,
        pct: {
          in_shadow: +(100 * count((r) => r.shadow) / n).toFixed(1),
          inert_or_hidden: +(100 * count((r) => r.inert || r.ariaHidden) / n).toFixed(1),
          pointer_events_none: +(100 * count((r) => r.noPointer) / n).toFixed(1),
          offscreen: +(100 * count((r) => !r.inViewport) / n).toFixed(1),
        },
        sample_shadow: rows.filter((r) => r.shadow).slice(0, 5).map((r) => r.name),
        sample_hidden: rows.filter((r) => r.inert || r.ariaHidden)
          .slice(0, 5).map((r) => r.name),
      };
    },
  };
})();
