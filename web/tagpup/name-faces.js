// TagPup's "Name faces from tags" (web/common/name-faces.js; docs/findings.md, #789): two ways into the one dialog. The button in
// the Organize folder view's header is the open folder's: the dialog asks how much to look at (only that folder, the default, or
// the whole library), and the result counts the folder's faces before and after. It is shown only while a folder is open on disk
// (folder.js, updateCurrentFolderLabel, with Show in library): in a library view or a search's results it could not act on the
// photos shown, and would name the whole library. The gear's item is the whole library's, with no folder step. What this adds is
// the page's side of it: the folder that is open, and, once names were written, the open photo's faces read again.
import { attachNameFaces, openNameFaces } from './common/name-faces.js';
import { btnNameFaces } from './elements.js';
import { upper } from './hooks.js';
import { state } from './state.js';

/** Names were written: the open photo's faces are drawn again. */
function namesWritten() {
    if (state.activePhotoPath) upper.renderPhotoFaces(state.activePhotoPath);
}

/** The folder view's button: the dialog offers the folder that is open as "Only this folder". */
export function startNamingFacesInFolder() {
    return openNameFaces({ folder: () => state.scannedFolder || null, changed: namesWritten });
}

/** The gear's item: the whole library, whatever is open. */
export function startNamingFacesInLibrary() {
    return openNameFaces({ folder: null, changed: namesWritten });
}

export function wireNameFaces() {
    btnNameFaces.addEventListener('click', startNamingFacesInFolder);
    // A job already under way (another page, a reload) shows its progress.
    attachNameFaces({ changed: namesWritten });
}
