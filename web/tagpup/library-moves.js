// TagPup's page: the move between a folder on disk and its view of the library (docs/ARCHITECTURE.md, phase 9c).
//
//  * Show in library (a folder in Organize): the same folder's library view, with its subfolders; openInOrganize (a
//    folder of the selection's "Folders to Organize", #675): a folder in Organize, scanned first, the sidebar's switch on
//    Organize (#712). The selection is cleared by the move (a view opening or closing clears it, 9b-1). The photo at
//    the top of the grid is the one to land on in the other, when it is in both: a folder view finds it by its path, a
//    library view by its id
//    (GET /api/library/find asks the library which id a path is). Best effort -- a photo that is not there leaves the
//    grid at the top.
// The strip's Show on disk and This folder only went with #670 (the owner: not needed in the Folders tab).
// The banner for photos the disk holds and the library does not is library-banner.js's.
import { api } from './common/api.js';
import { pathKey, samePath } from './common/paths.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import { btnShowInLibrary } from './elements.js';
import { checkNotHeld } from './library-banner.js';
import { closeViewOntoFolder, openLibraryView } from './library-view.js';

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

/**
 * Open `folder` in Organize from a library view (a folder of the selection's "Folders to Organize", #675): the folder is
 * scanned FIRST, and the view is closed only when the scan has answered (findings #569). A folder that is not on disk any
 * more (the scan answers 400), or cannot be read -- a share that is away -- leaves the view as it is and says so in the
 * strip: the page is never left with neither a view nor a folder. A second click while a scan is out does nothing. The scan
 * the folder view then asks for is answered by the server's cache of this one. Resolves true when the view was closed onto it.
 */
export function openInOrganize(folder) {
    const lib = state.library;
    if (!lib || !folder || state.moves.leaving) return Promise.resolve(false);
    state.moves.leaving = true;
    const done = () => {
        state.moves.leaving = false;
    };
    return api.fetch(`/api/folder/scan?path=${encodeURIComponent(folder)}&force=false`)
        .then(res => res.json().catch(() => ({})).then(body => ({ ok: res.ok, status: res.status, body })))
        .then(({ ok, status, body }) => {
            done();
            if (lib !== state.library) return false;       // another view since: nothing to leave
            if (!ok) {
                lib.notice = status === 400
                    ? 'That folder is not on disk any more.'
                    : `Could not open that folder on disk: ${(body && body.error) || `the server answered ${status}`}`;
                upper.libraryChanged();
                return false;
            }
            state.moves.anchor = { path: topPhotoPath(), folder, toKind: 'folder' };
            closeViewOntoFolder(folder);
            return true;
        })
        .catch(err => {
            done();
            console.error('Could not scan the folder to open it in Organize:', err);
            if (lib !== state.library) return false;
            lib.notice = `Could not open that folder on disk: ${err.message}`;
            upper.libraryChanged();
            return false;
        });
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
export function libraryViewPainted(lib, options) {
    landInLibrary(lib);
    checkNotHeld(lib, options);
}

export function wireMoves() {
    btnShowInLibrary.addEventListener('click', showInLibrary);
}
