// TagPup's page: a library view as it was left when a photo was opened over it, and the way back to it -- the one owner (#780).
//
// A library view's photo is shown in the details panel, over the grid; the grid is hidden, and the browser forgets a hidden
// element's scroll offset. What the person left is the view (the address), where the grid was scrolled to, what was selected (ids,
// or a whole source less the ones left out), the order, the navigator's open rows and the search box's words. The view is what
// `state.library` holds, and it holds the selection, the order and the cards while the photo is open; the search box and the
// navigator's rows are the page's and are not touched. What would be lost is the grid's place, and the way back. So:
//
//   * when a photo is opened from the grid (`rememberView`, called as the photo is shown), the grid's place is saved in
//     `state.viewLeft` -- the scroll offset and the photo at the top of the view, which holds the place if the order changes --
//     and a place is made in the history for the photo: Back, the page's own Back button and Escape all come to the view, never
//     off the page. The view's own place keeps where it was scrolled to, so a Back after a reload finds it, best-effort;
//   * the grid comes back through `openFolderView` (folder.js), which asks `restoreView` to put the place back: from the Back
//     button, Escape, the browser's Back, a Delete of the open photo, a bulk delete that took it, whichever way the photo is closed.
//     The snapshot is of one view (its token): opening another view forgets it (`forgetViewLeft`).
//
// Stepping from photo to photo is one place in the history, not one each; Forward onto the photo's place opens the photo again.
import { state } from './state.js';
import { btnPhotoBack, folderViewContent, folderViewMain } from './elements.js';
import { upper } from './hooks.js';
import { leavePhotoThen } from './edits.js';
import { openFolderView } from './folder.js';
import { pushEntry } from './history-entries.js';

/** Is this history state a place the page made for a photo? */
function isPhotoPlace(entry) {
    return Boolean(entry) && entry.photo !== undefined;
}

/** Where the view is scrolled to: the grid's own while it is shown, what was saved while a photo hides it. */
export function viewScrollTop() {
    return state.viewLeft ? state.viewLeft.scrollTop : folderViewMain.scrollTop;
}

/** The current place in the history keeps where the view is scrolled to (a Back from elsewhere returns there, best-effort). */
export function saveScroll() {
    try {
        window.history.replaceState({ ...(window.history.state || {}), scrollTop: viewScrollTop() }, '');
    } catch (err) {
        console.error('Could not remember where the view was scrolled to:', err);
    }
}

/**
 * A photo is being shown over the grid of a library view: keep the grid's place and make the photo a place in the history. Once
 * for the photo opened from the grid; the photos stepped to after it are the same place.
 */
export function rememberView() {
    const lib = state.library;
    if (!lib || lib.invalid || state.viewLeft) return;
    if (folderViewContent.classList.contains('hidden')) return;      // the grid is not what is on screen
    const extent = state.grid ? state.grid.extent() : null;
    const at = extent ? extent.viewFrom : 0;
    state.viewLeft = {
        token: lib.token, scrollTop: Math.round(folderViewMain.scrollTop), anchorId: lib.ids[at], anchorIndex: at,
        place: state.grid ? state.grid.placeOfView() : null,
    };
    // A place for the photo, unless the page is on one already (a reload kept it): the view's own place first saves its scroll.
    const here = window.history.state || {};
    if (isPhotoPlace(here)) return;
    try {
        saveScroll();
        // The photo's place is the view's, one place further: a search's `searchBack` goes with it (searchPlacesBack).
        pushEntry(here.searchBack > 0 ? { photo: true, searchBack: here.searchBack } : { photo: true }, new URL(window.location.href));
    } catch (err) {
        console.error('Could not make a place in the history for the photo:', err);
    }
}

/** The photo now shown is the one Forward would open again. */
export function notePhoto(id) {
    if (!state.library || !isPhotoPlace(window.history.state)) return;
    try {
        window.history.replaceState({ ...window.history.state, photo: id }, '');
    } catch (err) {
        console.error('Could not remember which photo is open:', err);
    }
}

/**
 * How many places back the view before a search is, from the place the page is at: the search's own `searchBack`, and one more
 * from a photo opened over it (the photo has a place of its own). 0 when it is not known.
 */
