// TagPup's page: a bulk edit of the library's photos as a JOB (docs/ARCHITECTURE.md, phase 9d-1 for the server's side, 9d-2 for this).
// The page starts it with a selection by id (POST /api/library/bulk/start) and asks how it is going about once a second (GET .../status:
// the server answers from memory); it can cancel it, resume a time shift that stopped, and find it again after the tab was closed or
// reloaded (GET .../current). The job belongs to the LIBRARY: the strip does not follow a view or a folder, and a job goes on when the
// page does not. Only one runs at a time: while one does the bulk controls of a library view are disabled, and a second start is
// refused by the server with a sentence the page shows, with a way to the one running.
//
// How it fails, each held by tests/frontend/bulk-job.test.mjs: an answer that does not come (kept asking, said so after three, given
// up after MAX_FAILURES -- never for ever -- and Ask again offered); a server that restarted (the job is `abandoned`: what was done,
// and Resume or Start again); Cancel twice (one request); Cancel after it ended (the status says so); a tab that is hidden (asked
// every 5 s); a page reloaded mid-job (the strip comes back); a start whose answer was lost (asked for what runs).
import { api } from './common/api.js';
import { samePath } from './common/paths.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import {
    btnBulkAddPeople, btnBulkAddTags, btnDeleteSelection, folderSelectionSidebar
} from './elements.js';
import { setStatus } from './status.js';
import { libraryName } from './looking.js';
import { forgetFolderCaches } from './cache.js';
import { hasUnsavedEdits } from './edits.js';
import { refreshHeldCards } from './library-source.js';
import { isRunning, renderBulkStrip, say, wireBulkStrip } from './bulk-strip.js';
import { endedSentence, widenedSentence } from './bulk-words.js';

/** How often a running job is asked about: about once a second; 5 s while the tab is hidden. */
export const POLL_MS = 1000;
export const HIDDEN_POLL_MS = 5000;
/** Answers that did not come, in a row, before the strip says so; and before it stops asking (Ask again goes on). */
export const SAY_AFTER = 3;
export const MAX_FAILURES = 40;
/** The longest wait between two tries after answers stopped coming. */
const MOST_BACKOFF = 6;

/** A request for how it is going that has not been answered in this long is given up as a miss (the server may be away or stuck). */
export const POLL_TIMEOUT_MS = 15000;

/** How much earlier than the moment a start was sent a job may have begun and still be that start's (the server's clock, rounded). */
const LOST_SLACK_MS = 5000;

/** A view that opens within this long of the page asking which bulk edit runs does not ask again. */
const ATTACH_AGAIN_MS = 5000;

const JSON_HEADERS = { 'Content-Type': 'application/json' };

/** Is a bulk edit running, or being started, in this library? */
export function bulkBusy() {
    return state.bulk.starting || isRunning(state.bulk.job);
}

/** The sentence a disabled bulk control says, as its tooltip. */
export const BUSY_SENTENCE = 'A bulk edit is running (see the strip at the top). Wait for it to finish, or cancel it.';

function repaint() {
    renderBulkStrip();
    lockBulkControls();
}

/**
 * While a bulk edit runs, the controls of a library view that would start another are disabled with a tooltip saying why (the
 * server would refuse the second: this says so first); a folder's own bulk writes are another route and stay as they were.
 */
export function lockBulkControls() {
    const lock = Boolean(state.library) && bulkBusy();
    for (const button of [btnBulkAddPeople, btnBulkAddTags, btnDeleteSelection]) {
        if (!button) continue;
        if (lock) {
            if (button.dataset.bulkTitle === undefined) button.dataset.bulkTitle = button.title || '';
            button.disabled = true;
            button.title = BUSY_SENTENCE;
        } else if (button.dataset.bulkTitle !== undefined) {
            button.disabled = false;
            button.title = button.dataset.bulkTitle;
            delete button.dataset.bulkTitle;
        }
    }
    for (const chip of folderSelectionSidebar.querySelectorAll('.selection-summary-chip-remove, .selection-summary-chip-apply')) {
        if (lock) {
            if (chip.dataset.bulkTitle === undefined) chip.dataset.bulkTitle = chip.title || '';
            chip.setAttribute('aria-disabled', 'true');
            chip.title = BUSY_SENTENCE;
        } else if (chip.dataset.bulkTitle !== undefined) {
            chip.removeAttribute('aria-disabled');
            chip.title = chip.dataset.bulkTitle;
            delete chip.dataset.bulkTitle;
        }
    }
}

