// TagPup's page: Image Details' unsaved edits, saving them, and the queue every write to
// a photo goes through.
import { dialogOpen, holdFocus, releaseFocus } from './common/dialog.js';
import { buildElement, replaceContent } from './common/dom.js';
import { baseName, pathKey, rekeyed, renamedBy, samePath } from './common/paths.js';
import { UNREADABLE_SAVE, inTurn, postOrSendAgain } from './common/photo-write.js';
import { leafOf, photoAlreadyHas, tagProblem, textProblem } from './common/vocabulary.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import { selectionRenamed } from './selected.js';
import {
    btnSaveDetails, inputAddPerson, inputAddTag, inputPhotoTitle, peopleDatalist, tagsDatalist
} from './elements.js';
import { setStatus } from './status.js';
import { MOVE_WAIT_SHOWN_AFTER_MS, hideMoveWaiting, showMoveWaiting } from './move-waits.js';
import { photosGone, recordsWritten } from './cache.js';
import {
    dismissEntry, markEntry, pendingWrites, photosOut, pictureCheckSaid, queueEntry, whereWritten
} from './write-queue.js';
import {
    fetchKnownTagsAndPeople, namesAPerson, resolveTagOrPerson, updateTagsDatalist
} from './tags.js';

// ---- Unsaved edits in Image Details -------------------------------------
//
// Enter, Add and a click on a pill still write at once, as they always have.
// What this covers is the text sitting in a field that nothing has written yet.
//
// It used to be committed when the field lost focus, and that lost it anyway:
// the commit resolved the tag first -- a round trip to the server for a pathed
// tag or a new person -- and clicking the next photo blurs the field on mousedown
// and navigates on click, long before that returns. selectPhoto had cleared the
// field by then, so the save found nothing to save. A title was never committed
// on leaving at all, and a brand-new keyword only said "press Enter".
//
// So nothing is written on blur. The panel knows when it holds something the
// photo does not; the header's Save button and Ctrl+S write it; and every way off
// the photo asks first. That is one question in one place, where the blur commit
// was a race against every route away.

/** Does the panel show anything the photo does not hold yet? */
export function hasUnsavedEdits() {
    if (!state.activePhotoPath) return false;
    const photo = state.folderPhotos.find(p => samePath(p.path, state.activePhotoPath));
    if (!photo) return false;
    if (inputAddTag.value.trim() || inputAddPerson.value.trim()) return true;
    return inputPhotoTitle.value.trim() !== String(photo.title || '').trim();
}

export function updateSaveButton() {
    if (!btnSaveDetails) return;
    btnSaveDetails.disabled = Boolean(openPhotoWrite()) || !hasUnsavedEdits();
}

/** Put the panel back to what the photo holds. */
export function discardDetailEdits() {
    inputAddTag.value = '';
    inputAddPerson.value = '';
    const photo = state.activePhotoPath && state.folderPhotos.find(p => samePath(p.path, state.activePhotoPath));
    inputPhotoTitle.value = photo ? (photo.title || '') : '';
    updateSaveButton();
}

