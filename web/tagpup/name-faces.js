// TagPup's "Name faces from tags" (web/common/name-faces.js; docs/findings.md, #789): the button in the Organize folder
// view's header opens the one dialog, which reads the plan of the whole library, asks, and writes. What this adds is the
// page's side of it: the folder that is open, whose faces the result counts before and after, and, once names were
// written, the open photo's faces read again.
import { openNameFaces } from './common/name-faces.js';
import { upper } from './hooks.js';
import { state } from './state.js';

/** Open the dialog for the folder that is open (none is no problem: the job is the library's). */
export function startNamingFaces() {
    return openNameFaces({
        folder: () => state.scannedFolder || null,
        changed: () => {
            if (state.activePhotoPath) upper.renderPhotoFaces(state.activePhotoPath);
        },
    });
}

export function wireNameFaces() {
    const button = document.getElementById('btn-name-faces');
    if (button) button.addEventListener('click', startNamingFaces);
}
