/**
 * The Activity page's File access section (web/activity/file-access.js; docs/ARCHITECTURE.md, "File
 * access check"): a row for each finding with its level, why, what to do and the commands in a box
 * with a Copy button; asked once when the page opens and again on Check again, never on a timer;
 * a warning dealt with is kept in the browser and shows greyed as ok; and TagTuner's Roots dialog
 * carries the quiet line that links to it.
 */
import { test, describe, afterEach } from "node:test";
import assert from "node:assert/strict";
import { loadApp, FakeServer, flush, click, closeAllApps } from "./harness.mjs";

afterEach(() => closeAllApps());

/** Where the page keeps the findings the owner has dealt with (web/activity/file-access.js). */
const DISMISSED_KEY = "tagpup.fileAccess.dismissed";
const DATA = "D:\\TagPup\\data";
const FINDINGS = {
  checked_at: "2026-10-03 10:00:00",
  cached: false,
  facts: {},
  findings: [
    { id: "defender", level: "info", title: "Microsoft Defender scans files as they are opened and written",
      why: "Real-time protection is on.", what_to_do: "Exclude the data folder.", commands: [], places: [] },
    { id: "defender-exclusions:unknown", level: "warn", title: "Whether Microsoft Defender excludes TagPup's folders is not known",
      why: "Only an administrator can read the exclusions.", what_to_do: "Run the commands as administrator.",
      commands: ["# Windows PowerShell, run as administrator", `Add-MpPreference -ExclusionPath '${DATA}'`], places: [DATA] },
    { id: "windows-search", level: "ok", title: "Windows Search does not index TagPup's folders", why: "", what_to_do: "",
      commands: [], places: [] },
  ],
};

function server(answer = FINDINGS) {
  return new FakeServer()
    .on("/api/file-access/check", answer)
    .on("/api/activity/", {});
}

async function open(t, fake = server(), { before } = {}) {
  return loadApp("activity", {
    t, server: fake, url: "http://localhost:8090/activity/?every=60000",
    ...(before ? { before } : {}),
  });
}

const text = (element) => element.textContent.replace(/\s+/g, " ").trim();
const asks = (fake) => fake.calls.filter((c) => c.url.includes("/api/file-access/check"));

