// TagPup's page: Sort by, in a library view's floating header (#714; docs/ARCHITECTURE.md, phase 9, the owner's second review).
//
// The button opens a small menu of two sections, as Windows Live Photo Gallery's does: the field -- Date Taken, Caption, File
// name -- and the direction -- Ascending, Descending -- each a group of radio items showing the current choice. A choice reads
// the view again in that order (library-view.js openLibraryView: a new place in the history, so Back returns to the order before,
// and the address keeps `order`), and the views opened after it are read in it (state.nav.order, set as the view opens).
// Choosing what is already chosen closes the menu and asks for nothing.
//
// The menu is a radio menu (radio-menu.js: its keys, and what closes it); ArrowDown on the button opens it on the field chosen
// and ArrowUp on the direction. Everything the menu holds is `state.sortMenu`.
import { state } from './state.js';
import { btnSortBy, sortByCurrent, sortMenu } from './elements.js';
import { openLibraryView } from './library-view.js';
import { SORT_FIELDS, orderLabel, orderOf, orderParts } from './library-source.js';
import { radioMenu } from './radio-menu.js';

/** Each item checked or not, as `order` is read. */
function markChecked(order) {
    const { field, descending } = orderParts(order);
    for (const item of menu.items()) {
        const checked = item.dataset.field ? item.dataset.field === field : (item.dataset.direction === 'desc') === descending;
        item.setAttribute('aria-checked', checked ? 'true' : 'false');
    }
}

/** Opening for the view that is open: the focus on the checked item of the field's section, or the direction's (ArrowUp). */
function prepare(up) {
    const lib = state.library;
    if (!lib || lib.invalid) return null;
    markChecked(lib.order);
    const wanted = up ? '[data-direction]' : '[data-field]';
    return { token: lib.token, target: menu.items().find(item => item.matches(wanted) && item.getAttribute('aria-checked') === 'true') };
}

/** An item was chosen: the view is read in the order the item makes of the one it has. */
function choose(item) {
    const lib = state.library;
    if (!lib || lib.invalid) return;
    const was = orderParts(lib.order);
    const order = item.dataset.field ? orderOf(item.dataset.field, was.descending) : orderOf(was.field, item.dataset.direction === 'desc');
    if (order === lib.order) return;
    // state.nav.order follows when the view opens (navigator.js navigatorFollows): a person who stays on a photo with
    // unsaved edits keeps the view, and the order of the next one, as they were.
    openLibraryView({ kind: lib.kind, value: lib.value, recursive: lib.recursive, order, media: lib.media });
}

const menu = radioMenu({ button: btnSortBy, menu: sortMenu, held: state.sortMenu, prepare, choose });

/**
 * What the button says of the order the view is read in, and the menu closed if it was drawn for another view. Called as the
 * view changes and as it closes (library-view.js, through `upper`).
 */
export function showSortOrder() {
    const lib = state.library;
    menu.follow(lib && !lib.invalid ? lib.token : 0);
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
    menu.wire();
    showSortOrder();
}
