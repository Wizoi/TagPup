/**
 * The replies the page's write routes make, as the real routes made them (tests/frontend/fixtures/*_replies.json, each
 * written by the Python test that names it, page_fixtures.hold_to), and the one rule of a page test's fake server: a
 * write route's reply is one of those shapes, never a reply spelled by hand that no route makes (#3342). Every `wrong`
 * of the photos-written cuts agreed with a reply spelled in the test that shipped it (#3326, #3330).
 *
 * The shape of a reply is its status and the names of its fields, and the names of the fields of each photo it names in
 * `written` and in `errors`: the values are the test's own (its paths, stamps and tags), the fields are the server's. A
 * field a route adds only when it happened (a tag write's `named_faces`, `unnamed_faces`, `renamed_faces`) is accepted
 * only once a real reply carrying it is in the route's fixture. A reply of another shape fails its test file
 * (harness.mjs FakeServer), naming the route and the shapes the route makes.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const FIXTURES = path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures");
const read = (name) => JSON.parse(fs.readFileSync(path.join(FIXTURES, name), "utf8"));

/** The {status, reply} entries of a fixture laid out {name: {status, reply}} (shift, tags, rename, delete). */
const named = (entries) => Object.values(entries);

/** save_replies.json holds its statuses apart ({status: {name: code}, name: reply}); a turn's reply is the rotate route's. */
function saves() {
  const { status, ...replies } = read("save_replies.json");
  return Object.entries(replies).filter(([name]) => name !== "rotated_picture_changed")
    .map(([name, reply]) => ({ status: status[name], reply }));
}

/** The path of a request's URL, without its query: "/library/api/clip/converted". */
const pathOf = (url) => url.split("?")[0];

/** A request to the route whose path ends in `tail` ("api/photo/delete"), by `method`. */
const at = (tail, method) => (url, how) => how === method && pathOf(url).endsWith(tail);

/** A request to the one write entry point (POST /api/photos/write) of the op `op`. */
const write = (op) => (url, how, body) => at("api/photos/write", "POST")(url, how) && Boolean(body) && body.op === op;

const CONVERT = read("convert_replies.json");

/**
 * The page's write routes: what a request to each is, and the replies the real route made. A route listed here is
 * answered by a test only from its fixture (harness.mjs saved, savedAs, notSaved, changedOnDisk, shiftedAs, taggedAs,
 * tagFiles, renamedAs, deletedAs, convertedAs). Rotate (POST /api/photo/rotate) and one photo's Smart Rename are not
 * yet here: no fixture holds their plain replies.
 */
export const WRITE_ROUTES = [
  { route: "a save (POST /api/photos/write, op set)", is: write("set"), replies: saves() },
  { route: "a Time Shift (POST /api/photos/write, op shift)", is: write("shift"), replies: named(read("shift_replies.json")) },
  { route: "a tag change (POST /api/photos/write, op tags)", is: write("tags"), replies: named(read("tags_replies.json")) },
  { route: "a folder's Smart Rename (POST /api/folder/rename-photos)", is: at("api/folder/rename-photos", "POST"),
    replies: named(read("rename_replies.json")) },
  { route: "a Delete (POST /api/photo/delete)", is: at("api/photo/delete", "POST"), replies: named(read("delete_replies.json")) },
  { route: "a Convert (POST /api/clip/convert)", is: at("api/clip/convert", "POST"), replies: named(CONVERT.start) },
  { route: "a Convert's end (GET /api/clip/converted)", is: at("api/clip/converted", "GET"), replies: named(CONVERT.end) },
];

/**
 * The replies every route makes beside its own: a write that raised, 500, and a share that did not answer, 503
 * ({success, error}: tagpup/web/responses.py error and of); and the server turning a request away while it updates (503
 * {success, updating, error}: tagpup/web/libraries.py _refuse_updating).
 */
const EVERY_ROUTE = [
  { status: 500, reply: { success: false, error: "" } },
  { status: 503, reply: { success: false, error: "" } },
  { status: 503, reply: { success: false, updating: true, error: "" } },
];

const fieldsOf = (object) => Object.keys(object || {}).sort().join(",");