// ---- Asking how it is going --------------------------------------------------------------------------------------------

function stopPolling() {
    const bulk = state.bulk;
    window.clearTimeout(bulk.timer);
    bulk.timer = 0;
    if (bulk.controller) bulk.controller.abort();
    bulk.controller = null;
}

function nextDelay() {
    const bulk = state.bulk;
    const base = document.hidden ? HIDDEN_POLL_MS : POLL_MS;
    const late = Math.max(0, bulk.failures - SAY_AFTER + 1);
    return base * Math.min(1 + late, MOST_BACKOFF);
}

function schedulePoll() {
    const bulk = state.bulk;
    window.clearTimeout(bulk.timer);
    bulk.timer = window.setTimeout(askHowItIsGoing, nextDelay());
}

/** What the server said of the job, taken in: shown, and -- the first time it says the job has ended -- acted on. */
function takeStatus(body) {
    const bulk = state.bulk;
    const was = bulk.job;
    bulk.job = { ...(was && was.job === body.job ? was : {}), ...body };
    if (!isRunning(bulk.job)) {
        bulk.cancelling = false;
        stopPolling();
        jobEnded(bulk.job);
    }
}

function askHowItIsGoing() {
    const bulk = state.bulk;
    bulk.timer = 0;
    const job = bulk.job;
    if (!isRunning(job) || bulk.controller) return;
    const controller = new AbortController();
    bulk.controller = controller;
    const wanted = job.job;
    const mine = () => bulk.controller === controller && bulk.job && bulk.job.job === wanted;
    let timedOut = false;
    const giveUp = window.setTimeout(() => {
        timedOut = true;
        controller.abort();
    }, POLL_TIMEOUT_MS);
    api.fetch(`/api/library/bulk/status?job=${wanted}`, { signal: controller.signal })
        .then(res => res.json().catch(() => ({})).then(body => ({ res, body })))
        .then(({ res, body }) => {
            window.clearTimeout(giveUp);
            if (!mine()) return;
            bulk.controller = null;
            if (res.status === 404) {
                // The library does not know this job any more (its record was removed): there is nothing left to ask.
                bulk.trouble = (body && body.error) || 'TagPup no longer knows this bulk edit.';
                bulk.gaveUp = true;
                repaint();
                return;
            }
            if (!res.ok || body.success === false) throw new Error((body && body.error) || `TagPup answered ${res.status}`);
            bulk.failures = 0;
            bulk.trouble = '';
            bulk.gaveUp = false;
            takeStatus(body);
            repaint();
            if (isRunning(bulk.job)) schedulePoll();
        })
        .catch(err => {
            window.clearTimeout(giveUp);
            if ((err.name === 'AbortError' && !timedOut) || !mine()) return;
            const reason = timedOut ? `no answer in ${POLL_TIMEOUT_MS / 1000} s` : err.message;
            bulk.controller = null;
            bulk.failures += 1;
            if (bulk.failures >= MAX_FAILURES) {
                bulk.gaveUp = true;
                bulk.trouble = `TagPup has not answered for a long time (${reason}). The edit goes on in TagPup if it is running; Ask again to look.`;
            } else if (bulk.failures >= SAY_AFTER) {
                bulk.trouble = `TagPup is not answering (${reason}). The edit goes on if TagPup is running; still trying.`;
            }
            repaint();
            if (!bulk.gaveUp) schedulePoll();
        });
}

// ---- What a finished job leaves for the page to do ------------------------------------------------------------------------

/**
 * A job ended (once for each end of it): say so in the status line; read the navigator's counts again; forget what the browser kept of
 * folders' scans (their photos' stamps changed); bring the cards in the window up to date (their thumbnails carry the file's stamp,
 * and a save from an older record would be refused as changed on disk, which is right); re-read the open photo, unless it has edits
 * of its own; and tally the selection again.
 */
function jobEnded(job) {
    const bulk = state.bulk;
    const key = `${job.job}:${job.state}:${job.finished || ''}:${job.done}`;
    if (bulk.endedKey === key) return;
    bulk.endedKey = key;
    const desc = bulk.request ? bulk.request.desc : null;
    const bad = job.state !== 'done' || (job.error_count || 0) > 0;
    setStatus(bad ? 'error' : 'ready', endedSentence(job, desc), { transient: false });
    if (!(job.changed || job.done)) return;
    forgetFolderCaches();
    refreshAfterWrites(job);
}