export function wireUnsavedEdits() {
    [inputPhotoTitle, inputAddTag, inputAddPerson].forEach(input => {
        input.addEventListener('input', updateSaveButton);
    });

    // Escape is the way out of text you have decided against: the tag and person
    // fields empty, the title goes back to what the photo has.
    [inputAddTag, inputAddPerson, inputPhotoTitle].forEach(input => {
        input.addEventListener('keydown', (e) => {
            if (e.key !== 'Escape') return;
            if (input === inputPhotoTitle) {
                const photo = state.folderPhotos.find(p => samePath(p.path, state.activePhotoPath));
                input.value = photo ? (photo.title || '') : '';
            } else {
                input.value = '';
            }
            updateSaveButton();
            input.blur();
        });
    });

    if (btnSaveDetails) btnSaveDetails.addEventListener('click', () => saveDetailEdits());

    // A tag or person picked from the box's list is saved by the pick: the Enter that picked it was a second Enter short (#2834).
    [[inputAddTag, tagsDatalist, { tags: true }], [inputAddPerson, peopleDatalist, { people: true }]].forEach(([box, list, fields]) => {
        box.addEventListener('input', (e) => {
            if (pickedFromList(e, box, list)) saveDetailEdits(fields);
        });
    });

    // Ctrl+S / Cmd+S. Captured, so it works from inside the field being typed in --
    // which is where you are when you reach for it -- and always kept from the
    // browser, whose Save Page dialog is never what was meant here.
    document.addEventListener('keydown', (e) => {
        if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
        if (e.key !== 's' && e.key !== 'S') return;
        e.preventDefault();
        // Not while a dialog is open: what it asks is not settled yet.
        if (state.leavePrompt || dialogOpen()) return;
        if (hasUnsavedEdits()) saveDetailEdits();
    }, true);

    // Closing the tab asks while a write waits or is under way too: it is lost.
    window.addEventListener('beforeunload', (e) => {
        if (!hasUnsavedEdits() && !pendingWrites()) return;
        e.preventDefault();
        e.returnValue = '';
    });
    // A page going with a write under way (reloaded, another library chosen, closed) abandons its fetch, written or not:
    // every scan this browser keeps holding its photos is forgotten, so opened again it is scanned (cache.js photosGone).
    window.addEventListener('pagehide', () => photosGone(photosOut()));
}

/**
 * Was this `input` event a pick from the box's list? A key typed or a paste says how it came (`insertText`,
 * `insertFromPaste`); a pick is the browser's own replacement of the text -- Chrome's event carries no inputType, Firefox's
 * says `insertReplacementText` -- and leaves the box holding one of the list's entries exactly.
 */
export function pickedFromList(e, box, list) {
    const how = e.inputType || '';
    if (how && how !== 'insertReplacementText') return false;
    const value = box.value;
    return Boolean(value) && Array.from(list.options).some(option => option.value === value);
}

/**
 * Write what the panel holds and the photo does not, as one request.
 *
 * `fields` narrows it to what Enter, a pick from a list or an Add button is about; the header's Save
 * and Ctrl+S write everything. Resolves true once the photo holds what the panel
 * showed -- including when that needed no write -- and false when it does not, in
 * which case the typed text is given back to its box.
 *
 * The text is taken when the save is asked for, for the photo open then, and the tag and person boxes are emptied
 * at once: a key typed while the save waits its turn or is out is the start of the next tag, where it used to be
 * added to the end of the one being saved and stay there (#2836). A second Enter finds the box empty and asks for nothing.
 */
export function saveDetailEdits(fields = { title: true, tags: true, people: true }, intent = null) {
    // A retry names the photo and the text the failed save held (its entry's `intent`), not the panel as it is now.
    const path = intent ? intent.path : state.activePhotoPath;
    if (!path) return Promise.resolve(true);
    const taken = intent ? null : takeTyped(path, fields);
    if (taken && !fields.title && !taken.tagText.trim() && !taken.personText.trim()) return Promise.resolve(true);
    return queueWriteOf(path, async (entry) => {
        let written = false;
        try {
            written = await writeDetailEdits(fields, { intent, taken, path, entry });
            return written;
        } finally {
            if (taken && written === false) giveBack(taken, fields);
        }
    }, `Save photo ${baseName(path)}`, (entry) => saveDetailEdits(fields, entry.intent || null));
}

/** The text a save is asked with, as the panel holds it now; the tag and person boxes it takes are emptied. */
function takeTyped(path, fields) {
    const taken = {
        path,
        title: inputPhotoTitle.value.trim(),
        tagText: fields.tags ? inputAddTag.value : '',
        personText: fields.people ? inputAddPerson.value : '',
    };
    if (fields.tags) inputAddTag.value = '';
    if (fields.people) inputAddPerson.value = '';
    updateSaveButton();
    return taken;
}

/**
 * A save that failed gives its text back to the box it came from, to be tried again -- while its photo is open and the
 * box is empty. Text typed since is not overwritten; the failed save's Retry (the queue's status) still holds its own.
 */
