/**
 * Every request a page makes, and every /api/ image it shows, carries the library.
 *
 * The first part of the page's URL names the library -- /kr-track/ -- and the server
 * answers /kr-track/api/tags from that library (tagpup/web/libraries.py). A request
 * without it goes nowhere: there is no startup library to fall back on (#100). The
 * pages used to get this by replacing `fetch` and HTMLImageElement's `src` setter with
 * versions that put the library in front of /api/ -- a copy in each page, reaching
 * every call silently, including ones that were not the page's. Now a page asks here:
 *
 *     api.json('/api/tags').then(tags => ...)           // the reply's JSON
 *     api.fetch('/api/photo/rotate', { method: ... })   // the Response, for its status
 *     img.src = api.image(`/api/face-crop?id=${id}`)
 *     api.url('/api/people')                            // /kr-track/api/people
 *
 * What covers every library -- the Activity page's /api/activity/ -- is asked under no
 * library, whatever the page's URL names: `api.site.json('/api/activity/now')`,
 * `api.site.fetch(...)`, `api.site.url(...)`.
 *
 * The library is read from the page's URL at each call, and `fetch` is looked up at
 * each call, so the page tests' stub answers (tests/frontend/harness.mjs).
 *
 * While the always-on process moves onto a new version (tagpup.web.lifecycle) the server
 * answers a request 503 with X-TagPup-Updating, having done nothing with it, and then
 * answers nothing while it restarts. A request so turned away is sent again after the
 * Retry-After it was given, and again while nothing answers, for up to UPDATE_WAIT_MS:
 * the page waits out the update rather than showing it as an error. An /api/ image has
 * no such retry: one that fails makes the page ask how the server is, and once it has
 * seen the server away and answering again, it asks for the image again
 * (imagesAfterAnUpdate).
 */

/** How long a request waits out a server moving onto a new version. */
const UPDATE_WAIT_MS = 120000;

/** How long to wait before sending again when nothing answered. */
const RESTART_RETRY_MS = 1000;

/** First URL parts that are no library's name: the routes (tagpup.core.library.ROUTES). */
const NOT_A_LIBRARY = ['activity', 'api', 'common', 'gui', 'gui_tagpup'];

/**
 * The library a page's URL path names, or '' for none: its first part that is neither
 * a route nor a file (/index.html). This splits a URL, not a tag.
 */
export function libraryIn(pathname) {
    for (const segment of String(pathname || '').split('/')) {
        if (segment && !NOT_A_LIBRARY.includes(segment) && !segment.includes('.')) return segment;
    }
    return '';
}

/** The library this page is open on, or '' at the site's root. */
function pageLibrary() {
    return libraryIn(location.pathname);
}

/** '/api/x' (or 'api/x') under the page's library; anything else as it is. */
function apiUrl(path) {
    const text = String(path);
    const route = text.startsWith('api/') ? '/' + text : text;
    if (!route.startsWith('/api/')) return text;
    const library = pageLibrary();
    return library ? '/' + library + route : route;
}

/**
 * What a page with no library may ask: which libraries there are, and to make one --
 * and what a new one may be called (/api/rules).
 */
function askableWithoutALibrary(route) {
    return /^\/api\/(?:databases|rules|server)(?:\/|\?|$)/.test(route);
}

/** Was `res` a refusal because the server is moving onto a new version? */
function refusedForAnUpdate(res) {
    return !!res && res.status === 503 && !!res.headers && typeof res.headers.get === 'function'
        && !!res.headers.get('X-TagPup-Updating');
}

