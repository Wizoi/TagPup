// TagPup's page: opening a view of the library -- the whole library, a year, a month, a keyword and
// everything under it, a person, or a folder and its subfolders -- in the grid that shows a folder
// (docs/ARCHITECTURE.md, phase 9b-2). The photos and their cards are library-source.js's; this is the
// page around them: the address that names the view, the strip that says which one is open, and the folder
// machinery put to rest while it is. The way to a folder is the navigator's Organize, a folder of the
// selection's "Folders to Organize" (library-moves.js), or Back; the strip's own "Back to folder view" went with #670.
//
// The address is the view: `?view=<kind>&value=<value>[&recursive=1]` (library-source.js,
// viewSpecFromSearch), pushed when a view is opened, so Back and Forward move between views and
// folders, and a bookmark opens one. A `?view` wins over a `?path`. The navigator (navigator.js) opens views
// through here and follows them (phase 9c): `upper.navigatorFollows` as one opens or closes; so does the search box
// (search.js, phase 9e-2: `upper.searchFollows`). A search's place in the history says how many places back the view before
// it is (`searchBack`), so clearing the search returns to that view.
import { VIEW_PARAMS } from './common/library.js';
import { baseName } from './common/paths.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import {
    btnApplyRename, btnDeleteSelection, btnFolderAutoApply, btnLibraryRefresh, btnRefreshList,
    btnToggleRename, btnToggleTimeshift,
    folderPathInput, folderViewHeader, folderViewMain, folderViewStats, folderViewTitle, folderViewTop, indexProgressContainer,
    libraryStrip, libraryStripSource, libraryStripStatus, libraryStripTotal,
    photoList, photoSearch, renamePanel, suggestProgressContainer, timeshiftPanel
} from './elements.js';
import { setStatus } from './status.js';
import { hasUnsavedEdits, leavePhotoThen, openPhotoWrite } from './edits.js';
import {
    openFolderView, scanFolder, updateCurrentFolderLabel, updateListStats, updatePhotoPosition
} from './folder.js';
import { clearSelection } from './selected.js';
import { attachBulk, lockBulkControls } from './bulk-job.js';
import { forgetBanner } from './library-banner.js';
import { clearSyncInfo, loadSyncInfo } from './sync-state.js';
import {
    destroyLibrary, forgetCards, loadLibraryIds, newLibrary, sameView, viewLabel, viewSearch, viewSpecFromSearch
} from './library-source.js';

const SEARCH_OFF = 'A library view has no file list to filter: search the library from the Library pane’s search box.';

/** Is this the view that is open (the same source in the same order)? */
function isOpen(spec) {
    const lib = state.library;
    return Boolean(lib) && !lib.invalid && sameView(lib, spec);
}

// ---- The folder, put to rest -----------------------------------------------------------------

/**
 * What belongs to the open folder is idle while a view of the library is open: nothing of the folder
 * is asked, polled or written. Opening the folder again (Back, or a folder to Organize) reads it as it was
 * opened the first time, from this browser's cache of the scan or the disk.
 */
function quietTheFolder() {
    if (state.scanAbortController) state.scanAbortController.abort();   // a scan under way would land in the folder
    window.clearInterval(state.progressTimer);
    window.clearInterval(state.indexProgressTimer);
    state.progressTimer = null;
    state.indexProgressTimer = null;
    suggestProgressContainer.classList.add('hidden');
    indexProgressContainer.classList.add('hidden');
    state.scannedFolder = null;
    state.folderPhotos = [];
    state.folderSuggestions = {};
    upper.checkFolderMembership(null);   // nothing is held back, no folder is asked to be added: every photo is held
    upper.checkDamagedPhotos(null);      // a card says for itself whether its photo is damaged
    btnToggleRename.disabled = true;
    btnToggleTimeshift.disabled = true;
    btnFolderAutoApply.disabled = true;
    btnApplyRename.disabled = true;
    renamePanel.classList.add('hidden');
    timeshiftPanel.classList.add('hidden');
    upper.updateSuggestButtonState();
    updateCurrentFolderLabel();
}

