// TagPup's page: a photo whose file is not as the library's row says (docs/ARCHITECTURE.md, phase 9c). The cards of
// a library view come with `stale` (GET /api/library/cards looks at each file once): "changed" when the file's size
// and time no longer describe the row, "missing" when the file is gone. Neither is hidden:
//
//  * a card carries a small badge -- "changed on disk" or "missing" -- and a screen reader hears it;
//  * a MISSING photo is shown (the library still holds its row) and is not editable: opening it shows a notice, and
//    every control that writes is inert (`data-writes` in the page). The server refuses a write to a missing file as
//    well; this is the page being honest about it before a click is wasted;
//  * a CHANGED photo, when it is opened, has been read from its file (GET /api/library/photo, 9b-2), so what the panel
//    shows is the file's: the badge clears on the card. The library's row is sync's to bring up to date; a view
//    refreshed before it has will say "changed" again, which is true.
//
// Any other photo offers the edits its record's `writes` names (tagpup.files.field_values.writes, the one statement of
// what a save may change): each control's `data-writes` names the field it changes, and a control whose field is not
// named is inert, with a sentence naming what is not edited. A photo's record names all five; a clip's its tags, title,
// name and date (fields.CLIP_WRITES), so its turn is not offered (the rotate buttons are hidden). The delete (`data-deletes`) is no write of a field: a
// clip is deleted as a photo is (recycle_bin, up to its MOST_CLIP_BYTES, a larger one for good once confirmed), and the
// button is inert only while the file is missing.
//
// A photo with tag changes waiting for it (pending.js pendingOf) is read-only until they are written: every `data-writes`
// control is inert, with a sentence saying how many wait. Viewing it, the arrows and moving on stay free; its Delete
// is offered, and refused with a sentence while any tag change for it is not written (pending.js deleteWaits).
import { buildElement } from './common/dom.js';
import { state } from './state.js';
import { mainImage, photoMissingNote } from './elements.js';
import { staleText } from './library-source.js';


/** On a card being built: its badge and mark, when its photo is stale. */
export function markStale(card, photo) {
    const text = staleText(photo.stale);
    card.classList.toggle('stale', Boolean(text));
    card.classList.toggle('stale-missing', photo.stale === 'missing');
    if (!text) return;
    const wrapper = card.querySelector('.thumbnail-img-wrapper');
    if (!wrapper) return;
    wrapper.appendChild(buildElement('span', {
        className: 'thumbnail-stale', text,
        title: photo.stale === 'missing'
            ? 'The library holds this photo but its file is not where the library says it is. It is shown as the library holds it, and cannot be edited.'
            : 'The file has changed since the library read it. Opening it reads the file itself.',
    }));
}

// What the sentence calls each field a control's `data-writes` names, in the order it names them.
// The turn is not among them: a record that leaves it out has its rotate controls hidden (applyPhotoStale), not explained.
const FIELD_WORDS = { date: 'date', title: 'title', tags: 'people and keywords' };

// The reply says a file is gone, not whether its folder is too: one sentence for both (the next full sync drops a photo
// whose folder is there; one whose folder is gone stays, as an unplugged drive's does: DECISIONS 2026-10-28).
const MISSING_SENTENCE = 'This photo’s file is not on disk, so it cannot be edited. If only the file is gone, the next full sync '
    + 'drops the photo. If its whole folder is gone, TagPup keeps the photo in case the drive or share comes back; '
    + 'to drop it, remove its folder in Manage folders.';

/** The sentence of a photo with `waiting` tag changes not written yet. */
function waitingSentence(waiting) {
    return waiting === 1
        ? '1 tag change is waiting for this photo. Edit it when it is written.'
        : `${waiting.toLocaleString()} tag changes are waiting for this photo. Edit it when they are written.`;
}

/** The sentence of a photo whose record's `writes` leaves out `held` (fields of FIELD_WORDS), or '' when none is. */
function heldSentence(held) {
    const words = Object.keys(FIELD_WORDS).filter((field) => held.has(field)).map((field) => FIELD_WORDS[field]);
    if (!words.length) return '';
    const listed = words.length === 1 ? words[0] : `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`;
    return `Not edited here: ${listed}.`;
}

