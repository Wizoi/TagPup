/**
 * The Tab order of a page as a browser computes it, for jsdom, which has no Tab of its own.
 *
 * The page's stylesheets are put into the document as they are linked (jsdom does not load a
 * <link>), and `display`, `visibility` and `opacity` are cascaded here as a browser does --
 * importance, then specificity, then order (jsdom's getComputedStyle takes the last rule that
 * matches, so a `.hidden { display: none !important }` loses to a later `display: flex`). A
 * control takes the Tab when it is focusable -- natively (a button, a field, a link with an href,
 * a player with controls) or by a tabindex of 0 or more -- not disabled, not inert, and rendered:
 * no `display: none` on it or above it (the `hidden` attribute is one, unless the page's own CSS
 * gives it a display), and `visibility` not hidden. The order is a positive tabindex first, then
 * document order. Opacity does not take a control out of the order: a browser tabs onto a control
 * nobody can see, which `invisibleStops` names. The window is a desktop's, 1400 px wide.
 */
import fs from "node:fs";
import path from "node:path";
import { REPO_ROOT } from "./harness.mjs";

const NATIVE = "a[href], button, input:not([type=hidden]), select, textarea, video[controls], audio[controls], summary, iframe, [contenteditable=''], [contenteditable=true]";
const PROPERTIES = ["display", "visibility", "opacity"];
const WIDTH = 1400;

/** Put the page's linked stylesheets into its document: call from loadApp's `before`. */
export function withStylesheets(appDir) {
  return (window) => {
    const { document } = window;
    for (const link of document.querySelectorAll('link[rel="stylesheet"]')) {
      const href = link.getAttribute("href");
      if (/^https?:/.test(href)) continue;
      const file = href.startsWith("common/")
        ? path.join(REPO_ROOT, "web", href)
        : path.join(REPO_ROOT, "web", appDir, href);
      const style = document.createElement("style");
      style.textContent = fs.readFileSync(file, "utf8");
      document.head.appendChild(style);
    }
  };
}

/** Every element the page gives a `click` listener (or an `onclick`): call from loadApp's `before`. */
export function recordClickTargets(window) {
  const targets = new Set();
  const add = window.EventTarget.prototype.addEventListener;
  window.EventTarget.prototype.addEventListener = function (type, ...rest) {
    if (type === "click" && this instanceof window.Element) targets.add(this);
    return add.call(this, type, ...rest);
  };
  const property = Object.getOwnPropertyDescriptor(window.HTMLElement.prototype, "onclick");
  Object.defineProperty(window.HTMLElement.prototype, "onclick", {
    configurable: true,
    get() { return property.get.call(this); },
    set(value) {
      if (value) targets.add(this);
      property.set.call(this, value);
    },
  });
  window.clickTargets = targets;
  return targets;
}

/** The text of a parenthesised argument starting at `open`, and the index after its `)`. */
function argument(text, open) {
  let depth = 0;
  for (let i = open; i < text.length; i++) {
    if (text[i] === "(") depth++;
    else if (text[i] === ")" && --depth === 0) return [text.slice(open + 1, i), i + 1];
  }
  return [text.slice(open + 1), text.length];
}

/** The selectors of a list, split at its top-level commas. */
function selectors(list) {
  const parts = [];
  let depth = 0, start = 0;
  for (let i = 0; i < list.length; i++) {
    if (list[i] === "(" || list[i] === "[") depth++;
    else if (list[i] === ")" || list[i] === "]") depth--;
    else if (list[i] === "," && depth === 0) { parts.push(list.slice(start, i).trim()); start = i + 1; }
  }
  parts.push(list.slice(start).trim());
  return parts;
}

const add = (a, b) => a.map((n, i) => n + b[i]);
const larger = (a, b) => (a[0] - b[0] || a[1] - b[1] || a[2] - b[2]) >= 0 ? a : b;

/** A selector's specificity [ids, classes, types]; :is/:not/:has count their most specific argument, :where nothing. */
function specificity(selector) {
  let total = [0, 0, 0];
  let rest = "";
  for (let i = 0; i < selector.length;) {
    const functional = /^:(is|not|has|where|matches)\(/.exec(selector.slice(i));
    if (functional) {
      const [inner, next] = argument(selector, i + functional[0].length - 1);
      if (functional[1] !== "where") total = add(total, selectors(inner).map(specificity).reduce(larger, [0, 0, 0]));
      rest += " ";
      i = next;
    } else {
      rest += selector[i++];
    }
  }
  rest = rest.replace(/\[[^\]]*\]/g, () => { total = add(total, [0, 1, 0]); return " "; });
  rest = rest.replace(/::[\w-]+(\([^)]*\))?/g, () => { total = add(total, [0, 0, 1]); return " "; });
  rest = rest.replace(/#[\w-]+/g, () => { total = add(total, [1, 0, 0]); return " "; });
  rest = rest.replace(/(\.[\w-]+|:[\w-]+(\([^)]*\))?)/g, () => { total = add(total, [0, 1, 0]); return " "; });
  rest.replace(/(^|[\s>+~])([a-zA-Z][\w-]*)/g, () => { total = add(total, [0, 0, 1]); return ""; });
  return total;
}

