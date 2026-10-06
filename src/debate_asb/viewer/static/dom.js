"use strict";

const byId = (id) => document.getElementById(id);

function element(tag, options = {}, children = []) {
  const node = document.createElement(tag);
  if (options.className) node.className = options.className;
  if (options.text !== undefined && options.text !== null) {
    node.textContent = String(options.text);
  }
  for (const [key, value] of Object.entries(options.attributes || {})) {
    node.setAttribute(key, String(value));
  }
  for (const [event, handler] of Object.entries(options.on || {})) {
    node.addEventListener(event, handler);
  }
  for (const child of Array.isArray(children) ? children : [children]) {
    if (child) node.appendChild(child);
  }
  return node;
}

function replaceChildren(id, children) {
  byId(id).replaceChildren(...children.filter(Boolean));
}

function pretty(value) {
  return JSON.stringify(value, null, 2);
}

function shortText(value, length = 150) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > length ? `${text.slice(0, length)}…` : text;
}

function plural(count, noun) {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}