function giveBack(taken, fields) {
    if (!samePath(state.activePhotoPath, taken.path)) return;
    if (fields.tags && !inputAddTag.value) inputAddTag.value = taken.tagText;
    if (fields.people && !inputAddPerson.value) inputAddPerson.value = taken.personText;
    updateSaveButton();
}

/**
 * queuePhotoWrite, for a write of one photo, `path`: the one leaving that photo waits
 * for (openPhotoWrite). A bulk write is queued without one, and leaving waits for none:
 * an arrow key waited for a slow bulk write on a network share to finish.
 */
export function queueWriteOf(path, job, label = `Save photo ${baseName(path)}`, again = () => queueWriteOf(path, job, label)) {
    const key = pathKey(path);
    // A failed save's Retry is a write of this photo too: the one leaving the photo waits for.
    const run = queuePhotoWrite(() => [path], job, label, again).finally(() => {
        if (state.photoWrites[key] === run) delete state.photoWrites[key];
        updateSaveButton();
    });
    state.photoWrites[key] = run;
    updateSaveButton();
    return run;
}

/** The write of the open photo queued or under way, if any: what leaving it waits for. */
export function openPhotoWrite() {
    const path = state.activePhotoPath;
    if (!path) return null;
    // The faces named on it write after their tags, and are the photo's writes too.
    const faces = state.faceBoxes.writes && state.faceBoxes.path && samePath(state.faceBoxes.path, path) ? state.faceBoxes.writes : null;
    return state.photoWrites[pathKey(path)] || faces;
}

/**
 * Every write to a photo's metadata from this page, one at a time, in order: a
 * bulk tag write (selection.js) and Apply All (suggestions.js) too, which posted at
 * once and met on the server, each planned from the files as they were before the
 * others wrote.
 *
 * Each writer used to post its own snapshot of the whole tag list, taken when it
 * was clicked, and redraw whichever photo was open when the server answered. Two
 * quick writes raced and the later one dropped the other's change; a reply that
 * landed after moving on drew the old photo's tags and chips on the new one.
 *
 * So a job runs only after every write queued before it, and computes what it
 * writes from the photo's tags as they are then. It redraws only when its photo
 * is still the one shown (redrawIfShowing). detailSaveInFlight is the tail of the
 * whole queue (photo-write.js inTurn, the phone's line too); leaving a photo waits only for that photo's writes
 * (queueWriteOf). Resolves to the job's result, or false if it threw; the queue carries on either way.
 */
/**
 * Queue `job`, a write to photo files, behind every write queued before it. `photos()` gives the paths it sends, asked
 * while it is out: the page going then forgets every scan kept of them (write-queue.js photosOut). A photo of a
 * folder the library does not hold is written to its file only, which the server decides per
 * photo (tagpup.services.file_only); the queue does not ask.
 */
export function queuePhotoWrite(photos, job, label = 'Save a photo', again = () => queuePhotoWrite(photos, job, label)) {
    // `again` is given the entry that failed: a save's entry holds what it tried to write (`intent`).
    // An entry of the queue's status (write-queue.js): `label` says what it does, and
    // the job is given it, to put a failure's reason in `error`. A job that resolves
    // false failed; Retry queues it again, as `again` says (a write of one photo is queued again as that photo's).
    const entry = queueEntry(label, photos);
    entry.retry = () => again(entry);
    const run = inTurn(state, 'detailSaveInFlight', () => {
        markEntry(entry, 'writing');
        upper.holdForWrite();   // a clip playing lets go of its file for the write, and plays on after the last one
        return job(entry);
    }, () => upper.resumeAfterWrite()).then(result => {
        markEntry(entry, result === false ? 'failed' : 'done');
        return result;
    }, err => {
        // Resolving a name can fail too (the taxonomy write); that is a failed
        // save like any other, and the text stays where it is.
        console.error(err);
        setStatus('error', `Not saved: ${err.message}`, { transient: false });
        entry.error = entry.error || err.message;
        markEntry(entry, 'failed');
        return false;
    }).finally(updateSaveButton);
    updateSaveButton();
    return run;
}

