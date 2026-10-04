// TagPup's page: the banner a library view of a folder shows when the disk holds photos the library does not
// (docs/ARCHITECTURE.md, phase 9c). The folder is asked what the library holds of it (GET /api/folder/membership, which
// WALKS the folder on disk) and the banner says "N photos in this folder are not in <library>" with Add them and Dismiss.
// Add them opens the add-folder question, the same one a folder opened offers: nothing is indexed until the person says so.
//
// A walk costs a folder's worth of disk, so it is not made on every view change (findings #568):
//  * the answer is kept per folder for the life of the page, and forgotten by Refresh view, an Add, and a change in when
//    the library was last in step (sync-state.js) -- the moments the disk and the library may have moved apart;
//  * it is asked by itself only for a view of THIS FOLDER ONLY and for one with its subfolders that holds under
//    AUTO_CHECK_BELOW photos; a larger view shows a quiet link, "Check this folder on disk for new photos", that asks when clicked;
//  * the question has a deadline, and any failure -- a share away ("could not check"), a folder gone from the disk, a library
//    that cannot say -- shows nothing, not an error: the view is the library's and the banner is only an offer.
import { api } from './common/api.js';
import { pathKey } from './common/paths.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import { btnMovesAdd, btnMovesCheck, btnMovesDismiss, movesBanner, movesBannerText } from './elements.js';
import { libraryName } from './looking.js';
import { askToAdd } from './membership.js';

/** How long the question about the disk may take: an answer after this is not wanted (the server gives up at 12 s). */
export const MEMBERSHIP_WAIT_MS = 15000;

/** A recursive view of fewer photos than this asks by itself; a larger one waits to be asked. */
export const AUTO_CHECK_BELOW = 1000;

/** What the banner shows: 'offer' (text, Add them, Dismiss), 'check' (the quiet link) or 'note' (text only). */
function paint(mode, text) {
    movesBanner.classList.remove('hidden');
    movesBanner.classList.toggle('moves-banner-quiet', mode !== 'offer');
    movesBannerText.textContent = text;
    movesBannerText.classList.toggle('hidden', !text);
    btnMovesAdd.classList.toggle('hidden', mode !== 'offer');
    btnMovesDismiss.classList.toggle('hidden', mode !== 'offer');
    btnMovesCheck.classList.toggle('hidden', mode !== 'check');
    btnMovesCheck.disabled = false;
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

function showOffer(found) {
    const count = found.photos_not_held;
    const name = libraryName() || 'the library';
    state.moves.banner = { kind: 'offer', folder: found.folder, count, found };
    paint('offer', `${count.toLocaleString()} ${count === 1 ? 'photo' : 'photos'} in this folder `
        + `${count === 1 ? 'is' : 'are'} not in ${name}.`);
}

function showCheckLink(lib) {
    state.moves.banner = { kind: 'check', folder: lib.value };
    paint('check', '');
}

/** What an answer from the disk makes the banner: the offer, or (when the person asked) a sentence, or nothing. */
function present(lib, found, asked) {
    if (!found || typeof found !== 'object') return asked ? paint('note', 'Could not check this folder on disk just now.') : undefined;
    if (found.could_not_check) {
        if (asked) paint('note', 'Could not check this folder on disk just now (' + (found.why || 'it did not answer') + ').');
        return undefined;
    }
    if (found.photos_not_held > 0) {
        showOffer({ ...found, folder: found.folder || lib.value });
        return undefined;
    }
    if (asked) paint('note', 'Every photo on disk in this folder is in the library.');
    return undefined;
}

/** Ask the disk about a library view's folder now (the answer is kept for the page). `asked`: the person clicked the link. */
function ask(lib, asked) {
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
            present(lib, found, asked);
        })
        .catch(() => {
            // A share away, a folder gone, a library that cannot say: the banner is an offer, nothing is shown (unless asked).
            if (token === moves.asked && lib === state.library && asked) present(lib, null, true);
        })
        .then(() => window.clearTimeout(timer));
}

/**
 * A library view of a folder has painted: say what the disk holds of it, from what the page already knows, by asking, or --
 * for a large view with subfolders -- by offering to ask. `refreshed`: Refresh view, which forgets what was known.
 */
export function checkNotHeld(lib, { refreshed = false } = {}) {
    forgetBanner();
    if (lib !== state.library || lib.invalid || lib.kind !== 'folder') return;
    const key = pathKey(lib.value);
    if (refreshed) state.moves.cache.delete(key);
    if (state.moves.dismissed.has(key)) return;
    const known = state.moves.cache.get(key);
    if (known) {
        present(lib, known, false);
        return;
    }
    if (lib.recursive && lib.total >= AUTO_CHECK_BELOW) {
        showCheckLink(lib);
        return;
    }
    ask(lib, false);
}

/** The quiet link was clicked: ask. */
function checkNow() {
    const lib = state.library;
    if (!lib || lib.invalid || lib.kind !== 'folder') return;
    btnMovesCheck.disabled = true;
    movesBannerText.textContent = 'Checking the folder on disk...';
    movesBannerText.classList.remove('hidden');
    state.moves.asked += 1;
    ask(lib, true);
}

/** Add them: the add-folder question for the view's folder, as a folder opened would ask it. */
function addThem() {
    const lib = state.library;
    const banner = state.moves.banner;
    if (!lib || !banner || banner.kind !== 'offer') return;
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
        lib.notice = `${folder} is added to ${libraryName()} and is being indexed. Refresh view when it has finished to see its photos.`;
        upper.libraryChanged();
    }
}

export function wireBanner() {
    btnMovesAdd.addEventListener('click', addThem);
    btnMovesDismiss.addEventListener('click', dismiss);
    btnMovesCheck.addEventListener('click', checkNow);
}
