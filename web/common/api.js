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
 * The library is read from the page's URL at each call, and `fetch` is looked up at
 * each call, so the page tests' stub answers (tests/frontend/harness.mjs).
 */

/** First URL parts that are no library's name: the routes (tagpup.core.library.ROUTES). */
const NOT_A_LIBRARY = ['api', 'common', 'gui', 'gui_tagpup'];

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
    return /^\/api\/(?:databases|rules)(?:\/|\?|$)/.test(route);
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
    return fetch(url, options);
}

/** The reply's JSON, whatever its status: callers read `success` and `error` from it. */
function apiJson(path, options) {
    return apiFetch(path, options).then(res => res.json());
}

export const api = Object.freeze({
    url: apiUrl,
    image: apiUrl,
    fetch: apiFetch,
    json: apiJson,
});