/**
 * What the page shows of photos a job wrote: the counts, the cards, the open photo (unless it has edits of its own), the selection's
 * tally. A Delete's photos are gone: the view's order is read again instead (library-view.js photosDeleted), which drops them from the
 * view and the selection, and closes the open photo if it was one of them.
 */
function refreshAfterWrites(job) {
    state.tally.key = '';          // what the photos carry has changed: the selection is counted again
    upper.navigatorCountsChanged({ now: true });
    if (!state.library) return;
    if (job && job.op === 'delete') {
        upper.photosDeleted();
        return;
    }
    refreshHeldCards();
    const open = state.activePhotoPath ? state.folderPhotos.find(photo => samePath(photo.path, state.activePhotoPath)) : null;
    if (open && open.id !== undefined && !hasUnsavedEdits()) upper.reloadChangedPhoto(open);
    upper.updateSelectedThumbnailsCount();
}

// ---- Starting, cancelling, resuming -----------------------------------------------------------------------------------------

/** The sentence of a refusal or a failure to start, shown where the owner is looking. */
function startFailed(sentence) {
    setStatus('error', sentence, { transient: false });
}

/**
 * Start a bulk edit: `selection` is selected.js's body ({ids} or {source, excluded}), `op` and `params` the server's, `desc` the
 * words (bulk-words.js). Resolves { ok: true } when a job began and the strip is showing it; { ok: false, why } when not -- a job is
 * running already (the server's 409 sentence is in the strip, with Show it), or the server refused it with a sentence.
 */
export function startBulk({ op, selection, params, desc, picked }) {
    const bulk = state.bulk;
    if (bulkBusy()) return Promise.resolve({ ok: false, why: BUSY_SENTENCE });
    bulk.starting = true;
    bulk.conflict = '';
    repaint();
    const sentAt = Date.now();
    return api.fetch('/api/library/bulk/start', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ op, selection, params }) })
        .then(res => res.json().catch(() => ({})).then(body => ({ res, body })))
        .then(({ res, body }) => {
            bulk.starting = false;
            if (res.status === 409) {
                bulk.conflict = (body && body.error) || 'TagPup refused the edit (409).';
                // An earlier job that stopped is still in the strip: it gives way, or the refusal would be drawn nowhere.
                if (!isRunning(bulk.job)) bulk.job = null;
                repaint();
                startFailed(bulk.conflict);
                say(bulk.conflict);
                return { ok: false, why: bulk.conflict };
            }
            if (!res.ok || !body.success) {
                repaint();
                const why = (body && body.error) || `TagPup answered ${res.status}`;
                startFailed(why);
                return { ok: false, why };
            }
            beginTracking({ job: body.job, op, state: 'running', total: body.total, done: 0, changed: 0, unchanged: 0,
                skipped_missing: 0, skipped_damaged: 0, error_count: 0, errors: [], eta_seconds: null },
                { op, selection, params, desc, picked });
            widenedNotice(body.total, picked, selection);
            return { ok: true, missing: body.missing || 0, total: body.total };
        })
        .catch(err => {
            bulk.starting = false;
            repaint();
            // The request may have been answered and the answer lost: ask what is running, once, in a moment.
            // A job that is over by then is not shown (the library offers only one that runs or stopped part-way), so no promise.
            const why = `${err.message}. If the edit did start, it shows here while it runs; if it is not there, look at the photos before making it again.`;
            startFailed(`Could not start the bulk edit: ${why}`);
            window.setTimeout(() => attachBulk({ force: true, quiet: true, lostSentAt: sentAt }), 2000);
            return { ok: false, why };
        });
}

/**
 * A selection sent as a source and the photos left out is resolved by the server NOW: photos that came into the view since the page read
 * its ids (a sync, another tab, a time shift) are in the job although nobody picked them. The server has no way to be told how many to
 * expect, and the job has begun when the page learns it, so the page says it plainly -- the strip is already offering Cancel, which stops
 * it after the photos being written now. Fewer than picked is no widening (photos that are gone are counted as missing).
 */
