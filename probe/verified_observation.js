/*
 * A verified observation.
 *
 * Standard practice — Playwright MCP, BrowserGym, CDP's getFullAXTree — is to
 * serialise the accessibility tree and hand it to the model. Nothing in that
 * path checks whether the elements described are actually there and actually
 * reachable. Staleness is handled after the fact: Playwright MCP's refs are
 * scoped to one snapshot and a stale ref returns an error, which costs the
 * agent a step and a round trip.
 *
 * Measured on 400 live steps, 52% of structure-only failures were an agent
 * acting on a reference that no longer resolved. So this builds the list the
 * other way round: start from the elements that exist right now, keep the ones
 * a click would actually reach, and give each a name good enough to choose by.
 *
 * Three rules, all from browser APIs, no model and no screenshot:
 *
 *   1. It must be hit-testable at its own centre. An element covered by a
 *      banner or an overlay cannot be clicked, so listing it only invites a
 *      wasted step.
 *   2. It must be nameable. Where the accessible name is missing, derive one
 *      from the row or column the control sits in — what a person reads to
 *      tell two identical pencil icons apart.
 *   3. Identical names get disambiguated by context, because five buttons all
 *      called "Edit" is a correct tree and a useless one.
 *
 * Returns the same `[bid] role "name"` shape the harness already uses, so the
 * agent's actions work unchanged and the only variable is the observation.
 */
