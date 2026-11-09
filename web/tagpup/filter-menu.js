// TagPup's page: Show, in a library view's floating header beside Sort by -- the view's photos and videos both, its photos alone,
// or its videos alone (the owner, 2026-11-08: a filter by kind, not by size).
//
// A choice reads the view again with `media` (library-view.js openLibraryView: a new place in the history, so Back returns to the
// filter before, and the address keeps `media`), and the views opened after it in this page are read under it (state.nav.media, set
// as the view opens). A page loaded or reloaded opens on Both (library-view.js openViewFromAddress). The navigator's counts stay
// the library's; the view's count says how many the source holds of either kind beside its own ("3 videos (of 1,204)", folder.js
// viewCountSaid), and a view the filter empties says why (library-source.js emptySentence). A selection of the view, and so a bulk
// edit or a Delete of it, is of what the filter shows (selected.js selectionRequest). Choosing what is already chosen closes the
// menu and asks for nothing.
//
// The menu is a radio menu (radio-menu.js: its keys, and what closes it); it opens on the choice. Everything the menu holds is
// `state.filterMenu`.
import { state } from './state.js';
import { btnFilterMedia, filterMediaCurrent, filterMenu } from './elements.js';
import { openLibraryView } from './library-view.js';
import { DEFAULT_MEDIA, mediaLabel } from './library-source.js';
import { radioMenu } from './radio-menu.js';

function markChecked(media) {
    for (const item of menu.items()) item.setAttribute('aria-checked', item.dataset.media === media ? 'true' : 'false');
}

function prepare() {
    const lib = state.library;
    if (!lib || lib.invalid) return null;
    markChecked(lib.media);
    return { token: lib.token, target: menu.items().find(item => item.getAttribute('aria-checked') === 'true') };
}

/** An item was chosen: the view is read again with what it shows. */
function choose(item) {
    const lib = state.library;
    if (!lib || lib.invalid || item.dataset.media === lib.media) return;
    // state.nav.media follows when the view opens (navigator.js navigatorFollows), as the order does.
    openLibraryView({ kind: lib.kind, value: lib.value, recursive: lib.recursive, order: lib.order, media: item.dataset.media });
}

const menu = radioMenu({ button: btnFilterMedia, menu: filterMenu, held: state.filterMenu, prepare, choose });

/** What the button says the view shows, lit while it shows one kind alone; the menu closed if it was drawn for another view. */
export function showMediaFilter() {
    const lib = state.library;
    menu.follow(lib && !lib.invalid ? lib.token : 0);
    const media = lib && !lib.invalid ? lib.media : state.nav.media;
    filterMediaCurrent.textContent = mediaLabel(media);
    btnFilterMedia.setAttribute('aria-label', `Show: ${mediaLabel(media)}`);
    btnFilterMedia.title = media === DEFAULT_MEDIA ? 'Photos and videos: choose Photos or Videos to see one kind alone'
        : `${mediaLabel(media)} alone: choose Both to see everything in this view`;
    btnFilterMedia.classList.toggle('active', media !== DEFAULT_MEDIA);
    btnFilterMedia.disabled = !lib || lib.invalid;
    if (state.filterMenu.open) markChecked(media);
}

export function wireFilterMenu() {
    menu.wire();
    showMediaFilter();
}