function widenedNotice(took, picked, selection) {
    const bulk = state.bulk;
    if (!selection || !selection.source || !Number.isFinite(picked) || !Number.isFinite(took) || took <= picked) return;
    bulk.notice = { took, picked };
    repaint();
    const sentence = widenedSentence(bulk.notice, true);
    startFailed(sentence);
    say(sentence);
}

function beginTracking(job, request) {
    const bulk = state.bulk;
    stopPolling();
    if (!bulk.job || bulk.job.job !== job.job) bulk.notice = null;
    bulk.job = job;
    bulk.request = request;
    bulk.conflict = '';
    bulk.cancelling = false;
    bulk.failures = 0;
    bulk.trouble = '';
    bulk.gaveUp = false;
    bulk.endedKey = '';
    bulk.announced = null;
    repaint();
    if (isRunning(job)) schedulePoll();
}

/** Cancel: the job stops after the chunk it is writing. Pressed twice, it is one request; after the job ended, the status says so. */
export function cancelBulk() {
    const bulk = state.bulk;
    const job = bulk.job;
    if (!isRunning(job) || bulk.cancelling) return Promise.resolve(false);
    bulk.cancelling = true;
    repaint();
    return api.fetch('/api/library/bulk/cancel', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ job: job.job }) })
        .then(res => res.json().catch(() => ({})).then(body => {
            if (!res.ok || body.success === false) throw new Error((body && body.error) || `TagPup answered ${res.status}`);
            if (bulk.job && bulk.job.job === job.job) takeStatus(body);
            repaint();
            if (isRunning(bulk.job)) schedulePoll();
            return true;
        }))
        .catch(err => {
            bulk.cancelling = false;
            repaint();
            startFailed(`Could not cancel the bulk edit: ${err.message}`);
            return false;
        });
}

/** Resume a time shift that stopped: the same job, from where it was, no photo shifted twice. */
export function resumeBulk() {
    const bulk = state.bulk;
    const job = bulk.job;
    if (!job || isRunning(job) || !job.resumable || bulk.starting) return Promise.resolve(false);
    bulk.starting = true;
    repaint();
    return api.fetch('/api/library/bulk/resume', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ job: job.job }) })
        .then(res => res.json().catch(() => ({})).then(body => {
            bulk.starting = false;
            if (!res.ok || !body.success) {
                bulk.trouble = (body && body.error) || `TagPup answered ${res.status}`;
                repaint();
                return false;
            }
            beginTracking({ ...job, state: 'running', message: null, finished: null, done: body.done ?? job.done, total: body.total ?? job.total,
                resumable: false, eta_seconds: null }, bulk.request);
            return true;
        }))
        .catch(err => {
            bulk.starting = false;
            bulk.trouble = `Could not resume: ${err.message}`;
            repaint();
            return false;
        });
}

/**
 * Start again what was cancelled, failed or abandoned: the same selection and edit, as started. A Delete is never sent again as it
 * was: it is asked about afresh, as a click on Delete is (bulk-edit.js deleteSelection: where the files are, the question, a new
 * token), so that what it deletes is what the new question names (#691).
 */
export function startAgain() {
    const request = state.bulk.request;
    if (!request || bulkBusy()) return Promise.resolve({ ok: false });
    if (request.op === 'delete') {
        return upper.deleteSelection({ selection: request.selection, count: request.picked }).then(ok => ({ ok }));
    }
    state.bulk.job = null;
    return startBulk(request);
}

/** Take the strip away. A job that stopped part-way is not offered again by a reload (the library remembers which one was dismissed). */
export function dismissBulk() {
    const bulk = state.bulk;
    if (isRunning(bulk.job)) return;
    if (bulk.job) rememberDismissed(bulk.job.job);
    stopPolling();
    bulk.job = null;
    bulk.request = null;
    bulk.conflict = '';
    bulk.notice = null;
    bulk.trouble = '';
    bulk.gaveUp = false;
    repaint();
}

function dismissedKey() {
    return `tagpup_bulk_dismissed_${libraryName()}`;
}

function rememberDismissed(job) {
    try {
        localStorage.setItem(dismissedKey(), String(job));
    } catch (err) {
        console.warn('Could not remember that the bulk edit was dismissed:', err);
    }
}

function wasDismissed(job) {
    try {
        return localStorage.getItem(dismissedKey()) === String(job);
    } catch (err) {
        return false;
    }
}

// ---- Finding a job again ---------------------------------------------------------------------------------------------------

