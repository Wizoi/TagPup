/**
 * A library whose root this machine does not place says so, at the top of the page
 * (docs/ARCHITECTURE.md, "Roots and machines"; tagpup/web/roots_gate.py).
 *
 * A library that holds a root remembers a photo as the root's name and the path under it;
 * this computer's machine_roots.json says where the root is. Without that, the server
 * answers each request that needs a photo's path 409, with a sentence that names the file
 * and the line to add, and the header X-TagPup-Roots-Problem. api.js sees the header and
 * announces it as a `tagpup:roots-problem` event; this puts the sentence in the banner, so
 * the page shows why it is empty instead of a list of failed requests.
 */
/** The event api.js raises on the document. */
export const ROOTS_PROBLEM = 'tagpup:roots-problem';

/**
 * Show `message` in the banner at the top of the page: each page's index.html holds it,
 * hidden --
 *
 *     <div id="roots-banner" class="roots-banner hidden" role="alert">
 *         <strong>This computer does not know where this library keeps its photos.</strong>
 *         <span class="roots-banner-message"></span>
 *     </div>
 */
export function showRootsBanner(message, heading) {
    const banner = document.getElementById('roots-banner');
    if (!banner) return;
    if (heading !== undefined) banner.querySelector('strong').textContent = heading;
    banner.querySelector('.roots-banner-message').textContent = String(message || '');
    banner.classList.remove('hidden');
}

/** The event api.js raises when the server rewrote a request's paths: the root's place changed. */
export const ROOTS_MOVED = 'tagpup:roots-moved';

/** At most one reload in this long, so a page that cannot converge does not reload for ever. */
const RELOAD_EVERY_MS = 30000;
const RELOAD_KEY = 'tagpup-roots-reload';

/**
 * The page holds paths spelled by a place the root has left: say so, and reload once (never more than
 * once in 30 s: the stamp is kept in sessionStorage). The page's held paths are not translated.
 */
export function rootsMoved(root) {
    showRootsBanner(`The place of root ${root} changed since this page loaded; reloading`, '');
    let last = 0;
    try {
        last = Number(window.sessionStorage.getItem(RELOAD_KEY)) || 0;
    } catch (err) {
        last = 0;
    }
    if (Date.now() - last < RELOAD_EVERY_MS) return false;
    try {
        window.sessionStorage.setItem(RELOAD_KEY, String(Date.now()));
    } catch (err) {
        // no storage: reload anyway, once per load (api.js announces once)
    }
    window.location.reload();
    return true;
}

/** The event api.js raises when a response names another version than the page's first did. */
export const UPDATED = 'tagpup:updated';

/**
 * The server was replaced by another version while this page was open (a launch of a newer
 * one, tagpup/launcher.py): say so in the banner. The page is not reloaded for the owner --
 * an unsaved edit would go with it -- and runs its old code until they reload.
 */
export function serverUpdated(detail) {
    const to = detail && detail.to && detail.to !== 'checkout' ? ` (${detail.to})` : '';
    showRootsBanner(`Reload this page to use it${to}; until then it runs the version it was opened with.`,
        'TagPup was updated while this page was open. ');
}

/** Listen for the problem, and for an update; the page's main.js calls this once. */
export function wireRootsBanner() {
    document.addEventListener(ROOTS_PROBLEM, (event) => {
        showRootsBanner(event.detail && event.detail.message, 'This computer does not know where this library keeps its photos. ');
    });
    document.addEventListener('tagpup:roots-moved', (event) => {
        rootsMoved(event.detail && event.detail.root);
    });
    document.addEventListener(UPDATED, (event) => {
        serverUpdated(event.detail);
    });
}
