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
import { after } from "node:test";
import { fileURLToPath } from "node:url";
import { JSDOM, VirtualConsole } from "jsdom";
import { filtered, ratchet, unmadeReply } from "./write-replies.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = path.resolve(HERE, "..", "..");

export const APPS = {
  tagpup: { dir: "web/tagpup", defaultDb: "photo_index" },
  tagtuner: { dir: "web/tuner", defaultDb: "photo_index" },
};

/** Every page: the two apps' pages, each on a library, the Activity page, on none (it covers every library:
 *  tagpup.web.activity_routes). `html` is a page's file when it is not index.html: the phone's page, mobile.html
 *  beside TagPup's index.html (docs/plans/phone-viewer.md). */
export const PAGES = {
  ...APPS,
  activity: { dir: "web/activity", page: "activity" },
  phone: { dir: "web/tagpup", html: "mobile.html", defaultDb: "photo_index" },
};

/** The file a page starts from: its html. */
export function htmlOf(app) {
  return app.html || "index.html";
}

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
 * `pageDir` is the page's folder; `commonDir` is what the server serves at common/; `htmlFile` the page's html.
 */
export function pageModules(pageDir, commonDir = COMMON_DIR, htmlFile = "index.html") {
  const html = fs.readFileSync(path.join(pageDir, htmlFile), "utf8");
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
  visit(new URL(entryModule(html), PAGE_URL).href, htmlFile);

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

/** A template literal's text emptied, its `${...}` expressions kept (they are code; the text is a sentence). */
function emptyTemplates(body) {
  let out = "";
  let i = 0;
  const template = () => {            // i is just past an opening backtick; leaves i on the closing one
    out += "``";
    while (i < body.length && body[i] !== "`") {
      if (body[i] === "\\") { i += 2; continue; }
      if (body[i] === "$" && body[i + 1] === "{") {
        i += 2;
        out += " ";
        let depth = 1;
        while (i < body.length) {
          const c = body[i];
          if (c === "`") { i += 1; template(); i += 1; continue; }
          if (c === "{") depth += 1;
          if (c === "}" && !(depth -= 1)) break;
          out += c;
          i += 1;
        }
        out += " ";
      }
      i += 1;
    }
  };
  while (i < body.length) {
    if (body[i] === "`") { i += 1; template(); } else out += body[i];
    i += 1;
  }
  return out;
}

/** A module's code with its comments and quoted strings emptied: where names are used. */
function codeOf(body) {
  return emptyTemplates(body)
    .replace(/'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"/g, "''")
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/(^|[^:\\])\/\/.*$/gm, "$1");
}

/** A page's modules as the one strict script the page's window evaluates. */
export function pageScript(pageDir, commonDir = COMMON_DIR, htmlFile = "index.html") {
  const modules = pageModules(pageDir, commonDir, htmlFile);
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
  // What each module exports, by its file (web/tagpup/state.js), for a test that has to reach
  // into the page: the page's own state, or the grid it built. Tests only; a browser has no such line.
  const exposed = modules
    .map((m, i) => `${JSON.stringify(path.relative(REPO_ROOT, m.file).split(path.sep).join("/"))}: __module${i}`)
    .join(", ");
  return `"use strict";\n${parts.join("\n")}\nglobalThis.__pageModules = { ${exposed} };\n`;
}

/** A module of the page, as the page itself holds it: `pageExports(window, "web/tagpup/state.js").state`. */
export function pageExports(window, file) {
  return window.__pageModules[file];
}

/** Every line of a page's modules, for tests that read the page's source. */
export function pageSource(appName) {
  const app = PAGES[appName];
  return pageModules(path.join(REPO_ROOT, app.dir), COMMON_DIR, htmlOf(app)).map((m) => m.body).join("\n");
}

/**
 * What /api/rules answers: the rules of what may be set (tagpup/core/validation.py).
 * tests/test_validation.py holds the file to what the server publishes, and every
 * FakeServer answers it unless a test says otherwise, as every page asks it.
 */
export const PUBLISHED_RULES = JSON.parse(
  fs.readFileSync(path.join(REPO_ROOT, "tests", "validation_rules.json"), "utf8"));

const ANSWER = Symbol("answer");

/**
 * A fake route's reply with a status of its own, for a route that answers one request otherwise than the rest, as the
 * real one does: `answer(409, { success: false, changed_on_disk: true, ... })` from a route's function.
 */
export function answer(status, body) {
  return { [ANSWER]: { status, body } };
}

/**
 * A page's save of one photo's title, tags and Date Taken (web/common/photo-write.js postPhotoMetadata): POST
 * /api/photos/write, op `set`. A route matcher of FakeServer (tried before every URL route, so a test's route of the tag
 * writes to /api/photos/write does not answer a save), and of the calls (saveCalls).
 */
export const SAVE = (url, body) => url.includes("/api/photos/write") && Boolean(body) && body.op === "set";

/** A save's body as the page named its one photo in it: {path, id, title, tags, stamp, base, ...the rest it sent}. */
export function savedPhoto(body) {
  const [[path, own]] = Object.entries(body.changes);
  return { path, ...own };
}

/** The saves the page sent, in order, each {url, method, body: savedPhoto}. */
export function saveCalls(server) {
  return server.calls.filter((c) => SAVE(c.url, c.body)).map((c) => ({ ...c, body: savedPhoto(c.body) }));
}

/** The last save's body (savedPhoto), or undefined when the page sent none. */
export function lastSave(server) {
  const sent = saveCalls(server);
  return sent.length ? sent.at(-1).body : undefined;
}

/**
 * A save's replies as the real entry point made them (fixtures/save_replies.json, written by
 * tests/test_tag_a_photo_read_it_back_and_undo.py ASaveFromThePage), each by name, its status in `status`, its one photo
 * named "PHOTO": `no_rename` (a save that wrote), `rename_failed`, `picture_changed`, `changed_on_disk` (refused 409: the
 * file is no longer what the page read), `not_written` (a file ExifTool could not write: a 200 with the photo among its
 * `errors`), `named_a_face` (a person put on a photo whose one face was unnamed: the face named, in `named_faces`). Every
 * fake reply of a save below is one of them, so that its fields are the server's, not a list kept here.
 */
export const SAVE_REPLIES = JSON.parse(fs.readFileSync(path.join(HERE, "fixtures", "save_replies.json"), "utf8"));

/**
 * The reply of a save that wrote, as the real entry point made it (SAVE_REPLIES.no_rename), a route's function of (url,
 * body): its photo's own part (`written[path]`) says the file holds now what this save sent -- its tags and title as the
 * `base`, `new_path` its path (not renamed), a stamp after the one it sent -- `own` over it (mtime, size, base, new_path,
 * taken, rename_failed; `taken` is null unless `own` names it, as the server spells it); `top` over what it
 * says of the write (file_only, with_rows, the faces it changed, pictures_refused). A page that ignores `written` keeps
 * its own base and stamp, which a later save sends and its test reads.
 */
export function saved(own = {}, top = {}) {
  const real = SAVE_REPLIES.no_rename;
  return (url, body) => {
    const sent = savedPhoto(body);
    const tags = (sent.tags || []).slice();
    const stamp = sent.stamp ? { mtime: sent.stamp.mtime + 1, size: sent.stamp.size } : {};
    const wrote = { ...real.written.PHOTO, new_path: sent.path, tags, base: { tags: tags.slice(), title: sent.title || "" },
      ...stamp, ...own };
    return { ...real, ...top, written: { [sent.path]: wrote } };
  };
}

/**
 * A save's reply as the server made it (`reply`, an entry of save_replies.json, its one photo named "PHOTO"), for
 * the photo this save names, `own` over what the server said of it.
 */
export function savedAs(reply, own = {}) {
  return (url, body) => ({ ...reply, written: { [savedPhoto(body).path]: { ...reply.written.PHOTO, ...own } } });
}

/**
 * The reply of a save the server could not write (SAVE_REPLIES.not_written: a 200, nothing written, the photo among its
 * `errors`), named by the path and id this save sent, `why` its sentence.
 */
export function notSaved(why) {
  const real = SAVE_REPLIES.not_written;
  return (url, body) => {
    const { path, id } = savedPhoto(body);
    return answer(SAVE_REPLIES.status.not_written,
      { ...real, errors: real.errors.map((e) => ({ ...e, path, id, why: why ?? e.why })) });
  };
}

/**
 * The reply of a save refused because the file is no longer the one the page read, or the id's photo no longer at its
 * path (SAVE_REPLIES.changed_on_disk: 409, `changed_on_disk`, the envelope beside the error), `why` its sentence when
 * a test names one. Nothing written.
 */
export function changedOnDisk(why) {
  const real = SAVE_REPLIES.changed_on_disk;
  return () => answer(SAVE_REPLIES.status.changed_on_disk, { ...real, ...(why === undefined ? {} : { error: why }) });
}

export const DELETE_REPLIES = JSON.parse(fs.readFileSync(path.join(HERE, "fixtures", "delete_replies.json"), "utf8"));

/**
 * A Delete's reply as the real route made it (POST /api/photo/delete; fixtures/delete_replies.json, written by
 * tests/test_delete_a_photo.py JustLookDelete): `binned`, a photo of a folder the library holds, to the Recycle Bin (no
 * `message`: the page says Ready); `binned_file_only`, one of a folder it does not hold, the file only, said in `message`;
 * `through_this_pc`, one on a share with no Bin, copied to this PC's; `not_kept`, one this PC's full Bin would not keep
 * (400, named in `not_kept`, `bin_full` 1; 0 when its drive's own Bin would not keep it), nothing deleted; `for_good`,
 * that one again, confirmed; `refused`, a file that is not a photo (400). `top` over what it says (its `message`,
 * `error`, `not_kept`, `bin_full`). A delete that raised is the 500 every route makes ({success: false, error}).
 */
export function deletedAs(name, top = {}) {
  const { status, reply } = DELETE_REPLIES[name];
  return () => answer(status, { ...reply, ...top });
}

/**
 * A folder's Time Shift (web/tagpup/rename.js applyTimeShift): POST /api/photos/write, op `shift`. A route matcher of
 * FakeServer, tried before the URL routes as SAVE is.
 */
export const SHIFT = (url, body) => url.includes("/api/photos/write") && Boolean(body) && body.op === "shift";

export const SHIFT_REPLIES = JSON.parse(fs.readFileSync(path.join(HERE, "fixtures", "shift_replies.json"), "utf8"));

/**
 * A Time Shift's reply as the real entry point made it (fixtures/shift_replies.json, written by
 * tests/test_a_write_through_the_one_entry_point.py): `shifted`, two photos written (200); `stopped`, a share that stopped
 * answering at the second of three, the first written (400); `stopped_first`, one that stopped answering at the first of
 * three, none written (400); `refused`, one asked while names are being given from tags (409, `success` and `error`
 * alone: no envelope, so no `written` and no `errors`). Its photos, PHOTO_0, PHOTO_1, ..., are named again by the
 * paths this shift sent, in the order sent; `own[n]` over what it says of photo n (`taken`, as the server spells it),
 * `top` over what it says of the write (`file_only`, `with_rows`: a folder the library does not hold).
 */
export function shiftedAs(name, own = [], top = {}) {
  const { status, reply } = SHIFT_REPLIES[name];
  return (url, body) => {
    const sent = Object.keys(body.changes);
    const named = (key) => sent[Number(key.replace("PHOTO_", ""))] ?? key;
    const shown = {};
    if (reply.written) {
      shown.written = Object.fromEntries(Object.entries(reply.written).map(([key, w]) =>
        [named(key), { ...w, ...(own[Number(key.replace("PHOTO_", ""))] || {}) }]));
    }
    if (reply.errors) shown.errors = reply.errors.map((e) => ({ ...e, path: named(e.path) }));
    return answer(status, { ...reply, ...top, ...shown });
  };
}

export const RENAME_REPLIES = JSON.parse(fs.readFileSync(path.join(HERE, "fixtures", "rename_replies.json"), "utf8"));

/**
 * A folder's Smart Rename's reply as the real route made it (POST /api/folder/rename-photos; fixtures/rename_replies.json,
 * written by tests/test_tag_a_photo_read_it_back_and_undo.py AFolderSmartRenameFromThePage): `chained`, three photos
 * renamed where an old name of one is the new name of another (IMG_0001.jpg -> Regatta - 1.jpg, Regatta - 1.jpg ->
 * Regatta - 2.jpg, Regatta - 2.jpg -> Regatta - 3.jpg; taken 10:00, 10:10, 10:20 on 2020:06:01), each stamp 1 and 1;
 * `swapped`, then Regatta - 1 dated 10:15 and Regatta - 2 10:05, the two Smart-Renamed again and each taking the other's
 * name (the reply's records the folder's three, each stamp 2 and 2); `part_way`, Regatta - 3 (held) and Scans\scan_01.jpg
 * (a subfolder the library does not hold) renamed as "Harbour", the second part stopped: 500, the held one's new name in
 * `updated_paths`, the folder's records in `updated_photos` (each stamp 3 and 3), what changed and how to recover in `error`.
 * Its folder, FOLDER, is named again `folder`, the folder the page test opened; `top` over what it says (the test's own
 * `updated_paths`, `updated_photos`, `error`).
 */
export function renamedAs(name, folder, top = {}) {
  const { status, reply } = RENAME_REPLIES[name];
  const text = JSON.stringify(reply).replaceAll("FOLDER", JSON.stringify(folder).slice(1, -1));
  return () => answer(status, { ...JSON.parse(text), ...top });
}

/** A route matcher of FakeServer for a page's tag change (web/common/photo-write.js): POST /api/photos/write, op `tags`. */
export const TAGS = (url, body) => url.includes("/api/photos/write") && Boolean(body) && body.op === "tags";

export const TAGS_REPLIES = JSON.parse(fs.readFileSync(path.join(HERE, "fixtures", "tags_replies.json"), "utf8"));

/**
 * A tag change's reply as the real entry point made it (fixtures/tags_replies.json, written by
 * tests/test_a_write_through_the_one_entry_point.py): `written`, every photo it named written (200: each photo's
 * `tags` as the file holds them, its stamp); `renamed_since`, refused 409 `changed_on_disk`, the first photo it named
 * no longer at its path, nothing written, the envelope beside the error. The reply is for the photos this request named
 * (the real one's two photos, PHOTO_0 and PHOTO_1, taken for each path of `body.changes`), the counts its own;
 * `own(path, n)` the fields over what it says of the photo at `path` (`tags`, `mtime`, `size`).
 * `not_written`, a share that stopped answering (400): the photo failed, nothing written, the failure said in `error`
 * (the page says that first) and in `errors`, one for each photo of `body.changes` with its id as sent; `said` is the
 * words the test's share says in their place (the real ones name the photo by its number and the share).
 */
export function taggedAs(name, own = () => ({}), said = null) {
  const { status, reply } = TAGS_REPLIES[name];
  return (url, body) => {
    const sent = Object.keys(body.changes);
    if (reply.errors && reply.errors.length) {
      const why = (each) => said || each.why;
      const errors = sent.map((path) => ({ ...reply.errors[0], path, id: body.changes[path].id, why: why(reply.errors[0]) }));
      return answer(status, { ...reply, attempted: sent.length, error_count: sent.length, errors, error: said || reply.error,
        stopped: { ...reply.stopped, of: sent.length } });
    }
    if (!reply.written || !Object.keys(reply.written).length) {
      const said = reply.error.replace(/^.*(?= is not the photo)/, sent[0].split(/[\\/]/).pop());
      return answer(status, { ...reply, attempted: sent.length, error: said });
    }
    const written = Object.fromEntries(sent.map((file, n) => [file, { ...reply.written.PHOTO_0, ...own(file, n) }]));
    return answer(status, { ...reply, attempted: sent.length, changed: sent.length, with_rows: sent.length, written });
  };
}

/**
 * The write replies the FakeServers of this test file gave that no route makes (write-replies.mjs unmadeReply), each once:
 * when its tests have run, the file fails unless the ratchet allows them (write-replies.mjs STILL_HAND_SPELLED), whatever
 * the page did with the reply (a page that swallows a failed request would otherwise pass on it; #3342).
 */
const UNMADE = new Set();
after(() => {
  const said = ratchet(path.basename(process.argv[1] || ""), [...UNMADE], filtered());
  if (said) throw new Error(said);
});

/** A fetch stub that routes by URL substring and records every call. */
export class FakeServer {
  constructor() {
    this.calls = [];
    this.routes = [];
    this.imageRequests = [];
  }

  /**
   * Reply with `body` (JSON-encoded) for any URL containing `match` (a string), or that `match` (a RegExp) matches, or
   * for which `match` (a function of the URL and the request's body, parsed: SAVE) is true.
   *
   * `body` may be a function of the URL (and the request's body, parsed) returning the body or a promise of it, for a
   * reply that has to arrive later -- a scan still running while someone types -- or one with a status of its own
   * (`answer`).
   */
  on(match, body, { status = 200, headers = {} } = {}) {
    this.routes.push({ match, body, status, headers });
    return this;
  }

  /** Like on(), ahead of every route there is: for a test that answers a longer path than one a page set up. */
  first(match, body, { status = 200, headers = {} } = {}) {
    this.routes.unshift({ match, body, status, headers });
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
      const method = (init && init.method) || "GET";
      self.calls.push({ url, method, body });

      // Match on the path regardless of a leading slash: the apps issue both
      // absolute ("/api/x") and relative ("api/x") URLs, and a relative one would
      // otherwise fall through to the default empty reply.
      // A function route (SAVE) is of the request's body too, and is tried first.
      const route = self.routes.find((r) => typeof r.match === "function" && r.match(url, body))
        || self.routes.find((r) => (typeof r.match === "string"
          ? url.includes(r.match) || url.includes(r.match.replace(/^\//, ""))
          : r.match instanceof RegExp && r.match.test(url)))
        || (url.includes("api/rules") ? { body: PUBLISHED_RULES, status: 200 } : undefined);
      const headers = (route && route.headers) || {};
      const reply = (sent) => {
        const { status, body: payload } = sent && sent[ANSWER] ? sent[ANSWER] : { status: route ? route.status : 200, body: sent };
        const unmade = route && unmadeReply(url, method, body, status, payload);
        if (unmade) UNMADE.add(unmade);
        return {
          ok: status >= 200 && status < 300,
          status,
          headers: { get: (name) => headers[name] ?? null },
          clone: () => reply(sent),
          json: () => Promise.resolve(payload),
          text: () => Promise.resolve(JSON.stringify(payload)),
        };
      };
      const answer = route && typeof route.body === "function"
        ? Promise.resolve(route.body(url, body)).then(reply)
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

/** The real servers when tests/page_server.py started them ({tagpup: url, tuner: url}); none under a plain `node --test`. */
export const REAL = process.env.TAGPUP_PAGE_SERVERS ? JSON.parse(process.env.TAGPUP_PAGE_SERVERS) : null;

/**
 * The real app in a FakeServer's place (contract table, rows 12 and 22): the page's `fetch` goes to the server at `base`; a
 * reply is read whole before the page sees it, status and headers kept (a 4xx keeps its JSON, a 503 its X-TagPup-Updating).
 */
export class RealServer extends FakeServer {
  constructor(base) {
    super();
    this.base = base;
    this.waiting = [];
    this.replied = [];   // the URLs answered whole
    this.inFlight = 0;   // requests sent and not answered yet
  }

  /**
   * Resolves once the page has no request out and its replies' renders have run: what a test's end waits for before its
   * window is closed. A reply landing on a closed window drew into a document that was gone ("generated asynchronous
   * activity after the test ended ... reading 'createElement'", #2598). At most `seconds`, so a page that never settles
   * cannot hold the run.
   */
  async quiet(window, seconds = 10) {
    const deadline = Date.now() + seconds * 1000;
    while (Date.now() < deadline) {
      await flush(window, 4);
      if (this.inFlight === 0) {
        await flush(window, 4);
        if (this.inFlight === 0) return;
      }
      await Promise.race([this.nextReply(), new Promise((resolve) => setTimeout(resolve, 200))]);
    }
  }

  nextReply() {
    return new Promise((resolve) => this.waiting.push(resolve));
  }

  install(window) {
    window.fetch = async (input, init = {}) => {
      const url = typeof input === "string" ? input : String(input.url);
      const body = typeof init.body === "string" && /^[\[{]/.test(init.body) ? JSON.parse(init.body) : init.body;
      this.calls.push({ url, method: init.method || "GET", body });
      this.inFlight += 1;
      const target = new URL(url, "http://page/");
      const abort = new AbortController();
      if (init.signal) init.signal.addEventListener("abort", () => abort.abort(), { once: true });
      try {
        const sent = await fetch(this.base + target.pathname + target.search, { ...init, signal: abort.signal });
        this.replied.push(url);
        return new Response([204, 304].includes(sent.status) ? null : await sent.arrayBuffer(), { status: sent.status, headers: sent.headers });
      } catch (error) {
        if (init.signal && init.signal.aborted) throw new window.DOMException("The operation was aborted.", "AbortError");
        throw error;
      } finally {
        this.inFlight -= 1;
        this.waiting.splice(0).forEach((resolve) => resolve());
      }
    };
    return this;
  }
}

/**
 * The match of the people reads alone -- /api/people and /api/people?include_hidden=1 -- not of /api/people-with-counts,
 * /api/people-faces and the rest, which the text "/api/people" is the start of.
 */
export const PEOPLE_ROUTE = /\/api\/people(\?|$)/;

/**
 * The people /api/people?include_hidden=1 answers for the tree `nodes` (as /api/taxonomy/tree sends it), as
 * tagpup.store.person_ids.People.read makes them: each node marked as holding faces with no node under it, but an
 * empty People (where a new library files people), as {id, name, tag}. A page told of a tree with people and of no
 * records holds a shape no server sends (docs/findings.md, #1358).
 */
export function peopleRecordsOf(nodes) {
  const parents = new Set(nodes.map((node) => node.parent_id));
  return nodes
    .filter((node) => node.has_face === 1 && !parents.has(node.id) && node.tag.toLowerCase() !== "people")
    .map((node) => ({ id: node.id, name: node.name, tag: node.tag }));
}

/** A library of the test's own, made by the Python seed `seed`: `{library, server, url, ...what the seed says}` to load a page on. */
export async function realLibrary(seed, appName = "tagpup") {
  const kind = appName === "tagtuner" ? "tuner" : "tagpup";
  const facts = await (await fetch(`${REAL[kind]}/__seed/${seed}`)).json();
  return { ...facts, server: new RealServer(REAL[kind]), url: `http://localhost:8090/${facts.library}/` };
}

/** Wait on replies, not on time, until `done()` holds; after `seconds` it fails saying what it waited for (`what`, or a function of it). */
export async function until(ctx, done, what, seconds = 30) {
  let timer;
  const gaveUp = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`gave up waiting for ${typeof what === "function" ? what() : what}`)), seconds * 1000);
  });
  gaveUp.catch(() => {});
  try {
    for (;;) {
      await flush(ctx.window, 2);
      if (done()) return;
      await Promise.race([ctx.server.nextReply(), gaveUp]);
    }
  } finally {
    clearTimeout(timer);
  }
}

/** Windows still open, so a failing test cannot leave timers running forever; each with the server its page talks to. */
const openWindows = new Map();

function closeWindow(window) {
  try {
    window.close();
  } catch {
    /* already closed */
  }
}

/** Close a window; one on a real server once its page has no request out (RealServer.quiet), so no reply lands on it closed. */
function closeSettled(window, server) {
  if (!server || typeof server.quiet !== "function") {
    closeWindow(window);
    return Promise.resolve();
  }
  return server.quiet(window).then(() => closeWindow(window), () => closeWindow(window));
}

/**
 * Close every open jsdom window, stopping its polling timers. A window on a fake server is closed at once; one on the real
 * server when its replies are in: `afterEach(() => closeAllApps())` returns the promise, which the runner waits for.
 */
export function closeAllApps() {
  const closing = [...openWindows].map(([window, server]) => closeSettled(window, server));
  openWindows.clear();
  return Promise.all(closing);
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
 * @param {"tagpup"|"tagtuner"|"activity"|"phone"} appName
 * @param {{url?: string, server?: FakeServer, t?: object, before?: function}} options
 */
export async function loadApp(appName, { url, server = new FakeServer(), t, before } = {}) {
  const app = PAGES[appName];
  if (!app) throw new Error(`unknown app: ${appName}`);

  const appDir = path.join(REPO_ROOT, app.dir);
  const html = fs.readFileSync(path.join(appDir, htmlOf(app)), "utf8");
  const script = pageScript(appDir, COMMON_DIR, htmlOf(app));

  // Silence expected console noise from the app; surface real jsdom errors.
  const virtualConsole = new VirtualConsole();
  const consoleErrors = [];
  virtualConsole.on("jsdomError", (e) => consoleErrors.push(e));
  virtualConsole.on("error", (...args) => consoleErrors.push(args.join(" ")));

  const dom = new JSDOM(html, {
    url: url || `http://localhost:8090/${app.page || app.defaultDb}/${app.html || ""}`,
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

  openWindows.set(window, server);
  if (t && typeof t.after === "function") {
    t.after(() => {
      const open = openWindows.delete(window);
      return open ? closeSettled(window, server) : undefined;
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
/** A record's `writes` as tagpup.services.photos.page_record gives it (field_values.writes): a photo's, and a clip's. */
export const PHOTO_WRITES = ["tags", "title", "date", "rotate", "name"];
export const CLIP_WRITES = ["tags", "title", "name", "date"];

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
    writes: PHOTO_WRITES,
    ...overrides,
  };
}

/**
 * A folder's scan as the real route sends it (GET /api/folder/scan, its records in the route's order; fixtures/
 * scan_replies.json, written by tests/test_folder_scan_follows_the_library.py AFolderScanFromThePage): `folder`, two
 * photos dated (IMG_0002.jpg 10:00, IMG_0001.jpg 10:20, on 2020:06:01) and IMG_0003.jpg holding no Date Taken, its file
 * time 1600000000, after them; each size 1, its folder FOLDER.
 */
export const SCAN_REPLIES = JSON.parse(fs.readFileSync(path.join(HERE, "fixtures", "scan_replies.json"), "utf8"));

/**
 * The `order` of `example`, a record of SCAN_REPLIES, with `real`, what the server made it of, put back as `wanted`.
 * Throws when the order no longer holds what it was made of: the server's key is then not one a test can make from a
 * date or a file time, and the tests say so rather than sort by a key the server never sends.
 */
function respelled(example, real, wanted) {
  if (!example.order.includes(real)) {
    throw new Error(`scan_replies.json: ${example.filename}'s order ${JSON.stringify(example.order)} does not hold ${real}`);
  }
  return example.order.replace(real, wanted);
}

/**
 * The `order` the server gives a photo taken at `taken` (as the server spells a Date Taken; null for none) whose file
 * time is `mtime` (tagpup.core.dates taken_sort_key: what a scan's record carries, and a write's reply of a photo it
 * dated), spelled as the real scan spelled its dated record and its undated one (SCAN_REPLIES.folder). An undated photo's
 * file time is a whole second, as the scan's is.
 */
export function orderOf(taken, mtime) {
  const records = SCAN_REPLIES.folder.reply;
  if (taken !== null && taken !== undefined) {
    const dated = records.find((record) => record.taken !== null);
    return respelled(dated, dated.taken, taken);
  }
  if (!Number.isInteger(mtime)) throw new Error(`orderOf: an undated photo's file time a whole second, as the scan's, not ${mtime}`);
  const undated = records.find((record) => record.taken === null);
  return respelled(undated, String(undated.mtime), String(mtime));
}

/** `record` (photoRecord's, a clip's) as a scan's reply gives it: with the `order` the server gives it (orderOf). */
export function scanned(record) {
  return { ...record, order: orderOf(record.taken ?? null, record.mtime) };
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
