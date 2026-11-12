// TagPup's page: the edits of a library view's selection -- Add tag, Add person, the pills' Remove and Apply, Delete -- each
// asked of the owner by name and count, and written in one request (bulk-run.js) with the selection BY ID (selected.js). The folder
// view's bulk writes are selection.js's and are not touched: a path, 5,000 of them, one request.
//
// What is asked is what is sent: the selection is read BEFORE the question and the request carries that, whatever is clicked while a
// question or a placement dialog is open. A limit of the request (more than 5,000 photos, or a selection too scattered to name) is said
// in a sentence before anything is asked of the server. Smart Rename and Shift Date Taken are Organize's, not a view's (#669).
import { api } from './common/api.js';
import { tagProblem } from './common/vocabulary.js';
import { state } from './state.js';
import { bulkAddPeopleInput, bulkAddTagsInput } from './elements.js';
import { setStatus } from './status.js';
import { resolveTagOrPerson, updatePeopleDatalist, updateTagsDatalist } from './tags.js';
import { isIdSelected, selectionCount, selectionKinds, selectionRequest } from './selected.js';
import { BUSY_SENTENCE, bulkBusy, runBulk } from './bulk-run.js';
import { confirmSentence, deleteQuestion, describeDelete, describeTags, forGoodQuestion, itemsOf } from './bulk-words.js';
import { isVideo, showMedia, stopVideo } from './media.js';

/**
 * The selection, as a request, how many photos and clips it is and of which kind (`kinds`, what its sentences say); or null, the
 * owner told why not (nothing is asked of the server).
 */
function readSelection() {
    if (!state.library) return null;
    if (bulkBusy()) {
        alert(BUSY_SENTENCE);
        return null;
    }
    const asked = selectionRequest();
    if (!asked.ok) {
        if (asked.sentence === 'Nothing is selected.') setStatus('error', 'Select some photos first — nothing is selected', { transient: false });
        else alert(asked.sentence);
        return null;
    }
    return { selection: asked.body, count: selectionCount(), kinds: selectionKinds() };
}

/** The question before a write: it names the edit, the count and how long it takes. */
function confirmed(desc, count) {
    return confirm(confirmSentence(desc, count));
}

/**
 * Add the tags (or people) typed in the panel to the selection. The names are checked first, all or nothing, the text stays to be
 * corrected; then the question; and only then is a name resolved -- resolving may make a node of the tag tree, and a question
 * refused makes none. Resolves true when the edit was written.
 */
export async function addTypedToSelection(isPeople) {
    const input = isPeople ? bulkAddPeopleInput : bulkAddTagsInput;
    const typed = input.value.trim();
    if (!typed || !state.library) return false;
    const names = typed.split(',').map(each => each.trim()).filter(Boolean);
    if (!names.length) return false;
    const refused = names.map(tagProblem).find(Boolean);
    if (refused) {
        alert(refused);
        return false;
    }
    const op = isPeople ? 'people' : 'tags';
    // A second click while the first one's question or placement dialog is open asks nothing more.
    if (state.bulk.asking) return false;
    const picked = readSelection();
    if (!picked) return false;
    state.bulk.asking = true;
    let started;
    try {
        if (!confirmed(describeTags({ op, add: names, kinds: picked.kinds }), picked.count)) return false;
        const resolved = [];
        for (const name of names) {
            const found = await resolveTagOrPerson(name, isPeople);
            if (found) resolved.push(found);
        }
        if (!resolved.length) return false;
        started = await runBulk({ op, selection: picked.selection, params: { add: resolved, remove: [] },
            desc: describeTags({ op, add: resolved, kinds: picked.kinds }), picked: picked.count });
    } finally {
        state.bulk.asking = false;
    }
    // Only the text that was written: what was typed since stays.
    if (started.ok && input.value.trim() === typed) input.value = '';
    if (started.ok) (isPeople ? updatePeopleDatalist : updateTagsDatalist)();
    return started.ok;
}

/**
 * A pill of the selection's tally: take the tag (or person) off every photo of the selection, or -- the arrow -- put it on every one.
 * `kind` is 'tag' or 'person'; `name` is the tag as the tally gives it, or the person's name; `person` the person as the tally
 * tells them ({id, name, tag, group, shared}): their id is what the edit names.
 */
export async function editByPill({ kind, name, remove, person = null }) {
    const op = kind === 'person' ? 'people' : 'tags';
    if (state.bulk.asking) return false;
    const picked = readSelection();
    if (!picked) return false;
    // A person the server told by id is edited by that id: the name may be another person's too.
    const byId = kind === 'person' && person && person.id !== null && person.id !== undefined;
    const shown = byId ? person.name : name;
    const desc = describeTags({ op, add: remove ? [] : [shown], remove: remove ? [shown] : [], kinds: picked.kinds });
    const params = byId
        ? { add: [], remove: [], ...(remove ? { remove_ids: [person.id] } : { add_ids: [person.id] }) }
        : (remove ? { add: [], remove: [name] } : { add: [name], remove: [] });
    state.bulk.asking = true;
    try {
        if (!confirmed(desc, picked.count)) return false;
        const started = await runBulk({ op, selection: picked.selection, params, desc, picked: picked.count });
        return started.ok;
    } finally {
        state.bulk.asking = false;
    }
}

