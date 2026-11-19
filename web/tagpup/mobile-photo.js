/**
 * The phone's Photo screen (docs/plans/phone-viewer.md, Screens 4): for now it names the photo a card opened; the picture,
 * swiping and the edit sheet are the next cut. Its place in history is { id, at }: the photo's id and where it is in the
 * Grid's view, so Back is the Grid as it was left.
 */
import { api } from './common/api.js';
import { state } from './mobile-state.js';
import { samePlace } from './mobile-screens.js';

/** Name the photo: from the card the Grid holds, or, after a reload, its card read again. */
export function showPhoto() {
    const place = state.place;
    const name = document.getElementById('photo-name');
    const card = place && state.photos ? state.photos.cards.get(place.id) : null;
    name.textContent = card ? card.name : '';
    if (card || !place || !Number.isInteger(place.id)) return;
    api.json(`/api/library/cards?ids=${place.id}`)
        .then((body) => {
            const read = body && Array.isArray(body.cards) ? body.cards.find((each) => each && each.id === place.id) : null;
            if (read && state.screen === 'photo' && samePlace(state.place, place)) name.textContent = read.name;
        })
        .catch(() => {});   // the name is all this screen says yet: none is shown
}
