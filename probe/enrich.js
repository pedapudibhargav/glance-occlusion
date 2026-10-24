/*
 * Level 2: selective disambiguation.
 *
 * The accessibility tree gives an element its *own* name. People do not
 * disambiguate that way. Shown five buttons all called "Edit", a person reads
 * the row each one sits in — "Billing alias", "Legal alias" — and the
 * ambiguity disappears. The tree carries none of that unless the site author
 * wired up aria-labelledby, and most do not.
 *
 * The usual answers are both bad. Ship the full DOM and you pay ~18,000
 * tokens for a page to recover context you needed for three elements. Ship the
 * bare tree and the agent guesses between identical labels — which is the
 * documented bottleneck on Mind2Web, where element selection fails more often
 * than planning does.
 *
 * The middle ground is to enrich *only what is ambiguous*. Most elements on a
 * page already have a unique name and need nothing. Only the duplicates need a
 * distinguishing phrase, and the distinguishing phrase is already in the DOM —
 * in the row, the heading, the fieldset legend or the column header.
 *
 * So: find the collisions, and spend tokens only on those.
 *
 * Context sources are tried in order of how specific they are, and the first
 * one that actually separates the colliding elements wins. A source that does
 * not separate them is not worth its tokens.
 */
(function () {
  "use strict";

  var MAX_CONTEXT_CHARS = 46;

  var INTERACTIVE =
    'a[href], button, input, select, textarea, summary, ' +
    '[role=button], [role=link], [role=tab], [role=checkbox], [role=radio], ' +
    '[role=menuitem], [role=option], [role=switch], [tabindex], [onclick]';

  function clean(text) {
    return (text || "").replace(/\s+/g, " ").trim();
  }

  function ownName(el) {
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
      var fromLabel = clean(el.labels[0].innerText);
      if (fromLabel) return fromLabel;
    }

    var text = clean(el.innerText);
    if (text) return text.split("\n")[0].slice(0, 70);

    var attr = el.getAttribute
      ? (el.getAttribute("placeholder") || el.getAttribute("title") ||
         el.getAttribute("alt") || el.getAttribute("value") || "")
      : "";
    return clean(attr).slice(0, 70);
  }

  // ---------------------------------------------------------------------
  // Context sources, most specific first.
  //
  // Specific beats general because a phrase that separates two elements is
  // worth more per token than one that merely describes the page. The row an
  // element sits in distinguishes it from its siblings; the page heading does
  // not distinguish it from anything.
  // ---------------------------------------------------------------------

  function rowContext(el) {
    var row = el.closest("tr, li, [role=row], [role=listitem], [role=treeitem]");
    if (!row) return "";
    // The row's own text, minus the element's, so the label does not repeat
    // the thing it is labelling.
    var own = clean(el.innerText);
    var text = clean(row.innerText);
    if (own && text.indexOf(own) === 0) text = clean(text.slice(own.length));
    else if (own) text = clean(text.replace(own, " "));
    return text.split("\n")[0].slice(0, MAX_CONTEXT_CHARS);
  }

  function cellContext(el) {
    // A table cell is identified by its column header, which is what a person
    // reads across to.
    var cell = el.closest("td, th, [role=cell], [role=gridcell]");
    if (!cell) return "";
    var row = cell.closest("tr, [role=row]");
    var table = cell.closest("table, [role=table], [role=grid]");
    if (!row || !table) return "";
    var index = Array.prototype.indexOf.call(row.children, cell);
    var headerRow = table.querySelector("thead tr, tr");
    if (!headerRow || index < 0) return "";
    var header = headerRow.children[index];
    return header ? clean(header.innerText).slice(0, MAX_CONTEXT_CHARS) : "";
  }

  function fieldsetContext(el) {
    var fieldset = el.closest("fieldset, [role=group], [role=radiogroup]");
    if (!fieldset) return "";
    var legend = fieldset.querySelector("legend");
    if (legend) return clean(legend.innerText).slice(0, MAX_CONTEXT_CHARS);
    var labelled = fieldset.getAttribute("aria-label");
    return clean(labelled).slice(0, MAX_CONTEXT_CHARS);
  }

  function sectionContext(el) {
    var section = el.closest(
      "section, article, nav, aside, form, [role=region], [role=navigation], [role=form]"
    );
    if (!section) return "";
    var labelledby = section.getAttribute("aria-labelledby");
    if (labelledby) {
      var ref = document.getElementById(labelledby);
      if (ref) return clean(ref.innerText).slice(0, MAX_CONTEXT_CHARS);
    }
    var aria = section.getAttribute("aria-label");
    if (clean(aria)) return clean(aria).slice(0, MAX_CONTEXT_CHARS);
    var heading = section.querySelector("h1,h2,h3,h4,h5,h6");
    return heading ? clean(heading.innerText).slice(0, MAX_CONTEXT_CHARS) : "";
  }

  function headingContext(el) {
    // Walk backwards through the document for the nearest preceding heading —
    // what a reader would have most recently seen above this control.
    var headings = document.querySelectorAll("h1,h2,h3,h4,h5,h6");
    var best = "";
    for (var i = 0; i < headings.length; i++) {
      var position = el.compareDocumentPosition(headings[i]);
      if (position & Node.DOCUMENT_POSITION_PRECEDING) {
        best = clean(headings[i].innerText);
      } else {
        break;
      }
    }
    return best.slice(0, MAX_CONTEXT_CHARS);
  }

  var CONTEXT_SOURCES = [
    { name: "cell", fn: cellContext },
    { name: "row", fn: rowContext },
    { name: "fieldset", fn: fieldsetContext },
    { name: "section", fn: sectionContext },
    { name: "heading", fn: headingContext },
  ];

  function collect() {
    var nodes;
    try { nodes = document.querySelectorAll(INTERACTIVE); } catch (e) { return []; }
    var out = [];
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var rect = el.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) continue;
      var name = ownName(el);
      out.push({
        el: el,
        bid: el.getAttribute("bid") || String(i),
        name: name,
        role: el.getAttribute("role") || el.tagName.toLowerCase(),
      });
    }
    return out;
  }

  function groupByName(items) {
    var groups = {};
    items.forEach(function (item) {
      var key = (item.name || "").toLowerCase();
      (groups[key] = groups[key] || []).push(item);
    });
    return groups;
  }

  /**
   * Measure how ambiguous this page is, and whether context can fix it.
   *
   * Reports, per context source, how many colliding groups it fully separates.
   * A source only earns its tokens if it makes the colliding elements
   * distinguishable from each other.
   */
  function analyse() {
    var items = collect();
    var groups = groupByName(items);

    var total = items.length;
    var unnamed = 0;
    var ambiguous = 0;
    var collidingGroups = 0;
    var resolvedBy = {};
    CONTEXT_SOURCES.forEach(function (s) { resolvedBy[s.name] = 0; });
    var unresolved = 0;
    var examples = [];

    Object.keys(groups).forEach(function (key) {
      var group = groups[key];
      if (!key) { unnamed += group.length; return; }
      if (group.length < 2) return;

      collidingGroups++;
      ambiguous += group.length;

      var solved = null;
      for (var s = 0; s < CONTEXT_SOURCES.length; s++) {
        var source = CONTEXT_SOURCES[s];
        var seen = {};
        var distinct = true;
        for (var g = 0; g < group.length; g++) {
          var context = source.fn(group[g].el);
          if (!context || seen[context]) { distinct = false; break; }
          seen[context] = true;
        }
        if (distinct) { solved = source.name; break; }
      }

      if (solved) resolvedBy[solved]++;
      else unresolved++;

      if (examples.length < 4) {
        examples.push({
          name: group[0].name.slice(0, 30),
          count: group.length,
          resolved_by: solved || "none",
          sample_context: solved
            ? group.map(function (g) {
                var fn = CONTEXT_SOURCES.filter(function (s) { return s.name === solved; })[0].fn;
                return fn(g.el).slice(0, 30);
              }).slice(0, 3)
            : [],
        });
      }
    });

    return {
      interactive: total,
      unnamed: unnamed,
      ambiguous_elements: ambiguous,
      colliding_groups: collidingGroups,
      resolved_by: resolvedBy,
      unresolved_groups: unresolved,
      examples: examples,
    };
  }

  /**
   * Build the Level 2 observation.
   *
   * Every element gets a line. Only elements whose name collides with another
   * get a context phrase appended, and only from the first source that
   * actually separates them. An element with a unique name costs exactly what
   * it costs in the plain tree.
   */
  function build(options) {
    options = options || {};
    var alwaysContext = !!options.alwaysContext;
    var items = collect();
    var groups = groupByName(items);

    var contextFor = {};
    var contextSourceUsed = {};

    Object.keys(groups).forEach(function (key) {
      var group = groups[key];
      var needsContext = alwaysContext || (key && group.length > 1) || !key;
      if (!needsContext) return;

      for (var s = 0; s < CONTEXT_SOURCES.length; s++) {
        var source = CONTEXT_SOURCES[s];
        var values = group.map(function (item) { return source.fn(item.el); });
        var usable = values.every(function (v) { return !!v; });
        var distinct = new Set(values).size === values.length;
        // For a single unnamed element there is nothing to separate it from,
        // so any context that exists is an improvement.
        if (usable && (distinct || group.length === 1)) {
          group.forEach(function (item, index) {
            contextFor[item.bid] = values[index];
            contextSourceUsed[item.bid] = source.name;
          });
          return;
        }
      }
    });

    var lines = items.map(function (item) {
      var label = item.name;
      var context = contextFor[item.bid];
      if (!label && context) return "[" + item.bid + "] " + item.role + ' in "' + context + '"';
      if (!label) return "[" + item.bid + "] " + item.role;
      if (context) return "[" + item.bid + "] " + item.role + ' "' + label + '" in "' + context + '"';
      return "[" + item.bid + "] " + item.role + ' "' + label + '"';
    });

    return {
      text: lines.join("\n"),
      elements: items.length,
      enriched: Object.keys(contextFor).length,
      sources_used: contextSourceUsed,
    };
  }

  function plain() {
    var items = collect();
    return {
      text: items.map(function (i) {
        return "[" + i.bid + "] " + i.role + (i.name ? ' "' + i.name + '"' : "");
      }).join("\n"),
      elements: items.length,
    };
  }

  window.__glanceEnrich = { analyse: analyse, build: build, plain: plain, version: "1.0.0" };
})();
