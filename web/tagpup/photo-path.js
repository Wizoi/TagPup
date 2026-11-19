// TagPup's page: where the open photo is, in Image Details (owner, 2026-11-19). Its folder's name and its own name, each a link
// cut with an ellipsis when long and whole in its tooltip, and a button that copies the photo's whole path as the record spells
// it. The folder opens as a view of the library this page is on (api.js sends every request there, so a folder two libraries
// hold opens in this one); the name opens the photo in this computer's default app (photo.js openPhotoInDefaultApp, which
// main.js wires).
import { baseName, folderOf, samePath } from './common/paths.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import { btnCopyPath, detailFileName, detailFolder } from './elements.js';
import { setStatus } from './status.js';
import { backToView } from './view-left.js';

/** Show where `photo` is: the two names, and the whole of each in its tooltip. */
export function showPhotoPath(photo) {
    const folder = folderOf(photo.path);
    detailFolder.textContent = baseName(folder);
    detailFolder.title = `Show this folder in the library: ${folder}`;
    detailFileName.textContent = baseName(photo.path);
    detailFileName.title = `Open in the default app: ${photo.path}`;
}

/**
 * The open photo's folder, as a view of the library with its subfolders (as the navigator opens a folder). When that folder's
 * view is the one the photo was opened from, it is gone back to: the view would otherwise be found open already and nothing shown.
 */
function showFolder() {
    const folder = folderOf(state.activePhotoPath);
    const lib = state.library;
    if (lib && lib.kind === 'folder' && samePath(lib.value, folder)) backToView();
    else upper.openLibraryView({ kind: 'folder', value: folder, recursive: true });
}

/** Copy the open photo's whole path; a browser that will not let the page copy (an address that is not this computer's) says so. */
function copyPath() {
    const path = state.activePhotoPath;
    Promise.resolve()
        .then(() => window.navigator.clipboard.writeText(path))
        .then(() => setStatus('ready', 'Copied the full path.'))
        .catch(err => setStatus('error', `Could not copy the path: ${err.message}`, { transient: false }));
}

export function wirePhotoPath() {
    detailFolder.addEventListener('click', showFolder);
    btnCopyPath.addEventListener('click', copyPath);
}
