// TagPup's page: Image Details' unsaved edits, saving them, and the queue every write to
// a photo goes through.
import { api } from './common/api.js';
import { dialogOpen } from './common/dialog.js';
import { buildElement, replaceContent } from './common/dom.js';
import { baseName } from './common/paths.js';
import { leafOf, photoAlreadyHas, tagProblem, textProblem } from './common/vocabulary.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import { btnSaveDetails, inputAddPerson, inputAddTag, inputPhotoTitle } from './elements.js';
import { setStatus } from './status.js';
import { saveToLocalStorageCache } from './cache.js';
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
    const photo = state.folderPhotos.find(p => p.path === state.activePhotoPath);
    if (!photo) return false;
    if (inputAddTag.value.trim() || inputAddPerson.value.trim()) return true;
    return inputPhotoTitle.value.trim() !== String(photo.title || '').trim();
}

export function updateSaveButton() {
    if (!btnSaveDetails) return;
    btnSaveDetails.disabled = Boolean(state.detailSaveInFlight) || !hasUnsavedEdits();
}

/** Put the panel back to what the photo holds. */
export function discardDetailEdits() {
    inputAddTag.value = '';
    inputAddPerson.value = '';
    const photo = state.activePhotoPath && state.folderPhotos.find(p => p.path === state.activePhotoPath);
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
                const photo = state.folderPhotos.find(p => p.path === state.activePhotoPath);
                input.value = photo ? (photo.title || '') : '';
            } else {
                input.value = '';
            }
            updateSaveButton();
            input.blur();
        });
    });

    if (btnSaveDetails) btnSaveDetails.addEventListener('click', () => saveDetailEdits());

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

    window.addEventListener('beforeunload', (e) => {
        if (!hasUnsavedEdits()) return;
        e.preventDefault();
        e.returnValue = '';
    });
}

/**
 * Write what the panel holds and the photo does not, as one request.
 *
 * `fields` narrows it to what Enter or an Add button is about; the header's Save
 * and Ctrl+S write everything. Resolves true once the photo holds what the panel
 * showed -- including when that needed no write -- and false when it does not, in
 * which case the typed text is left where it was.
 *
 * It waits its turn in the photo write queue and reads the fields when it runs,
 * so a second call sees what the first one wrote.
 */
export function saveDetailEdits(fields = { title: true, tags: true, people: true }) {
    const path = state.activePhotoPath;
    // The fields belong to the photo they were typed on. Leaving waits for the
    // queue, so this should always hold; if it does not, the text is not ours.
    return queuePhotoWrite(() => (state.activePhotoPath === path ? writeDetailEdits(fields) : true));
}

/**
 * Every write to a photo's metadata from this page, one at a time, in order.
 *
 * Each writer used to post its own snapshot of the whole tag list, taken when it
 * was clicked, and redraw whichever photo was open when the server answered. Two
 * quick writes raced and the later one dropped the other's change; a reply that
 * landed after moving on drew the old photo's tags and chips on the new one.
 *
 * So a job runs only after every write queued before it, and computes what it
 * writes from the photo's tags as they are then. It redraws only when its photo
 * is still the one shown (redrawIfShowing), and detailSaveInFlight is the tail of
 * the whole queue, which is what leaving a photo waits for. Resolves to the job's
 * result, or false if it threw; the queue carries on either way.
 */
export function queuePhotoWrite(job) {
    const before = state.detailSaveInFlight || Promise.resolve();
    const run = before.then(() => job()).catch(err => {
        // Resolving a name can fail too (the taxonomy write); that is a failed
        // save like any other, and the text stays where it is.
        console.error(err);
        setStatus('error', `Not saved: ${err.message}`, { transient: false });
        return false;
    }).finally(() => {
        if (state.detailSaveInFlight === run) state.detailSaveInFlight = null;
        updateSaveButton();
    });
    state.detailSaveInFlight = run;
    updateSaveButton();
    return run;
}

/** POST a photo's title and tags -- as they are now, unless given -- and check the reply. */
export async function postPhotoMetadata(photo, { title = photo.title, tags = photo.tags || [], ...extra } = {}) {
    const res = await api.fetch('/api/photo/save-metadata', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: photo.path, title, tags, ...extra })
    });
    const data = await res.json();
    if (!data.success) throw new Error(data.error || 'Failed to save');
    return data;
}

/** After a write: redraw the panel for this photo, but only if it is still the one open. */
export function redrawIfShowing(photo) {
    if (state.activePhotoPath !== photo.path) return;
    upper.renderTags(photo.tags);
    upper.renderSuggestionsPanel(photo.path);
    upper.updateCarryForwardState();
}