/**
 * The server refused a write because the file changed since the page read it: nothing was written. The photo
 * is read again -- the panel shows what the file holds now -- and the owner is told, in the status line that stays
 * and by the caller's own message; nothing is merged and nothing is overwritten. A save is sent once more first
 * (photo-write.js postOrSendAgain, given this by `onThisPage`); this is what is left when it was not, or was refused again.
 */
export function photoChangedOnDisk(photo) {
    return upper.reloadChangedPhoto(photo, () => setStatus('error',
        'This photo changed on disk since you opened it. It was read again: look at it, then save again.',
        { transient: false }));
}

/** What the status line says after a save sent once more after its file was read again (postOrSendAgain). */
const SENT_AGAIN = ' after reading its file again (indexing, or another program, had written to it)';

/**
 * What this page hands photo-write.js postOrSendAgain of its own: its way of showing a refusal (photoChangedOnDisk), and
 * whether its list still holds the photo -- the open folder's, or the view's one open photo -- which a save goes to only
 * then (the queue's own rule, writeDetailEdits).
 */
export const onThisPage = {
    changed: photoChangedOnDisk,
    stillListed: photo => state.folderPhotos.some(p => samePath(p.path, photo.path)),
};

/** The status line's words of a save, `said`; `sentAgain` and so, when it was sent once more after its file was read again. */
export function saidOfSave(reply, said, sentAgain = said) {
    return reply.readAgain ? sentAgain + SENT_AGAIN : said;
}

/**
 * A save renamed the photo's file (its caption is part of the name): everything the page keeps by the photo's path moves
 * to the new one -- the record, the selection and its suggestions (the server keeps those by the photo's id, so they
 * were never lost, only filed under a name the page no longer asks for; finding #2415). The one place a rename is taken.
 */
export function photoRenamed(photo, newPath) {
    const oldPath = photo.path;
    photo.path = newPath;
    photo.filename = baseName(newPath);
    photosRenamed({ [oldPath]: newPath });
}

/**
 * Photos were renamed, `renames` {old path: new path} (a save, a Convert, or Smart Rename's `updated_paths`): the page's
 * selection and suggestions move to the new names, each photo's its own however the renames chain or swap (renamedBy,
 * #3336). The records are the write's to move (cache.js recordsWritten, photosWritten: the kept scans' suggestions too).
 */
export function photosRenamed(renames) {
    const now = renamedBy(renames);
    state.folderSuggestions = rekeyed(state.folderSuggestions, now);
    selectionRenamed(now);
}

/** After a write: redraw the panel for this photo, but only if it is still the one open; its faces follow the write's `reply`. */
export function redrawIfShowing(photo, reply) {
    if (!samePath(state.activePhotoPath, photo.path)) return;
    upper.renderTags();
    upper.renderSuggestionsPanel(photo.path);
    upper.updateCarryForwardState();
    upper.facesFollowReply(photo.path, reply);
}

/**
 * Write what a save holds. `intent` is the text of a save that failed ({ path, title, tagText, personText }), written as it
 * was and not as the fields are now; `taken` is the text the save was asked with, in the same shape (takeTyped); without
 * either it is read from the fields. It is kept on `entry` so a Retry can write it (findings #877). Once it is written, an
 * earlier failed save of the photo is settled (findings #881).
 */
