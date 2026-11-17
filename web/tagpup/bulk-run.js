// TagPup's page: an edit of the library view's selection -- Add tag, Add person, a pill's Remove or Apply, Delete -- written in ONE
// request that answers when every photo is done (POST /api/library/bulk-edit; docs/ARCHITECTURE.md). Up to 5,000 photos, about a twentieth
// of a second each: while the request is out the shared strip shows how far the server has got, with Cancel (web/common/batch-strip.js),
// and the view's edit controls stay off until the answer comes. The server finishes the edit whether the page waits or not.
//
// How it fails. A refusal (the server's sentence: a tag the rules forbid, a selection that changed, an edit already under way, one
// stopped as asked) or an error says so and nothing more is assumed. An answer that does not come (the connection dropped, the server
// restarted) says that the edit may still be going on or may be written: it does not retry, since an edit made again changes nothing a
// photo holds already but a Delete asks its question afresh. A selection over 5,000 is said before anything is asked of the server.
// A folder's Delete (Organize, its selection by path) is the same request; its follow reads the folder again (folder.js).
import { api } from './common/api.js';
import { samePath } from './common/paths.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import { btnBulkAddPeople, btnBulkAddTags, btnDeleteSelection, btnRefreshList, folderSelectionSidebar } from './elements.js';
import { setStatus } from './status.js';
import { forgetFolderCaches } from './cache.js';
import { hasUnsavedEdits } from './edits.js';
import { refreshHeldCards } from './library-source.js';
import { endedSentence, showEnded } from './common/batch-strip.js';
import { doneWords, itemsOf } from './bulk-words.js';
import { isJustLooking, libraryName } from './looking.js';
import { folderPhotosDeleted } from './folder.js';

const JSON_HEADERS = { 'Content-Type': 'application/json' };

/**
 * Is a delete or an edit being written from this page now: the selection's (`state.bulk.running`, runBulk) or the panel's
 * one-photo Delete (`state.deleting`, photo.js deleteActivePhoto)? The one question both deletes and every bulk control ask; the
 * two flags stay two (the panel's own Delete is held by its own, stale.js holdDeletes).
 */
export function bulkBusy() {
    return state.bulk.running || state.deleting;
}

/** The sentence a disabled bulk control says, as its tooltip. */
const BUSY_SENTENCE = 'A bulk edit is being written (see the strip at the top). Wait for it to finish.';
/** The same while the panel's one-photo Delete is out. */
const ONE_DELETE_OUT = 'A photo is being deleted; wait for it to finish.';

/** Why a bulk control does nothing now: what is being written (bulkBusy). */
export function busySentence() {
    return state.bulk.running ? BUSY_SENTENCE : ONE_DELETE_OUT;
}

/** Is this title one lockBulkControls put on a held control (so not the control's own)? */
function heldTitle(title) {
    return title === BUSY_SENTENCE || title === ONE_DELETE_OUT;
}

/** Why the selection's Delete does nothing in a folder the library does not hold (Just look): it has no row to delete by. */
export function justLookingDeleteSaid() {
    return `${libraryName()} does not hold this folder: delete one photo at a time with the panel's Delete`;
}

/** What the selection's Delete does, as the page gives it. */
const DELETE_TITLE = btnDeleteSelection ? btnDeleteSelection.title : '';

/**
 * The selection's Delete, by one rule: shown in every library view (#674), and in Organize while something of the open folder is
 * selected; inert, its title saying why, in a folder looked at without adding it (Just look); disabled with the busy sentence
 * while a delete or an edit is written (bulkBusy). Its title is made here from the three, never saved and put back, so Just look
 * changing while a delete runs is not undone by the delete's end. Called when one of them changes: the selection (selection.js),
 * Just look (membership.js applyJustLooking), a write starting or ending (lockBulkControls).
 */
export function paintDeleteSelection() {
    const button = btnDeleteSelection;
    if (!button) return;
    const inFolder = !state.library;
    button.classList.toggle('hidden', inFolder && !(state.scannedFolder && state.selectedThumbnails.length > 0));
    const inert = inFolder && isJustLooking();
    const busy = bulkBusy();
    button.disabled = busy;
    if (inert) button.setAttribute('aria-disabled', 'true');
    else button.removeAttribute('aria-disabled');
    button.title = busy ? busySentence() : (inert ? justLookingDeleteSaid() : DELETE_TITLE);
}

/**
 * While a delete or an edit is written, the controls that would start another are disabled with a tooltip saying why (the server
 * would refuse the second: this says so first), in a library view and in a folder alike; a folder's own tag writes ask the same
 * question when clicked (selection.js confirmBulkWrite). The sidebar's Refresh too: its scan would take the place of a folder
 * Delete's follow (folder.js folderPhotosDeleted, one `state.afterScan`). A title the page gives a held control meanwhile (a view
 * opened: library-view.js showChrome) is the one put back.
 */