function pause(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * fetch(url, options), sent again while the server is moving onto a new version: after
 * a refusal saying so, and -- once one has said so -- while nothing answers.
 */
function fetchThroughAnUpdate(url, options, started = Date.now(), updating = false) {
    const again = (ms) => pause(ms).then(() => fetchThroughAnUpdate(url, options, started, true));
    return fetch(url, options).then(res => {
        if (refusedForAnUpdate(res) && Date.now() - started < UPDATE_WAIT_MS) {
            const seconds = Number(res.headers.get('Retry-After'));
            return again(Number.isFinite(seconds) && seconds >= 0 ? seconds * 1000 : RESTART_RETRY_MS);
        }
        return res;
    }, err => {
        if (updating && Date.now() - started < UPDATE_WAIT_MS) return again(RESTART_RETRY_MS);
        throw err;
    });
}

/**
 * The server's Response to `path` under the page's library. With no library in the
 * URL only the picker's routes are asked: every other one needs a library, and a page
 * that asked anyway got a 404 for each and showed them as errors.
 */
function apiFetch(path, options) {
    const url = apiUrl(path);
    if (!pageLibrary() && url.startsWith('/api/') && !askableWithoutALibrary(url)) {
        return Promise.reject(new Error('No library is open'));
    }
    return fetchThroughAnUpdate(url, options);
}

/** The reply's JSON, whatever its status: callers read `success` and `error` from it. */
function apiJson(path, options) {
    return apiFetch(path, options).then(res => res.json());
}

/** '/api/x' under no library: what covers every library. Anything but an /api/ path is refused. */
function siteUrl(path) {
    const text = String(path);
    const route = text.startsWith('api/') ? '/' + text : text;
    if (!route.startsWith('/api/')) throw new Error(`${text} is not an /api/ path`);
    return route;
}

function siteFetch(path, options) {
    let url;
    try {
        url = siteUrl(path);
    } catch (err) {
        return Promise.reject(err);
    }
    return fetchThroughAnUpdate(url, options);
}

/** How often a page asks whether the server is back, after an /api/ image failed. */
const IMAGE_PROBE_MS = 2000;

/**
 * Images that failed while the server moved onto a new version, asked for again once it
 * answers. `watch(document)` listens for an /api/ image's error (once per document; the
 * module does it for the page's own); `every` is how often it asks, a test's shorter.
 */
export const imagesAfterAnUpdate = (() => {
    const failed = new Set();
    const watched = new WeakSet();
    let probing = false;
    let every = IMAGE_PROBE_MS;

    function again(img) {
        if (!img.isConnected) return;
        const src = (img.getAttribute('src') || '').replace(/[?&]_again=\d+$/, '');
        img.setAttribute('src', src + (src.includes('?') ? '&' : '?') + '_again=' + Date.now());
    }

    function probe() {
        if (probing) return;
        probing = true;
        const started = Date.now();
        let away = false;
        const step = () => fetch(apiUrl('/api/server')).then(
            res => {
                if (refusedForAnUpdate(res)) away = true;
                return !refusedForAnUpdate(res);
            },
            () => { away = true; return false; },
        ).then(back => {
            if (back || Date.now() - started > UPDATE_WAIT_MS) {
                probing = false;
                const images = [...failed];
                failed.clear();
                // Only after the server was away: an image that fails with the server
                // there is broken for its own reasons, and asking again changes nothing.
                if (back && away) images.forEach(again);
                return;
            }
            setTimeout(step, every);
        });
        step();
    }

    function onError(event) {
        const target = event.target;
        if (!target || target.tagName !== 'IMG') return;
        if (!(target.getAttribute('src') || '').includes('/api/')) return;
        failed.add(target);
        probe();
    }

    return Object.freeze({
        watch(doc, options = {}) {
            if (options.every) every = options.every;
            if (!doc || watched.has(doc)) return;
            watched.add(doc);
            doc.addEventListener('error', onError, true);   // an image's error does not bubble
        },
    });
})();

if (typeof document !== 'undefined') imagesAfterAnUpdate.watch(document);

export const api = Object.freeze({
    url: apiUrl,
    image: apiUrl,
    fetch: apiFetch,
    json: apiJson,
    site: Object.freeze({
        url: siteUrl,
        fetch: siteFetch,
        json: (path, options) => siteFetch(path, options).then(res => res.json()),
    }),
});
