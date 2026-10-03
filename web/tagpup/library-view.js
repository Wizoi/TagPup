// TagPup's page: opening a view of the library -- the whole library, a year, a month, a keyword and
// everything under it, a person, or a folder and its subfolders -- in the grid that shows a folder
// (docs/ARCHITECTURE.md, phase 9b-2). The photos and their cards are library-source.js's; this is the
// page around them: the address that names the view, the strip that says which one is open, the folder
// machinery put to rest while it is, and the way back.
//
// The address is the view: `?view=<kind>&value=<value>[&recursive=1]` (library-source.js,
// viewSpecFromSearch), pushed when a view is opened, so Back and Forward move between views and
// folders, and a bookmark opens one. A `?view` wins over a `?path`. The navigator (navigator.js) opens views
// through here and follows them (phase 9c): `upper.navigatorFollows` as one opens or closes.
import { baseName } from './common/paths.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import {
    btnApplyRename, btnFolderAutoApply, btnLibraryRefresh, btnLibraryScope, btnRefreshList, btnShowOnDisk,
    btnToggleRename, btnToggleTimeshift,
    timeshiftCameraField, timeshiftDirectionField, timeshiftViewNote,
    folderPathInput, folderViewHeader, folderViewMain, folderViewStats, folderViewTitle, indexProgressContainer,
    libraryStrip, libraryStripBack, libraryStripSource, libraryStripStatus, libraryStripTotal,
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
    destroyLibrary, forgetCards, loadLibraryIds, newLibrary, viewLabel, viewSearch, viewSpecFromSearch
} from './library-source.js';

const SEARCH_OFF = 'Search arrives with the library views’ later stages.';
const FOLDER_ONLY = 'Not in a library view: this works on a folder, and arrives for photos across folders with editing from a library view.';

/** Is this the view that is open? */
function isOpen(spec) {
    const lib = state.library;
    return Boolean(lib) && !lib.invalid && lib.kind === spec.kind && (lib.value ?? null) === (spec.value ?? null)
        && lib.recursive === Boolean(spec.recursive);
}

// ---- The folder, put to rest -----------------------------------------------------------------

/**
 * What belongs to the open folder is idle while a view of the library is open: nothing of the folder
 * is asked, polled or written. Opening the folder again (Back to folder view) reads it as it was
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

/** The page as a library view has it: no list, no filter, no folder-only buttons. */
function showChrome() {
    photoList.querySelectorAll('.photo-item-file').forEach(el => el.remove());
    photoSearch.disabled = true;
    photoSearch.title = SEARCH_OFF;
    btnToggleRename.title = FOLDER_ONLY;
    // Shift Date Taken works on the selection of a view, by minutes and a direction: no camera (bulk-edit.js).
    btnToggleTimeshift.disabled = false;
    btnToggleTimeshift.title = 'Shift Date Taken of the selected photos';
    timeshiftCameraField.classList.add('hidden');
    timeshiftDirectionField.classList.remove('hidden');
    timeshiftViewNote.classList.remove('hidden');
    btnRefreshList.title = 'Ask the library for this view again';
    libraryStrip.classList.remove('hidden');
    folderViewHeader.classList.remove('hidden');
}