export function lockBulkControls() {
    const lock = bulkBusy();
    for (const button of [btnBulkAddPeople, btnBulkAddTags, btnRefreshList]) {
        if (!button) continue;
        if (lock) {
            if (button.dataset.bulkTitle === undefined || !heldTitle(button.title)) button.dataset.bulkTitle = button.title || '';
            button.disabled = true;
            button.title = busySentence();
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
            chip.title = busySentence();
        } else if (chip.dataset.bulkTitle !== undefined) {
            chip.removeAttribute('aria-disabled');
            chip.title = chip.dataset.bulkTitle;
            delete chip.dataset.bulkTitle;
        }
    }
    paintDeleteSelection();
}

/**
 * What the page shows of photos an edit wrote: the counts, the cards, the open photo (unless it has edits of its own), the selection's
 * tally. A Delete's photos are gone: the view's order is read again instead (library-view.js photosDeleted), which drops them from the
 * view and the selection, and closes the open photo if it was one of them. A folder's Delete (`folder`, the folder it was asked in)
 * reads that folder again when it is still the one open (folder.js folderPhotosDeleted), and says `said` (how it ended, {kind,
 * sentence}) again once the list is on screen; the folders kept in this browser are forgotten first, so another folder opened
 * meanwhile is read afresh when it is next opened (#532). Returns the re-read's promise.
 */
function refreshAfterWrites(op, folder, said) {
    forgetFolderCaches();
    state.tally.key = '';          // what the photos carry has changed: the selection is counted again
    upper.navigatorCountsChanged({ now: true });
    if (folder) return folderPhotosDeleted(folder, said);
    if (!state.library) return undefined;
    if (op === 'delete') return upper.photosDeleted();
    refreshHeldCards();
    const open = state.activePhotoPath ? state.folderPhotos.find(photo => samePath(photo.path, state.activePhotoPath)) : null;
    if (open && open.id !== undefined && !hasUnsavedEdits()) upper.reloadChangedPhoto(open);
    upper.updateSelectedThumbnailsCount();
    return undefined;
}

/** Something that stopped the edit, or left its outcome unknown: said in the status line and in the strip. */
function failed(sentence) {
    state.bulk.running = false;
    lockBulkControls();
    showEnded({ text: sentence, problem: true });
    setStatus('error', sentence, { transient: false });
}

/**
 * Write a bulk edit: `selection` is selected.js's body ({ids} or {source, excluded}; a folder's Delete {paths}), `op` and `params` the
 * server's, `desc` the words (bulk-words.js), `picked` how many photos the owner selected, `folder` the folder a folder's Delete was
 * asked in. Resolves { ok: true, total } when the server answered that it was done (some photos may be counted as errors: the strip
 * lists them), { ok: false, why } when not; each with `reread`, the view's order (library-view.js photosDeleted) or the folder
 * (folder.js folderPhotosDeleted) read again after a Delete that may have deleted some, or undefined.
 */
export async function runBulk({ op, selection, params, desc, picked, folder }) {
    const bulk = state.bulk;
    if (bulkBusy()) {
        // Free when the selection was read, busy now: the panel's Delete began while this edit's check or question was out
        // (bulk-edit.js). Nothing is sent, and that is said, as every refusal is.
        const why = busySentence();
        setStatus('error', why, { transient: false });
        alert(why);
        return { ok: false, why };
    }
    bulk.running = true;
    lockBulkControls();
    setStatus('busy', `${desc.verb} ${desc.what} ${desc.prep} ${itemsOf(picked, desc.kinds)}...`, { transient: false });
    let res;
    let body;
    try {
        res = await api.fetch('/api/library/bulk-edit', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ op, selection, params }) });
        body = await res.json().catch(() => ({}));
    } catch (err) {
        const why = `TagPup did not answer (${err.message}). The edit may still be being written, or may be written already: look at the photos before making it again.`;
        failed(why);
        return { ok: false, why };
    }
    if (!res.ok || !body.success) {
        const why = (body && body.error) || `TagPup answered ${res.status}`;
        failed(why);
        // A refusal before anything was written changes nothing; the error of a write that stopped part-way may have.
        const reread = res.status >= 500 ? refreshAfterWrites(op, folder, { kind: 'error', sentence: why }) : undefined;
        return { ok: false, why, reread };
    }
    bulk.running = false;
    lockBulkControls();
    // Photos came into the view since it was read: the server took more than were picked.
    const wider = body.total > picked && selection && selection.source
        ? ` TagPup took ${body.total.toLocaleString()} but ${itemsOf(picked, desc.kinds)} ${picked === 1 ? 'was' : 'were'} selected: `
            + `${(body.total - picked).toLocaleString()} more came into this view since it was read.` : '';
    // A batch that stopped before its end (as asked, or ExifTool stopped answering) answers with its own sentence: N of M, and
    // the change in History, in place of the same error on each photo it left.
    const stopped = body.stopped;
    const ended = stopped || endedSentence(doneWords(desc, body.changed || 0, body.for_good || 0), body) + wider;
    const errors = body.error_count || 0;
    showEnded({ text: ended, problem: errors > 0, errors: stopped ? [] : body.errors, errorCount: stopped ? 0 : errors });
    const said = { kind: errors > 0 ? 'error' : 'ready', sentence: ended };
    setStatus(said.kind, said.sentence, { transient: false });
    const reread = body.changed || body.unchanged || body.skipped_missing ? refreshAfterWrites(op, folder, said) : undefined;
    return { ok: true, total: body.total, reread };
}
