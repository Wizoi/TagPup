// TagPup's page: the move between a folder on disk and its view of the library (docs/ARCHITECTURE.md, phase 9c).
//
//  * Show in library (a folder view): the same folder's library view, with its subfolders; Show on disk (a library
//    view of a folder): the folder view of the same path, scanned. The selection is cleared by the move (a view
//    opening or closing clears it, 9b-1). The photo at the top of the grid is the one to land on in the other, when
//    it is in both: a folder view finds it by its path, a library view by its id (GET /api/library/find asks the
//    library which id a path is). Best effort -- a photo that is not there leaves the grid at the top.
//  * This folder only <-> with its subfolders: another view of the same folder.
// The banner for photos the disk holds and the library does not is library-banner.js's.
import { api } from './common/api.js';
import { pathKey, samePath } from './common/paths.js';
import { state } from './state.js';
import { btnLibraryScope, btnShowInLibrary, btnShowOnDisk } from './elements.js';
import { checkNotHeld } from './library-banner.js';
import { backToFolder, openLibraryView } from './library-view.js';

/** The path of the photo at the top of the grid, in a folder view or a library view; null if there is none to name. */
export function topPhotoPath() {
    if (!state.grid) return null;
    const at = state.grid.extent().viewFrom;
    const lib = state.library;
    if (lib) {
        const id = lib.ids[at];
        const card = id === undefined ? null : lib.cards.get(id);
        return card ? card.path : null;
    }
    const record = state.shownPhotos[at];
    return record ? record.path : null;
}

export function showInLibrary() {
    const folder = state.scannedFolder;
    if (!folder || state.library) return;
    state.moves.anchor = { path: topPhotoPath(), folder, toKind: 'library' };
    openLibraryView({ kind: 'folder', value: folder, recursive: true });
}

export function showOnDisk() {
    const lib = state.library;
    if (!lib || lib.invalid || lib.kind !== 'folder') return;
    state.moves.anchor = { path: topPhotoPath(), folder: lib.value, toKind: 'folder' };
    backToFolder(lib.value);
}

/** This folder only <-> with its subfolders: another view of the same folder, in the history. */
export function toggleScope() {
    const lib = state.library;
    if (!lib || lib.invalid || lib.kind !== 'folder') return;
    openLibraryView({ kind: 'folder', value: lib.value, recursive: !lib.recursive });
}

/** A folder view has just been put on screen: land on the photo the move was made from, if the folder is the one. */
export function landOnAnchor() {
    const anchor = state.moves.anchor;
    if (!anchor || anchor.toKind !== 'folder' || state.library || !state.grid) return;
    if (!state.scannedFolder || !samePath(state.scannedFolder, anchor.folder)) return;   // the folder is not here yet
    state.moves.anchor = null;
    if (!anchor.path) return;
    const at = state.shownIndex.get(pathKey(anchor.path));
    if (at !== undefined) state.grid.scrollToIndex(at, 'start');
}

function landInLibrary(lib) {
    const anchor = state.moves.anchor;
    if (!anchor) return;
    state.moves.anchor = null;
    if (anchor.toKind !== 'library' || !anchor.path || !samePath(anchor.folder, lib.value)) return;
    api.json(`/api/library/find?path=${encodeURIComponent(anchor.path)}`).then(found => {
        if (lib !== state.library || !found || !Number.isInteger(found.id) || !state.grid) return;
        const at = lib.ids.indexOf(found.id);
        if (at >= 0) state.grid.scrollToIndex(at, 'start');
    }).catch(() => {});   // best effort: the view stays where it is
}

/** A library view has been drawn (opened or refreshed): land where the move said, and ask what the disk holds. */
export function libraryViewPainted(lib) {
    landInLibrary(lib);
    checkNotHeld(lib);
}

export function wireMoves() {
    btnShowInLibrary.addEventListener('click', showInLibrary);
    btnShowOnDisk.addEventListener('click', showOnDisk);
    btnLibraryScope.addEventListener('click', toggleScope);
}