/**
 * A job found already stopped. The page did not see it end, so not what a job's end does in full (#606: that is for one that ended
 * under this page, and every load would repeat it). What it left is settled once: the folder scans kept from before it ended are
 * forgotten (those saved after it show what it wrote); and when it is the edit this page tried to start and lost the answer of, the
 * status line says how it ended, and the counts and cards are read again.
 */
function foundStopped(job, lost) {
    // An abandoned job has no end time (finished is null): when it stopped is not known, so every scan kept is older than it may be.
    forgetFolderCaches({ before: job.finished ? job.finished * 1000 : Infinity });
    if (!lost) return;
    const bad = job.state !== 'done' || (job.error_count || 0) > 0;
    setStatus(bad ? 'error' : 'ready', endedSentence(job, null), { transient: false });
    if (job.changed || job.done) refreshAfterWrites(job);
}

/**
 * Ask the library which bulk edit to show: the one running (started by another tab, or before this page was opened) or the latest that
 * stopped part-way. Done as the page starts and as a view opens; it does nothing when the page already shows one. `force` (Show it
 * after a refusal, Ask again after giving up) replaces what the strip holds. A read that fails says nothing: the strip is an offer, and a
 * second start is refused by the server with the sentence anyway.
 */
export function attachBulk({ force = false, quiet = false, lostSentAt = 0 } = {}) {
    const lost = lostSentAt > 0;
    const bulk = state.bulk;
    if (!force && (bulk.job || bulk.starting)) return Promise.resolve(false);
    // The page asks as it starts and again as a view opens: one question when they come together, none when it was just asked.
    if (bulk.attaching) return bulk.attaching;
    if (!force && Date.now() - bulk.attachedAt < ATTACH_AGAIN_MS) return Promise.resolve(false);
    bulk.attachedAt = Date.now();
    const asking = api.fetch('/api/library/bulk/current')
        .then(res => res.json().catch(() => ({})).then(body => {
            if (!res.ok) throw new Error((body && body.error) || `TagPup answered ${res.status}`);
            const found = body && body.job && typeof body.job === 'object' && !Array.isArray(body.job) ? body.job : null;
            if (!force && (bulk.job || bulk.starting)) return false;
            if (!found) {
                if (force && bulk.conflict) {
                    bulk.conflict = '';
                    repaint();
                    setStatus('ready', 'No bulk edit is running now. Make your edit again to start it.', { transient: false });
                }
                return false;
            }
            if (!force && !isRunning(found) && wasDismissed(found.job)) return false;
            // Asking after a start whose answer was lost: only a job begun since the start was sent can be the one lost. An older one
            // (the library's latest that stopped part-way) says nothing of this edit, so the 'Could not start' sentence stays.
            if (lost && !(Number(found.started) * 1000 >= lostSentAt - LOST_SLACK_MS)) return false;
            const same = bulk.request && bulk.job && bulk.job.job === found.job;
            // Shown, and nothing more: a job that stopped before this page looked is not one that ended under it (what a job's
            // end does -- forget the folders' scans, read the counts, say it in the status line -- is for the end of one that ran here).
            beginTracking({ ...found }, same ? bulk.request : null);
            if (!isRunning(found)) foundStopped(found, lost);
            return true;
        }))
        .catch(err => {
            if (!quiet) console.warn('Could not ask which bulk edit is running:', err);
            return false;
        })
        .then(found => {
            bulk.attaching = null;
            return found;
        });
    bulk.attaching = asking;
    return asking;
}

/** The strip's "Show it" (after a refusal) and "Ask again" (after giving up). */
function showIt() {
    const bulk = state.bulk;
    if (bulk.gaveUp && isRunning(bulk.job)) {
        bulk.gaveUp = false;
        bulk.failures = 0;
        bulk.trouble = '';
        repaint();
        askHowItIsGoing();
        return;
    }
    attachBulk({ force: true });
}

export function wireBulk() {
    wireBulkStrip({ cancel: cancelBulk, resume: resumeBulk, again: startAgain, show: showIt, dismiss: dismissBulk });
    // A tab brought back asks at once, rather than waiting out the slow pace it was hidden at.
    document.addEventListener('visibilitychange', () => {
        const bulk = state.bulk;
        if (!document.hidden && isRunning(bulk.job) && !bulk.controller && !bulk.gaveUp) askHowItIsGoing();
    });
}
