/*
 * Structured observation: layering, grouping, state.
 *
 * The accessibility tree, serialised the way agents serialise it, is a flat
 * list of named elements. Three things a person uses constantly are missing
 * from it, and all three are recoverable from the DOM for free.
 *
 * 1. LAYERING. When a cookie banner or modal is open, most of the list is
 *    behind it and unclickable. The tree says nothing — the elements are still
 *    present, still correctly named. An agent clicks one, the overlay eats the
 *    click, and a round trip is spent discovering that.
 *
 *    An earlier version of this project *removed* covered elements. That was
 *    wrong: it threw away the agent's knowledge that the thing it wants exists
 *    behind the overlay, and task success dropped. Labelling beats deleting.
 *    "187 elements are behind this modal" lets the agent plan two steps —
 *    dismiss, then act — instead of failing into the discovery.
 *
 * 2. GROUPING. A form field in a flat list is `textbox "Email"`. Filling a
 *    form needs more: which form it belongs to, what is already filled, what
 *    is still required, what is invalid, and which button submits *that* form
 *    rather than one of the others on the page. Without grouping an agent
 *    rediscovers this every step, which is the obvious suspect for why agents
 *    average 2.6 steps on tasks a person does in one.
 *
 * 3. STATE. Required, invalid, checked, expanded, current value. Some of this
 *    is in the tree when authors set ARIA correctly, and absent when they do
 *    not; the DOM has it either way.
 *
 * None of this needs a model or a screenshot.
 */