/**
 * The details panel shows `photo`: a missing one gets the notice and every write control inert; any other has live
 * exactly the controls whose field its `writes` names, and a sentence naming the others. Called for every photo shown,
 * so the state of one photo is never carried to the next. No photo (none found) offers no edit.
 */
/**
 * A delete of the panel's photo is out (`on`) or answered: its Delete (`data-deletes`) is inert from the click to the
 * reply, for a photo as for a clip, whose copy through this PC can take minutes; a second click would start a second copy into
 * the same `.partial`. Answered (`on` false), the Delete is offered as applyPhotoStale offers it for `photo`, the record shown now.
 */
export function holdDeletes(on, photo) {
    state.deleting = on;
    for (const el of document.querySelectorAll('[data-deletes]')) inertUnless(el, !deletesHeld(photo));
}

/** Is the Delete of `photo` held: no photo, its file missing, or a delete of the panel's photo out. */
function deletesHeld(photo) {
    return !photo || Boolean(photo.missing) || state.deleting;
}

/** The note's button: the gear's Manage folders, where a gone folder is marked "not on disk". */
photoMissingNote.addEventListener('click', (e) => {
    if (e.target.closest('button')) document.querySelector('#gear-menu [data-action="manage-folders"]')?.click();
});

function inertUnless(el, offered) {
    if (offered) {
        el.removeAttribute('inert');
        el.removeAttribute('aria-disabled');
    } else {
        el.setAttribute('inert', '');
        el.setAttribute('aria-disabled', 'true');
    }
}

/**
 * The panel's controls for `photo` and the sentence over them: a missing one has every write control inert, one with
 * `waiting` tag changes (pending.js pendingOf) too; any other has live the controls whose field its `writes` names.
 * pending.js calls it again for the open photo whenever the changes waiting for it change.
 */
export function offerWrites(photo, waiting) {
    const missing = Boolean(photo && photo.missing);
    const writes = (photo && !missing && !waiting && photo.writes) || [];
    const controls = [...document.querySelectorAll('[data-writes]')];
    const held = new Set(controls.map((el) => el.dataset.writes).filter((field) => !writes.includes(field)));
    const sentence = missing
        ? MISSING_SENTENCE
        : (!photo ? '' : waiting ? waitingSentence(waiting) : heldSentence(held));
    photoMissingNote.classList.toggle('hidden', !sentence);
    photoMissingNote.textContent = sentence;
    if (missing) photoMissingNote.append(' ', buildElement('button', {
        className: 'link-button', text: 'Open Manage folders', attrs: { type: 'button' },
    }));
    const deletes = [...document.querySelectorAll('[data-deletes]')];
    for (const el of controls.concat(deletes)) {
        inertUnless(el, !(el.hasAttribute('data-deletes') ? deletesHeld(photo) : held.has(el.dataset.writes)));
    }
}

/** `waiting`: the tag changes waiting for `photo` (pending.js pendingOf). */
export function applyPhotoStale(photo, waiting) {
    const missing = Boolean(photo && photo.missing);
    // A clip cannot be turned: when its record names its writes and leaves the turn out, the rotate controls are not
    // offered at all (a missing file, no photo, or a record naming no writes shows them inert, as every control is).
    const turnless = Boolean(photo) && !missing && Array.isArray(photo.writes) && !photo.writes.includes('rotate');
    // No picture to show, and no broken-image icon in its place.
    mainImage.classList.toggle('hidden', missing);
    for (const el of document.querySelectorAll('[data-writes="rotate"]')) el.classList.toggle('hidden', turnless);
    offerWrites(photo, waiting);
    const lib = state.library;
    if (!lib || !photo || photo.id === undefined) return;
    const card = lib.cards.get(photo.id);
    if (!card) return;
    // Opened, it was read from the file: a changed card is current. A missing one is missing, whatever the card said.
    const now = missing ? 'missing' : (card.stale === 'changed' ? undefined : card.stale);
    if (now === card.stale) return;
    if (now === undefined) delete card.stale;
    else card.stale = now;
    if (state.grid) state.grid.patch([`#${photo.id}`]);
}