/** The field lists the photos of `part` ("written" or "errors") have in a route's real replies. */
function partsOf(replies, part) {
  const lists = new Set();
  for (const { reply } of replies) {
    const held = reply && reply[part];
    for (const each of Array.isArray(held) ? held : Object.values(held || {})) lists.add(fieldsOf(each));
  }
  return lists;
}

/**
 * Why the reply `payload` (with `status`) to the request (url, method, body) is one no route makes, or null: it is not
 * a write route's, or its shape is one of the route's real replies' or one every route makes.
 */
export function unmadeReply(url, method, body, status, payload) {
  const route = WRITE_ROUTES.find((each) => each.is(url, method, body));
  if (!route) return null;
  const fields = fieldsOf(payload);
  const made = [...route.replies, ...EVERY_ROUTE];
  if (!made.some((each) => (each.status ?? 200) === status && fieldsOf(each.reply) === fields)) {
    const shapes = [...new Set(route.replies.map((each) => `${each.status ?? 200} {${fieldsOf(each.reply)}}`))].join("; ");
    return `${route.route}: the reply ${status} {${fields}} is not one the route makes (${shapes})`;
  }
  for (const part of ["written", "errors"]) {
    const held = payload[part];
    const lists = partsOf(route.replies, part);
    for (const each of Array.isArray(held) ? held : Object.values(held || {})) {
      if (!lists.has(fieldsOf(each))) {
        return `${route.route}: a photo of its \`${part}\` has {${fieldsOf(each)}}, which no reply of the route has `
          + `(${[...lists].map((l) => `{${l}}`).join("; ")})`;
      }
    }
  }
  return null;
}

/**
 * The page test files that still answer a write route with a reply spelled by hand, each with how many distinct such
 * replies it gives (#3342). A ratchet in the style of tests/test_fixtures_make_photo_rows_like_the_indexer.py: a file
 * may not be added, nor its count grow; a file that gives fewer, run whole, must lower its count, and leave the list
 * at none, so the list only shrinks.
 */
export const STILL_HAND_SPELLED = {
  "bulk-writes-queue.test.mjs": 4,
  "clip-convert.test.mjs": 1, "delete-while-folder-changes.test.mjs": 1,
  "just-look-edits.test.mjs": 3, "library-photo-back.test.mjs": 1, "library-view.test.mjs": 1,
  "photo-position.test.mjs": 1,
  "rotate-refreshes-thumbnails.test.mjs": 1, "scenario-a-write-reaches-every-scan-kept.test.mjs": 1,
  "scenario-deleting-the-selected-photos-of-a-folder.test.mjs": 1,
  "scenario-tagging-keeps-the-faces-in-place.test.mjs": 3,
  "scenario-tagging-many-photos-at-once.test.mjs": 5,
  "smart-rename-count.test.mjs": 1,
  "smart-rename-one-photo.test.mjs": 1, "suggestions-follow-a-saved-photo.test.mjs": 2,
  "video-viewer.test.mjs": 3,
};

/** The node flags that run only some of a file's tests: such a run gives fewer replies, and says nothing of the list. */
const FILTERS = ["--test-name-pattern", "--test-skip-pattern", "--test-only"];

/** Whether this process runs only some of its file's tests (a flag of FILTERS, as node --test hands it to the file). */
export const filtered = (flags = process.execArgv) => flags.some((flag) => FILTERS.some((name) => flag.startsWith(name)));

/**
 * What the ratchet says of the test file `file` that gave the distinct replies `unmade` no route makes, or null: run
 * whole (`partial` false), a file of the list giving fewer must lower its count; a file off the list may give none, and
 * one on it no more than its count.
 */
export function ratchet(file, unmade, partial, listed = STILL_HAND_SPELLED) {
  const allowed = listed[file] ?? 0;
  const said = unmade.map((each) => `\n  ${each}`).join("");
  if (unmade.length > allowed) {
    return `${file}: ${unmade.length} write replies no route makes (${allowed} allowed): answer each from its route's `
      + `fixture (harness.mjs saved, shiftedAs, taggedAs, tagFiles, renamedAs, deletedAs, convertedAs):${said}`;
  }
  if (!partial && unmade.length < allowed) {
    return `${file}: ${unmade.length} write replies no route makes, ${allowed} listed: lower its count in `
      + `write-replies.mjs STILL_HAND_SPELLED to ${unmade.length}${unmade.length ? "" : " (take it off the list)"}`;
  }
  return null;
}