export async function writeDetailEdits(fields, { intent = null, taken = null, path = state.activePhotoPath, entry = null } = {}) {
    const photo = path && state.folderPhotos.find(p => samePath(p.path, path));
    if (!photo) {
        if (!intent) return true;
        if (entry) entry.error = 'The photo is not in the open folder any more, so the text it held was not written';
        return false;
    }
    if (photo.unreadable) {
        setStatus('error', UNREADABLE_SAVE, { transient: false });
        alert(UNREADABLE_SAVE);
        return false;
    }

    const asked = intent || taken;
    const typedTitle = asked ? asked.title : inputPhotoTitle.value.trim();
    const tagText = asked ? asked.tagText : (fields.tags ? inputAddTag.value : '');
    const personText = asked ? asked.personText : (fields.people ? inputAddPerson.value : '');
    if (entry) entry.intent = { path, title: typedTitle, tagText, personText };
    const newTitle = fields.title && typedTitle !== String(photo.title || '').trim()
        ? typedTitle
        : null;

    // Resolve everything before writing anything. A name whose placement was
    // not settled stops the whole save, so that nothing is half-written and the
    // text stays in the field to be tried again.
    const typed = [
        ...splitTyped(tagText).map(text => ({ text, isPerson: false })),
        ...splitTyped(personText).map(text => ({ text, isPerson: true })),
    ];
    // A tag that cannot be set stops the save the same way, and says why; so does a
    // caption, when it is the one being changed.
    const refused = (newTitle === null ? null : textProblem(newTitle))
        || typed.map(item => tagProblem(item.text)).find(Boolean);
    if (refused) {
        setStatus('error', `Not saved: ${refused}`, { transient: false });
        alert(refused);
        return false;
    }
    const resolved = [];
    const unresolved = [];
    for (const item of typed) {
        const tag = await resolveTagOrPerson(item.text, item.isPerson);
        if (tag) resolved.push({ tag, isPerson: item.isPerson });
        else unresolved.push(item.text);
    }
    if (unresolved.length) {
        setStatus('error', `Not saved: ${unresolved.join(', ')} still needs a place`,
            { transient: false });
        return false;
    }

    const tags = (photo.tags || []).slice();
    const added = [];
    for (const item of resolved) {
        if (photoAlreadyHas({ tags }, item.tag, namesAPerson, state.people)) continue;
        tags.push(item.tag);
        added.push(item);
    }

    const clearTyped = () => {
        if (!samePath(state.activePhotoPath, photo.path)) return;
        // Only what was written (a failed save's text given back, then retried): text typed while the request was out stays.
        if (fields.tags && inputAddTag.value === tagText) inputAddTag.value = '';
        if (fields.people && inputAddPerson.value === personText) inputAddPerson.value = '';
    };

    if (!added.length && newTitle === null) {
        clearTyped();
        settleFailedSaves(path, entry);
        return true;
    }

    setStatus('busy', 'Saving...');
    let data;
    try {
        data = await postOrSendAgain(photo, { title: newTitle === null ? photo.title : newTitle, tags }, onThisPage);
    } catch (err) {
        console.error(err);
        setStatus('error', `Not saved: ${err.message}`, { transient: false });
        alert(`Error saving ${photo.filename || baseName(path)}: ${err.message}`);
        return false;
    }

    photo.tags = tags;
    if (newTitle !== null) {
        photo.title = newTitle;
        photo.captions = newTitle ? [newTitle] : [];
    }
    const addedPeople = added.filter(item => item.isPerson);
    if (addedPeople.length) {
        if (!photo.people) photo.people = [];
        addedPeople.forEach(item => {
            const leaf = leafOf(item.tag);
            if (!photo.people.includes(leaf)) photo.people.push(leaf);
        });
    }
    clearTyped();
    if (data.new_path && data.new_path !== path) {
        photoRenamed(photo, data.new_path);
    }

    // The file is written. Whatever goes wrong redrawing the page from here on
    // is logged, not reported as a failed save: saying "Not saved" about a
    // photo that was saved would keep you on it, offering to save it again.
    try {
        refreshAfterDetailSave(photo, path, newTitle, added, data);
    } catch (err) {
        console.error('Saved, but redrawing the page failed:', err);
    }
    if (data.rename_failed) {
        // The caption is saved, the file keeps its name: said, and kept on the line until the next thing happens.
        setStatus('error', `Saved the title, but could not rename the photo: ${data.rename_failed}${pictureCheckSaid(data)}`,
            { transient: false });
    } else {
        const said = pictureCheckSaid(data);
        const saved = `${saidOfSave(data, `Saved ${photo.filename || baseName(photo.path)}`)}.${whereWritten(data)}`;
        setStatus('ready', said ? saved + said : saved.replace(/\.$/, ''));
    }
    settleFailedSaves(path, entry);
    return true;
}

