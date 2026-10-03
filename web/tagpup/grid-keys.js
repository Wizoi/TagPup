// TagPup's page: the grid, operated from the keyboard (docs/ARCHITECTURE.md, phase 9c). The grid is ONE tab stop
// (roving tabindex: the card the arrow keys are on has tabindex 0, the others -1, and Tab leaves the grid after it):
//
//   Arrows        one card left or right, one row up or down;        Home / End   the first / the last photo of the view
//   PageUp/Down   a window (the rows in view);                       Enter        opens the photo in the details panel
//   Space         selects or deselects the photo;                    Shift+Space  selects from the last one picked to this one
//   Shift+arrows  extend the selection from where the move began (each step adds; stepping back does not take away).
//
// The moves are by INDEX in the view's order, not by card, so they cross the window's edge and 20,000 photos: the grid
// scrolls to the index (the window is drawn around it), and focus goes to its card -- or, if the card has not arrived
// (a placeholder is not focusable), waits on the grid itself and goes to the card when it is drawn. The focused card
// is kept in the DOM by vgrid.js while it holds the focus, so a scroll by the wheel does not lose the place.
//
// What each key does to a photo (open it, select it) is grid.js's; this is the keys and the tab stop.
import { state } from './state.js';
import { thumbnailsGrid } from './elements.js';

/**
 * The index a key moves to from `current`, or null for a key that moves nowhere. `columns` is the grid's, `pageRows` the
 * rows in view. Pure, so the keys can be tested without a page.
 */
export function gridKeyTarget(key, current, count, columns, pageRows) {
    if (count <= 0) return null;
    const last = count - 1;
    const wide = Math.max(1, columns);
    if (current < 0) return ['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp', 'PageDown', 'PageUp', 'Home', 'End'].includes(key)
        ? (key === 'End' ? last : 0) : null;
    const at = Math.min(current, last);
    switch (key) {
        case 'ArrowRight': return Math.min(at + 1, last);
        case 'ArrowLeft': return Math.max(at - 1, 0);
        case 'ArrowDown': {
            if (at + wide <= last) return at + wide;
            // A short last row: a card below this one's column is not there, but a row below is: its last card.
            return Math.floor(at / wide) < Math.floor(last / wide) ? last : at;
        }
        case 'ArrowUp': return at - wide >= 0 ? at - wide : at;
        case 'PageDown': return Math.min(at + wide * Math.max(1, pageRows), last);
        case 'PageUp': return Math.max(at - wide * Math.max(1, pageRows), 0);
        case 'Home': return 0;
        case 'End': return last;
        default: return null;
    }
}

/** What a screen reader hears of a card: its file name, its date, whether it is selected, and what is wrong with it. */
export function cardLabel({ name, date, selected, damaged, stale }) {
    const parts = [name || 'Photo'];
    if (date) parts.push(date);
    parts.push(selected ? 'selected' : 'not selected');
    if (damaged) parts.push('damaged');
    if (stale === 'missing') parts.push('file missing');
    else if (stale === 'changed') parts.push('changed on disk');
    return parts.join(', ');
}

function indexOfCard(card) {
    let found = -1;
    state.grid.eachCard((each, record, index) => {
        if (each === card) found = index;
    });
    return found;
}

function cardAtIndex(index) {
    let found = null;
    state.grid.eachCard((card, record, at) => {
        if (at === index) found = card;
    });
    return found;
}

/** The tab stop after a draw: the card at the keys' index, else the first real card; placeholders take no focus. */
export function settleRoving() {
    const grid = state.grid;
    if (!grid) return;
    const keys = state.gridKeys;
    let stop = null;
    let first = null;
    grid.eachCard((card, record, index) => {
        if (card.classList.contains('placeholder')) {
            card.removeAttribute('tabindex');
            return;
        }
        card.tabIndex = -1;
        if (first === null) first = card;
        if (index === keys.index) stop = card;
    });
    const tab = stop || first;
    if (tab) tab.tabIndex = 0;
    // The focus was waiting for a card to arrive: it goes to it.
    if (stop && keys.waiting && thumbnailsGrid.ownerDocument.activeElement === thumbnailsGrid) {
        keys.waiting = false;
        stop.focus({ preventScroll: true });
    }
}

function currentIndex(target) {
    if (target && target !== thumbnailsGrid) {
        const at = indexOfCard(target);
        if (at >= 0) return at;
    }
    return state.gridKeys.index;
}

function goTo(index) {
    const keys = state.gridKeys;
    keys.index = index;
    state.grid.scrollToIndex(index, 'nearest');
    const card = cardAtIndex(index);
    if (card && !card.classList.contains('placeholder')) {
        keys.waiting = false;
        settleRoving();
        card.focus({ preventScroll: true });
    } else {
        // The card is not here yet (the grid asked for it as it drew its place): the focus waits on the grid.
        keys.waiting = true;
        thumbnailsGrid.focus({ preventScroll: true });
    }
}

/**
 * Listen on the grid. `handlers`: count() photos in the view; open(i); toggle(i, shift); extend(from, to).
 */
export function wireGridKeys(handlers) {
    thumbnailsGrid.addEventListener('focusin', (e) => {
        const card = e.target.closest ? e.target.closest('.thumbnail-card') : null;
        if (!card || card.classList.contains('placeholder') || !state.grid) return;
        const at = indexOfCard(card);
        if (at >= 0 && at !== state.gridKeys.index) {
            state.gridKeys.index = at;
            settleRoving();
        }
    });
    thumbnailsGrid.addEventListener('keydown', (e) => {
        if (e.ctrlKey || e.metaKey || e.altKey || !state.grid) return;
        // A title being typed, a button in a card: theirs. Only a card or the grid itself is the keys'.
        if (e.target !== thumbnailsGrid && !(e.target.classList && e.target.classList.contains('thumbnail-card'))) return;
        const count = handlers.count();
        if (count <= 0) return;
        const keys = state.gridKeys;
        const at = currentIndex(e.target);
        if (e.key === 'Enter') {
            if (at < 0) return;
            e.preventDefault();
            handlers.open(at);
            return;
        }
        if (e.key === ' ') {
            e.preventDefault();
            if (at < 0) return;
            keys.anchor = -1;
            handlers.toggle(at, e.shiftKey);
            return;
        }
        const geometry = state.grid.geometry() || { columns: 1, rows: 10 };
        const to = gridKeyTarget(e.key, at, count, geometry.columns, geometry.rows);
        if (to === null) return;
        e.preventDefault();
        e.stopPropagation();
        if (e.shiftKey && at >= 0) {
            if (keys.anchor < 0) keys.anchor = at;
            handlers.extend(keys.anchor, to);
        } else {
            keys.anchor = -1;
        }
        goTo(to);
    });
}