(function () {
  "use strict";

  var MAX_LABEL = 60;

  var INTERACTIVE =
    'a[href], button, input, select, textarea, summary, ' +
    '[role=button], [role=link], [role=tab], [role=checkbox], [role=radio], ' +
    '[role=menuitem], [role=option], [role=switch], [role=combobox], ' +
    '[role=slider], [tabindex], [onclick]';

  function clean(t) { return (t || "").replace(/\s+/g, " ").trim(); }

  function accessibleName(el) {
    var aria = el.getAttribute && el.getAttribute("aria-label");
    if (clean(aria)) return clean(aria);
    var labelledby = el.getAttribute && el.getAttribute("aria-labelledby");
    if (labelledby) {
      var parts = [];
      labelledby.split(/\s+/).forEach(function (id) {
        var ref = document.getElementById(id);
        if (ref) parts.push(clean(ref.innerText));
      });
      if (clean(parts.join(" "))) return clean(parts.join(" "));
    }
    if (el.labels && el.labels.length) {
      var l = clean(el.labels[0].innerText);
      if (l) return l;
    }
    var text = clean(el.innerText);
    if (text) return text.split("\n")[0].slice(0, MAX_LABEL);
    var attr = el.getAttribute
      ? (el.getAttribute("placeholder") || el.getAttribute("title") ||
         el.getAttribute("alt") || el.getAttribute("name") || "")
      : "";
    return clean(attr).slice(0, MAX_LABEL);
  }

  function roleOf(el) {
    var explicit = el.getAttribute && el.getAttribute("role");
    if (explicit) return explicit;
    var tag = el.tagName.toLowerCase();
    if (tag === "a") return "link";
    if (tag === "button" || tag === "summary") return "button";
    if (tag === "select") return "combobox";
    if (tag === "textarea") return "textbox";
    if (tag === "input") {
      var type = (el.getAttribute("type") || "text").toLowerCase();
      return ({ checkbox: "checkbox", radio: "radio", range: "slider",
                submit: "button", button: "button", file: "file" })[type] || "textbox";
    }
    return tag;
  }

  function deepFromPoint(x, y) {
    var el = document.elementFromPoint(x, y);
    var guard = 0;
    while (el && el.shadowRoot && guard++ < 10) {
      var inner = el.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === el) break;
      el = inner;
    }
    return el;
  }

  // ---------------------------------------------------------------------
  // Layering
  //
  // A page can have an overlay the DOM does not declare as a dialog — the
  // common cookie banner is often just a fixed div with a high z-index. So
  // rather than trusting [role=dialog], the blocker is identified empirically:
  // hit-test each element, and whatever is sitting on top of other elements is
  // the overlay.
  // ---------------------------------------------------------------------

  function findOverlay() {
    // Declared dialogs first — when a site does it properly, use what it says.
    var declared = document.querySelector(
      "dialog[open], [role=dialog][aria-modal=true], [role=alertdialog][aria-modal=true]"
    );
    if (declared) {
      return { node: declared, declared: true, label: overlayLabel(declared) };
    }
    try {
      var modal = document.querySelector(":modal");
      if (modal) return { node: modal, declared: true, label: overlayLabel(modal) };
    } catch (e) { /* :modal unsupported */ }
    return null;
  }

  function overlayLabel(node) {
    var aria = node.getAttribute && node.getAttribute("aria-label");
    if (clean(aria)) return clean(aria).slice(0, MAX_LABEL);
    var labelledby = node.getAttribute && node.getAttribute("aria-labelledby");
    if (labelledby) {
      var ref = document.getElementById(labelledby);
      if (ref) {
        // Only accept a non-empty label, and read an iframe by its title.
        //
        // BBC's consent dialog sets aria-labelledby to the id of the iframe
        // holding its content. Reading that element's innerText gives "",
        // because the text lives in another document — and returning it
        // produced an overlay announced as OVERLAY "" on two major sites.
        // The iframe's title attribute carries the real name.
        var referenced = clean(ref.innerText);
        if (referenced) return referenced.slice(0, MAX_LABEL);
        if (ref.tagName === "IFRAME") {
          var iframeTitle = clean(ref.getAttribute("title") || ref.getAttribute("name"));
          if (iframeTitle) return iframeTitle.slice(0, MAX_LABEL);
        }
      }
    }
    var heading = node.querySelector("h1,h2,h3,h4,h5,h6,[role=heading]");
    if (heading && clean(heading.innerText)) return clean(heading.innerText).slice(0, MAX_LABEL);

    var text = clean(node.innerText).split("\n")[0].slice(0, MAX_LABEL);
    if (text) return text;

    // A consent dialog often renders its content in an iframe, so the host
    // element has no text of its own. BBC and the Guardian both came back
    // with an empty label for exactly this reason. Name it from what is
    // inside, or from the element itself.
    var frame = node.querySelector("iframe");
    if (frame) {
      var frameTitle = frame.getAttribute("title") || frame.getAttribute("name") || "";
      if (clean(frameTitle)) return clean(frameTitle).slice(0, MAX_LABEL);
      return "dialog (content in an iframe)";
    }
    var identifier = node.id || (node.classList && node.classList[0]) || "";
    return identifier ? "dialog (" + identifier.slice(0, 40) + ")" : "dialog";
  }

  /**
   * Where a link goes, as a path.
   *
   * Free from the DOM, and in neither the accessibility tree's serialisation
   * nor Playwright's AI snapshot. Most web tasks are navigation, so this is
   * the fact most often needed and never supplied: "World" and "World" are
   * indistinguishable by name, and trivially distinguishable by destination.
   *
   * Paths only. A full URL repeats the origin on every line for nothing, and
   * the origin is already known to the agent.
   */
  function hrefOf(el) {
    if (el.tagName !== "A") return null;
    var raw = el.getAttribute("href");
    if (!raw || raw.charAt(0) === "#" || /^javascript:/i.test(raw)) return null;
    try {
      var u = new URL(el.href, location.href);
      if (u.origin !== location.origin) return u.hostname + u.pathname;
      return u.pathname + (u.search ? u.search.slice(0, 24) : "");
    } catch (e) { return raw.slice(0, 48); }
  }

  function collect() {
    var nodes;
    try { nodes = document.querySelectorAll(INTERACTIVE); } catch (e) { return []; }
    var items = [];
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var rect = el.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) continue;
      items.push({
        el: el,
        bid: el.getAttribute("bid") || String(i + 1),
        name: accessibleName(el),
        role: roleOf(el),
        href: hrefOf(el),
        rect: rect,
      });
    }
    return items;
  }

  /**
   * Classify each element as reachable, or blocked and by what.
   *
   * The blocker is whatever the hit-test actually returns, which catches
   * undeclared overlays that no ARIA attribute would reveal.
   */
  function layer(items) {
    var blockers = {};
    items.forEach(function (item) {
      var rect = item.rect;
      var cx = rect.left + rect.width / 2;
      var cy = rect.top + rect.height / 2;
      if (cx < 0 || cy < 0 || cx >= window.innerWidth || cy >= window.innerHeight) {
        item.state = "offscreen";
        return;
      }
      var hit = deepFromPoint(cx, cy);
      if (!hit) { item.state = "unknown"; return; }
      if (item.el === hit || item.el.contains(hit)) { item.state = "reachable"; return; }

      // An ancestor hit is a layout artifact, not occlusion.
      //
      // When elementFromPoint returns something the element sits *inside*,
      // nothing is covering it — the centre point simply landed on padding or
      // a wrapper. Treating that as blocked produced nonsense: 36 GitHub
      // elements "blocked" by a layout div, and Wikipedia reporting an
      // overlay called "Culture".
      if (hit.contains(item.el)) { item.state = "reachable"; return; }

      item.state = "blocked";
      // Walk up from whatever was hit to the outermost positioned ancestor —
      // that container is the overlay, not the particular pixel that was hit.
      var node = hit;
      var container = hit;
      var guard = 0;
      while (node && node !== document.body && guard++ < 24) {
        var style = getComputedStyle(node);
        if (style.position === "fixed" || style.position === "sticky" ||
            (style.position === "absolute" && parseInt(style.zIndex, 10) > 0)) {
          container = node;
        }
        node = node.parentElement;
      }
      item.blockedBy = container;
      var key = container.tagName.toLowerCase() +
        (container.id ? "#" + container.id : "") +
        (container.classList && container.classList.length ? "." + container.classList[0] : "");
      blockers[key] = blockers[key] || { node: container, count: 0, key: key };
      blockers[key].count++;
      item.blockerKey = key;
    });

    // Only a positioned element can float above the page.
    //
    // A `position: static` blocker is normal document flow, not an overlay,
    // and announcing it as one would send the agent hunting for a dialog to
    // dismiss that does not exist. Wikipedia's "Culture" sidebar and GitHub's
    // layout wrappers were both static.
    var ranked = Object.keys(blockers)
      .map(function (k) { return blockers[k]; })
      .filter(function (b) {
        var style = getComputedStyle(b.node);
        var positioned = style.position === "fixed" || style.position === "sticky" ||
          style.position === "absolute";
        var declared = b.node.getAttribute &&
          (b.node.getAttribute("aria-modal") === "true" ||
           /dialog/.test(b.node.getAttribute("role") || ""));
        b.positioned = positioned;
        b.declared = declared;
        return positioned || declared;
      })
      .sort(function (a, b) { return b.count - a.count; });
    return ranked;
  }

  // ---------------------------------------------------------------------
  // Forms
  // ---------------------------------------------------------------------

  function fieldState(el) {
    var bits = [];
    if (el.required || el.getAttribute("aria-required") === "true") bits.push("required");
    if (el.getAttribute("aria-invalid") === "true" ||
        (el.validity && !el.validity.valid && el.value !== "")) bits.push("invalid");
    if (el.disabled || el.getAttribute("aria-disabled") === "true") bits.push("disabled");
    if (typeof el.checked === "boolean") bits.push(el.checked ? "checked" : "unchecked");
    if (typeof el.value === "string" && el.tagName !== "BUTTON") {
      bits.push(el.value ? 'filled="' + el.value.slice(0, 24) + '"' : "empty");
    }
    var described = el.getAttribute && el.getAttribute("aria-describedby");
    if (described) {
      var ref = document.getElementById(described);
      var message = ref ? clean(ref.innerText).slice(0, 40) : "";
      if (message) bits.push('hint="' + message + '"');
    }
    return bits;
  }

  function groupForms(items) {
    var forms = new Map();
    items.forEach(function (item) {
      if (!/^(textbox|checkbox|radio|combobox|slider|file)$/.test(item.role)) return;
      var form = item.el.closest("form, [role=form], fieldset, [role=group]");
      if (!form) return;
      if (!forms.has(form)) forms.set(form, { node: form, fields: [], submits: [] });
      forms.get(form).fields.push(item);
    });

    // Attach the submit control belonging to each form, which is what an agent
    // needs and what a flat list makes impossible to tell apart when a page
    // has several forms.
    items.forEach(function (item) {
      if (item.role !== "button") return;
      var form = item.el.closest("form, [role=form]");
      if (form && forms.has(form)) forms.get(form).submits.push(item);
    });

    return Array.from(forms.values()).filter(function (f) { return f.fields.length > 0; });
  }

  function formLabel(node) {
    var aria = node.getAttribute && node.getAttribute("aria-label");
    if (clean(aria)) return clean(aria).slice(0, MAX_LABEL);
    var legend = node.querySelector && node.querySelector("legend");
    if (legend) return clean(legend.innerText).slice(0, MAX_LABEL);
    var heading = node.querySelector && node.querySelector("h1,h2,h3,h4,h5,h6");
    if (heading) return clean(heading.innerText).slice(0, MAX_LABEL);
    var name = node.getAttribute && (node.getAttribute("name") || node.getAttribute("id"));
    return clean(name) || "form";
  }

  // ---------------------------------------------------------------------
  // Serialisation
  // ---------------------------------------------------------------------

  // Which facts this build includes. Set by the caller so each gap can be
  // switched on alone, and its contribution measured on its own.
  var OPTS = { occlusion: true, href: false };

  function line(item) {
    // No per-element "offscreen" marker.
    //
    // It was on 228 of 253 lines on one page and accounted for 100% of the
    // structured observation's token overhead there — the entire +46%. It also
    // buys nothing: the driver scrolls an element into view before clicking,
    // so "offscreen" does not change what the agent should do. Structure
    // should cost tokens only where it tells the agent something it would
    // otherwise get wrong.
    var bits = fieldState(item.el);
    var suffix = bits.length ? " " + bits.join(" ") : "";
    var dest = OPTS.href && item.href ? " -> " + item.href : "";
    return "[" + item.bid + "] " + item.role +
      (item.name ? ' "' + item.name + '"' : "") + dest + suffix;
  }


  // -------------------------------------------------------------------
  // Landmarks
  // -------------------------------------------------------------------

  /**
   * Which region of the page an element sits in.
   *
   * The DOM already says this — <nav>, <main>, <header>, <footer>, <aside>
   * and their ARIA equivalents — and no agent observation carries it. A flat
   * list gives "World news" twice with nothing to say that one is in the
   * primary navigation and one is in a footer sitemap, which is one of the
   * commonest name collisions on a news site.
   *
   * Nearest enclosing landmark wins, so a nav inside a footer reports the nav.
   */
  function landmarkOf(el) {
    var node = el;
    var depth = 0;
    while (node && node !== document.body && depth++ < 30) {
      var tag = node.tagName ? node.tagName.toLowerCase() : "";
      var role = node.getAttribute ? (node.getAttribute("role") || "") : "";
      if (tag === "nav" || role === "navigation") return label(node, "navigation");
      if (tag === "main" || role === "main") return "main";
      if (tag === "header" || role === "banner") return "header";
      if (tag === "footer" || role === "contentinfo") return "footer";
      if (tag === "aside" || role === "complementary") return "sidebar";
      if (tag === "form" || role === "search") return role === "search" ? "search" : "form";
      node = node.parentElement;
    }
    return "body";

    function label(n, kind) {
      var aria = clean(n.getAttribute("aria-label"));
      return aria ? kind + ' "' + aria + '"' : kind;
    }
  }

  // -------------------------------------------------------------------
  // Which control leaves the overlay
  // -------------------------------------------------------------------

  // Words that dismiss, across the languages in the measured sample. Kept
  // apart from the generic signals below, and switched on separately, because
  // a word list is domain knowledge about consent dialogs. The honest question
  // is how far the generic signals get on their own, so the two are measured
  // as separate variants rather than shipped as one.
  var ACCEPT_WORDS = /\b(accept|agree|allow|continue|ok|okay|got it|understood|akzeptieren|zustimmen|einverstanden|annehmen|aceptar|acepto|accetta|accetto|accepter|accepteren|akkoord|godta|godkend|acceptera|tillad|aceitar|zgadzam|souhlas|kabul)\b/i;

  // Words that look like a way out and are not one. "Manage preferences"
  // opens a second panel; "refuse and subscribe" leads to a paywall. On
  // Repubblica the agent clicked "pannello delle preferenze pubblicitarie"
  // instead of ACCETTA and never came back, which is what this exists for.
  var TRAP_WORDS = /(preferen|setting|manage|gestis|personaliz|einstellung|configurar|param|more option|abbonati|subscribe|purpose|partner|vendor|policy|polic[ií]|privacy|terms|cookie policy|legal)/i;

  /**
   * Rank the controls inside an overlay by how likely each is to close it.
   *
   * Generic signals only, in `generic` mode: a button rather than a link
   * (links in a consent dialog go to policies), autofocus, a declared default
   * action, being inside the dialog's action row, and area. `lexical` mode
   * adds the word lists above.
   */
  function rankDismiss(controls, mode) {
    if (!controls.length) return null;
    var scored = controls.map(function (item) {
      var el = item.el;
      var score = 0;
      if (el.tagName === "BUTTON" || /^(button|submit)$/i.test(el.getAttribute("type") || "")) score += 3;
      if (el.tagName === "A") score -= 2;
      if (el.hasAttribute("autofocus")) score += 3;
      if (el.getAttribute("data-testid") || el.getAttribute("data-action")) score += 1;
      var rect = el.getBoundingClientRect();
      score += Math.min(2, (rect.width * rect.height) / 8000);
      if (mode === "lexical") {
        var text = (item.name || "") + " " + (el.getAttribute("aria-label") || "");
        if (ACCEPT_WORDS.test(text)) score += 6;
        if (TRAP_WORDS.test(text)) score -= 5;
      }
      return { item: item, score: score };
    }).sort(function (a, b) { return b.score - a.score; });
    // A winner only when it is clearly ahead; a tie is not information.
    if (scored.length > 1 && scored[0].score - scored[1].score < 1.5) return null;
    return scored[0].item;
  }

  /**
   * Is this overlay actually in the way?
   *
   * Learned from RTÉ and Al Jazeera. On both, the flat arm solved the task in
   * one step and the structured arm took four: the dialog covers part of the
   * page but not the navigation, so announcing it sent the agent to dismiss
   * something that was never blocking it. Announcing a harmless overlay costs
   * steps.
   *
   * The test is deliberately parameter-free rather than a tuned fraction of
   * blocked elements — a threshold picked on these sites would be fitted to
   * them. An overlay is obstructive when it covers something in the page's
   * primary interactive regions, navigation or main content, which is where a
   * task's target almost always is.
   */
  function isObstructive(blocked) {
    for (var i = 0; i < blocked.length; i++) {
      var mark = landmarkOf(blocked[i].el);
      if (mark === "main" || mark.indexOf("navigation") === 0) return true;
    }
    return false;
  }

  function build(opts) {
    OPTS = {
      occlusion: !opts || opts.occlusion !== false,
      href: !!(opts && opts.href),
      landmarks: !!(opts && opts.landmarks),
      rank: (opts && opts.rank) || null,  // null | "generic" | "lexical"
      // Learned from failures; off by default so earlier results reproduce.
      directive: !!(opts && opts.directive),
      obstruction: !!(opts && opts.obstruction),
    };
    var items = collect();
    var blockers = layer(items);
    var overlay = findOverlay();

    // Only elements blocked by a *surviving* blocker count as blocked; the
    // rest were static-layout false positives and are reachable.
    var realBlockerKeys = {};
    blockers.forEach(function (b) { realBlockerKeys[b.key] = true; });
    items.forEach(function (i) {
      if (i.state === "blocked" && !realBlockerKeys[i.blockerKey]) i.state = "reachable";
    });

    var reachable = items.filter(function (i) { return i.state !== "blocked"; });
    var blocked = items.filter(function (i) { return i.state === "blocked"; });

    var out = [];

    // The overlay goes first, because it is what the agent must deal with
    // before anything else on the page is available.
    // One or two blocked elements is a tooltip, a sticky header edge, or a
    // loading bar — not something the agent must dismiss. Announcing it as an
    // overlay sends it hunting for a dialog that is not there. A declared
    // aria-modal is trusted at any size; anything else has to block several
    // elements before it is worth saying.
    var MIN_BLOCKED_TO_ANNOUNCE = 3;
    var significant = OPTS.occlusion && blockers.length &&
      (blockers[0].declared || blocked.length >= MIN_BLOCKED_TO_ANNOUNCE);

    var obstructive = true;
    if (blocked.length && significant) {
      var top = blockers[0];
      var label = overlay ? overlay.label : overlayLabel(top.node);
      var inOverlay = reachable.filter(function (i) {
        return top.node.contains(i.el);
      });
      var pick = OPTS.rank ? rankDismiss(inOverlay, OPTS.rank) : null;
      if (OPTS.obstruction) obstructive = isObstructive(blocked);

      if (!obstructive) {
        // One line, no instruction, no list. The overlay exists and is not in
        // the way, so saying more only invites a wasted step.
        out.push("OVERLAY \"" + label + "\" is present but navigation and main " +
          "content are still reachable; " + blocked.length +
          " element(s) behind it are not.");
      } else {
        out.push("OVERLAY \"" + label + "\" is on top and blocks " + blocked.length +
          " element(s) behind it." +
          (overlay && overlay.declared ? "" : " (not declared as a dialog; detected by hit-testing)"));

        if (OPTS.directive && pick) {
          // Imperative and singular.
          //
          // Learned from Mirror, where the observation listed
          // `[345] button "ACCEPT"` correctly and the agent never touched it,
          // clicking blocked page elements for six steps instead. A list of
          // overlay controls states a fact; it does not say what to do first.
          out.push("FIRST: activate [" + pick.bid + "] " + pick.role +
            (pick.name ? ' "' + pick.name + '"' : "") +
            " to clear the overlay, then act on the page.");
        }

        if (inOverlay.length) {
          out.push("Actionable in the overlay:");
          inOverlay.forEach(function (i) {
            out.push("  " + line(i) +
              (pick && pick.bid === i.bid && !OPTS.directive
                ? "  [most likely closes the overlay]" : ""));
          });
        } else if (OPTS.directive) {
          // Careful with the wording here. This line cost two tasks.
          //
          // It first read "No control in this document closes it — the
          // overlay's own controls are in an iframe and are not listed here."
          // On Euronews the blocker is an ADVERTISEMENT iframe, and the agent
          // read that as "there is no way out": it stopped exploring and
          // clicked one element five times, where the same arm without the
          // line had succeeded. Stating an absence terminally makes an agent
          // abandon; the corrected line states the same fact and leaves the
          // page open.
          var declared = overlay && overlay.declared;
          out.push(declared
            ? "Its own controls are inside an iframe and are not listed here; " +
              "the page behind it may still respond to scrolling."
            : "This looks like an advertisement or injected frame rather than a " +
              "dialog. Its controls are not listed here; the page behind it may " +
              "still respond, and the overlay may clear on its own.");
        }
      }
      out.push("");
    }

    var forms = groupForms(reachable);
    var inForm = new Set();
    forms.forEach(function (form) {
      form.fields.forEach(function (f) { inForm.add(f.bid); });
      form.submits.forEach(function (s) { inForm.add(s.bid); });
    });

    forms.forEach(function (form) {
      var required = form.fields.filter(function (f) {
        return f.el.required || f.el.getAttribute("aria-required") === "true";
      }).length;
      var empty = form.fields.filter(function (f) {
        return typeof f.el.value === "string" && !f.el.value;
      }).length;
      out.push('FORM "' + formLabel(form.node) + '" — ' + form.fields.length +
        " field(s), " + required + " required, " + empty + " empty");
      form.fields.forEach(function (f) { out.push("  " + line(f)); });
      form.submits.forEach(function (s) { out.push("  " + line(s) + "  [submits this form]"); });
      out.push("");
    });

    var loose = reachable.filter(function (i) {
      return !inForm.has(i.bid) && !(blockers.length && blockers[0].node.contains(i.el));
    });
    if (loose.length && OPTS.landmarks) {
      // Grouped by region rather than document order. The heading carries the
      // region once instead of repeating it per element, so this is close to
      // free in tokens.
      var groups = new Map();
      loose.forEach(function (i) {
        var key = landmarkOf(i.el);
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(i);
      });
      var ORDER = ["header", "navigation", "search", "main", "sidebar", "form", "footer", "body"];
      var keys = Array.from(groups.keys()).sort(function (a, b) {
        var ia = ORDER.findIndex(function (o) { return a.indexOf(o) === 0; });
        var ib = ORDER.findIndex(function (o) { return b.indexOf(o) === 0; });
        return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
      });
      keys.forEach(function (key) {
        out.push(key.toUpperCase() + ":");
        groups.get(key).forEach(function (i) { out.push("  " + line(i)); });
      });
    } else if (loose.length) {
      if (forms.length || blocked.length) out.push("PAGE:");
      loose.forEach(function (i) { out.push(forms.length || blocked.length ? "  " + line(i) : line(i)); });
    }

    if (blocked.length && significant && obstructive) {
      out.push("");
      out.push("Blocked until the overlay closes: " +
        blocked.slice(0, 40).map(function (i) { return "[" + i.bid + "]"; }).join(" ") +
        (blocked.length > 40 ? " ... and " + (blocked.length - 40) + " more" : ""));
    }

    return {
      text: out.join("\n"),
      total: items.length,
      reachable: reachable.length,
      blocked: significant ? blocked.length : 0,
      overlay_present: !!(blocked.length > 0 && significant),
      overlay_obstructive: !!(blocked.length > 0 && significant && obstructive),
      overlay_declared: !!(overlay && overlay.declared),
      overlay_label: significant ? (overlay ? overlay.label : overlayLabel(blockers[0].node)) : null,
      forms: forms.length,
      form_fields: forms.reduce(function (n, f) { return n + f.fields.length; }, 0),
    };
  }

  function flat(opts) {
    var withHref = !!(opts && opts.href);
    var items = collect();
    return {
      text: items.map(function (i) {
        return "[" + i.bid + "] " + i.role + (i.name ? ' "' + i.name + '"' : "") +
          (withHref && i.href ? " -> " + i.href : "");
      }).join("\n"),
      total: items.length,
    };
  }

  window.__glanceStructure = { build: build, flat: flat, version: "1.4.0" };
})();
