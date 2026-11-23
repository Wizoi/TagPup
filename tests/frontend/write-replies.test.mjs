// A page test's fake server answers a write route only with a reply the real route makes (write-replies.mjs, #3342):
// the replies each fixture holds pass, a reply spelled by hand fails naming its route, and the list of files still
// answering by hand only shrinks, whatever part of a file a run chose.
import test from "node:test";
import assert from "node:assert/strict";
import { filtered, ratchet, unmadeReply, WRITE_ROUTES } from "./write-replies.mjs";

const WRITE = "/library/api/photos/write";
const TAGS = { op: "tags", changes: { "C:/Pictures/Quay/q0.jpg": { id: 3, add: ["Places/Quay"] } } };

test("every reply a route's fixture holds is one the route makes", () => {
  const asked = {
    "a save": [WRITE, "POST", { op: "set" }], "a Time Shift": [WRITE, "POST", { op: "shift" }],
    "a tag change": [WRITE, "POST", { op: "tags" }], "a folder's Smart Rename": ["api/folder/rename-photos", "POST"],
    "a Delete": ["/library/api/photo/delete", "POST"], "a Convert": ["/library/api/clip/convert", "POST"],
    "a Convert's end": ["/library/api/clip/converted?ticket=1", "GET"],
  };
  for (const { route, replies } of WRITE_ROUTES) {
    const [url, method, body] = asked[route.split(" (")[0]];
    assert.ok(replies.length, route);
    for (const { status, reply } of replies) assert.equal(unmadeReply(url, method, body, status ?? 200, reply), null, route);
  }
});

test("a tag change answered by hand with a bare success is a reply no route makes", () => {
  const said = unmadeReply(WRITE, "POST", TAGS, 200, { success: true });
  assert.match(said, /^a tag change \(POST \/api\/photos\/write, op tags\): the reply 200 \{success\} is not one/);
  // A real reply whose photo's part has a field no reply of the route has.
  const written = WRITE_ROUTES.find((each) => each.route.startsWith("a tag change")).replies.find((each) => each.status === 200);
  const forged = { ...written.reply, written: { "C:/Pictures/Quay/q0.jpg": { tags: [], mtime: 1, size: 1, taken: null } } };
  assert.match(unmadeReply(WRITE, "POST", TAGS, 200, forged), /a photo of its `written` has \{mtime,size,tags,taken\}/);
});

test("a write that raised, or a share away, is the reply every route makes; an error without `success` is none", () => {
  for (const status of [500, 503]) {
    assert.equal(unmadeReply("/library/api/photo/delete", "POST", { path: "x" }, status, { success: false, error: "e" }), null);
  }
  assert.ok(unmadeReply(WRITE, "POST", TAGS, 500, { error: "e" }));
  assert.ok(unmadeReply(WRITE, "POST", TAGS, 409, { success: false, error: "e" }), "a refusal has the envelope");
});

test("a Convert's start, its end and its plan are three requests: the plan is no write", () => {
  const start = { success: true, ticket: 1, library: "library" };
  assert.equal(unmadeReply("/library/api/clip/convert", "POST", { id: 1 }, 200, start), null);
  assert.ok(unmadeReply("/library/api/clip/converted?ticket=1", "GET", undefined, 200, start), "a start is no end");
  assert.equal(unmadeReply("/library/api/clip/converted?ticket=1", "GET", undefined, 200, { done: false }), null);
  assert.equal(unmadeReply("/library/api/clip/convert-plan?id=1", "GET", undefined, 200, { action: "NOTHING" }), null);
});

test("the list of files answering by hand only shrinks, and a run of some of a file's tests says nothing of it", () => {
  const listed = { "old.test.mjs": 2 };
  const two = ["a tag change: one", "a Delete: two"];
  assert.match(ratchet("new.test.mjs", two.slice(0, 1), false, listed), /^new\.test\.mjs: 1 write replies no route makes \(0 allowed\)/);
  assert.match(ratchet("old.test.mjs", [...two, "a save: three"], true, listed), /3 write replies no route makes \(2 allowed\)/);
  assert.equal(ratchet("old.test.mjs", two, false, listed), null);
  assert.match(ratchet("old.test.mjs", two.slice(0, 1), false, listed), /lower its count .* to 1$/);
  assert.match(ratchet("old.test.mjs", [], false, listed), /take it off the list/);
  assert.equal(ratchet("old.test.mjs", [], true, listed), null, "a filtered run gives fewer and says nothing");
  assert.equal(ratchet("new.test.mjs", [], false, listed), null);
  assert.equal(filtered(["--test-name-pattern=quay"]), true);
  assert.equal(filtered(["--test-isolation=process", "--test-timeout=0"]), false);
});
