/**
 * Test harness for the TagPup and TagTuner web interfaces.
 *
 * Both apps are a single `DOMContentLoaded` closure with no exports, so nothing inside
 * them can be imported directly. Rather than refactor 6,400 lines of working UI code,
 * these tests load the real page into jsdom, stub the network, and drive the app the
 * way a user does -- through DOM events. What is under test is the shipped file.
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
  tagpup: { dir: "gui_tagpup", defaultDb: "photo_index" },
  tagtuner: { dir: "gui", defaultDb: "photo_index" },
};

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
      );
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
 * @param {"tagpup"|"tagtuner"} appName
 * @param {{url?: string, server?: FakeServer, t?: object}} options
 */
export async function loadApp(appName, { url, server = new FakeServer(), t } = {}) {
  const app = APPS[appName];
  if (!app) throw new Error(`unknown app: ${appName}`);

  const appDir = path.join(REPO_ROOT, app.dir);
  const html = fs.readFileSync(path.join(appDir, "index.html"), "utf8");
  const script = fs.readFileSync(path.join(appDir, "app.js"), "utf8");

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
  // hand would initialise the app twice and double every listener.
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
