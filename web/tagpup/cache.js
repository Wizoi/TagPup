// TagPup's page: a folder's scan, kept in this browser for half an hour, so opening it
// again does not scan it again. Each library's own: two libraries can hold one folder.
import { libraryIn } from './common/api.js';
import { isUnder, pathKey, rekeyed, renamedBy } from './common/paths.js';
import { state } from './state.js';

// Browser local storage cache configuration (30 minutes timeout)
export const CACHE_TTL_MS = 30 * 60 * 1000;

// The shape of the photo records an entry holds. An entry saved by a page that made records of another shape (no version at
// all, or an older one) is not used: the folder is scanned again. Raise it whenever a record gains a field a page reads
// (2: `camera`, which Shift Date Taken's list of cameras is made of: a record without it is "Unknown Camera"; 3: a
// folder's clips, with `media` and a recorded clip's `id` and `thumb`: an older entry lists none of them; 4: `writes`, the
// edits a photo's panel offers (stale.js): a record without it offers none; 5: `writes` naming "name", a photo's and a
// clip's Smart Rename button: an older record holds it inert; 6: a clip's `writes` naming "date", and its `taken` read
// from its XMP date first: an older record holds a clip's date inert, or shows its QuickTime date; 7: `order`, the key a
// folder's records are put in its order by, given by the server (inFolderOrder): an older record holds none).
export const CACHE_VERSION = 7;

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
    const key = folderCacheKey(state.scannedFolder);
    try {
        localStorage.setItem(key, JSON.stringify(cacheEntry));
    } catch (e) {
        forgetKept(key, e);
    }
}

/**
 * Photos written, `paths` as the write's reply names them: `take(record)` changes one record of one of them, and is applied
 * the same to every record of it the page holds and this browser keeps -- the open view's (a folder's, or a library view's),
 * and every scan kept of this library whose folder holds the photo, its own folder's or one above it (a scan is recursive:
 * isUnder; #3308). A folder's records are put back in its order (inFolderOrder); a library view's keep theirs. Another
 * library's kept scans are its own (#1359). `renames`, {path sent: path now}, names photos the write renamed: a kept
 * scan's suggestions follow them to their new names (renamedBy), as the open view's do (edits.js photosRenamed). Returns
 * how many of the open view's records took it, for the caller to draw.
 */
export function photosWritten(paths, take, renames = {}) {
    const now = renamedBy(renames);
    const keys = new Set(paths.map(pathKey));
    const takeAll = (records) => records.filter(record => keys.has(pathKey(record.path))).map(take).length;
    const shown = takeAll(state.folderPhotos);
    const open = state.scannedFolder ? folderCacheKey(state.scannedFolder) : null;
    if (open && shown) {
        state.folderPhotos = inFolderOrder(state.folderPhotos);
        saveToLocalStorageCache();
    }
    for (const [key, folder] of keptScans()) {
        if (key === open || !paths.some(path => isUnder(path, folder))) continue;
        try {
            const kept = JSON.parse(localStorage.getItem(key));
            // A scan an older page kept is scanned again when opened (folder.js): nothing to change in it.
            if (!kept || kept.version !== CACHE_VERSION || !Array.isArray(kept.photos) || !takeAll(kept.photos)) continue;
            kept.photos = inFolderOrder(kept.photos);
            if (kept.suggestions) kept.suggestions = rekeyed(kept.suggestions, now);
            localStorage.setItem(key, JSON.stringify(kept));
        } catch (e) {
            forgetKept(key, e);
        }
    }
    return shown;
}

/**
 * Records a write left as the page holds them now -- a save of one photo, a turn, a Smart Rename, a Convert: each took its
 * reply (its stamp and base, tags and title, Date Taken and `order`, its new path) -- and every other record of each that
 * the page holds or this browser keeps takes the same, a copy of the written record over it (photosWritten: the open
 * folder and every kept scan holding it, put back in its order, so a new Date Taken moves the photo at once). `renames`
 * {path sent: path now}, photosRenamed's shape, names photos the write renamed: another record of one holds the path sent,
 * and takes the record written at the path that photo has now (renamedBy: in a chain an old name of one photo is the new
 * name of another, and a record still at Regatta - 1 is the photo now at Regatta - 2; #3329). A record of a photo the reply
 * gives no record of takes nothing. The records given are the write's own, already as it left them, and take nothing.
 * Returns photosWritten's count of the open view's records.
 */
export function recordsWritten(records, renames = {}) {
    const own = new Set(records);
    const now = renamedBy(renames);
    const copies = new Map(records.map(record => [pathKey(record.path), JSON.stringify(record)]));
    const paths = [...records.map(record => record.path), ...Object.keys(renames)];
    return photosWritten(paths, (record) => {
        const copy = copies.get(pathKey(now(record.path)));
        if (!own.has(record) && copy) Object.assign(record, JSON.parse(copy));
    }, renames);
}

/**
 * Photos this browser can no longer vouch for: the page going with a write of them out (a reload, another library chosen,
 * the tab closed) abandons its reply, written or not. Every scan kept of this library whose folder holds one of `paths`
 * -- its own folder's, every one above it (isUnder), the open folder's too -- is forgotten, so opened again it is scanned
 * rather than drawn with stamps older than the files, which a save would be refused for as changed on disk (#3308,
 * #3323). Another library's kept scans are its own (#1359). Returns how many were forgotten.
 */
export function photosGone(paths) {
    const gone = keptScans().filter(([, folder]) => paths.some(path => isUnder(path, folder)));
    gone.forEach(([key]) => localStorage.removeItem(key));
    return gone.length;
}

/**
 * A folder's records in its order: by each one's `order`, the key the server gives every record of a scan and every photo
 * a write gave a Date Taken (tagpup.core.dates.taken_sort_key); the page holds no rule of its own (#3304). Stable: records
 * of one key keep their places, as the server's sort keeps them.
 */
export function inFolderOrder(records) {
    return [...records].sort((a, b) => (a.order < b.order ? -1 : a.order > b.order ? 1 : 0));
}

/** This library's kept scans, [key, folder] (the folder as its key spells it, pathKey). */
function keptScans() {
    const prefix = folderCacheKey('');
    const found = [];
    for (let at = 0; at < localStorage.length; at++) {
        const key = localStorage.key(at);
        if (key && key.startsWith(prefix)) found.push([key, key.slice(prefix.length)]);
    }
    return found;
}

/**
 * A kept scan that could not be written back (local storage full) is forgotten, never left holding the records from
 * before a write: their stamps would have a save refused as changed on disk, and their dates would be shown. Opened again,
 * the folder is scanned.
 */
function forgetKept(key, why) {
    console.warn('A folder kept in this browser could not be written back, and is forgotten:', why);
    localStorage.removeItem(key);
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

/**
 * Where a folder's scan is cached: one entry per library and folder, however the folder was typed. The library is the
 * page's (its URL): choosing another keeps the folder (?path, web/common/library.js goToLibrary), and a key of the folder
 * alone showed the first library's records and suggestions there as "(cached)" (docs/findings.md, #1359). A library's name
 * holds no "/" (it is a segment of the URL), so `folderCacheKey('')` begins every key of this library's scans and no other's.
 */
export function folderCacheKey(folder) {
    return `tagpup_cache_${libraryIn(window.location.pathname)}/${pathKey(folder)}`;
}
