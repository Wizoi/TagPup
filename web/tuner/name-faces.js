// TagTuner's "Name faces from tags" (web/common/name-faces.js; docs/findings.md, #789): the button in the Folder Matches
// header and the gear's item both open the one dialog, which reads the plan of the whole library, asks, and writes.
// What this adds is the page's side of it: the button is shown with Folder Matches, the target it belongs to, and
// once names were written the list beside the photo and the names offered while typing are read again.
import { attachNameFaces, openNameFaces } from './common/name-faces.js';
import { modeSelect } from './elements.js';
import { fetchKnownPeople } from './shared.js';
import { refreshSidebarQuietly } from './sidebar.js';
import { state } from './state.js';

/**
 * TagTuner has no one open folder: Folder Matches lists photos in folder groups. The folder the dialog offers ("Only this
 * folder", docs/findings.md #994) is the group of the photo selected in the list, or none (the whole library, as before).
 */
export function selectedFolder() {
    if (!state.activePhotoPath) return null;
    const photo = state.allPhotos.find(each => each.path === state.activePhotoPath);
    // A photo with no folder is listed under the group "Root" (sidebar.js): that is no folder, so none is offered.
    if (!photo || !photo.folder) return null;
    return (photo.folderGroup && photo.folderGroup.name) || null;
}

/** What the page gives the dialog: the selected photo's folder (or none); names written read its lists again. */
const nameFacesOptions = {
    folder: selectedFolder,
    changed: () => {
        fetchKnownPeople();
        refreshSidebarQuietly();
    },
};

/** Open the dialog: from the header's button and from the gear. */
export function startNamingFaces() {
    return openNameFaces(nameFacesOptions);
}

/** The header's button, shown with Folder Matches (the list of photos whose faces still wait for a name). */
export function wireNameFaces() {
    const container = document.getElementById('name-faces-container');
    const button = document.getElementById('btn-name-faces');
    if (!container || !button) return;
    const sync = () => container.classList.toggle('hidden', modeSelect.value !== 'folder-match');
    modeSelect.addEventListener('change', sync);
    button.addEventListener('click', startNamingFaces);
    sync();
    // A job already under way (another page, a reload) shows its progress.
    attachNameFaces(nameFacesOptions);
}
