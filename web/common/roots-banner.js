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
export function showRootsBanner(message) {
    const banner = document.getElementById('roots-banner');
    if (!banner) return;
    banner.querySelector('.roots-banner-message').textContent = String(message || '');
    banner.classList.remove('hidden');
}

/** Listen for the problem; the page's main.js calls this once. */
export function wireRootsBanner() {
    document.addEventListener(ROOTS_PROBLEM, (event) => {
        showRootsBanner(event.detail && event.detail.message);
    });
}
