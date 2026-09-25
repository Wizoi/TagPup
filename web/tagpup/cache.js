// TagPup's page: a folder's scan, kept in this browser for half an hour, so opening it
// again does not scan it again.
import { pathKey } from './common/paths.js';
import { state } from './state.js';

// Browser local storage cache configuration (30 minutes timeout)
export const CACHE_TTL_MS = 30 * 60 * 1000;

export function saveToLocalStorageCache() {
    if (!state.scannedFolder) return;
    const cacheEntry = {
        timestamp: Date.now(),
        photos: state.folderPhotos,
        suggestions: state.folderSuggestions
    };
    try {
        localStorage.setItem(folderCacheKey(state.scannedFolder), JSON.stringify(cacheEntry));
        // An entry under the folder as it was typed predates keying by pathKey;
        // this one supersedes it, so drop it rather than leave it using quota.
        const legacyKey = `tagpup_cache_${state.scannedFolder}`;
        if (legacyKey !== folderCacheKey(state.scannedFolder)) localStorage.removeItem(legacyKey);
    } catch (e) {
        console.warn("Storage quota exceeded, could not cache folder data.");
    }
}

// ------------------------------------------------------------------ paths --
// Every photo and folder path the server sends is already in one spelling -- the
// native absolute path, exactly as the database holds it -- so server paths are
// compared with `===` and never rewritten here. pathKey and samePath
// (web/common/paths.js) are for the two cases that are not server paths: what
// somebody typed into the folder box, and what the native Browse dialog returned
// (forward slashes). tests/frontend/path-helpers.test.mjs fails on a separator
// conversion anywhere else.

/** Where a folder's scan is cached: one entry per folder, however it was typed. */
export function folderCacheKey(folder) {
    return `tagpup_cache_${pathKey(folder)}`;
}