describe("File access", () => {
  test("it is asked once when the page opens, under no library, and not again on its own", async (t) => {
    const fake = server();
    const { window } = await open(t, fake);
    await flush(window, 8);
    assert.equal(asks(fake).length, 1);
    assert.ok(asks(fake)[0].url.startsWith("/api/file-access/check"), "the request is not under a library");
    assert.ok(!asks(fake)[0].url.includes("refresh"));
    await flush(window, 8);
    assert.equal(asks(fake).length, 1, "no timer asks it again");
  });

  test("each finding is a row with its level, why, what to do and its commands in a box", async (t) => {
    const { document, window } = await open(t);
    await flush(window, 8);
    const body = document.getElementById("file-access-body");
    const rows = body.querySelectorAll(".finding");
    assert.equal(rows.length, 3);
    assert.deepEqual([...rows].map((row) => row.dataset.level), ["info", "warn", "ok"]);
    const warn = body.querySelector('.finding[data-id="defender-exclusions:unknown"]');
    assert.ok(warn.classList.contains("finding-warn"));
    assert.match(text(warn), /Only an administrator can read the exclusions\./);
    assert.match(text(warn), /What to do: Run the commands as administrator\./);
    assert.equal(warn.querySelector("pre.commands").textContent,
      `# Windows PowerShell, run as administrator\nAdd-MpPreference -ExclusionPath '${DATA}'`);
    assert.ok(warn.querySelector("button.copy"));
    assert.equal(body.querySelector('.finding[data-id="windows-search"] pre'), null, "no commands, no box");
    assert.equal(text(document.getElementById("file-access-checked")), "Last checked 2026-10-03 10:00:00");
  });

  test("Copy puts the commands on the clipboard", async (t) => {
    const { document, window } = await open(t);
    await flush(window, 8);
    const copied = [];
    Object.defineProperty(window.navigator, "clipboard", { value: { writeText: (x) => copied.push(x) }, configurable: true });
    const button = document.querySelector('.finding[data-id="defender-exclusions:unknown"] button.copy');
    click(window, button);
    assert.deepEqual(copied, [`# Windows PowerShell, run as administrator\nAdd-MpPreference -ExclusionPath '${DATA}'`]);
    assert.equal(button.textContent, "Copied");
  });

  test("Check again asks for a fresh read and shows the new time", async (t) => {
    const fake = server();
    const { document, window } = await open(t, fake);
    await flush(window, 8);
    fake.first("/api/file-access/check", { ...FINDINGS, checked_at: "2026-10-03 10:05:00" });
    click(window, document.getElementById("file-access-again"));
    await flush(window, 8);
    assert.equal(asks(fake).length, 2);
    assert.match(asks(fake)[1].url, /refresh=1/);
    assert.equal(text(document.getElementById("file-access-checked")), "Last checked 2026-10-03 10:05:00");
  });

  test("a rapid second click on Check again sends nothing while one is under way", async (t) => {
    const fake = server();
    const { document, window } = await open(t, fake);
    await flush(window, 8);
    let release;
    fake.first("/api/file-access/check", () => new Promise((resolve) => { release = () => resolve(FINDINGS); }));
    click(window, document.getElementById("file-access-again"));
    await flush(window, 2);
    click(window, document.getElementById("file-access-again"));
    click(window, document.getElementById("file-access-again"));
    assert.equal(asks(fake).length, 2, "the first load and one Check again");
    release();
    await flush(window, 8);
  });

  test("a warning dealt with is greyed as ok, kept in the browser, and can be shown again", async (t) => {
    const { document, window } = await open(t);
    await flush(window, 8);
    const warn = () => document.querySelector('.finding[data-id="defender-exclusions:unknown"]');
    click(window, warn().querySelector("button.dismiss"));
    assert.ok(warn().classList.contains("finding-ok"));
    assert.ok(warn().classList.contains("dismissed"));
    assert.equal(warn().querySelector("pre"), null, "its commands are put away");
    assert.deepEqual(JSON.parse(window.localStorage.getItem(DISMISSED_KEY)), { "defender-exclusions:unknown": true });
    assert.equal(document.querySelector('.finding[data-id="defender"] button.dismiss'), null, "only a warning is dismissed");
    click(window, warn().querySelector("button.dismiss"));
    assert.ok(warn().classList.contains("finding-warn"));
    assert.deepEqual(JSON.parse(window.localStorage.getItem(DISMISSED_KEY)), {});
  });

  test("a page opened after a dismissal shows the warning greyed", async (t) => {
    const { document, window } = await open(t, server(), {
      before: (win) => win.localStorage.setItem(DISMISSED_KEY, JSON.stringify({ "defender-exclusions:unknown": true })),
    });
    await flush(window, 8);
    assert.ok(document.querySelector('.finding[data-id="defender-exclusions:unknown"]').classList.contains("dismissed"));
  });

  test("a check that fails says so and leaves the page standing", async (t) => {
    const { document, window } = await open(t, server({ success: false, error: "The check broke." }));
    await flush(window, 8);
    const body = document.getElementById("file-access-body");
    assert.match(text(body), /The check broke\./);
    assert.ok(document.getElementById("file-access-again"));
  });

  test("a finding's text is shown as text, never as markup", async (t) => {
    const evil = { ...FINDINGS, findings: [{ ...FINDINGS.findings[1], title: "<img src=x onerror=alert(1)>",
      commands: ["<script>bad()</script>"] }] };
    const { document, window } = await open(t, server(evil));
    await flush(window, 8);
    const body = document.getElementById("file-access-body");
    assert.equal(body.querySelector("img"), null);
    assert.equal(body.querySelector("script"), null);
    assert.match(text(body), /<img src=x onerror=alert\(1\)>/);
  });
});
