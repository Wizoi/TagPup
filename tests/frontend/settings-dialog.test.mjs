/**
 * The library's settings, opened from TagTuner's gear (web/common/settings-dialog.js;
 * docs/ARCHITECTURE.md, phase 7.6).
 *
 * The dialog is made from what the server says of each setting -- the declarations in
 * tagpup/core/validation.py, which /api/settings answers with the library's values and
 * /api/rules publishes with each setting's rules. The answer here is built from the
 * published rules the page tests are served (tests/validation_rules.json, which
 * tests/test_validation.py holds to the server's), so a setting declared there is a
 * setting this dialog is tested with.
 *
 * Locked settings -- the CLIP model, face detection, the ExifTool program -- are shown,
 * not editable, and open only through Change..., whose consequences must each be ticked
 * before Save is enabled; after a locked one is saved the page reloads. Candidate words
 * and the rename format are edited directly.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, PUBLISHED_RULES, flush, click, closeAllApps } from "./harness.mjs";
import { OPEN_DIALOG } from "../../web/common/dialog.js";

afterEach(() => closeAllApps());

const LIBRARY = "kr-track";
const GROUP_TITLES = { clip: "The CLIP model", faces: "Face detection", exiftool: "ExifTool", suggest: "Suggest",
                       renaming: "Smart Rename" };

/** What /api/settings answers for a library holding the defaults, from the declarations. */
function settingsAnswer(values = {}) {
  const groups = [];
  for (const [kind, declared] of Object.entries(PUBLISHED_RULES.kinds)) {
    if (!kind.startsWith("setting ")) continue;
    const key = kind.slice("setting ".length);
    let group = groups.find((g) => g.name === declared.group);
    if (!group) {
      group = { name: declared.group, title: GROUP_TITLES[declared.group], locked: declared.locked,
                consequences: declared.consequences, settings: [] };
      groups.push(group);
    }
    group.settings.push({ key, kind, label: declared.label, type: declared.type, default: declared.default,
                          info: declared.info, locked: declared.locked, consequences: declared.consequences,
                          value: key in values ? values[key] : declared.default });
  }
  return { library: LIBRARY, stamped: true, exiftool_found: "C:/Programs/ExifTool/exiftool.exe", groups };
}

const DECLARED = Object.keys(PUBLISHED_RULES.kinds).filter((k) => k.startsWith("setting ")).map((k) => k.slice(8));
const LOCKED = DECLARED.filter((key) => PUBLISHED_RULES.kinds[`setting ${key}`].locked);

async function tunerWithSettings(t, { saved = { success: true, changed: 1, settings: [], locked: false, change: 7 } } = {}) {
  const server = new FakeServer()
    .on("/api/apps", { this: "tuner", apps: {} })
    .on("/api/taxonomy/tree", [])
    .on("/api/folder/index-active", { active: [], queued: [], busy: false, remaining: 0 })
    .on("/api/databases", { databases: [LIBRARY] })
    .on("/api/people-with-counts", [])
    .on("/api/people", [])
    .on("/api/photos", [])
    .on("/api/settings", (url) => (server.calls.at(-1).method === "POST" ? saved : settingsAnswer()));
  const ctx = await loadApp("tagtuner", { t, url: `http://localhost:8080/${LIBRARY}/`, server });
  await flush(ctx.window, 6);
  const { document, window } = ctx;
  click(window, document.getElementById("btn-gear"));
  await flush(window);
  click(window, document.querySelector('[data-action="library-settings"]'));
  await flush(window, 6);
  ctx.dialog = document.getElementById("settings-modal");
  ctx.field = (key) => ctx.dialog.querySelector(`[data-setting="${key}"]`);
  ctx.save = () => document.getElementById("btn-settings-save");
  ctx.changeButton = (group) => ctx.dialog.querySelector(`.settings-change[data-group="${group}"]`);
  ctx.boxes = (group) => [...ctx.dialog.querySelectorAll(`.settings-group[data-group="${group}"] .settings-consequences input`)];
  ctx.type = (key, value) => {
    const input = ctx.field(key);
    input.value = value;
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
  };
  ctx.posted = () => server.calls.filter((c) => c.url.endsWith("/api/settings") && c.method === "POST");
  return ctx;
}

