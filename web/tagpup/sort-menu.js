// TagPup's page: Sort by, in a library view's floating header (#714; docs/ARCHITECTURE.md, phase 9, the owner's second review).
//
// The button opens a small menu of two sections, as Windows Live Photo Gallery's does: the field -- Date Taken, Caption, File
// name -- and the direction -- Ascending, Descending -- each a group of radio items showing the current choice. A choice reads
// the view again in that order (library-view.js openLibraryView: a new place in the history, so Back returns to the order before,
// and the address keeps `order`), and the views opened after it are read in it (state.nav.order, set as the view opens).
// Choosing what is already chosen closes the menu and asks for nothing.
//
// The keys: on the button, Enter and Space open and close it (the button's own click), ArrowDown opens it on the field chosen
// and ArrowUp on the direction; in it, ArrowDown and ArrowUp move through the five items (Home, End), Enter and Space choose,
// Escape closes it and gives the focus back to the button, Tab closes it. A click or the focus anywhere else closes it, and so
// does the view changing under it (Back, a navigator row): it is drawn for one view. The page steps through photos on the
// arrow keys: the button and the menu keep them (`data-own-keys`). Everything the menu holds is `state.sortMenu`.
import { state } from './state.js';
import { btnSortBy, sortByCurrent, sortMenu } from './elements.js';
import { openLibraryView } from './library-view.js';
import { SORT_FIELDS, orderLabel, orderOf, orderParts } from './library-source.js';

function items() {
    return [...sortMenu.querySelectorAll('[role="menuitemradio"]')];
}

/** Each item checked or not, as `order` is read. */
function markChecked(order) {
    const { field, descending } = orderParts(order);
    for (const item of items()) {
        const checked = item.dataset.field ? item.dataset.field === field : (item.dataset.direction === 'desc') === descending;
        item.setAttribute('aria-checked', checked ? 'true' : 'false');
    }
}

function onOutside(event) {
    if (sortMenu.contains(event.target) || btnSortBy.contains(event.target)) return;
    closeSortMenu();
}

/** Close the menu; with `returnFocus` the button has the focus again. */
export function closeSortMenu({ returnFocus = false } = {}) {
    if (!state.sortMenu.open) return;
    state.sortMenu.open = false;
    state.sortMenu.token = 0;
    sortMenu.classList.add('hidden');
    btnSortBy.setAttribute('aria-expanded', 'false');
    document.removeEventListener('mousedown', onOutside, true);
    document.removeEventListener('focusin', onOutside, true);
    if (returnFocus) btnSortBy.focus();
}

/** Open the menu for the view that is open, the focus on the checked item of the field's section or the direction's. */
function openSortMenu(section = 'field') {
    const lib = state.library;
    if (!lib || lib.invalid) return;
    markChecked(lib.order);
    if (!state.sortMenu.open) {
        state.sortMenu.open = true;
        sortMenu.classList.remove('hidden');
        btnSortBy.setAttribute('aria-expanded', 'true');
        document.addEventListener('mousedown', onOutside, true);
        document.addEventListener('focusin', onOutside, true);
    }
    state.sortMenu.token = lib.token;
    const wanted = section === 'direction' ? '[data-direction]' : '[data-field]';
    const target = items().find(item => item.matches(wanted) && item.getAttribute('aria-checked') === 'true') || items()[0];
    target.focus();
}

/** An item was chosen: the menu closes onto its button, and the view is read in the order the item makes of the one it has. */
function choose(item) {
    const lib = state.library;
    closeSortMenu({ returnFocus: true });
    if (!lib || lib.invalid) return;
    const was = orderParts(lib.order);
    const order = item.dataset.field ? orderOf(item.dataset.field, was.descending) : orderOf(was.field, item.dataset.direction === 'desc');
    if (order === lib.order) return;
    // state.nav.order follows when the view opens (navigator.js navigatorFollows): a person who stays on a photo with
    // unsaved edits keeps the view, and the order of the next one, as they were.
    openLibraryView({ kind: lib.kind, value: lib.value, recursive: lib.recursive, order });
}

/**
 * What the button says of the order the view is read in, and the menu closed if it was drawn for another view. Called as the
 * view changes and as it closes (library-view.js, through `upper`).
 */
export function showSortOrder() {
    const lib = state.library;
    if (state.sortMenu.open && (!lib || lib.invalid || lib.token !== state.sortMenu.token)) {
        closeSortMenu({ returnFocus: sortMenu.contains(document.activeElement) });
    }
    const order = lib && !lib.invalid ? lib.order : state.nav.order;
    const { field, descending } = orderParts(order);
    const named = SORT_FIELDS.find(each => each.field === field);
    sortByCurrent.textContent = `${named ? named.label : field} ${String.fromCodePoint(descending ? 0x2193 : 0x2191)}`;
    btnSortBy.setAttribute('aria-label', `Sort by: ${orderLabel(order)}`);
    btnSortBy.title = `Sorted by ${orderLabel(order)}: choose the field and the direction`;
    btnSortBy.disabled = !lib || lib.invalid;
    if (state.sortMenu.open) markChecked(order);
}

export function wireSortMenu() {
    btnSortBy.addEventListener('click', () => {
        if (state.sortMenu.open) closeSortMenu({ returnFocus: true });
        else openSortMenu('field');
    });
    btnSortBy.addEventListener('keydown', (event) => {
        if (event.ctrlKey || event.metaKey || event.altKey) return;
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            event.stopPropagation();
            openSortMenu(event.key === 'ArrowUp' ? 'direction' : 'field');
        } else if (event.key === 'Escape' && state.sortMenu.open) {
            event.preventDefault();
            event.stopPropagation();
            closeSortMenu({ returnFocus: true });
        }
    });
    sortMenu.addEventListener('keydown', (event) => {
        const all = items();
        const at = all.indexOf(document.activeElement);
        const moves = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: all.length - 1 };
        if (event.key in moves) {
            event.preventDefault();
            event.stopPropagation();   // the page steps through photos on the arrow keys
            all[(moves[event.key] + all.length) % all.length].focus();
        } else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
            event.preventDefault();    // one column: nowhere to go, and not the page's
            event.stopPropagation();
        } else if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            closeSortMenu({ returnFocus: true });
        } else if (event.key === 'Tab') {
            closeSortMenu();
        } else if ((event.key === 'Enter' || event.key === ' ') && at >= 0) {
            event.preventDefault();    // chosen here, not by the button's own click as well
            event.stopPropagation();
            choose(all[at]);
        }
    });
    sortMenu.addEventListener('click', (event) => {
        const item = event.target.closest('[role="menuitemradio"]');
        if (item && sortMenu.contains(item)) choose(item);
    });
    showSortOrder();
}