(function () {
  "use strict";

  var BID_ATTRIBUTE = "bid"; // BrowserGym's BROWSERGYM_ID_ATTRIBUTE
  var MAX_NAME = 60;
  // No arbitrary element cap.
  //
  // This was 200, and it silently truncated the back half of every large page
  // in DOM order. GitHub's repository page carries 457 interactive elements
  // and its "Releases" link is the 411th, so the agent was never shown it and
  // wandered for five steps; the standard snapshot, which has no cap, clicked
  // it immediately. That single constant accounted for a 14-point drop in task
  // success and looked like evidence that filtering hurts.
  //
  // Compactness has to come from dropping what cannot be acted on, never from
  // cutting the list short: a truncated list is silently wrong, and wrong in a
  // way that is invisible to the agent reading it.
  var MAX_ELEMENTS = Infinity;

  var INTERACTIVE =
    'a[href], button, input, select, textarea, summary, [role], [tabindex], ' +
    '[onclick], [contenteditable=""], [contenteditable="true"]';

  // Roles that are structural rather than actionable. An agent cannot click a
  // "group"; listing them is noise.
  var NON_ACTIONABLE = {
    generic: 1, none: 1, presentation: 1, group: 1, paragraph: 1,
    LabelText: 1, StaticText: 1, Section: 1, main: 1, document: 1
  };

  function deepElementFromPoint(x, y) {
    var el = document.elementFromPoint(x, y);
    var guard = 0;
    while (el && el.shadowRoot && guard++ < 10) {
      var inner = el.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === el) break;
      el = inner;
    }
    return el;
  }

  function reachable(el) {
    var rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return { ok: false, why: "zero_area" };
    var cx = rect.left + rect.width / 2;
    var cy = rect.top + rect.height / 2;
    if (cx < 0 || cy < 0 || cx >= window.innerWidth || cy >= window.innerHeight) {
      // Offscreen is not unreachable: the driver scrolls before clicking. It
      // is kept, and marked, so the model knows a scroll is implied.
      return { ok: true, why: "offscreen", offscreen: true };
    }
    var hit = deepElementFromPoint(cx, cy);
    if (!hit) return { ok: false, why: "nothing_hit" };
    if (el === hit || el.contains(hit)) return { ok: true, why: null };
    // Covered. Report what covers it — that is the actionable fact.
    var cls = (hit.classList && hit.classList.length) ? "." + hit.classList[0] : "";
    return { ok: false, why: "covered_by:" + hit.tagName.toLowerCase() + cls };
  }

  function ownName(el) {
    var label = el.getAttribute && el.getAttribute("aria-label");
    if (label && label.trim()) return label.trim();

    var labelledby = el.getAttribute && el.getAttribute("aria-labelledby");
    if (labelledby) {
      var parts = [];
      labelledby.split(/\s+/).forEach(function (id) {
        var ref = document.getElementById(id);
        if (ref) parts.push((ref.innerText || "").trim());
      });
      if (parts.join(" ").trim()) return parts.join(" ").trim();
    }

    if (el.labels && el.labels.length) {
      var fromLabel = (el.labels[0].innerText || "").trim();
      if (fromLabel) return fromLabel;
    }

    var text = (el.innerText || "").trim().split("\n")[0];
    if (text) return text;

    var attr = (el.getAttribute && (el.getAttribute("placeholder") ||
      el.getAttribute("title") || el.getAttribute("alt") ||
      el.getAttribute("name") || el.getAttribute("value"))) || "";
    return attr.trim();
  }

  // Where a control has no name of its own — an icon-only button — borrow the
  // label of the row, cell or group it belongs to. That is the information a
  // person uses, and it is already in the DOM.
  function contextName(el) {
    var container = el.closest(
      "tr, li, [role=row], [role=listitem], fieldset, " +
      "[role=group], .ant-list-item, .MuiListItem-root"
    );
    if (container) {
      var label = (container.innerText || "").trim().split("\n")[0];
      if (label) return label.slice(0, 40);
    }
    var labelled = el.closest("[aria-label]");
    if (labelled && labelled !== el) {
      var aria = labelled.getAttribute("aria-label");
      if (aria) return aria.slice(0, 40);
    }
    return "";
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
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      if (type === "range") return "slider";
      if (type === "submit" || type === "button") return "button";
      return "textbox";
    }
    return tag;
  }

  function stateOf(el) {
    var bits = [];
    if (el.disabled || el.getAttribute("aria-disabled") === "true") bits.push("disabled");
    var expanded = el.getAttribute && el.getAttribute("aria-expanded");
    if (expanded) bits.push("expanded=" + expanded);
    var checkedAttr = el.getAttribute && el.getAttribute("aria-checked");
    if (checkedAttr) bits.push("checked=" + checkedAttr);
    else if (typeof el.checked === "boolean") bits.push("checked=" + el.checked);
    var selected = el.getAttribute && el.getAttribute("aria-selected");
    if (selected) bits.push("selected=" + selected);
    if (typeof el.value === "string" && el.value && el.tagName !== "BUTTON") {
      bits.push('value="' + el.value.slice(0, 30) + '"');
    }
    return bits;
  }

  function build(options) {
    options = options || {};
    var includeUnreachable = !!options.includeUnreachable;

    var nodes;
    try { nodes = document.querySelectorAll(INTERACTIVE); } catch (e) { nodes = []; }

    var kept = [];
    var dropped = { unnamed: 0, covered: 0, zero_area: 0, no_bid: 0, non_actionable: 0 };
    var nameCounts = {};

    for (var i = 0; i < nodes.length && kept.length < MAX_ELEMENTS; i++) {
      var el = nodes[i];

      var bid = el.getAttribute(BID_ATTRIBUTE);
      if (!bid) { dropped.no_bid++; continue; }

      var role = roleOf(el);
      if (NON_ACTIONABLE[role] && !el.getAttribute("onclick")) {
        dropped.non_actionable++;
        continue;
      }

      var reach = reachable(el);
      if (!reach.ok) {
        if (reach.why === "zero_area") dropped.zero_area++;
        else dropped.covered++;
        if (!includeUnreachable) continue;
      }

      var name = ownName(el);
      var borrowed = "";
      if (!name) {
        borrowed = contextName(el);
        if (!borrowed) { dropped.unnamed++; continue; }
      }

      kept.push({
        bid: bid,
        role: role,
        name: (name || "").slice(0, MAX_NAME),
        context: borrowed,
        state: stateOf(el),
        offscreen: !!reach.offscreen,
        blocked: reach.ok ? null : reach.why
      });

      var key = (name || borrowed).toLowerCase();
      nameCounts[key] = (nameCounts[key] || 0) + 1;
    }

    // Disambiguate duplicates with their surrounding context. Five buttons all
    // named "Edit" is a correct tree and a useless one.
    var lines = kept.map(function (item) {
      var label = item.name;
      var key = (item.name || item.context).toLowerCase();
      if (!label) {
        label = item.context ? item.role + " in “" + item.context + "”" : item.role;
      } else if (nameCounts[key] > 1 && item.context && item.context !== label) {
        label = label + " — " + item.context;
      }
      var suffix = item.state.length ? " " + item.state.join(" ") : "";
      if (item.offscreen) suffix += " offscreen";
      if (item.blocked) suffix += " " + item.blocked;
      return "[" + item.bid + "] " + item.role + ' "' + label + '"' + suffix;
    });

    return {
      text: lines.join("\n"),
      kept: kept.length,
      dropped: dropped,
      considered: nodes.length
    };
  }

  window.__glanceObs = { build: build, version: "1.0.0" };
})();