/** The clip the panel shows, when the selection about to be deleted holds it; else null. */
function panelClipDeleted() {
    const lib = state.library;
    const open = lib && lib.activeId !== null ? state.folderPhotos.find(photo => photo.id === lib.activeId) : null;
    return isVideo(open) && isIdSelected(open.id) ? open : null;
}

/** The clip stopped for a delete is the panel's still, once the view's order is read again: it was not deleted, and plays again. */
function playAgainIfKept(clip) {
    const lib = state.library;
    if (!lib || lib.activeId !== clip.id) return;
    showMedia(state.folderPhotos.find(photo => photo.id === clip.id) || clip, '');
}

/**
 * Delete the selection of a library view (#674): the photos' files go to the Recycle Bin -- on a share or a drive with none, through
 * this PC's (#694) -- or, a clip over 2 GiB on any drive and a file its drive's Bin would not keep, are deleted for good once
 * confirmed; their rows leave the library, each as the folder view's Delete does it (the server's photos.delete), in one request
 * (bulk-run.js), one edit at a time (bulkBusy, lockBulkControls). The selection is read first (a limit is said, nothing asked); then
 * the server is asked where the files would go (POST /api/library/selection/delete-check, one answer a folder); then the question
 * names the server's count of the photos, how long it takes and whether any are deleted for good. What is sent is what was asked: the
 * selection as read, the answer's `token` -- the server refuses the request, nothing deleted, when the selection no longer resolves to
 * exactly those photos (#691) -- and `for_good` only when a second question named the files that are deleted for good (a clip over 2 GiB, a file
 * its drive's Recycle Bin would not keep) and the owner pressed OK (DECISIONS 2026-11-08; the token is of that answer). Files from folders with no Recycle Bin go through
 * this PC's (#694), and the question says so and where they restore to. A clip the panel plays that is being deleted is stopped first
 * (the server reads it a range at a time, and a range read as its file is deleted leaves the file there), and plays again if it
 * stayed; a clip opened while the delete is out is asked of the server, which refuses the one whose file it is deleting
 * (/api/video-file, 409: media.js says its sentence). Rapid clicks ask once (`state.bulk.asking`). Resolves true when the edit
 * was written.
 */
export async function deleteSelection() {
    if (state.bulk.asking) return false;
    const picked = state.library ? readSelection() : null;
    if (!picked) return false;
    state.bulk.asking = true;
    try {
        setStatus('busy', `Looking where the files of ${itemsOf(picked.count, picked.kinds)} are...`);
        let where;
        try {
            const res = await api.fetch('/api/library/selection/delete-check', {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ selection: picked.selection }),
            });
            const body = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error((body && body.error) || `TagPup answered ${res.status}`);
            where = body;
        } catch (err) {
            setStatus('error', `Could not tell where the files are, so nothing was deleted: ${err.message}`, { transient: false });
            return false;
        }
        setStatus('ready', 'Ready');
        // Copies from places with no Recycle Bin that this PC has not room for: said, and nothing asked (#694).
        if (where.no_room) {
            setStatus('error', where.no_room, { transient: false });
            alert(where.no_room);
            return false;
        }
        // The server's count of the photos, which the token is of: the question names exactly what would be deleted.
        const count = Number.isFinite(where.total) ? where.total : picked.count;
        if (count === 0) {
            setStatus('ready', 'Nothing to delete: none of these photos is in the library any more.', { transient: false });
            return false;
        }
        if (!confirm(deleteQuestion(where, count, picked.kinds))) return false;
        // Files whose drive's Recycle Bin would not keep them are named, and deleted for good only on OK; the token is of that
        // answer, so a start that did not ask is refused by the server.
        const forGood = (where.for_good && where.for_good.count) || 0;
        if (forGood && !confirm(forGoodQuestion(where.for_good.names, forGood, where.bin_full))) return false;
        const desc = describeDelete(where, count, picked.kinds);
        const params = forGood ? { token: where.token, for_good: true } : { token: where.token };
        const clip = panelClipDeleted();
        if (clip) stopVideo();
        const started = await runBulk({ op: 'delete', selection: picked.selection, params, desc, picked: count });
        // Not deleted (refused, or an error of its own): it plays again; deleted, the re-read has closed the panel.
        if (clip) Promise.resolve(started.reread).then(() => playAgainIfKept(clip), () => playAgainIfKept(clip));
        return started.ok;
    } finally {
        state.bulk.asking = false;
    }
}