/** A save of the photo worked: an earlier save of it that failed is settled, and stops offering a Retry that would write it again. */
function settleFailedSaves(path, entry) {
    for (const each of state.writeQueue.entries.slice()) {
        if (each !== entry && each.status === 'failed' && each.intent && samePath(each.intent.path, path)) dismissEntry(each);
    }
}

export function refreshAfterDetailSave(photo, path, newTitle, added, reply) {
    // Every record of the photo the page holds or keeps takes the save, re-keyed when its caption renamed it.
    recordsWritten([photo], { [path]: photo.path });
    // `path !== photo.path` asks whether the file's spelling changed (a rename, even of case only): exact on purpose.
    if (path !== photo.path && samePath(state.activePhotoPath, path)) {
        state.activePhotoPath = photo.path;
        upper.selectPhoto(photo.path);
    }
    if (samePath(state.activePhotoPath, photo.path)) {
        upper.renderTags();
        upper.facesFollowReply(photo.path, reply);
    }
    if (newTitle !== null) {
        upper.renderFileList();
        upper.renderThumbnails();
    }
    if (added.length) {
        updateTagsDatalist();
        fetchKnownTagsAndPeople();
    }
}

/** "Beach, Rowan" typed into one field is two entries. */
export function splitTyped(text) {
    return String(text || '').split(',').map(t => t.trim()).filter(Boolean);
}

/**
 * May the panel move off this photo? Asks when it holds unsaved edits.
 *
 * Resolves true to go ahead: nothing was pending, it was saved, or it was
 * discarded. False means stay -- Cancel, a save that failed, or the waiting move
 * let go (letHeldMoveGo). A save already under way is waited for rather than
 * asked about, and a wait longer than a moment says so over the photo (move-waits.js).
 */
async function confirmLeavingPhoto(held) {
    // Every write of this photo, not just the one running when this began: one
    // queued behind it may be about to write the very text the question below
    // would offer to discard. Not the bulk writes: they write no typed text.
    if (openPhotoWrite()) {
        await whileWaiting(held, async () => {
            while (openPhotoWrite() && !held.gone) await openPhotoWrite();
        });
    }
    if (held.gone) return false;
    if (!hasUnsavedEdits()) return true;

    const photo = state.folderPhotos.find(p => samePath(p.path, state.activePhotoPath));
    const name = (photo && photo.filename) || baseName(state.activePhotoPath);
    state.leavePrompt = askToSaveEdits(name);
    let choice;
    try {
        choice = await state.leavePrompt;
    } finally {
        state.leavePrompt = null;
    }
    if (choice === 'save') return whileWaiting(held, saveDetailEdits);
    if (choice === 'discard') {
        discardDetailEdits();
        setStatus('ready', `Discarded changes to ${name}`);
        return true;
    }
    return false;
}

/** Wait for `work` (a write of the open photo) as the held move: a wait longer than a moment says so, and may be let go. */
async function whileWaiting(held, work) {
    const name = baseName(state.activePhotoPath);
    held.waiting = true;
    const shown = window.setTimeout(() => {
        if (!held.gone) held.note = showMoveWaiting(name, letHeldMoveGo);
    }, MOVE_WAIT_SHOWN_AFTER_MS);
    try {
        return await work();
    } finally {
        held.waiting = false;
        window.clearTimeout(shown);
        hideMoveWaiting(held.note);
        held.note = null;
    }
}

/**
 * Run a move off the photo, asking first if that would drop unsaved edits.
 *
 * Returns true when the move ran now; otherwise it runs once the photo's writes are
 * done and the question is answered, if the answer allows it.
 *
 * One move waits at a time (state.heldMove), the last one asked: arrow presses made
 * while a save was out each moved on once it was written, skipping photos (owner,
 * 2026-11-19). A move asked while one waits for the writes takes its place, and the one
 * replaced is told it stayed (its onStay, as Cancel tells it); one asked while the
 * question about the photo is open stays, as it always has.
 */