describe("the settings dialog", () => {
  test("is made from the declarations: every setting, in its group, with the library's value", async (t) => {
    const { dialog, field, document } = await tunerWithSettings(t);
    assert.ok(!dialog.classList.contains("hidden"));
    assert.equal(document.getElementById("settings-title").textContent, `Library settings: ${LIBRARY}`);
    assert.deepEqual([...dialog.querySelectorAll("[data-setting]")].map((i) => i.dataset.setting), DECLARED);
    assert.deepEqual([...dialog.querySelectorAll(".settings-group h4")].map((h) => h.textContent),
                     ["The CLIP model", "Face detection", "ExifTool", "Suggest", "Smart Rename"]);
    assert.equal(field("model.name").value, "ViT-H-14");
    assert.equal(field("model.preserve_full_frame").type, "checkbox");
    assert.equal(field("model.preserve_full_frame").checked, true);
    assert.equal(field("paths.exiftool").placeholder, "Found: C:/Programs/ExifTool/exiftool.exe");
  });

  test("each setting has an info button saying what it changes", async (t) => {
    const { window, dialog } = await tunerWithSettings(t);
    for (const key of DECLARED) {
      const button = dialog.querySelector(`.settings-info-button[data-info="${key}"]`);
      assert.ok(button, `${key} has no info button`);
      const info = dialog.querySelector(`#${button.getAttribute("aria-controls")}`);
      assert.ok(info.classList.contains("hidden"));
      click(window, button);
      assert.ok(!info.classList.contains("hidden"), `${key}'s info did not show`);
      assert.equal(info.textContent, PUBLISHED_RULES.kinds[`setting ${key}`].info);
      assert.equal(button.getAttribute("aria-expanded"), "true");
      click(window, button);
      assert.ok(info.classList.contains("hidden"));
    }
  });

  test("locked settings show their value and cannot be edited; the others can", async (t) => {
    const { field } = await tunerWithSettings(t);
    assert.deepEqual(LOCKED.sort(), ["faces.confidence_threshold", "faces.min_face_size", "faces.mtcnn_thresholds",
                                     "model.force_image_size", "model.max_aspect_ratio", "model.name",
                                     "model.preserve_full_frame", "model.pretrained", "paths.exiftool"]);
    for (const key of LOCKED) {
      const input = field(key);
      assert.ok(input.type === "checkbox" ? input.disabled : input.readOnly, `${key} is editable`);
      assert.equal(input.getAttribute("aria-readonly"), "true");
    }
    for (const key of ["candidates.tags", "renaming.format"]) {
      assert.ok(!field(key).readOnly && !field(key).disabled, `${key} is locked`);
    }
  });

  test("Change... lists each consequence, and Save waits for every one to be ticked", async (t) => {
    const { window, field, save, changeButton, boxes, type } = await tunerWithSettings(t);
    assert.ok(save().disabled, "Save is enabled with nothing changed");
    click(window, changeButton("clip"));
    assert.ok(!field("model.name").readOnly, "Change... left the model locked");
    const consequences = boxes("clip");
    assert.equal(consequences.length, PUBLISHED_RULES.kinds["setting model.name"].consequences.length);
    assert.ok(consequences.length >= 2);
    type("model.name", "ViT-B-32");
    assert.ok(save().disabled, "Save is enabled before the consequences are ticked");
    for (const [i, box] of consequences.entries()) {
      assert.ok(save().disabled, `Save enabled with ${i} of ${consequences.length} ticked`);
      click(window, box);
    }
    assert.ok(!save().disabled, "Save stays disabled with every consequence ticked");
    click(window, consequences[0]);
    assert.ok(save().disabled, "unticking one left Save enabled");
  });

  test("a value the rules refuse is shown, and keeps Save disabled", async (t) => {
    const { window, dialog, save, changeButton, boxes, type } = await tunerWithSettings(t);
    click(window, changeButton("faces"));
    boxes("faces").forEach((box) => click(window, box));
    type("faces.confidence_threshold", "1.5");
    assert.ok(save().disabled);
    const problem = dialog.querySelector('.settings-row[data-key="faces.confidence_threshold"] .validation-error');
    assert.equal(problem.textContent, "The confidence must be between 0 and 1.");
    type("faces.confidence_threshold", "0.9");
    assert.ok(!save().disabled);
    assert.equal(problem.textContent, "");
    type("renaming.format", "{grouping}");
    assert.ok(save().disabled, "a rename format without {index} was allowed");
  });

  test("an unlocked setting saves directly, with only what changed, and the page stays", async (t) => {
    const ctx = await tunerWithSettings(t);
    const { window, save, type, posted, consoleErrors } = ctx;
    type("candidates.tags", "Kayak, Lighthouse");
    assert.ok(!save().disabled);
    click(window, save());
    await flush(window, 6);
    assert.deepEqual(posted().map((c) => c.body), [{ values: { "candidates.tags": "Kayak, Lighthouse" },
                                                     acknowledged: [] }]);
    assert.ok(!consoleErrors.some((e) => /navigation/i.test(String(e && (e.message || e)))), "the page reloaded");
  });

  test("the page reloads after a locked setting is saved", async (t) => {
    const ctx = await tunerWithSettings(t, { saved: { success: true, changed: 1, settings: ["faces.min_face_size"],
                                                      locked: true, change: 8 } });
    const { window, save, changeButton, boxes, type, posted, consoleErrors } = ctx;
    click(window, changeButton("faces"));
    type("faces.min_face_size", "40");
    boxes("faces").forEach((box) => click(window, box));
    click(window, save());
    await flush(window, 6);
    // The server refuses a locked change its group is not acknowledged for: the
    // dialog names the group whose consequences were ticked.
    assert.deepEqual(posted().map((c) => c.body), [{ values: { "faces.min_face_size": "40" },
                                                     acknowledged: ["faces"] }]);
    // jsdom does not navigate; it reports the reload it was asked for as not implemented.
    assert.ok(consoleErrors.some((e) => /navigation/i.test(String(e && (e.message || e)))),
              "the page did not reload after a locked setting was saved");
  });

  test("a refusal from the server is shown, and nothing reloads", async (t) => {
    const ctx = await tunerWithSettings(t, { saved: { success: false, error: "Nothing was written: settings model.name is not what the plan read" } });
    const { window, dialog, save, type, consoleErrors } = ctx;
    type("renaming.format", "{index} {grouping}");
    click(window, save());
    await flush(window, 6);
    assert.match(dialog.querySelector(".settings-status").textContent, /not what the plan read/);
    assert.ok(!consoleErrors.some((e) => /navigation/i.test(String(e && (e.message || e)))));
  });

  test("while it is open the page's keys leave it alone, and Escape closes it", async (t) => {
    const { window, document, dialog } = await tunerWithSettings(t);
    assert.equal(document.querySelector(OPEN_DIALOG), dialog, "dialogOpen() does not see it");
    dialog.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    assert.ok(dialog.classList.contains("hidden"));
    assert.equal(document.activeElement, document.getElementById("btn-gear"), "the focus did not go back to the gear");
  });

  test("Escape closes it with the focus on the page behind, as after a save disables Save", async (t) => {
    // Seen in a real browser: saved, Save disabled itself, the focus fell to the body,
    // and Escape did nothing.
    const { window, document, dialog, type, save } = await tunerWithSettings(t);
    type("renaming.format", "{index} {grouping}");
    click(window, save());
    await flush(window, 6);
    document.activeElement.blur();
    document.body.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    assert.ok(dialog.classList.contains("hidden"), "Escape on the page did not close it");
  });
});