/**
 * The page as a library view has it: no list, no filter, and none of Organize's buttons. Smart Rename and Camera Time Shift
 * are work on one folder (#669: the owner, 2026-10-04): a library view is for seeing the library, and offers neither. The top
 * of the grid is one floating header (#713): the strip -- the view, its total, Refresh view -- and the header card's actions
 * beside it, the card's own title and count, which said the same, hidden (style.css, `.in-library-view`).
 */
function showChrome() {
    photoList.querySelectorAll('.photo-item-file').forEach(el => el.remove());
    photoSearch.disabled = true;
    photoSearch.title = SEARCH_OFF;
    btnToggleRename.classList.add('hidden');
    btnToggleTimeshift.classList.add('hidden');
    btnDeleteSelection.classList.remove('hidden');     // Delete of the selection, a view's (#674)
    btnRefreshList.title = 'Ask the library for this view again';
    libraryStrip.classList.remove('hidden');
    folderViewTop.classList.add('in-library-view');
    folderViewHeader.classList.remove('hidden');
}

function hideChrome() {
    photoSearch.disabled = false;
    photoSearch.title = '';
    btnToggleRename.classList.remove('hidden');
    btnToggleTimeshift.classList.remove('hidden');
    btnDeleteSelection.classList.add('hidden');
    // A folder's own scan enables it again; with no folder open there is nothing to shift.
    btnToggleTimeshift.disabled = true;
    btnToggleTimeshift.classList.remove('active');
    timeshiftPanel.classList.add('hidden');
    btnRefreshList.title = 'Refresh files list';
    libraryStrip.classList.add('hidden');
    folderViewTop.classList.remove('in-library-view');
    upper.showSortOrder();   // its menu, if it was open, is of no view now
    forgetBanner();
    clearSyncInfo();
    folderViewTitle.textContent = 'Folder View';
    updateCurrentFolderLabel();
}

/** The strip, the counts and the window's title say what the view is and where it stands. */
export function libraryChanged() {
    const lib = state.library;
    if (!lib) return;
    const label = lib.invalid ? 'This address does not name a view' : viewLabel(lib);
    libraryStripSource.textContent = label;
    libraryStripSource.title = lib.invalid ? '' : label;
    let total = '';
    if (lib.status === 'loading') total = 'Opening...';
    else if (lib.status === 'ready' || lib.status === 'empty') {
        total = `${lib.total.toLocaleString()} ${lib.total === 1 ? 'photo' : 'photos'}`;
        if (!lib.complete) total += `, the first ${lib.ids.length.toLocaleString()} shown`;
    }
    libraryStripTotal.textContent = total;
    libraryStripTotal.classList.toggle('hidden', !total);
    let status = lib.notice;
    if (lib.waiting) {
        status = lib.waiting;        // the library asked to be asked again: a 503 with Retry-After (library-source.js)
    } else if (lib.loading && lib.status !== 'loading') {
        status = 'Refreshing...';
    } else if (lib.status === 'error') {
        status = lib.message;
    } else if (lib.status === 'empty') {
        status = lib.message;
    }
    libraryStripStatus.textContent = status || '';
    libraryStripStatus.classList.toggle('library-strip-problem', lib.status === 'error' || Boolean(lib.notice) || Boolean(lib.waiting));
    libraryStripStatus.title = status || '';
    btnLibraryRefresh.disabled = lib.invalid || lib.loading;
    upper.showSortOrder();
    folderViewTitle.textContent = lib.invalid ? 'Library view' : label;
    folderViewStats.textContent = `${lib.total.toLocaleString()} photos`;
    folderViewHeader.textContent = '';
    folderViewHeader.append(`\u{1F4C2} Library view${lib.invalid ? '' : ` — ${label}`}`);
    document.title = `${label} — TagPup`;
    updateListStats();
    updatePhotoPosition();
    upper.searchFollows();   // the search box shows the search that is open, and what the library said of it
}

// ---- The address ---------------------------------------------------------------------------

function saveScroll() {
    try {
        window.history.replaceState({ ...(window.history.state || {}), scrollTop: folderViewMain.scrollTop }, '');
    } catch (err) {
        console.error('Could not remember where the view was scrolled to:', err);
    }
}

/**
 * How many places back the view before a search is, for the search's own place: 1 from another view, a folder or nothing; one
 * more than the search it replaces in a new place (a sort of it); 0 when that is not known (a search opened by its address).
 */
