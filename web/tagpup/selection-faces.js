// TagPup's page: who the unnamed faces of the selected photos look like, for the selection panel's Suggested People
// (selection.js counts them with what a Suggest run stored). The people and the cut each photo's own faces strip offers,
// asked of the server once for the photos of the selection it has not answered yet (/api/photo-faces/suggestions), and
// kept per photo in `state.selectionFaces` until the folder changes (folder.js, library-view.js) or a face of the photo is
// named, has its name taken off or is left out (face-boxes.js). A library view's selection is not asked about.
import { api } from './common/api.js';
import { samePath } from './common/paths.js';
import { upper } from './hooks.js';
import { state } from './state.js';

/** The most photos one ask names; a larger selection asks nothing (tagpup.web.photo_face_routes.SUGGESTIONS_AT_MOST). */
const FACES_ASKED_AT_MOST = 500;

/**
 * Ask who the faces of the selected `paths` look like, those not answered yet, in one request, and draw the panel again
 * when it answers. An ask still out for other photos is given up: the selection moved on, and its answer must not draw.
 * A failed ask keeps nothing, says nothing (the panel shows what it showed), and is asked again with the next selection.
 */
export function askSelectionFaces(paths) {
    const wanted = paths.length > FACES_ASKED_AT_MOST ? [] : paths.filter(path => !Object.hasOwn(state.selectionFaces, path));
    const key = wanted.join('\n');
    const asking = state.selectionFacesAsking;
    if (asking && asking.key === key) return;   // these photos are being asked about already
    if (asking) asking.controller.abort();
    state.selectionFacesAsking = null;
    if (!wanted.length) return;
    const mine = { key, controller: new AbortController() };
    state.selectionFacesAsking = mine;
    api.fetch('/api/photo-faces/suggestions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths: wanted }),
        signal: mine.controller.signal,
    })
        .then(res => api.answered(res, 'The faces of the selection could not be read.'))
        .then(data => {
            if (state.selectionFacesAsking !== mine) return;
            state.selectionFacesAsking = null;
            const found = (data && data.suggestions) || {};
            wanted.forEach(path => { state.selectionFaces[path] = found[path] || []; });
            upper.updateSelectedThumbnailsCount();
        })
        .catch(() => {
            if (state.selectionFacesAsking === mine) state.selectionFacesAsking = null;
        });
}

/** The folder changed: nothing kept of the last one's faces, and an ask still out is given up. */
export function forgetSelectionFaces() {
    if (state.selectionFacesAsking) state.selectionFacesAsking.controller.abort();
    state.selectionFacesAsking = null;
    state.selectionFaces = {};
}

/** A face of the photo at `path` was named, had its name taken off or was left out: ask again who its faces look like. */
export function forgetFacesOfPhoto(path) {
    // An ask still out may answer from before the change: given up, and asked again by the redraw.
    if (state.selectionFacesAsking) state.selectionFacesAsking.controller.abort();
    state.selectionFacesAsking = null;
    Object.keys(state.selectionFaces).filter(held => samePath(held, path)).forEach(held => delete state.selectionFaces[held]);
    upper.updateSelectedThumbnailsCount();
}
