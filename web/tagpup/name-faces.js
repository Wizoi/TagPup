// TagPup's "Name faces from tags" (web/common/name-faces.js; docs/findings.md, #789): the button in the Organize folder
// view's header opens the one dialog, which reads the plan of the whole library, asks, and writes. What this adds is the
// page's side of it: the folder that is open, whose faces the result counts before and after, and, once names were
// written, the open photo's faces read again.
import { wireFaceJobBanner } from './common/face-job-banner.js';
import { attachNameFaces, openNameFaces } from './common/name-faces.js';
import { upper } from './hooks.js';
import { state } from './state.js';

/** What the page gives the dialog: the folder that is open (none is no problem: the job is the library's), and what to draw again. */
const nameFacesOptions = {
    folder: () => state.scannedFolder || null,
    changed: () => {
        if (state.activePhotoPath) upper.renderPhotoFaces(state.activePhotoPath);
    },
};

/** Open the dialog for the folder that is open. */
export function startNamingFacesInFolder() {
    return openNameFaces(nameFacesOptions);
}

/** A bulk assignment of faces that stopped part-way, or runs in another window, is offered at the top of the page (#907). */
export function wireFaceJobs() {
    return wireFaceJobBanner({
        changed: () => {
            if (state.activePhotoPath) upper.renderPhotoFaces(state.activePhotoPath);
        },
    });
}

export function wireNameFaces() {
    const button = document.getElementById('btn-name-faces');
    if (button) button.addEventListener('click', startNamingFacesInFolder);
    // A job already under way (another page, a reload) shows its progress.
    attachNameFaces(nameFacesOptions);
}