function searchBackOf(previous) {
    if (!previous || previous.invalid || previous.kind !== 'search') return 1;
    const here = Number(window.history.state && window.history.state.searchBack);
    return here > 0 ? here + 1 : 0;
}

/**
 * A new place in the history, tagged with its position when the place the page is at has one (#770): positions are consecutive,
 * since a new place drops the places after the one it is made from.
 */
function pushEntry(entry, url) {
    const at = state.entries.at;
    const pos = at === null ? null : at + 1;
    window.history.pushState(pos === null ? entry : { ...entry, entryLoad: state.entries.load, entryPos: pos }, '', url);
    state.entries.at = pos;
}

/** The position of a place in the history this load made, or null. */
function positionOf(entry) {
    return entry && entry.entryLoad === state.entries.load && Number.isInteger(entry.entryPos) ? entry.entryPos : null;
}

function writeAddress(spec, mode, back = 0) {
    if (mode === 'none' || spec.error) return;
    const url = new URL(window.location.href);
    url.search = viewSearch(spec);
    if (mode === 'push') {
        saveScroll();
        pushEntry(back > 0 ? { searchBack: back } : {}, url);
    } else {
        window.history.replaceState(window.history.state, '', url);
    }
}

function clearAddress() {
    const url = new URL(window.location.href);
    for (const name of VIEW_PARAMS) url.searchParams.delete(name);
    window.history.replaceState(window.history.state, '', url);
}

// ---- Opening and closing ---------------------------------------------------------------------

/**
 * Open a view of the library. `spec` is { kind, value, recursive } (or { error }, an address that cannot
 * be one: the page says so, in a sentence, and shows an empty view). `history` is how the address is
 * written: 'push' (a new place to go Back to), 'replace', or 'none' (the address is already right: Back
 * and Forward). Asks first about edits in the open photo. Replaces the view that is open.
 */
export function openLibraryView(spec, { history = 'push', scrollTop = 0 } = {}) {
    // A view opened without an order is read in the one chosen in the navigator (#671).
    if (!spec.error && !spec.order) spec = { ...spec, order: state.nav.order };
    if (!spec.error && isOpen(spec)) return;
    leavePhotoThen(() => beginView(spec, history, scrollTop), { onStay: keepAddress });
}

/** The person stayed on the photo: the address goes back to the place the page is still at, and the search box with it. */
function keepAddress() {
    const lib = state.library;
    if (lib && !lib.invalid) writeAddress(lib, 'replace');
    upper.searchFollows({ force: true });
}

/**
 * The person stayed on the photo after Back or Forward had already moved the history (#770): the history goes back to the place
 * the page shows (`from`), whose own state -- a search's `searchBack` -- is then the place's again; the popstate of that return
 * names what is shown and asks nothing. When either position is not known, the place moved to is given the view's address and
 * the view's own state, less `searchBack`, which no longer says where the view before the search is (Clear then closes the view).
 */
function stayAfterMove(from, to) {
    if (from !== null && to !== null && from !== to) {
        window.history.go(from - to);
        upper.searchFollows({ force: true });
        return;
    }
    const lib = state.library;
    if (lib && !lib.invalid) {
        const kept = { ...(lib.entryState || {}) };
        for (const name of ['searchBack', 'entryLoad', 'entryPos', 'scrollTop']) delete kept[name];
        const here = window.history.state || {};
        if (positionOf(here) !== null) Object.assign(kept, { entryLoad: here.entryLoad, entryPos: here.entryPos });
        const url = new URL(window.location.href);
        url.search = viewSearch(lib);
        window.history.replaceState(kept, '', url);
    }
    upper.searchFollows({ force: true });
}

/** Does the address name what the page shows: the view open, or no view while none is? A return to it asks nothing. */
function addressNamesWhatIsShown() {
    const spec = viewSpecFromSearch(window.location.search);
    if (!spec) return !state.library;
    return !spec.error && isOpen(spec);
}

