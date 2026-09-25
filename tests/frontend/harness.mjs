/**
 * Test harness for the TagPup and TagTuner web interfaces.
 *
 * Each page is ES modules started from index.html (`<script type="module"
 * src="main.js">`), whose main closure runs on `DOMContentLoaded`. These tests load
 * the real page into jsdom, its modules as one script (below), stub the network, and
 * drive the app the way a user does -- through DOM events. What is under test is the
 * shipped files.
 *
 * The only dependency is jsdom; the runner is node's built-in `node --test`.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { JSDOM, VirtualConsole } from "jsdom";

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = path.resolve(HERE, "..", "..");

export const APPS = {
  tagpup: { dir: "web/tagpup", defaultDb: "photo_index" },
  tagtuner: { dir: "web/tuner", defaultDb: "photo_index" },
};

// ---- A page's modules, as one script ----------------------------------------------
//
// jsdom cannot load ES modules, a bundler is a build step this project does not have,
// and modules run under node would keep their timers on node's clock, which closing a
// page's window cannot stop (docs/ARCHITECTURE.md, phase 6, decided 2026-09-25). So a
// page's modules are put in the order the browser runs them -- each module's imports
// before it, in the order it names them -- with the import lines dropped and the
// `export` words taken off, and evaluated in the page's window as one strict script.
// Each module runs in a function of its own, handed its imports and returning its
// exports, so each has its own scope, as in a browser.

/** Where the modules both pages share live: the server serves them at common/. */
export const COMMON_DIR = path.join(REPO_ROOT, "web", "common");

/** The page's own URL, which every module name is resolved against, as a browser does. */
const PAGE_URL = "http://page/";

/**
 * `import ... from "./x.js";` or `import "./x.js";`, anywhere a statement starts, over
 * as many lines as the braces take.
 */
