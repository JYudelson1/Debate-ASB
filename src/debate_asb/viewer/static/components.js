"use strict";

// Reusable building blocks. Each returns a DOM node and knows nothing about
// which report or protocol it is showing.

function tag(text, kind = "") {
  return element("span", { className: `tag ${kind}`.trim(), text });
}

function verdictClass(verdict) {
  if (verdict === "sabotaged") return "verdict-sabotaged";
  if (verdict === "honest") return "verdict-honest";
  return "";
}

function markdownBlock(markdown) {
  const node = element("div", { className: "markdown" });
  // markdown.html comes from viewer/markdown.py, which renders with raw HTML
  // disabled; never pass unrendered model text here.
  node.innerHTML = markdown.html;
  return node;
}

function collapsible(content, { collapsedHeight = 320 } = {}) {
  const body = element("div", { className: "collapsible-body" }, [content]);
  const toggle = element("button", {
    className: "collapsible-toggle",
    text: "Show more",
    attributes: { type: "button", "aria-expanded": "false" },
  });
  const wrapper = element("div", { className: "collapsible" }, [body, toggle]);
  wrapper.style.setProperty("--collapsed-height", `${collapsedHeight}px`);
  toggle.addEventListener("click", () =>
    setCollapsibleExpanded(wrapper, !wrapper.classList.contains("expanded"))
  );
  // Height is only known once the node is laid out in the document.
  requestAnimationFrame(() => {
    if (body.scrollHeight <= collapsedHeight + 48) wrapper.classList.add("fits");
  });
  return wrapper;
}

function setCollapsibleExpanded(wrapper, expanded) {
  wrapper.classList.toggle("expanded", expanded);
  const toggle = wrapper.querySelector(":scope > .collapsible-toggle");
  toggle.textContent = expanded ? "Show less" : "Show more";
  toggle.setAttribute("aria-expanded", String(expanded));
}

function expandAllButton(getScope) {
  const button = element("button", {
    className: "small-button",
    text: "Expand all",
    attributes: { type: "button" },
  });
  button.addEventListener("click", () => {
    const expand = button.textContent === "Expand all";
    getScope()
      .querySelectorAll(".collapsible:not(.fits)")
      .forEach((wrapper) => setCollapsibleExpanded(wrapper, expand));
    button.textContent = expand ? "Collapse all" : "Expand all";
  });
  return button;
}

function sectionHeader(title, actions = []) {
  return element("div", { className: "section-header" }, [
    element("h2", { text: title }),
    actions.length ? element("div", { className: "section-actions" }, actions) : null,
  ]);
}

// --------------------------------------------------------------------------
// Diagnostics
// --------------------------------------------------------------------------

const SEVERITIES = {
  error: { icon: "✕", label: "Error", plural: "errors" },
  warning: { icon: "⚠", label: "Warning", plural: "warnings" },
  info: { icon: "ℹ", label: "Note", plural: "notes" },
};
const SEVERITY_ORDER = ["error", "warning", "info"];
const PARTICIPANT_NAMES = {
  sabotaged: "SABOTAGED debater",
  clean: "CLEAN debater",
  judge: "judge",
  extractor: "extractor",
};

function describeLocation(diagnostic) {
  const parts = [];
  if (diagnostic.bundle != null) parts.push(`Bundle ${diagnostic.bundle}`);
  if (diagnostic.round != null) parts.push(`round ${diagnostic.round}`);
  if (diagnostic.participant) {
    parts.push(PARTICIPANT_NAMES[diagnostic.participant] || diagnostic.participant);
  }
  return parts.join(" · ");
}

function severityCounts(diagnostics) {
  const counts = { error: 0, warning: 0, info: 0 };
  for (const diagnostic of diagnostics) counts[diagnostic.severity] += 1;
  return counts;
}

function worstSeverity(diagnostics) {
  const counts = severityCounts(diagnostics);
  return SEVERITY_ORDER.find((severity) => counts[severity] > 0) || null;
}

function severityBadges(diagnostics, { includeInfo = false } = {}) {
  const counts = severityCounts(diagnostics);
  const shown = SEVERITY_ORDER.filter(
    (severity) => counts[severity] && (includeInfo || severity !== "info")
  );
  if (!shown.length) return null;
  return element("span", { className: "severity-badges" }, shown.map((severity) =>
    element("span", {
      className: `severity-badge ${severity}`,
      text: `${SEVERITIES[severity].icon} ${counts[severity]}`,
      attributes: { title: plural(counts[severity], SEVERITIES[severity].label.toLowerCase()) },
    })
  ));
}

function diagnosticTitle(diagnostic) {
  return `${diagnostic.title}${diagnostic.count > 1 ? ` (×${diagnostic.count})` : ""}`;
}

function diagnosticCard(diagnostic, { showLocation = true } = {}) {
  const meta = SEVERITIES[diagnostic.severity];
  const location = showLocation ? describeLocation(diagnostic) : "";
  return element("div", { className: `diagnostic ${diagnostic.severity}` }, [
    element("div", { className: "diagnostic-heading" }, [
      element("span", { className: "diagnostic-icon", text: meta.icon, attributes: { "aria-label": meta.label } }),
      element("strong", { text: diagnosticTitle(diagnostic) }),
      location ? element("span", { className: "diagnostic-location", text: location }) : null,
    ]),
    element("p", { text: diagnostic.detail }),
    diagnostic.evidence
      ? element("details", {}, [
          element("summary", { text: "Show evidence" }),
          element("pre", { text: diagnostic.evidence }),
        ])
      : null,
  ]);
}

function diagnosticCallout(diagnostics) {
  if (!diagnostics.length) return null;
  return element("ul", { className: "diagnostic-callout" }, diagnostics.map((diagnostic) =>
    element("li", {
      className: diagnostic.severity,
      text: `${SEVERITIES[diagnostic.severity].icon} ${diagnosticTitle(diagnostic)}`,
      attributes: { title: diagnostic.detail },
    })
  ));
}

// Errors and warnings as full cards; notes folded away so they don't drown them.
function diagnosticList(diagnostics, options = {}) {
  const prominent = diagnostics.filter((diagnostic) => diagnostic.severity !== "info");
  const notes = diagnostics.filter((diagnostic) => diagnostic.severity === "info");
  return [
    ...prominent.map((diagnostic) => diagnosticCard(diagnostic, options)),
    notes.length
      ? element("details", { className: "diagnostic-notes" }, [
          element("summary", { text: plural(notes.length, "note") }),
          ...notes.map((diagnostic) => diagnosticCard(diagnostic, options)),
        ])
      : null,
  ].filter(Boolean);
}