export function searchPlacesBack() {
    const here = window.history.state || {};
    const back = Number(here.searchBack);
    return back > 0 ? back + (isPhotoPlace(here) ? 1 : 0) : 0;
}

/** A view opened in the photo's place, replacing it: the place is the new view's, not a photo's (its `searchBack` still counts). */
export function leavePhotoPlace() {
    const here = window.history.state;
    if (!isPhotoPlace(here)) return;
    const kept = { ...here };
    delete kept.photo;
    try {
        window.history.replaceState(kept, '');
    } catch (err) {
        console.error('Could not change the place of a photo in the history:', err);
    }
}

/** Another view opened, or the view closed: what was left of the one before is not this view's. */
export function forgetViewLeft() {
    state.viewLeft = null;
}

/**
 * The grid is shown again (folder.js openFolderView): put the view back as it was left. The same order: the same offset. An order
 * that changed while the photo was open (a photo deleted, Refresh view): the photo that was at the top stays at the top, or the
 * place nearest it when it is gone.
 */
export function restoreViewAsLeft() {
    const lib = state.library;
    const left = state.viewLeft;
    state.viewLeft = null;
    if (!lib || !left || left.token !== lib.token || !state.grid || !lib.ids.length) return;
    const at = left.anchorId === undefined ? -1 : lib.ids.indexOf(left.anchorId);
    const geometry = state.grid.geometry();
    const row = (geometry && geometry.columns) || 1;
    // The order is as it was, or a few photos before the top went (less than a row's worth): the same offset is the same place.
    if (left.anchorId === undefined || (at >= 0 && Math.abs(at - left.anchorIndex) < row)) {
        // The row at the top, read again at the grid's width now: a card's height can differ by a pixel or two from when it was left,
        // and an offset is then not the same place (vgrid.js showPlace). With no layout to go by, the offset.
        if (left.place && state.grid.showPlace(left.place)) return;
        folderViewMain.scrollTop = left.scrollTop;
        state.grid.render();
        return;
    }
    state.grid.scrollToIndex(at >= 0 ? at : Math.max(0, Math.min(left.anchorIndex, lib.ids.length - 1)), 'start');
}

/**
 * Close the photo onto the grid: Back, Escape, the page's Back button. When the photo is a place in the history this goes back
 * in it, so the browser's Back, Forward and this agree; the popstate shows the grid (`photoPlaceMoved`). Otherwise the grid is shown.
 */
export function backToView() {
    if (state.library && state.activePhotoPath && state.viewLeft && isPhotoPlace(window.history.state)) {
        window.history.back();
        return;
    }
    leavePhotoThen(openFolderView);
}

/** The folder view's header, in Organize; in a library view with a photo open it is Back. */
export function showGrid() {
    if (state.library && state.activePhotoPath) backToView();
    else leavePhotoThen(openFolderView);
}

/**
 * Back or Forward moved the history while a photo is (or was) over the view of the address. From the photo's place to the view's: the
 * grid, as left (asking first about edits in the photo; `stay` is what to do when they are kept). Forward onto the photo's place
 * with the grid shown: the photo again. Returns true when it was one of these and the page has dealt with it.
 */
export function photoPlaceMoved(event, { namesWhatIsShown, stay }) {
    if (!state.library || !namesWhatIsShown) return false;
    const placed = isPhotoPlace(event.state);
    if (state.activePhotoPath && !placed) {
        leavePhotoThen(openFolderView, { onStay: stay });
        return true;
    }
    if (!state.activePhotoPath && placed) {
        if (Number.isInteger(event.state.photo)) upper.openLibraryPhoto(event.state.photo);
        return true;
    }
    return false;
}

/** A Back to the view's place after a reload: the grid was drawn from the address, at the top; the place says where it had been. */
export function bestEffortScroll(entry) {
    const lib = state.library;
    if (!lib || state.activePhotoPath || !state.grid || !lib.ids.length || folderViewMain.scrollTop > 0) return;
    const top = entry && Number(entry.scrollTop);
    if (!(top > 0)) return;
    folderViewMain.scrollTop = top;
    state.grid.render();
}

/** The photo panel's Back button. */
export function wireViewLeft() {
    btnPhotoBack.addEventListener('click', backToView);
}
