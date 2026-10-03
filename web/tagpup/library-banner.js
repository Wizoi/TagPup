// TagPup's page: the banner a library view of a folder shows when the disk holds photos the library does not
// (docs/ARCHITECTURE.md, phase 9c). Once the view has painted, the folder is asked what the library holds of it
// (GET /api/folder/membership) and the banner says "N photos in this folder are not in <library>" with Add them
// and Dismiss. Add them opens the add-folder question, the same one a folder opened offers: nothing is indexed until
// the person says so. The question has a deadline, and any failure -- a share away, a folder gone from the disk, a
// library that cannot say -- shows nothing, not an error: the view is the library's and the banner is only an offer.
import { api } from './common/api.js';
import { pathKey } from './common/paths.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import { btnMovesAdd, btnMovesDismiss, movesBanner, movesBannerText } from './elements.js';
import { libraryName } from './looking.js';
import { askToAdd } from './membership.js';

/** How long the question about the disk may take: an answer after this is not wanted. */
export const MEMBERSHIP_WAIT_MS = 8000;

export function hideBanner() {
    state.moves.banner = null;
    movesBanner.classList.add('hidden');
}

/** A view opened, closed or refreshed: an answer still on its way is not wanted, and the banner goes. */
export function forgetBanner() {
    const moves = state.moves;
    moves.asked += 1;
    if (moves.controller) moves.controller.abort();
    moves.controller = null;
    hideBanner();
}

function showBanner(found) {
    const count = found.photos_not_held;
    const name = libraryName() || 'the library';
    state.moves.banner = { folder: found.folder, count, found };
    movesBannerText.textContent = `${count.toLocaleString()} ${count === 1 ? 'photo' : 'photos'} in this folder `
        + `${count === 1 ? 'is' : 'are'} not in ${name}.`;
    movesBanner.classList.remove('hidden');
}

/** Ask what the disk holds of a library view's folder, once the view has painted. Nothing is shown unless it holds more. */
export function checkNotHeld(lib) {
    forgetBanner();
    if (lib !== state.library || lib.invalid || lib.kind !== 'folder') return;
    if (state.moves.dismissed.has(pathKey(lib.value))) return;
    const moves = state.moves;
    const token = moves.asked;
    const controller = new AbortController();
    moves.controller = controller;
    const timer = window.setTimeout(() => controller.abort(), MEMBERSHIP_WAIT_MS);
    api.fetch(`/api/folder/membership?path=${encodeURIComponent(lib.value)}`, { signal: controller.signal })
        .then(res => (res && res.ok ? res.json() : null))
        .then(found => {
            if (token !== moves.asked || lib !== state.library) return;
            if (!found || typeof found !== 'object' || !(found.photos_not_held > 0)) return;
            showBanner({ ...found, folder: found.folder || lib.value });
        })
        .catch(() => { /* a share away, a folder gone, a library that cannot say: the banner is an offer */ })
        .then(() => window.clearTimeout(timer));
}

/** Add them: the add-folder question for the view's folder, as a folder opened would ask it. */
function addThem() {
    const lib = state.library;
    const banner = state.moves.banner;
    if (!lib || !banner) return;
    state.moves.addFor = banner.folder;
    askToAdd({ ...banner.found, folder: banner.folder });
}

function dismiss() {
    const lib = state.library;
    if (lib && lib.kind === 'folder') state.moves.dismissed.add(pathKey(lib.value));
    hideBanner();
}

/** The folder was added from a view (membership.js addFolderToLibrary): the banner goes and the strip says it is being indexed. */
export function addedFromView(folder) {
    const lib = state.library;
    if (lib && lib.kind === 'folder') state.moves.dismissed.add(pathKey(lib.value));
    hideBanner();
    if (lib) {
        lib.notice = `${folder} is added to ${libraryName()} and is being indexed. Refresh view when it has finished to see its photos.`;
        upper.libraryChanged();
    }
}

export function wireBanner() {
    btnMovesAdd.addEventListener('click', addThem);
    btnMovesDismiss.addEventListener('click', dismiss);
}
