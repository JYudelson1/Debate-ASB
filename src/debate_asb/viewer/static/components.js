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
