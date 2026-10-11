// TagPup's page: an edit of the library view's selection -- Add tag, Add person, a pill's Remove or Apply, Delete -- written in ONE
// request that answers when every photo is done (POST /api/library/bulk-edit; docs/ARCHITECTURE.md). Up to 5,000 photos, about a twentieth
// of a second each: the page says so while it waits, with the count, in the strip under the header, and keeps the view's edit controls
// off until the answer comes. There is nothing to poll, cancel or find again: the server finishes the edit whether the page waits or not.
//
// How it fails. A refusal (the server's sentence: a tag the rules forbid, a selection that changed, an edit already under way) or an
// error says so and nothing more is assumed. An answer that does not come (the connection dropped, the server restarted) says that the
// edit may still be going on or may be written: it does not retry, since an edit made again changes nothing a photo holds already but a
// Delete asks its question afresh. A selection over 5,000 is said before anything is asked of the server (selected.js).
import { api } from './common/api.js';
import { samePath } from './common/paths.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import { btnBulkAddPeople, btnBulkAddTags, btnDeleteSelection, folderSelectionSidebar } from './elements.js';
import { setStatus } from './status.js';
import { forgetFolderCaches } from './cache.js';
import { hasUnsavedEdits } from './edits.js';
import { refreshHeldCards } from './library-source.js';
import { renderBulkStrip, say, wireBulkStrip } from './bulk-strip.js';
import { endedSentence, widenedSentence, writingSentence } from './bulk-words.js';

const JSON_HEADERS = { 'Content-Type': 'application/json' };

/** Is an edit being written now? */
export function bulkBusy() {
    return state.bulk.running;
}

/** The sentence a disabled bulk control says, as its tooltip. */
export const BUSY_SENTENCE = 'A bulk edit is being written (see the strip at the top). Wait for it to finish.';

function repaint() {
    renderBulkStrip();
    lockBulkControls();
}

/**
 * While an edit is written, the controls of a library view that would start another are disabled with a tooltip saying why (the
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

/**
 * What the page shows of photos an edit wrote: the counts, the cards, the open photo (unless it has edits of its own), the selection's
 * tally. A Delete's photos are gone: the view's order is read again instead (library-view.js photosDeleted), which drops them from the
 * view and the selection, and closes the open photo if it was one of them.
 */
function refreshAfterWrites(op) {
    forgetFolderCaches();
    state.tally.key = '';          // what the photos carry has changed: the selection is counted again
    upper.navigatorCountsChanged({ now: true });
    if (!state.library) return;
    if (op === 'delete') {
        upper.photosDeleted();
        return;
    }
    refreshHeldCards();
    const open = state.activePhotoPath ? state.folderPhotos.find(photo => samePath(photo.path, state.activePhotoPath)) : null;
    if (open && open.id !== undefined && !hasUnsavedEdits()) upper.reloadChangedPhoto(open);
    upper.updateSelectedThumbnailsCount();
}

/** Something that stopped the edit, or left its outcome unknown: said in the status line and in the strip. */
function failed(sentence, title) {
    const bulk = state.bulk;
    bulk.running = false;
    bulk.result = null;
    bulk.problem = { title, sentence };
    repaint();
    setStatus('error', sentence, { transient: false });
    say(sentence);
}

/**
 * Write a bulk edit: `selection` is selected.js's body ({ids} or {source, excluded}), `op` and `params` the server's, `desc` the words
 * (bulk-words.js), `picked` how many photos the owner selected. Resolves { ok: true, total } when the server answered that it was done
 * (some photos may be counted as errors: the strip lists them), { ok: false, why } when not.
 */
export async function runBulk({ op, selection, params, desc, picked }) {
    const bulk = state.bulk;
    if (bulkBusy()) return { ok: false, why: BUSY_SENTENCE };
    bulk.running = true;
    bulk.result = null;
    bulk.problem = null;
    bulk.request = { op, desc, picked };
    repaint();
    const sentence = writingSentence(desc, picked);
    setStatus('busy', sentence, { transient: false });
    say(sentence);
    let res;
    let body;
    try {
        res = await api.fetch('/api/library/bulk-edit', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ op, selection, params }) });
        body = await res.json().catch(() => ({}));
    } catch (err) {
        const why = `TagPup did not answer (${err.message}). The edit may still be being written, or may be written already: look at the photos before making it again.`;
        failed(why, 'The answer did not come');
        return { ok: false, why };
    }
    if (!res.ok || !body.success) {
        const why = (body && body.error) || `TagPup answered ${res.status}`;
        failed(why, 'The edit did not go through');
        // A refusal before anything was written changes nothing; the error of a write that stopped part-way may have.
        if (res.status >= 500) refreshAfterWrites(op);
        return { ok: false, why };
    }
    bulk.running = false;
    bulk.problem = null;
    bulk.result = { ...body, op };
    if (body.total > picked && selection && selection.source) bulk.notice = { took: body.total, picked };
    else bulk.notice = null;
    repaint();
    const ended = endedSentence(bulk.result, desc);
    const bad = (body.error_count || 0) > 0;
    setStatus(bad ? 'error' : 'ready', ended, { transient: false });
    say(ended);
    if (bulk.notice) say(widenedSentence(bulk.notice));
    if (body.changed || body.unchanged || body.skipped_missing) refreshAfterWrites(op);
    return { ok: true, total: body.total };
}

/** Take the strip away (an edit being written cannot be dismissed). */
export function dismissBulk() {
    const bulk = state.bulk;
    if (bulk.running) return;
    bulk.result = null;
    bulk.problem = null;
    bulk.notice = null;
    bulk.request = null;
    repaint();
}

export function wireBulk() {
    wireBulkStrip({ dismiss: dismissBulk });
}
