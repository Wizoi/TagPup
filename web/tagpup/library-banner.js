// TagPup's page: the banner a library view of a folder shows when the disk holds photos the library does not
// (docs/ARCHITECTURE.md, phase 9c). The folder is asked what the library holds of it (GET /api/folder/membership, which
// WALKS the folder on disk) and the banner says "N photos in this folder are not in <library>" with Add them and Dismiss.
// Add them opens the add-folder question, the same one a folder opened offers: nothing is indexed until the person says so.
//
// A walk costs a folder's worth of disk, so it is not made on every view change (findings #568):
//  * the answer is kept per folder for the life of the page, and forgotten by Refresh view, an Add, and a change in when
//    the library was last in step (sync-state.js) -- the moments the disk and the library may have moved apart;
//  * it is asked by itself only for a view of THIS FOLDER ONLY and for one with its subfolders that holds under
//    AUTO_CHECK_BELOW photos; a larger view is asked by Refresh view (the owner, 2026-11-13: the quiet link that asked it,
//    "Check this folder on disk for new photos", was one button too many beside Refresh view);
//  * the question has a deadline, and any failure -- a share away ("could not check"), a folder gone from the disk, a library
//    that cannot say -- shows nothing, not an error: the view is the library's and the banner is only an offer;
//  * one question is listened to at a time: a Refresh, another view or a closed one drops the answer still on its way (the
//    server walks a folder once however often it is asked, tagpup.services.libraries.membership_checked).
import { api } from './common/api.js';
import { pathKey } from './common/paths.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import { btnMovesAdd, btnMovesDismiss, movesBanner, movesBannerText } from './elements.js';
import { libraryName } from './looking.js';
import { askToAdd } from './membership.js';

/** How long the question about the disk may take: an answer after this is not wanted (the server gives up at 12 s). */
export const MEMBERSHIP_WAIT_MS = 15000;

/** A recursive view of fewer photos than this asks by itself; a larger one is asked by Refresh view. */
export const AUTO_CHECK_BELOW = 1000;

/** The offer: "N photos in this folder are not in <library>.", Add them, Dismiss. */
function showOffer(found) {
    const count = found.photos_not_held;
    const name = libraryName() || 'the library';
    state.moves.banner = { folder: found.folder, count, found };
    movesBannerText.textContent = `${count.toLocaleString()} ${count === 1 ? 'photo' : 'photos'} in this folder `
        + `${count === 1 ? 'is' : 'are'} not in ${name}.`;
    movesBanner.classList.remove('hidden');
    upper.renderSyncInfo();     // photos the library does not hold: when it was last in step is worth saying (#670)
}

export function hideBanner() {
    state.moves.banner = null;
    movesBanner.classList.add('hidden');
    upper.renderSyncInfo();
}

/** A view opened, closed or refreshed: an answer still on its way is not wanted, and the banner goes. */
export function forgetBanner() {
    const moves = state.moves;
    moves.asked += 1;
    if (moves.controller) moves.controller.abort();
    moves.controller = null;
    hideBanner();
}

/** The disk and the library may have moved apart: what was said of any folder is not to be trusted. */
export function forgetWhatTheDiskHeld(folder = null) {
    if (folder) state.moves.cache.delete(pathKey(folder));
    else state.moves.cache.clear();
}

/** What an answer from the disk makes the banner: the offer, or nothing. */
function present(lib, found) {
    if (found && typeof found === 'object' && !found.could_not_check && found.photos_not_held > 0) {
        showOffer({ ...found, folder: found.folder || lib.value });
    }
}

/** Ask the disk about a library view's folder now (the answer is kept for the page). */
function ask(lib) {
    const moves = state.moves;
    const token = moves.asked;
    const controller = new AbortController();
    moves.controller = controller;
    const timer = window.setTimeout(() => controller.abort(), MEMBERSHIP_WAIT_MS);
    api.fetch(`/api/folder/membership?path=${encodeURIComponent(lib.value)}`, { signal: controller.signal })
        .then(res => (res && res.ok ? res.json() : null))
        .then(found => {
            if (token !== moves.asked || lib !== state.library) return;
            // Only an answer is kept: not a failure, not a "could not check".
            if (found && typeof found === 'object' && !found.could_not_check) moves.cache.set(pathKey(lib.value), found);
            present(lib, found);
        })
        .catch(() => {})    // a share away, a folder gone, a library that cannot say: the banner is an offer, nothing is shown
        .then(() => window.clearTimeout(timer));
}

/**
 * A library view of a folder has painted: say what the disk holds of it, from what the page already knows or by asking -- a
 * large view with subfolders only when `refreshed`: Refresh view, which forgets what was known and asks whatever the size.
 */
export function checkNotHeld(lib, { refreshed = false } = {}) {
    forgetBanner();
    if (lib !== state.library || lib.invalid || lib.kind !== 'folder') return;
    const key = pathKey(lib.value);
    if (refreshed) state.moves.cache.delete(key);
    if (state.moves.dismissed.has(key)) return;
    const known = state.moves.cache.get(key);
    if (known) {
        present(lib, known);
        return;
    }
    if (!refreshed && lib.recursive && lib.of >= AUTO_CHECK_BELOW) return;   // the folder's size, whatever Show shows
    ask(lib);
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
    forgetWhatTheDiskHeld();
    hideBanner();
    if (lib) {
        lib.notice = `${folder} is added to ${libraryName()} and is being indexed. The view refreshes by itself when it has finished.`;
        upper.libraryChanged();
    }
}

export function wireBanner() {
    btnMovesAdd.addEventListener('click', addThem);
    btnMovesDismiss.addEventListener('click', dismiss);
}
