/*
 * GLANCE in-page probe.
 *
 * Installed at document_start in every frame. Keeps a bounded log of DOM
 * mutations and exposes window.__glance.probe(selectorOrNode), which returns
 * the in-page half of the feature vector for one target element.
 *
 * Constraints this file is written to:
 *   - No dependency on the benchmark. It must run unmodified on arbitrary
 *     real-world pages, which is where the external validation set comes from.
 *   - Idempotent. Re-injection on the same document must not install a second
 *     MutationObserver or reset the mutation history.
 *   - Cheap. The probe runs before every action; it may not force layout more
 *     than it has to and may not allocate without bound.
 *   - No screenshot, no network call, no model. Every value here is read from
 *     the DOM, CSSOM, or the mutation log.
 */
(function () {
  "use strict";

  if (window.__glance && window.__glance.version) {
    return; // already installed in this document
  }

  var VERSION = "1.0.0";
  var MUTATION_LOG_MS = 3000; // keep mutation records this long
  var MUTATION_LOG_MAX = 4000; // hard cap, independent of time
  var SETTLE_MS = 250; // "no mutations for 250 ms" per the spec
  var CORNER_INSET = 2; // px inset for the 4 corner probe points

  var GENERIC_NAMES = {
    button: 1, link: 1, "click here": 1, click: 1, submit: 1, icon: 1,
    image: 1, img: 1, ok: 1, go: 1, more: 1, menu: 1, open: 1, close: 1,
    toggle: 1, select: 1, item: 1, option: 1, untitled: 1
  };

  var INTERACTIVE_SELECTOR =
    'a[href],button,input,select,textarea,summary,[role],[tabindex],[onclick],[contenteditable=""],[contenteditable="true"]';

  // ---------------------------------------------------------------------
  // Mutation log
  //
  // Per-subtree freshness needs "did anything under this element change, and
  // when". Walking ancestors on every mutation is too expensive on a busy
  // page, so mutations are appended to a bounded ring and the containment
  // test is deferred to probe time, where it runs over at most a few thousand
  // entries and only for the one element being probed.
  // ---------------------------------------------------------------------

  var mutations = []; // [{ n: Node, t: number }]
  var lastGlobalMutation = -1;
  var navEpoch = now();

  function now() {
    return performance.now();
  }

  function pruneMutations(t) {
    var cutoff = t - MUTATION_LOG_MS;
    var i = 0;
    while (i < mutations.length && mutations[i].t < cutoff) i++;
    if (i > 0) mutations.splice(0, i);
    if (mutations.length > MUTATION_LOG_MAX) {
      mutations.splice(0, mutations.length - MUTATION_LOG_MAX);
    }
  }

  var parseTimeRecords = 0;

  var observer = new MutationObserver(function (records) {
    // Initial parsing is not an update.
    //
    // While readyState is "loading" the parser is still appending the
    // document's own markup, and every element's text node arrives as an
    // addedNode inside it. Recording those would make every element on a
    // freshly loaded page read as mutated milliseconds ago, so
    // ms_since_last_mutation would track page age and page_settled would be
    // false for every early step — a feature correlated with step_index
    // rather than with real instability.
    //
    // Post-load async rendering (framework hydration, a component mounting,
    // a fetch resolving) is exactly the instability this feature exists to
    // catch, and that is all recorded.
    if (document.readyState === "loading") {
      parseTimeRecords += records.length;
      return;
    }

    var t = now();
    lastGlobalMutation = t;
    for (var i = 0; i < records.length; i++) {
      var r = records[i];
      mutations.push({ n: r.target, t: t });
      // An added node's own subtree is new; record the node itself so a
      // freshly rendered target reads as recently mutated rather than stale.
      if (r.addedNodes && r.addedNodes.length) {
        for (var j = 0; j < r.addedNodes.length && j < 8; j++) {
          mutations.push({ n: r.addedNodes[j], t: t });
        }
      }
    }
    pruneMutations(t);
  });

  function startObserver() {
    try {
      observer.observe(document, {
        subtree: true,
        childList: true,
        attributes: true,
        characterData: true
      });
    } catch (e) {
      // document not ready yet; retry once the documentElement exists
      document.addEventListener("readystatechange", startObserver, { once: true });
    }
  }
  startObserver();

  // A navigation resets the page; ms_since_navigation is also measured
  // CDP-side from Page.frameNavigated, but keeping an in-page epoch means the
  // probe still reports it when running without CDP (real-world pages).
  window.addEventListener("pageshow", function () { navEpoch = now(); });
  window.addEventListener("popstate", function () { navEpoch = now(); });

  // ---------------------------------------------------------------------
  // Observation marks
  //
  // scroll_offset_delta and snapshot_age_ms are both "how much has changed
  // since the observation the agent is acting on was taken". The harness calls
  // markObservation() at snapshot time; probe() diffs against that mark.
  // ---------------------------------------------------------------------

  var observationMark = null;

  function scrollSignature(el) {
    var sig = [];
    var node = el;
    while (node && node !== document.documentElement) {
      if (node.scrollHeight > node.clientHeight || node.scrollWidth > node.clientWidth) {
        sig.push({ n: node, x: node.scrollLeft, y: node.scrollTop });
      }
      node = node.parentElement;
    }
    sig.push({ n: document.documentElement, x: window.scrollX, y: window.scrollY });
    return sig;
  }

  function markObservation() {
    observationMark = {
      t: now(),
      x: window.scrollX,
      y: window.scrollY,
      containers: []
    };
    return observationMark.t;
  }

  // ---------------------------------------------------------------------
  // Element resolution
  // ---------------------------------------------------------------------

  function resolve(target) {
    if (!target) return null;
    if (typeof target !== "string") return target.nodeType === 1 ? target : null;
    // Try as a selector first, then as a BrowserGym bid, then as an id.
    var el = null;
    try { el = document.querySelector(target); } catch (e) { el = null; }
    if (el) return el;
    var escaped = String(target).replace(/"/g, '\\"');
    var attempts = [
      '[bid="' + escaped + '"]',
      '[data-bid="' + escaped + '"]',
      '[browsergym_id="' + escaped + '"]',
      '[data-testid="' + escaped + '"]'
    ];
    for (var i = 0; i < attempts.length; i++) {
      try {
        el = document.querySelector(attempts[i]);
        if (el) return el;
      } catch (e) { /* malformed selector, keep trying */ }
    }
    try { return document.getElementById(target); } catch (e) { return null; }
  }

  // ---------------------------------------------------------------------
  // Hit testing
  // ---------------------------------------------------------------------

  // elementFromPoint stops at a shadow boundary. Descending through open
  // roots is what makes hit_is_self correct for component libraries that use
  // shadow DOM; without it every shadow-hosted control looks occluded.
  function deepElementFromPoint(x, y) {
    var el = document.elementFromPoint(x, y);
    var guard = 0;
    while (el && el.shadowRoot && guard++ < 20) {
      var inner = el.shadowRoot.elementFromPoint ? el.shadowRoot.elementFromPoint(x, y) : null;
      if (!inner || inner === el) break;
      el = inner;
    }
    return el;
  }

  // Node distance from the hit target up to the element: 0 if identical,
  // positive for a descendant, -1 if unrelated. Crosses shadow boundaries via
  // the host so a hit inside a shadow root still resolves to the host.
  function descendantDepth(el, hit) {
    if (!hit) return -1;
    if (hit === el) return 0;
    var node = hit;
    var depth = 0;
    while (node && depth < 64) {
      if (node === el) return depth;
      var next = node.parentElement;
      if (!next) {
        var root = node.getRootNode ? node.getRootNode() : null;
        next = root && root.host ? root.host : null;
      }
      node = next;
      depth++;
    }
    return -1;
  }

  function probePoints(rect) {
    var inset = CORNER_INSET;
    var cx = rect.left + rect.width / 2;
    var cy = rect.top + rect.height / 2;
    return [
      [cx, cy],
      [rect.left + inset, rect.top + inset],
      [rect.right - inset, rect.top + inset],
      [rect.left + inset, rect.bottom - inset],
      [rect.right - inset, rect.bottom - inset]
    ];
  }

  // The viewport, with fallbacks.
  //
  // window.innerWidth/innerHeight can both be 0 — in a background or hidden
  // page, in a static snapshot renderer, and before first layout. That case
  // must not be confused with occlusion: if every probe point looks offscreen
  // then hit_is_self reads false, the router learns "hidden page" as
  // "occluded element", and the signal is poisoned by a rendering condition
  // that has nothing to do with the page's structure. So an unusable viewport
  // yields null (unknown) and an explicit error, never false.
  function viewportSize() {
    var w = window.innerWidth || 0;
    var h = window.innerHeight || 0;
    if (!w || !h) {
      var docEl = document.documentElement;
      w = w || (docEl && docEl.clientWidth) || 0;
      h = h || (docEl && docEl.clientHeight) || 0;
    }
    if ((!w || !h) && window.visualViewport) {
      w = w || window.visualViewport.width || 0;
      h = h || window.visualViewport.height || 0;
    }
    return { w: w, h: h, usable: w > 0 && h > 0 };
  }

  function inViewport(x, y) {
    var vp = viewportSize();
    if (!vp.usable) return false;
    return x >= 0 && y >= 0 && x < vp.w && y < vp.h;
  }

  // ---------------------------------------------------------------------
  // Accessible name (cheap approximation)
  //
  // The authoritative name comes from CDP Accessibility. This local version
  // exists only for sibling_name_collisions, which needs a name for every
  // candidate in the viewport and cannot afford a round trip per element.
  // ---------------------------------------------------------------------

  function cheapAccName(el) {
    if (!el || el.nodeType !== 1) return "";
    var label = el.getAttribute && el.getAttribute("aria-label");
    if (label && label.trim()) return label.trim();
    var labelledby = el.getAttribute && el.getAttribute("aria-labelledby");
    if (labelledby) {
      var parts = [];
      labelledby.split(/\s+/).forEach(function (id) {
        var ref = document.getElementById(id);
        if (ref) parts.push((ref.innerText || ref.textContent || "").trim());
      });
      var joined = parts.join(" ").trim();
      if (joined) return joined;
    }
    if (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT") {
      if (el.labels && el.labels.length) {
        var fromLabel = (el.labels[0].innerText || "").trim();
        if (fromLabel) return fromLabel;
      }
      var ph = el.getAttribute("placeholder");
      if (ph && ph.trim()) return ph.trim();
    }
    if (el.tagName === "IMG") {
      var alt = el.getAttribute("alt");
      if (alt && alt.trim()) return alt.trim();
    }
    var text = (el.innerText || el.textContent || "").trim();
    if (text) return text.replace(/\s+/g, " ").slice(0, 200);
    var title = el.getAttribute && el.getAttribute("title");
    return title ? title.trim() : "";
  }

  function normaliseName(s) {
    return (s || "").toLowerCase().replace(/\s+/g, " ").trim();
  }

  // Normalised edit similarity, 1 - levenshtein/maxlen. Bounded input length
  // keeps this O(200*200) worst case, which is nothing next to a layout flush.
  function editSimilarity(a, b) {
    a = normaliseName(a).slice(0, 200);
    b = normaliseName(b).slice(0, 200);
    if (!a && !b) return 1;
    if (!a || !b) return 0;
    if (a === b) return 1;
    var m = a.length, n = b.length;
    var prev = new Array(n + 1), cur = new Array(n + 1);
    for (var j = 0; j <= n; j++) prev[j] = j;
    for (var i = 1; i <= m; i++) {
      cur[0] = i;
      for (var k = 1; k <= n; k++) {
        var cost = a.charCodeAt(i - 1) === b.charCodeAt(k - 1) ? 0 : 1;
        cur[k] = Math.min(prev[k] + 1, cur[k - 1] + 1, prev[k - 1] + cost);
      }
      var swap = prev; prev = cur; cur = swap;
    }
    return 1 - prev[n] / Math.max(m, n);
  }

  function countNameCollisions(el, name) {
    var target = normaliseName(name);
    if (!target) return 0;
    var nodes;
    try { nodes = document.querySelectorAll(INTERACTIVE_SELECTOR); } catch (e) { return 0; }
    var count = 0;
    var vp = viewportSize();
    var vw = vp.w, vh = vp.h;
    for (var i = 0; i < nodes.length && i < 1500; i++) {
      var other = nodes[i];
      if (other === el) continue;
      var r = other.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) continue;
      if (r.bottom < 0 || r.right < 0 || r.top > vh || r.left > vw) continue;
      if (normaliseName(cheapAccName(other)) === target) count++;
    }
    return count;
  }

  // ---------------------------------------------------------------------
  // Feature computation
  // ---------------------------------------------------------------------

  function rectIntersectionFraction(rect) {
    var vp = viewportSize();
    if (!vp.usable) return null; // unknown, not "outside the viewport"
    var w = Math.max(0, Math.min(rect.right, vp.w) - Math.max(rect.left, 0));
    var h = Math.max(0, Math.min(rect.bottom, vp.h) - Math.max(rect.top, 0));
    var area = rect.width * rect.height;
    return area > 0 ? (w * h) / area : 0;
  }

  function hasAriaHiddenAncestor(el) {
    var node = el;
    var guard = 0;
    while (node && guard++ < 64) {
      if (node.getAttribute && node.getAttribute("aria-hidden") === "true") return true;
      var next = node.parentElement;
      if (!next) {
        var root = node.getRootNode ? node.getRootNode() : null;
        next = root && root.host ? root.host : null;
      }
      node = next;
    }
    return false;
  }

  function hasClickHandlerHint(el) {
    var node = el;
    var guard = 0;
    while (node && guard++ < 8) {
      if (node.onclick) return true;
      if (node.getAttribute) {
        if (node.getAttribute("onclick") !== null) return true;
        if (node.getAttribute("role") !== null) return true;
        if (node.getAttribute("tabindex") !== null) return true;
      }
      node = node.parentElement;
    }
    return false;
  }

  function hasRunningAnimation(el) {
    try {
      if (el.getAnimations && el.getAnimations().length > 0) return true;
    } catch (e) { /* not supported */ }
    var node = el.parentElement;
    var guard = 0;
    while (node && guard++ < 8) {
      try {
        var anims = node.getAnimations ? node.getAnimations() : [];
        for (var i = 0; i < anims.length; i++) {
          if (anims[i].playState === "running") return true;
        }
      } catch (e) { /* ignore */ }
      var cs = getComputedStyle(node);
      if (cs && cs.transitionDuration && cs.transitionDuration !== "0s") {
        // A declared transition is not a running one; only count it when the
        // element also mutated recently, which the freshness features cover.
      }
      node = node.parentElement;
    }
    return false;
  }

  function findOpenModal() {
    var modal = null;
    try { modal = document.querySelector("dialog[open], [aria-modal='true']"); } catch (e) { modal = null; }
    if (modal) return modal;
    try { modal = document.querySelector(":modal"); } catch (e) { modal = null; }
    return modal;
  }

  function subtreeMutationStats(el) {
    var t = now();
    pruneMutations(t);
    var lastTouch = -1;
    var count1s = 0;
    for (var i = mutations.length - 1; i >= 0; i--) {
      var rec = mutations[i];
      var node = rec.n;
      var contained = false;
      if (node === el) {
        contained = true;
      } else if (node.nodeType === 1 || node.nodeType === 3) {
        try { contained = el.contains(node); } catch (e) { contained = false; }
      }
      if (!contained) continue;
      if (rec.t > lastTouch) lastTouch = rec.t;
      if (t - rec.t <= 1000) count1s++;
    }
    return {
      ms_since_last_mutation: lastTouch < 0 ? null : t - lastTouch,
      mutation_count_1s: count1s,
      ms_since_last_global_mutation: lastGlobalMutation < 0 ? null : t - lastGlobalMutation
    };
  }

  function probe(target, opts) {
    var started = now();
    opts = opts || {};
    var errors = [];
    var el = resolve(target);
    if (!el) {
      return {
        probe_ok: false,
        probe_errors: ["element_not_resolved:" + String(target).slice(0, 80)],
        selector: typeof target === "string" ? target : null,
        probe_in_page_ms: now() - started
      };
    }

    var out = { probe_ok: true, probe_errors: errors };
    out.selector = typeof target === "string" ? target : null;

    // --- geometry -------------------------------------------------------
    var rect = el.getBoundingClientRect();
    out.rect_area_px = rect.width * rect.height;
    out.rect_min_dim = Math.min(rect.width, rect.height);
    out.rect_in_viewport = rectIntersectionFraction(rect);
    out.client_rect = {
      x: rect.left, y: rect.top, width: rect.width, height: rect.height
    };

    // --- occlusion / hit testing ---------------------------------------
    var viewport = viewportSize();
    out.viewport_usable = viewport.usable;

    if (!viewport.usable) {
      // Unknown, not occluded. See viewportSize().
      out.hit_is_self = null;
      out.hit_is_descendant_depth = null;
      out.corner_hit_agreement = null;
      out.occluder_tag = null;
      out.occluder_is_fixed = null;
      out.occluder_z_index = null;
      errors.push("viewport_unavailable");
    } else {
      var points = probePoints(rect);
      var agree = 0;
      var tested = 0;
      var centreHit = null;
      var centreTested = false;
      for (var i = 0; i < points.length; i++) {
        var px = points[i][0], py = points[i][1];
        if (!inViewport(px, py)) continue;
        tested++;
        var hit = deepElementFromPoint(px, py);
        if (i === 0) { centreHit = hit; centreTested = true; }
        if (descendantDepth(el, hit) >= 0) agree++;
      }
      out.corner_hit_agreement = tested > 0 ? agree / tested : null;

      if (tested === 0) {
        // The element is scrolled out of view. That is a real, informative
        // condition — distinct from an unusable viewport — and the geometry
        // features (rect_in_viewport) carry it, so the hit features stay null
        // rather than claiming an occluder that was never tested.
        out.hit_is_self = null;
        out.hit_is_descendant_depth = null;
        out.occluder_tag = null;
        out.occluder_is_fixed = null;
        out.occluder_z_index = null;
        errors.push("all_probe_points_offscreen");
      } else {
        // The centre point is the one the agent's click will land on, so
        // hit_is_self is defined on it specifically, not on the aggregate.
        if (!centreTested) {
          out.hit_is_self = null;
          out.hit_is_descendant_depth = null;
          out.occluder_tag = null;
          out.occluder_is_fixed = null;
          out.occluder_z_index = null;
          errors.push("centre_point_offscreen");
        } else {
          var depth = descendantDepth(el, centreHit);
          out.hit_is_self = depth >= 0;
          out.hit_is_descendant_depth = depth;
          if (!out.hit_is_self && centreHit) {
            var cls = (centreHit.classList && centreHit.classList.length)
              ? centreHit.classList[0] : "";
            out.occluder_tag = centreHit.tagName.toLowerCase() + (cls ? "." + cls : "");
            var ocs = getComputedStyle(centreHit);
            out.occluder_is_fixed = ocs.position === "fixed" || ocs.position === "sticky";
            var z = parseInt(ocs.zIndex, 10);
            out.occluder_z_index = isNaN(z) ? 0 : z;
          } else if (!out.hit_is_self) {
            // The point is inside the viewport but nothing was hit at all:
            // pointer-events:none over the whole stack, or a detached layer.
            out.occluder_tag = "none";
            out.occluder_is_fixed = null;
            out.occluder_z_index = null;
            errors.push("hit_test_returned_null");
          } else {
            out.occluder_tag = null;
            out.occluder_is_fixed = false;
            out.occluder_z_index = null;
          }
        }
      }
    }

    // --- semantic -------------------------------------------------------
    var cs = getComputedStyle(el);
    var text = (el.innerText || el.textContent || "").trim().replace(/\s+/g, " ");
    out.element_text = text.slice(0, 300);
    out.local_acc_name = cheapAccName(el);
    out.local_name_is_generic = !!GENERIC_NAMES[normaliseName(out.local_acc_name)];
    out.aria_hidden_ancestor = hasAriaHiddenAncestor(el);
    out.is_div_with_pointer =
      (el.tagName === "DIV" || el.tagName === "SPAN") && cs.cursor === "pointer";
    out.has_click_handler_hint = hasClickHandlerHint(el);
    out.in_shadow_root = !!(el.getRootNode && el.getRootNode() instanceof ShadowRoot);
    out.in_iframe = window !== window.top;
    out.sibling_name_collisions = countNameCollisions(el, out.local_acc_name);
    out.tag_name = el.tagName.toLowerCase();
    out.dom_role = el.getAttribute ? el.getAttribute("role") : null;
    out.dom_disabled = !!(el.disabled || (el.getAttribute && el.getAttribute("aria-disabled") === "true"));

    // --- freshness ------------------------------------------------------
    var stats = subtreeMutationStats(el);
    out.ms_since_last_mutation = stats.ms_since_last_mutation;
    out.ms_since_last_global_mutation = stats.ms_since_last_global_mutation;
    out.mutation_count_1s = stats.mutation_count_1s;
    out.has_running_animation = hasRunningAnimation(el);
    out.ms_since_navigation_in_page = now() - navEpoch;
    // page_settled is derived in probe.py, which also knows the in-flight
    // request count. The mutation half of the condition is reported here.
    out.mutations_quiet = stats.ms_since_last_global_mutation === null ||
      stats.ms_since_last_global_mutation >= SETTLE_MS;

    // --- scroll drift since the observation -----------------------------
    if (observationMark) {
      var dx = Math.abs(window.scrollX - observationMark.x);
      var dy = Math.abs(window.scrollY - observationMark.y);
      out.scroll_offset_delta = Math.max(dx, dy);
      out.observation_age_ms = now() - observationMark.t;
    } else {
      out.scroll_offset_delta = null;
      out.observation_age_ms = null;
    }

    // --- modal ----------------------------------------------------------
    var modal = findOpenModal();
    out.modal_open = !!modal;
    out.target_inside_modal = !!(modal && (modal === el || modal.contains(el)));

    // --- viewport -------------------------------------------------------
    out.viewport_w = window.innerWidth;
    out.viewport_h = window.innerHeight;
    out.dpr = window.devicePixelRatio;

    out.probe_in_page_ms = now() - started;
    return out;
  }

  // ---------------------------------------------------------------------
  // State snapshot
  //
  // The primary label asks whether the page changed in a way consistent with
  // the intended action. That needs the same handful of state bits read twice,
  // once before dispatch and once after, so both reads must come from one
  // function — a label whose two halves are computed differently is a label
  // that encodes the difference between the two code paths.
  // ---------------------------------------------------------------------

  function nearestLandmark(el) {
    var node = el;
    var guard = 0;
    while (node && guard++ < 64) {
      if (node.matches) {
        try {
          if (node.matches(
            'main,[role=main],section,form,dialog,[role=dialog],[role=region],article,nav,[role=navigation]'
          )) return node;
        } catch (e) { /* ignore */ }
      }
      node = node.parentElement;
    }
    return document.body;
  }

  function stateSnapshot(target) {
    var el = resolve(target);
    var t = now();
    var snap = {
      t: t,
      element_present: !!el,
      banner_present: !!document.getElementById("cb-success-banner"),
      task_done_attr: document.documentElement.dataset
        ? document.documentElement.dataset.taskDone === "true"
        : false,
      task_done_global: window.__COMPONENT_BENCH_TASK_DONE__ === true,
      dialog_count: 0,
      modal_open: !!findOpenModal(),
      url: location.href,
      doc_mutation_t: lastGlobalMutation
    };

    try {
      snap.dialog_count = document.querySelectorAll(
        "dialog[open],[role=dialog],[aria-modal='true']"
      ).length;
    } catch (e) { snap.dialog_count = 0; }

    if (el) {
      snap.aria_expanded = el.getAttribute ? el.getAttribute("aria-expanded") : null;
      snap.aria_checked = el.getAttribute ? el.getAttribute("aria-checked") : null;
      snap.aria_selected = el.getAttribute ? el.getAttribute("aria-selected") : null;
      snap.aria_pressed = el.getAttribute ? el.getAttribute("aria-pressed") : null;
      snap.value = typeof el.value === "string" ? el.value : null;
      snap.checked = typeof el.checked === "boolean" ? el.checked : null;
      snap.text = (el.innerText || el.textContent || "").trim().slice(0, 200);
      snap.class_name = el.className && el.className.toString
        ? el.className.toString().slice(0, 200)
        : "";
      var landmark = nearestLandmark(el);
      snap.landmark_text_len = landmark
        ? (landmark.innerText || "").length
        : null;
      snap.landmark_child_count = landmark
        ? landmark.querySelectorAll("*").length
        : null;
      // Mutation timestamp inside the landmark, used for condition (2) of the
      // label: "the DOM subtree under the target's nearest landmark mutated".
      snap.landmark_mutation_t = landmark
        ? subtreeMutationStats(landmark).ms_since_last_mutation
        : null;
      var rect = el.getBoundingClientRect();
      snap.rect = { x: rect.left, y: rect.top, width: rect.width, height: rect.height };
    }
    return snap;
  }

  // Which element a click at the target's centre would actually reach. The
  // label's condition (3) rejects a dispatch whose click would land on a
  // different element, and this is the pre-flight hit-test that decides it.
  function preflight(target) {
    var el = resolve(target);
    if (!el) return { ok: false, reason: "element_not_resolved" };
    var rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return { ok: false, reason: "zero_area_rect" };
    var cx = rect.left + rect.width / 2;
    var cy = rect.top + rect.height / 2;
    if (!inViewport(cx, cy)) return { ok: false, reason: "centre_offscreen" };
    var hit = deepElementFromPoint(cx, cy);
    var depth = descendantDepth(el, hit);
    if (depth < 0) {
      var cls = (hit && hit.classList && hit.classList.length) ? hit.classList[0] : "";
      return {
        ok: false,
        reason: "hit_test_mismatch",
        hit_tag: hit ? hit.tagName.toLowerCase() + (cls ? "." + cls : "") : null
      };
    }
    return { ok: true, reason: null, depth: depth };
  }

  window.__glance = {
    version: VERSION,
    probe: probe,
    markObservation: markObservation,
    stateSnapshot: stateSnapshot,
    preflight: preflight,
    resolve: resolve,
    // Exposed for the harness and for tests; not features.
    _state: function () {
      return {
        mutation_records: mutations.length,
        parse_time_records_ignored: parseTimeRecords,
        ready_state: document.readyState,
        ms_since_last_global_mutation:
          lastGlobalMutation < 0 ? null : now() - lastGlobalMutation,
        has_observation_mark: !!observationMark
      };
    }
  };
})();
