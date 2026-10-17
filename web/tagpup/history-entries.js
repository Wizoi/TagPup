// TagPup's page: the places this load made in the tab's history (#770), kept in one place.
//
// Each place the page makes (a view opened, a photo opened over it) is tagged with this load's id and its position, and
// `state.entries.at` is the position of the place the page shows. Positions are consecutive, since a new place drops the places
// after the one it is made from; a Back or Forward cancelled for unsaved edits goes back to `at` (library-view.js stayAfterMove).
// A place another load made, or one a folder's address replaced, has no position (null).
import { state } from './state.js';

/**
 * A new place in the history, tagged with its position when the place the page is at has one: `entry` is its state, `url` its address.
 */
export function pushEntry(entry, url) {
    const at = state.entries.at;
    const pos = at === null ? null : at + 1;
    window.history.pushState(pos === null ? entry : { ...entry, entryLoad: state.entries.load, entryPos: pos }, '', url);
    state.entries.at = pos;
}

/**
 * The address of the place the page is at replaced -- a folder opened, or closed: no place is made or lost, so the place keeps its
 * position. What a view or a photo made it (a photo to open again, a search's distance back, a scroll) is not the folder's.
 */
export function replaceAddress(url) {
    const here = window.history.state;
    const kept = positionOf(here) !== null ? { entryLoad: here.entryLoad, entryPos: here.entryPos } : {};
    window.history.replaceState(kept, '', url);
}

/** The position of a place in the history this load made, or null. */
export function positionOf(entry) {
    return entry && entry.entryLoad === state.entries.load && Number.isInteger(entry.entryPos) ? entry.entryPos : null;
}