function hideChrome() {
    photoSearch.disabled = false;
    photoSearch.title = '';
    btnToggleRename.title = 'Smart Rename Files';
    btnToggleTimeshift.title = 'Camera Time Shift';
    timeshiftCameraField.classList.remove('hidden');
    timeshiftDirectionField.classList.add('hidden');
    timeshiftViewNote.classList.add('hidden');
    // A folder's own scan enables it again; with no folder open there is nothing to shift.
    btnToggleTimeshift.disabled = true;
    btnToggleTimeshift.classList.remove('active');
    timeshiftPanel.classList.add('hidden');
    btnRefreshList.title = 'Refresh files list';
    libraryStrip.classList.add('hidden');
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
    if (lib.loading && lib.status !== 'loading') {
        status = 'Refreshing...';
    } else if (lib.status === 'error') {
        status = lib.message;
    } else if (lib.status === 'empty') {
        status = lib.message;
    }
    libraryStripStatus.textContent = status || '';
    libraryStripStatus.classList.toggle('library-strip-problem', lib.status === 'error' || Boolean(lib.notice));
    btnLibraryRefresh.disabled = lib.invalid || lib.loading;
    // A folder's view can be this folder only or with its subfolders, and the folder can be shown on disk.
    const ofFolder = lib.kind === 'folder' && !lib.invalid;
    btnLibraryScope.classList.toggle('hidden', !ofFolder);
    btnShowOnDisk.classList.toggle('hidden', !ofFolder);
    if (ofFolder) {
        btnLibraryScope.textContent = lib.recursive ? 'This folder only' : 'With subfolders';
        btnLibraryScope.title = lib.recursive
            ? 'Show only the photos directly in this folder'
            : 'Show the photos in this folder and in the folders under it';
    }
    folderViewTitle.textContent = lib.invalid ? 'Library view' : label;
    folderViewStats.textContent = `${lib.total.toLocaleString()} photos`;
    folderViewHeader.textContent = '';
    folderViewHeader.append(`\u{1F4C2} Library view${lib.invalid ? '' : ` — ${label}`}`);
    document.title = `${label} — TagPup`;
    updateListStats();
    updatePhotoPosition();
}

// ---- The address ---------------------------------------------------------------------------

function saveScroll() {
    try {
        window.history.replaceState({ ...(window.history.state || {}), scrollTop: folderViewMain.scrollTop }, '');
    } catch (err) {
        console.error('Could not remember where the view was scrolled to:', err);
    }
}

function writeAddress(spec, mode) {
    if (mode === 'none' || spec.error) return;
    const url = new URL(window.location.href);
    url.search = viewSearch(spec);
    if (mode === 'push') {
        saveScroll();
        window.history.pushState({}, '', url);
    } else {
        window.history.replaceState(window.history.state, '', url);
    }
}

function clearAddress() {
    const url = new URL(window.location.href);
    for (const name of ['view', 'value', 'recursive']) url.searchParams.delete(name);
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
    if (!spec.error && isOpen(spec)) return;
    leavePhotoThen(() => beginView(spec, history, scrollTop), { onStay: keepAddress });
}

/** The person stayed on the photo: the address goes back to the place the page is still at. */
function keepAddress() {
    const lib = state.library;
    if (lib && !lib.invalid) writeAddress(lib, 'replace');
}

function beginView(spec, history, scrollTop) {
    const previous = state.library;
    if (previous) destroyLibrary(previous);
    else {
        // The folder open now is the one Back to folder view returns to.
        if (state.scannedFolder) state.libraryReturn = state.scannedFolder;
        quietTheFolder();
    }
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
    writeAddress(spec, history);
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

/** Back to folder view: the folder that was open when the view was, opened again (or `folder`: Show on disk). */
export function backToFolder(folder = state.libraryReturn) {
    leavePhotoThen(() => {
        if (!state.library) return;
        const url = new URL(window.location.href);
        for (const name of ['view', 'value', 'recursive']) url.searchParams.delete(name);
        saveScroll();
        window.history.pushState({}, '', url);
        closeLibraryView({ folder });
        if (!folder) setStatus('ready', 'No folder was open. Choose one in the sidebar.');
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
    state.libraryReturn = new URLSearchParams(window.location.search).get('path') || '';
    openLibraryView(spec, { history: 'none' });
    return true;
}

export function wireLibraryView() {
    btnLibraryRefresh.addEventListener('click', refreshLibraryView);
    libraryStripBack.addEventListener('click', (event) => {
        event.preventDefault();
        backToFolder();
    });
    window.addEventListener('popstate', (event) => {
        // Back or Forward between a folder and a view, or between two views.
        if (hasUnsavedEdits() || openPhotoWrite()) {
            leavePhotoThen(() => showAddress((event.state && event.state.scrollTop) || 0), { onStay: keepAddress });
            return;
        }
        showAddress((event.state && event.state.scrollTop) || 0);
    });
}