/** Does a media condition hold for a desktop window (min-width and max-width; anything else does not). */
function mediaHolds(text) {
  if (text === "" || text === "all" || text === "screen") return true;
  const conditions = [...text.matchAll(/\((min|max)-width:\s*(\d+)px\)/g)];
  if (conditions.length === 0 || /\b(or|not|,)\b/.test(text)) return false;
  return conditions.every(([, side, px]) => (side === "min" ? WIDTH >= Number(px) : WIDTH <= Number(px)));
}

/** The style rules declaring display, visibility or opacity, each selector with its specificity, in order. */
function rulesOf(document) {
  const sheets = [...document.styleSheets];
  const cached = document.__tabOrderRules;
  if (cached && cached.sheets === sheets.length) return cached.rules;
  const rules = [];
  const walk = (list) => {
    for (const rule of list) {
      if (rule.media && rule.cssRules) {
        if (mediaHolds(rule.media.mediaText.trim())) walk(rule.cssRules);
      } else if (rule.selectorText && rule.style) {
        const declared = PROPERTIES.filter((name) => rule.style.getPropertyValue(name) !== "");
        if (declared.length === 0) continue;
        for (const selector of selectors(rule.selectorText)) {
          if (/::/.test(selector)) continue;
          rules.push({ selector, specificity: specificity(selector), style: rule.style, declared, order: rules.length });
        }
      }
    }
  };
  for (const sheet of sheets) walk(sheet.cssRules);
  document.__tabOrderRules = { sheets: sheets.length, rules };
  return rules;
}

/** Does a declaration ranked [importance, ids, classes, types, order] win over another. */
function beats(rank, other) {
  const i = rank.findIndex((n, at) => n !== other[at]);
  return i >= 0 && rank[i] > other[i];
}

function matches(el, selector) {
  try { return el.matches(selector); } catch { return false; }
}

/** The value of `name` the cascade gives `el` from the page's CSS and its style attribute, or "". */
export function cascaded(el, name) {
  const inline = el.style.getPropertyValue(name);
  if (inline !== "" && el.style.getPropertyPriority(name) === "important") return inline;
  let best = null;
  for (const rule of rulesOf(el.ownerDocument)) {
    if (!rule.declared.includes(name) || !matches(el, rule.selector)) continue;
    const important = rule.style.getPropertyPriority(name) === "important" ? 1 : 0;
    const rank = [important, ...rule.specificity, rule.order];
    if (!best || beats(rank, best.rank)) best = { rank, value: rule.style.getPropertyValue(name) };
  }
  if (best && best.rank[0] === 1) return best.value;
  if (inline !== "") return inline;
  return best ? best.value : "";
}

function displayNone(el) {
  const display = cascaded(el, "display");
  if (display !== "") return display === "none";
  return el.hasAttribute("hidden");
}

function rendered(el) {
  for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
    if (node.hasAttribute("inert") || displayNone(node)) return false;
  }
  for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
    const visibility = cascaded(node, "visibility");
    if (visibility !== "" && visibility !== "inherit") return visibility === "visible";
  }
  return true;
}

function disabled(el) {
  if (!el.matches("button, input, select, textarea")) return false;
  return el.disabled || Boolean(el.closest("fieldset[disabled]"));
}

/** Is the element a Tab stop: focusable, enabled, rendered, its tabindex not negative. */
export function tabStop(el) {
  const index = el.getAttribute("tabindex");
  if (index !== null && Number(index) < 0) return false;
  if (index === null && !el.matches(NATIVE)) return false;
  if (disabled(el)) return false;
  return rendered(el);
}

/** The page's Tab stops, in the order Tab visits them. */
export function tabOrder(document) {
  const stops = [...document.querySelectorAll(`${NATIVE}, [tabindex]`)].filter(tabStop);
  const positive = stops.filter((el) => el.tabIndex > 0).sort((a, b) => a.tabIndex - b.tabIndex);
  return [...positive, ...stops.filter((el) => !(el.tabIndex > 0))];
}

/** How a test names a stop: its id, else its tag and first class, and its data-action. */
export function nameOf(el) {
  if (el.id) return `#${el.id}`;
  const cls = el.classList[0] ? `.${el.classList[0]}` : "";
  const action = el.dataset.action ? `[${el.dataset.action}]` : "";
  return `${el.tagName.toLowerCase()}${cls}${action}`;
}

/** The stops a browser tabs onto that nobody can see: at opacity 0, or inside something that is. */
export function invisibleStops(document) {
  return tabOrder(document).filter((el) => {
    for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
      if (Number(cascaded(node, "opacity") || 1) === 0) return true;
    }
    return false;
  });
}

/** Rendered elements a click acts on that the keyboard cannot reach: not focusable, no tabindex. */
export function mouseOnly(window) {
  return [...window.clickTargets].filter((el) =>
    el.isConnected && !el.matches(NATIVE) && !el.hasAttribute("tabindex") && rendered(el));
}