export async function writeDetailEdits(fields) {
    const path = state.activePhotoPath;
    const photo = path && state.folderPhotos.find(p => p.path === path);
    if (!photo) return true;

    const typedTitle = inputPhotoTitle.value.trim();
    const tagText = fields.tags ? inputAddTag.value : '';
    const personText = fields.people ? inputAddPerson.value : '';
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
        if (photoAlreadyHas({ tags }, item.tag, namesAPerson)) continue;
        tags.push(item.tag);
        added.push(item);
    }

    const clearTyped = () => {
        if (state.activePhotoPath !== photo.path) return;
        // Only what was written: text typed while the request was out stays.
        if (fields.tags && inputAddTag.value === tagText) inputAddTag.value = '';
        if (fields.people && inputAddPerson.value === personText) inputAddPerson.value = '';
    };

    if (!added.length && newTitle === null) {
        clearTyped();
        return true;
    }

    setStatus('busy', 'Saving...');
    let data;
    try {
        const res = await api.fetch('/api/photo/save-metadata', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                path,
                title: newTitle === null ? photo.title : newTitle,
                tags,
            })
        });
        data = await res.json();
        if (!data.success) throw new Error(data.error || 'Failed to save');
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
        photo.path = data.new_path;
        photo.filename = baseName(data.new_path);
    }

    // The file is written. Whatever goes wrong redrawing the page from here on
    // is logged, not reported as a failed save: saying "Not saved" about a
    // photo that was saved would keep you on it, offering to save it again.
    try {
        refreshAfterDetailSave(photo, path, newTitle, added);
    } catch (err) {
        console.error('Saved, but redrawing the page failed:', err);
    }
    setStatus('ready', `Saved ${photo.filename || baseName(photo.path)}`);
    return true;
}

export function refreshAfterDetailSave(photo, path, newTitle, added) {
    saveToLocalStorageCache();
    if (path !== photo.path && state.activePhotoPath === path) {
        state.activePhotoPath = photo.path;
        upper.selectPhoto(photo.path);
    }
    if (state.activePhotoPath === photo.path) upper.renderTags(photo.tags);
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
 * discarded. False means stay -- Cancel, or a save that failed. A save already
 * under way is waited for rather than asked about.
 */
export async function confirmLeavingPhoto() {
    // The whole queue, not just the write that was running when this began: one
    // queued behind it may be about to write the very text the question below
    // would offer to discard.
    while (state.detailSaveInFlight) await state.detailSaveInFlight;
    if (!hasUnsavedEdits()) return true;
    if (state.leavePrompt) return false;   // already asking; this route waits its turn

    const photo = state.folderPhotos.find(p => p.path === state.activePhotoPath);
    const name = (photo && photo.filename) || baseName(state.activePhotoPath);
    state.leavePrompt = askToSaveEdits(name);
    let choice;
    try {
        choice = await state.leavePrompt;
    } finally {
        state.leavePrompt = null;
    }
    if (choice === 'save') return saveDetailEdits();
    if (choice === 'discard') {
        discardDetailEdits();
        setStatus('ready', `Discarded changes to ${name}`);
        return true;
    }
    return false;
}

/**
 * Run a move off the photo, asking first if that would drop unsaved edits.
 *
 * Returns true when the move ran now; otherwise it runs once the question is
 * answered, if the answer allows it.
 */
export function leavePhotoThen(move, { onStay } = {}) {
    if (!hasUnsavedEdits() && !state.detailSaveInFlight) {
        move();
        return true;
    }
    confirmLeavingPhoto().then(ok => {
        if (ok) move();
        else if (onStay) onStay();
    });
    return false;
}

/**
 * "Save changes to IMG_0001.jpg?" -- Save, Discard or Cancel.
 *
 * Save is focused, so Enter saves; Escape cancels. Resolves 'save', 'discard' or
 * 'cancel'. Built like showPlacementModal, from the page's modal classes.
 */
export function askToSaveEdits(fileName) {
    return new Promise((resolve) => {
        const returnFocusTo = document.activeElement;
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
            if (choice === 'cancel' && returnFocusTo && returnFocusTo.focus) {
                returnFocusTo.focus();
            }
            resolve(choice);
        };
        overlay.querySelectorAll('[data-choice]').forEach(btn => {
            btn.addEventListener('click', () => close(btn.dataset.choice));
        });
        overlay.addEventListener('keydown', (e) => {
            // Nothing underneath hears these keys while the question is open.
            e.stopPropagation();
            if (e.key === 'Escape') {
                e.preventDefault();
                close('cancel');
            } else if (e.key === 'Enter') {
                e.preventDefault();
                const focused = document.activeElement;
                const choice = focused && overlay.contains(focused) && focused.dataset.choice;
                close(choice || 'save');
            }
        });
        overlay.querySelector('[data-choice="save"]').focus();
    });
}
