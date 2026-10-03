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
import { buildElement } from './common/dom.js';
import { state } from './state.js';
import { photoMissingNote } from './elements.js';
import { libraryName } from './looking.js';

/** The badge's words for a card's `stale`, or ''. */
export function staleText(stale) {
    if (stale === 'missing') return 'missing';
    if (stale === 'changed') return 'changed on disk';
    return '';
}

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

/**
 * The details panel shows `photo`: a missing one gets the notice and inert write controls; any other gets them
 * back. Called for every photo shown, so the state of one photo is never carried to the next.
 */
export function applyPhotoStale(photo) {
    const missing = Boolean(photo && photo.missing);
    photoMissingNote.classList.toggle('hidden', !missing);
    photoMissingNote.textContent = missing
        ? `This photo’s file is not on disk. ${libraryName() || 'The library'} still holds it, and it is shown as the library holds it. `
            + 'It cannot be edited until the file is back or the photo is removed from the library.'
        : '';
    for (const el of document.querySelectorAll('[data-writes]')) {
        if (missing) {
            el.setAttribute('inert', '');
            el.setAttribute('aria-disabled', 'true');
        } else {
            el.removeAttribute('inert');
            el.removeAttribute('aria-disabled');
        }
    }
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