const IMPORT = /^import\s+(?:([\w$\s{},*]+?)\s+from\s+)?(["'])([^"']+)\2\s*;?[ \t]*$/gm;

/** A declaration at the top of a module: what it may export. */
const DECLARATION = /^(export\s+)?(?:(?:async\s+)?function\s*\*?\s*|class\s+|(?:const|let|var)\s+)([\w$]+)/gm;

/** A top-level `const {a, b} = ...` or `let [a] = ...`: names the loader cannot read. */
const DESTRUCTURING = /^(?:export\s+)?(?:const|let|var)\s*[{[]/m;

/** Any other `export`: default, lists, re-exports, which the loader does not take. */
const OTHER_EXPORT = /^export\s+(?!(?:async\s+)?function\b|class\b|const\b|let\b|var\b)/m;

class PageModuleError extends Error {}

/** The file a module URL names: the page's own folder, or web/common/ at common/. */
function fileFor(url, pageDir, commonDir) {
  const { pathname } = new URL(url);
  const common = pathname.match(/^\/common\/([\w-]+\.js)$/);
  if (common) return path.join(commonDir, common[1]);
  const own = pathname.match(/^\/([\w-]+\.js)$/);
  if (own) return path.join(pageDir, own[1]);
  throw new PageModuleError(`${pathname} is not a module the server serves (a page's own, or common/)`);
}

/** The entry module index.html starts: `<script type="module" src="main.js">`. */
export function entryModule(html) {
  const tags = [...html.matchAll(/<script\b([^>]*)>/g)].map((m) => m[1]);
  const modules = tags.filter((attrs) => /\btype=["']module["']/.test(attrs));
  if (tags.length !== 1 || modules.length !== 1) {
    throw new PageModuleError(
      `index.html should start the page with one <script type="module" src="...">; it has ${tags.length} script tag(s), ${modules.length} a module`
    );
  }
  const src = modules[0].match(/\bsrc=["']([^"']+)["']/);
  if (!src) throw new PageModuleError("the page's module script names no src");
  return src[1];
}

/** One module read and taken apart: what it imports, declares and exports. */
function readModule(file, url) {
  const source = fs.readFileSync(file, "utf8");
  const imports = [];
  for (const m of source.matchAll(IMPORT)) {
    const [, clause, , specifier] = m;
    if (!/^\.\.?\//.test(specifier)) {
      throw new PageModuleError(`${url} imports ${specifier}: only relative imports are loaded`);
    }
    const names = [];
    if (clause) {
      const braces = clause.trim().match(/^\{([\s\S]*)\}$/);
      if (!braces) {
        throw new PageModuleError(`${url}: import ${clause.trim()} -- the page loader takes only named imports, { a, b }`);
      }
      for (const name of braces[1].split(",").map((s) => s.trim()).filter(Boolean)) {
        if (!/^[\w$]+$/.test(name)) {
          throw new PageModuleError(`${url}: import { ${name} } -- the page loader takes no renamed import`);
        }
        names.push(name);
      }
    }
    imports.push({ url: new URL(specifier, url).href, names });
  }
  if (DESTRUCTURING.test(source)) {
    throw new PageModuleError(`${url} declares names by destructuring at its top; declare each by name`);
  }
  const other = source.match(OTHER_EXPORT);
  if (other) throw new PageModuleError(`${url}: "${other[0]}..." -- the page loader takes only exported declarations`);
  const declared = [];
  const exported = new Set();
  for (const m of source.matchAll(DECLARATION)) {
    declared.push(m[2]);
    if (m[1]) exported.add(m[2]);
  }
  const body = source.replace(IMPORT, "").replace(/^export\s+/gm, "");
  return { file, url, imports, declared, exported, body };
}

/**
 * A page's modules in the order the browser runs them, each read and taken apart.
 * `pageDir` is the page's folder; `commonDir` is what the server serves at common/.
 */
export function pageModules(pageDir, commonDir = COMMON_DIR) {
  const html = fs.readFileSync(path.join(pageDir, "index.html"), "utf8");
  const ordered = [];
  const state = new Map(); // url -> "visiting" | module
  const visit = (url, from) => {
    const seen = state.get(url);
    if (seen === "visiting") throw new PageModuleError(`${from} and ${url} import each other`);
    if (seen) return seen;
    state.set(url, "visiting");
    const module = readModule(fileFor(url, pageDir, commonDir), url);
    for (const dependency of module.imports) {
      const target = visit(dependency.url, url);
      for (const name of dependency.names) {
        if (!target.exported.has(name)) {
          throw new PageModuleError(`${url} imports ${name} from ${dependency.url}, which does not export it`);
        }
      }
    }
    state.set(url, module);
    ordered.push(module);
    return module;
  };
  visit(new URL(entryModule(html), PAGE_URL).href, "index.html");

  const owner = new Map();
  for (const module of ordered) {
    for (const name of module.declared) if (!owner.has(name)) owner.set(name, module.url);
  }

  // A module using another's top-level name without importing it -- exported or not --
  // fails in a browser. In the
  // tests it fails too -- each module has its own scope -- but only on a path a test
  // runs; this finds it on every path. A name the module binds itself (a parameter, a
  // local) is its own, not a use of the other module's.
  for (const module of ordered) {
    const imported = new Set(module.imports.flatMap((i) => i.names));
    const code = codeOf(module.body);
    const own = boundIn(code);
    for (const [name, where] of owner) {
      if (where === module.url || imported.has(name) || own.has(name) || module.declared.includes(name)) continue;
      // Not after a dot (a property) nor before a colon (an object's key).
      if (new RegExp(`(?<![\\w$.])${name.replace(/\$/g, "\\$")}(?![\\w$]|\\s*:)`).test(code)) {
        throw new PageModuleError(`${module.url} uses ${name} from ${where} without importing it; a browser would not find it`);
      }
    }
  }
  return ordered;
}

/** The names a module binds anywhere in it: declarations, parameters, catch variables. */
function boundIn(code) {
  const names = new Set();
  const add = (list) => {
    for (const part of list.replace(/[{}\[\]]/g, ",").split(",")) {
      const name = part.split("=")[0].replace(/\.\.\./, "").trim();
      if (/^[\w$]+$/.test(name)) names.add(name);
    }
  };
  for (const m of code.matchAll(/\b(?:const|let|var|function\*?|class)\s+([\w$]+)/g)) names.add(m[1]);
  for (const m of code.matchAll(/\b(?:const|let|var)\s*([{[][^=]*)=/g)) add(m[1]);
  for (const m of code.matchAll(/\bfunction\b[^(]*\(([^)]*)\)/g)) add(m[1]);
  for (const m of code.matchAll(/\(([^()]*)\)\s*=>/g)) add(m[1]);
  for (const m of code.matchAll(/(?<![\w$.])([\w$]+)\s*=>/g)) names.add(m[1]);
  for (const m of code.matchAll(/\bcatch\s*\(\s*([\w$]+)/g)) names.add(m[1]);
  return names;
}

/** A module's code with its comments and quoted strings emptied: where names are used. */
function codeOf(body) {
  return body
    .replace(/'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"/g, "''")
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/(^|[^:\\])\/\/.*$/gm, "$1");
}

/** A page's modules as the one strict script the page's window evaluates. */
export function pageScript(pageDir, commonDir = COMMON_DIR) {
  const modules = pageModules(pageDir, commonDir);
  const slot = new Map(modules.map((m, i) => [m.url, `__module${i}`]));
  const parts = modules.map((m) => {
    const imports = m.imports
      .filter((i) => i.names.length)
      .map((i) => `const { ${i.names.join(", ")} } = ${slot.get(i.url)};`)
      .join("\n");
    return [
      `// ---- ${path.relative(REPO_ROOT, m.file).split(path.sep).join("/")}`,
      `const ${slot.get(m.url)} = (() => {`,
      imports,
      m.body,
      `return { ${[...m.exported].join(", ")} };`,
      "})();",
    ].join("\n");
  });
  return `"use strict";\n${parts.join("\n")}\n`;
}

/** Every line of a page's modules, for tests that read the page's source. */
export function pageSource(appName) {
  return pageModules(path.join(REPO_ROOT, APPS[appName].dir)).map((m) => m.body).join("\n");
}

/**
 * What /api/rules answers: the rules of what may be set (tagpup/core/validation.py).
 * tests/test_validation.py holds the file to what the server publishes, and every
 * FakeServer answers it unless a test says otherwise, as every page asks it.
 */
export const PUBLISHED_RULES = JSON.parse(
  fs.readFileSync(path.join(REPO_ROOT, "tests", "validation_rules.json"), "utf8"));

/** A fetch stub that routes by URL substring and records every call. */
export class FakeServer {
  constructor() {
    this.calls = [];
    this.routes = [];
    this.imageRequests = [];
  }

  /**
   * Reply with `body` (JSON-encoded) for any URL containing `match`.
   *
   * `body` may be a function of the URL returning the body or a promise of it, for a
   * reply that has to arrive later -- a scan still running while someone types.
   */
  on(match, body, { status = 200 } = {}) {
    this.routes.push({ match, body, status });
    return this;
  }

  urls() {
    return this.calls.map((c) => c.url);
  }

  /** URLs whose path begins with the given prefix, e.g. "/kr-track/api/". */
  urlsStartingWith(prefix) {
    return this.urls().filter((u) => u.startsWith(prefix));
  }

  lastBody(match) {
    for (let i = this.calls.length - 1; i >= 0; i--) {
      if (this.calls[i].url.includes(match)) return this.calls[i].body;
    }
    return undefined;
  }

  install(window) {
    const self = this;
    window.fetch = function (input, init) {
      const url = typeof input === "string" ? input : String(input && input.url);
      let body;
      if (init && init.body) {
        try {
          body = JSON.parse(init.body);
        } catch {
          body = init.body;
        }
      }
      self.calls.push({ url, method: (init && init.method) || "GET", body });

      // Match on the path regardless of a leading slash: the apps issue both
      // absolute ("/api/x") and relative ("api/x") URLs, and a relative one would
      // otherwise fall through to the default empty reply.
      const route = self.routes.find(
        (r) => url.includes(r.match) || url.includes(r.match.replace(/^\//, ""))
      ) || (url.includes("api/rules") ? { body: PUBLISHED_RULES, status: 200 } : undefined);
      const status = route ? route.status : 200;
      const reply = (payload) => ({
        ok: status >= 200 && status < 300,
        status,
        json: () => Promise.resolve(payload),
        text: () => Promise.resolve(JSON.stringify(payload)),
      });
      const answer = route && typeof route.body === "function"
        ? Promise.resolve(route.body(url)).then(reply)
        : Promise.resolve(reply(route ? route.body : []));
      // An aborted request rejects with AbortError, as a browser's does. Ignoring the
      // signal hid every bug where one request cancelled another's.
      const signal = init && init.signal;
      if (!signal) return answer;
      const aborted = () => new window.DOMException("The operation was aborted.", "AbortError");
      if (signal.aborted) return Promise.reject(aborted());
      return new Promise((resolve, reject) => {
        signal.addEventListener("abort", () => reject(aborted()), { once: true });
        answer.then(resolve, reject);
      });
    };
    return this;
  }
}

/** Windows still open, so a failing test cannot leave timers running forever. */
const openWindows = new Set();

/** Close every open jsdom window, stopping its polling timers. */
export function closeAllApps() {
  for (const window of openWindows) {
    try {
      window.close();
    } catch {
      /* already closed */
    }
  }
  openWindows.clear();
}

/**
 * Load an app into jsdom and run it.
 *
 * Pass the test context as `t` so the window is closed when the test ends. The apps
 * poll on intervals, and an open window keeps node's event loop alive -- without this
 * the test run hangs instead of finishing.
 *
 * `before(window)` runs once the window exists and before the app does: where a test
 * puts what the browser remembers (localStorage) from an earlier visit.
 *
 * @param {"tagpup"|"tagtuner"} appName
 * @param {{url?: string, server?: FakeServer, t?: object, before?: function}} options
 */
export async function loadApp(appName, { url, server = new FakeServer(), t, before } = {}) {
  const app = APPS[appName];
  if (!app) throw new Error(`unknown app: ${appName}`);

  const appDir = path.join(REPO_ROOT, app.dir);
  const html = fs.readFileSync(path.join(appDir, "index.html"), "utf8");
  const script = pageScript(appDir);

  // Silence expected console noise from the app; surface real jsdom errors.
  const virtualConsole = new VirtualConsole();
  const consoleErrors = [];
  virtualConsole.on("jsdomError", (e) => consoleErrors.push(e));
  virtualConsole.on("error", (...args) => consoleErrors.push(args.join(" ")));

  const dom = new JSDOM(html, {
    url: url || `http://localhost:8090/${app.defaultDb}/`,
    runScripts: "outside-only",
    pretendToBeVisual: true,
    virtualConsole,
  });

  const { window } = dom;

  // jsdom does not implement CSS.escape, and the apps use it to build attribute
  // selectors. Without it selectPhoto throws before it can open the details panel,
  // which looks like an application bug and is purely an environment gap.
  if (!window.CSS) window.CSS = {};
  if (typeof window.CSS.escape !== "function") {
    // Enough of the spec for building attribute selectors: backslash-escape
    // every character that is not a safe identifier character. Non-ASCII is
    // left alone, as the real CSS.escape does.
    window.CSS.escape = (value) =>
      String(value).replace(/[^\w\u00A0-\uFFFF-]/g, (ch) => "\\" + ch);
  }

  // jsdom has no scrollIntoView, and both apps call it after moving the selection.
  // Unshimmed it throws, and where that call sits inside a promise chain the rejection
  // is caught by an outer .catch and surfaces as "Error loading people" -- an
  // environment gap wearing the costume of an application bug.
  if (typeof window.Element.prototype.scrollIntoView !== "function") {
    window.Element.prototype.scrollIntoView = function () {};
  }

  openWindows.add(window);
  if (t && typeof t.after === "function") {
    t.after(() => {
      openWindows.delete(window);
      try {
        window.close();
      } catch {
        /* already closed */
      }
    });
  }
  server.install(window);
  if (before) before(window);

  // Record image loads instead of attempting them, and keep the src rewriting
  // interceptor observable.
  Object.defineProperty(window.HTMLImageElement.prototype, "loading", {
    set() {},
    get() {
      return "lazy";
    },
    configurable: true,
  });

  // Evaluate the app before the document finishes parsing when possible, so its
  // DOMContentLoaded listener runs exactly once -- dispatching a second event by
  // hand would initialise the app twice and double every listener. A browser runs a
  // page's modules at the same moment: after parsing, before DOMContentLoaded.
  if (window.document.readyState === "loading") {
    window.eval(script);
    await new Promise((resolve) =>
      window.document.addEventListener("DOMContentLoaded", resolve, { once: true })
    );
  } else {
    window.eval(script);
    window.document.dispatchEvent(
      new window.Event("DOMContentLoaded", { bubbles: true, cancelable: false })
    );
  }

  await flush(window);

  return { dom, window, document: window.document, server, consoleErrors };
}

/** Let pending promise callbacks and timers settle. */
export async function flush(window, times = 3) {
  for (let i = 0; i < times; i++) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}

/** Advance jsdom's clock far enough for a polling interval to fire. */
export async function tick(window, ms) {
  const deadline = Date.now() + 50;
  window.jest_now = ms;
  await new Promise((resolve) => setTimeout(resolve, Math.min(ms, 40)));
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 5));
    break;
  }
}

/** Fire a click, optionally with modifiers, the way a browser would. */
export function click(window, element, { shiftKey = false, ctrlKey = false, metaKey = false } = {}) {
  const event = new window.MouseEvent("click", {
    bubbles: true,
    cancelable: true,
    shiftKey,
    ctrlKey,
    metaKey,
  });
  element.dispatchEvent(event);
  return event;
}

/** A photo record in the shape /api/folder/scan returns. */
export function photoRecord(overrides = {}) {
  const filename = overrides.filename || "IMG_0001.jpg";
  return {
    path: `D:\\Library\\2020\\${filename}`,
    filename,
    tags: [],
    people: [],
    title: "",
    mtime: 1600000000,
    size: 1024,
    year: "2020",
    raw_metadata: {},
    ...overrides,
  };
}

/**
 * Open a folder the way a user does: type the path and commit it.
 *
 * There is no Scan Folder button any more -- choosing a folder opens it -- so tests
 * go through the same `change` event the autocomplete list and the browse dialog do.
 * Routed through here so the next change to that mechanism is one edit, not eight.
 */
export async function openFolder(ctx, folderPath, { settle = 6 } = {}) {
  const input = ctx.document.getElementById("folder-path-input");
  input.value = folderPath;
  input.dispatchEvent(new ctx.window.Event("change", { bubbles: true }));
  await flush(ctx.window, settle);
  return ctx;
}