function beginView(spec, history, scrollTop) {
    const previous = state.library;
    const back = !spec.error && spec.kind === 'search' ? searchBackOf(previous) : 0;
    if (previous) destroyLibrary(previous);
    else quietTheFolder();
    const lib = newLibrary(spec.error ? { kind: 'all' } : spec);
    if (spec.error) {
        lib.invalid = true;
        lib.status = 'error';
        lib.message = spec.error;
    }
    state.library = lib;
    state.shownSource = null;
    clearSelection();
    state.lastSelectedPath = null;
    state.folderPhotos = [];
    forgetBanner();            // what the disk held of the view before is not this view's
    writeAddress(spec, history, back);
    lib.entryState = { ...(window.history.state || {}) };   // the state of its place in the history (#770)
    showChrome();
    upper.navigatorFollows();  // the sidebar shows the source, in the tab it belongs to
    openFolderView();          // the grid, empty: 'Opening the view...' until the order is here
    libraryChanged();
    if (lib.invalid) {
        clearSyncInfo();
        return;
    }
    loadSyncInfo();            // when the library was last in step with its folders
    lockBulkControls();        // a bulk edit that is running keeps the view's edit controls off
    attachBulk();              // a bulk edit started elsewhere (another tab, before a reload) shows in the strip
    loadLibraryIds(lib).then(replaced => {
        if (lib !== state.library) return;
        if (!replaced) {
            upper.renderThumbnails();   // the grid says why there is nothing in it
            libraryChanged();
            return;
        }
        upper.renderThumbnails();
        if (scrollTop > 0) {
            folderViewMain.scrollTop = scrollTop;
            state.grid.render();
        }
        libraryChanged();
        upper.libraryViewPainted(lib);   // land where a move said, and ask what the disk holds beyond the library
    });
}

/**
 * Close the library view. With `folder`, that folder is opened in its place; with none the page is left
 * as a page with no folder open. The address loses the view's parameters.
 */
export function closeLibraryView({ folder = '' } = {}) {
    const lib = state.library;
    if (!lib) return;
    destroyLibrary(lib);
    state.library = null;
    state.shownSource = null;
    state.shownPhotos = [];
    state.shownIndex = new Map();
    clearSelection();
    state.lastSelectedPath = null;
    state.folderPhotos = [];
    state.activePhotoPath = null;
    clearAddress();
    hideChrome();
    lockBulkControls();
    upper.navigatorFollows();   // no source is open: the sidebar goes back to the pane a folder view has
    upper.searchFollows();      // and the search box, if it showed a search, is empty
    if (folder) {
        folderPathInput.value = folder;
        scanFolder(false);
        return;
    }
    openFolderView();
    updateListStats();
}

/** A folder is being opened from the folder box: the view that was open goes. */
export function leaveLibraryView() {
    closeLibraryView();
}

/**
 * Close the view onto `folder`, opened in Organize, as a new place in the history (Back returns to the view): a folder of the
 * selection's "Folders to Organize" (library-moves.js openInOrganize), whose scan has already answered. Asks first about edits
 * in the open photo. The sidebar's switch goes to Organize as its own click takes it there (#712): the folder is Organize's,
 * whichever pane was last chosen for a folder.
 */
export function closeViewOntoFolder(folder) {
    leavePhotoThen(() => {
        if (!state.library) return;
        const url = new URL(window.location.href);
        for (const name of VIEW_PARAMS) url.searchParams.delete(name);
        saveScroll();
        pushEntry({}, url);
        closeLibraryView({ folder });
        upper.choosePane('folder');
    });
}

/**
 * Close the view onto nothing, as a new place in the history (Back returns to the view): a search cleared that no view came
 * before in this page (it was opened by its address). Asks first about edits in the open photo.
 */
export function closeViewAsNewPlace() {
    leavePhotoThen(() => {
        if (!state.library) return;
        const url = new URL(window.location.href);
        for (const name of VIEW_PARAMS) url.searchParams.delete(name);
        saveScroll();
        pushEntry({}, url);
        closeLibraryView();
    });
}