export function leavePhotoThen(move, { onStay } = {}) {
    const waiting = state.heldMove;
    if (waiting && state.leavePrompt) {
        if (onStay) onStay();
        return false;
    }
    if (waiting) {
        const replaced = waiting.onStay;
        Object.assign(waiting, { move, onStay });
        if (replaced) replaced();
        return false;
    }
    if (!hasUnsavedEdits() && !openPhotoWrite()) {
        move();
        return true;
    }
    const held = { move, onStay, gone: false, waiting: false, note: null };
    state.heldMove = held;
    moveWhenLeft(held);
    return false;
}

/** The held move, once the photo may be left: run, or told it stayed. Let go (letHeldMoveGo), it is neither. */
async function moveWhenLeft(held) {
    let ok = false;
    try {
        ok = await confirmLeavingPhoto(held);
    } finally {
        // A move asked after this one was let go holds the slot now, with its own wait.
        if (state.heldMove === held) state.heldMove = null;
    }
    if (held.gone) return;
    if (ok) held.move();
    else if (held.onStay) held.onStay();
}

/**
 * Let the move waiting for the photo's writes go (Escape, or the note's Stay here): the
 * writes go on, the photo stays, and the move is told it stayed. False when no move waits
 * for the writes (the question about the photo, when open, keeps its own Escape).
 */
export function letHeldMoveGo() {
    const held = state.heldMove;
    if (!held || !held.waiting) return false;
    state.heldMove = null;
    held.gone = true;
    hideMoveWaiting(held.note);
    held.note = null;
    if (held.onStay) held.onStay();
    return true;
}

/**
 * "Save changes to IMG_0001.jpg?" -- Save, Discard or Cancel.
 *
 * Save is focused, so Enter saves; Escape cancels (web/common/dialog.js), and the focus goes
 * back to what had it however it closes. Resolves 'save', 'discard' or 'cancel'. Built like
 * showPlacementModal, from the page's modal classes.
 */
export function askToSaveEdits(fileName) {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay active unsaved-edits-modal';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        const choice = (className, value, text) =>
            buildElement('button', { className: `btn ${className}`, data: { choice: value }, text });
        overlay.appendChild(buildElement('div', { className: 'modal-container', style: 'max-width: 420px;' }, [
            buildElement('div', { className: 'modal-header' }, [
                // The file name goes in as text, never as markup.
                buildElement('h2', { text: `Save changes to ${fileName}?` }),
            ]),
            buildElement('div', { className: 'modal-body' }, [
                buildElement('p', {
                    style: 'margin: 0; color: var(--text-secondary); line-height: 1.5; font-size: 14px;',
                    text: 'This photo has edits that have not been written to the file.',
                }),
            ]),
            buildElement('div', { className: 'modal-footer' }, [
                choice('btn-secondary', 'cancel', 'Cancel'),
                choice('btn-secondary', 'discard', 'Discard'),
                choice('btn-primary', 'save', 'Save'),
            ]),
        ]));
        document.body.appendChild(overlay);

        const close = (choice) => {
            overlay.remove();
            releaseFocus(overlay);
            resolve(choice);
        };
        overlay.querySelectorAll('[data-choice]').forEach(btn => {
            btn.addEventListener('click', () => close(btn.dataset.choice));
        });
        overlay.addEventListener('keydown', (e) => {
            // Nothing underneath hears these keys while the question is open.
            e.stopPropagation();
            if (e.key === 'Enter') {
                e.preventDefault();
                const focused = document.activeElement;
                const choice = focused && overlay.contains(focused) && focused.dataset.choice;
                close(choice || 'save');
            }
        });
        holdFocus(overlay, { cancel: () => close('cancel'), focus: overlay.querySelector('[data-choice="save"]') });
    });
}
