// TagPup's page: a folder's scan, kept in this browser for half an hour, so opening it
// again does not scan it again.
import { pathKey, samePath } from './common/paths.js';
import { state } from './state.js';

// Browser local storage cache configuration (30 minutes timeout)
export const CACHE_TTL_MS = 30 * 60 * 1000;

// The shape of the photo records an entry holds. An entry saved by a page that made records of another shape (no version at
// all, or an older one) is not used: the folder is scanned again. Raise it whenever a record gains a field a page reads
// (2: `camera`, which Shift Date Taken's list of cameras is made of: a record without it is "Unknown Camera").
export const CACHE_VERSION = 2;

export function saveToLocalStorageCache() {
    if (!state.scannedFolder) return;
    const cacheEntry = {
        version: CACHE_VERSION,
        timestamp: Date.now(),
        photos: state.folderPhotos,
        suggestions: state.folderSuggestions,
        // Found by a Suggest that only looked, and kept in the server's memory (suggestions.js).
        inMemory: Boolean(state.suggestionsInMemory)
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

/**
 * A photo deleted from a folder that is not the one open any more: the scan this browser kept for that folder must not
 * list it (the open folder's is saved as it stands, saveToLocalStorageCache). Nothing is kept for a folder never scanned.
 */
export function forgetCachedPhoto(folder, photoPath) {
    try {
        const key = folderCacheKey(folder);
        const kept = JSON.parse(localStorage.getItem(key) || 'null');
        if (!kept || !Array.isArray(kept.photos)) return;
        kept.photos = kept.photos.filter(photo => !samePath(photo.path, photoPath));
        localStorage.setItem(key, JSON.stringify(kept));
    } catch (e) {
        console.warn('Could not take a deleted photo out of the scan kept for its folder:', e);
    }
}

/**
 * Forget every folder's scan this browser kept: a bulk edit rewrote photo files across folders, and a scan kept from before names their
 * old stamps (a save from it would be refused as changed on disk) and their old tags. Other keys of local storage are left alone.
 * With `before` (a time in ms) only the scans kept before then go: a scan saved after a job ended already shows what it wrote. A scan
 * whose time cannot be read goes too. Returns how many were forgotten.
 */
export function forgetFolderCaches({ before = Infinity } = {}) {
    try {
        const stale = [];
        for (let at = 0; at < localStorage.length; at++) {
            const key = localStorage.key(at);
            if (!key || !key.startsWith('tagpup_cache_')) continue;
            let kept = NaN;
            try {
                kept = Number(JSON.parse(localStorage.getItem(key)).timestamp);
            } catch (e) {
                kept = NaN;
            }
            if (!Number.isFinite(kept) || kept < before) stale.push(key);
        }
        for (const key of stale) localStorage.removeItem(key);
        return stale.length;
    } catch (e) {
        console.warn('Could not forget the folders kept in this browser:', e);
        return 0;
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