/** Ask the library for this view's order again, keeping the place near the same photo. */
export function refreshLibraryView() {
    const lib = state.library;
    if (!lib || lib.invalid || lib.loading) return Promise.resolve(false);
    const extent = state.grid ? state.grid.extent() : null;
    const anchor = extent ? lib.ids[extent.viewFrom] : undefined;
    forgetCards(lib);
    loadSyncInfo();
    return loadLibraryIds(lib, true).then(replaced => {
        if (!replaced || lib !== state.library) {
            // The order could not be read: the cards are asked for again as they are drawn, the order is as it was.
            if (lib === state.library) upper.renderThumbnails();
            return false;
        }
        upper.renderThumbnails();
        if (anchor !== undefined) {
            const at = lib.ids.indexOf(anchor);
            if (at >= 0) state.grid.scrollToIndex(at, 'start');
        }
        libraryChanged();
        upper.updateSelectedThumbnailsCount();          // a selection of the whole source is the new total less what was left out
        upper.navigatorCountsChanged({ now: true });   // the counts are read at each call: ask again
        upper.libraryViewPainted(lib, { refreshed: true });   // what the disk held is asked again, by the rule for its size
        return true;
    });
}

/**
 * A Delete of the selection has ended (bulk-job.js, #674): the view's order is read again, so the deleted photos leave it, its
 * total and the selection; and the photo open in the details panel, if it was one of them, is closed onto the grid -- its file
 * is gone. Resolves whether the order was read again.
 */
export function photosDeleted() {
    const lib = state.library;
    if (!lib) return Promise.resolve(false);
    return refreshLibraryView().then(refreshed => {
        if (lib !== state.library) return false;
        if (state.activePhotoPath && lib.activeId !== null && !lib.ids.includes(lib.activeId)) {
            lib.activeId = null;
            state.folderPhotos = [];
            openFolderView();
        }
        return refreshed;
    });
}

/** The Refresh button of the sidebar: the folder's list, or the view's order. */
export function refreshFolderOrView() {
    if (state.library) refreshLibraryView();
    else scanFolder(true);
}

// ---- The menu, the address, Back and Forward ----------------------------------------------------

export function browseWholeLibrary() {
    openLibraryView({ kind: 'all', value: null, recursive: false });
}

export function libraryViewOfFolder() {
    const folder = state.scannedFolder;
    if (!folder) {
        setStatus('error', 'Open a folder first: the library view of a folder starts from the folder that is open.');
        return;
    }
    openLibraryView({ kind: 'folder', value: folder, recursive: true });
}

/** Show or hide the menu's item that needs a folder open. */
export function syncLibraryMenu(menu) {
    const item = menu && menu.querySelector('[data-action="library-of-folder"]');
    if (!item) return;
    // Shown either way (the menu's arrow keys walk its items); without a folder it says why it does nothing.
    if (state.scannedFolder) item.removeAttribute('aria-disabled');
    else item.setAttribute('aria-disabled', 'true');
    item.title = state.scannedFolder
        ? `The library's photos in ${baseName(state.scannedFolder)} and its subfolders`
        : 'Open a folder first: this shows the library\u2019s photos of the folder that is open';
}

/** What the address says, shown: a view, a folder, or neither. */
function showAddress(scrollTop) {
    const spec = viewSpecFromSearch(window.location.search);
    if (spec) {
        openLibraryView(spec, { history: 'none', scrollTop });
        return;
    }
    if (!state.library) return;
    const folder = new URLSearchParams(window.location.search).get('path') || '';
    closeLibraryView({ folder });
}

/**
 * Open the view the page's address names, if it names one: called as the page starts. Returns true if it
 * did, so that a remembered `?path` is left alone: a `?view` wins.
 */
export function openViewFromAddress() {
    const spec = viewSpecFromSearch(window.location.search);
    if (!spec) return false;
    openLibraryView(spec, { history: 'none' });
    return true;
}

export function wireLibraryView() {
    btnLibraryRefresh.addEventListener('click', refreshLibraryView);
    // This place in the history is the first this load knows: position 0 (#770).
    window.history.replaceState({ ...(window.history.state || {}), entryLoad: state.entries.load, entryPos: 0 }, '');
    state.entries.at = 0;
    window.addEventListener('popstate', (event) => {
        // Back or Forward between a folder and a view, or between two views.
        const from = state.entries.at;
        const to = positionOf(event.state);
        state.entries.at = to;
        if (addressNamesWhatIsShown()) return;   // a cancelled move put back (stayAfterMove): nothing to show, nothing to ask
        if (hasUnsavedEdits() || openPhotoWrite()) {
            leavePhotoThen(() => showAddress((event.state && event.state.scrollTop) || 0), { onStay: () => stayAfterMove(from, to) });
            return;
        }
        showAddress((event.state && event.state.scrollTop) || 0);
    });
}
