/**
 * When two spellings name the same path, in both pages.
 *
 * Every photo and folder path the server sends is already in one spelling -- the
 * native absolute path, exactly as the database holds it -- so server paths are
 * compared with `===` and never rewritten in the browser. These are for the paths
 * that are not the server's: what somebody typed into the folder box, the ?photo= in
 * a URL, what the native Browse dialog returned (forward slashes). The server's rule
 * is tagpup/core/paths.py's key(). tests/frontend/path-helpers.test.mjs fails on a
 * separator conversion anywhere else in the pages.
 */

/**
 * The one comparable form of a path. Separators are unified to backslashes,
 * trailing ones dropped, and case folded -- what Windows' os.path.normcase does,
 * and what the server's paths.key() does. Case-insensitive because the library
 * lives on a Windows filesystem, where D:\Run and d:\run are the same folder.
 */
export function pathKey(p) {
    if (!p) return '';
    return String(p).trim().replace(/\//g, '\\').replace(/\\+$/, '').toLowerCase();
}

/**
 * The last segment of a path: a photo's file name, or a folder's name. Either
 * separator, and a trailing one is ignored ("D:\\Run\\2019\\" is "2019"; a drive
 * root is its drive). A path with no last segment -- null, '', a bare "/" -- gives '',
 * and the caller says what to show instead. Both pages' own versions differed there
 * (#146).
 */
export function baseName(p) {
    if (!p) return '';
    return String(p).replace(/[\\/]+$/, '').split(/[\\/]/).pop() || '';
}

/**
 * The folder a path is in, spelled as the path spells it ("D:\\Run\\2019\\a.jpg" is "D:\\Run\\2019"), or '' for a path
 * with no folder in it. Either separator; a trailing one is ignored, as baseName ignores it.
 */
export function folderOf(p) {
    const text = String(p || '').replace(/[\\/]+$/, '');
    const at = Math.max(text.lastIndexOf('\\'), text.lastIndexOf('/'));
    return at < 0 ? '' : text.slice(0, at);
}

/**
 * Is `p` spelled as a network share (\\\\server\\share\\..., or //server/share/...)? Windows has no
 * Recycle Bin there: a delete goes through this PC's (#694). (A mapped drive letter is not told by its
 * spelling; the server says it in the folder's membership, `permanent_delete`.)
 */
export function isUnc(p) {
    return /^[\\/]{2}[^\\/]/.test(String(p || '').trim());
}

/** Whether two spellings name the same file or folder. */
export function samePath(a, b) {
    if (!a || !b) return false;
    return pathKey(a) === pathKey(b);
}

/**
 * Is `p` inside `folder`, at any depth? A folder is not under itself. By pathKey, as the server's paths.is_under: a
 * folder's scan is recursive, so it holds every photo under it (web/tagpup/cache.js photosWritten).
 */
export function isUnder(p, folder) {
    const at = pathKey(folder);
    return Boolean(at) && pathKey(p).startsWith(at + '\\');
}

/**
 * A rename map, {path before: path after} (a folder's Smart Rename's `updated_paths`, a save or a Convert that renamed its
 * file), as one look-up: where the photo that was at `path` is now, or `path` itself when the map does not move it. Every
 * move is read from the map before any is taken, so a chain (IMG_0001 -> Regatta - 1, Regatta - 1 -> Regatta - 2) and a
 * swap (Regatta - 1 <-> Regatta - 2) take each photo once to its own new name, whatever order the map lists them in
 * (#3329, #3336). Old paths are matched by pathKey: the server may spell one otherwise than the page did (#92). The one
 * place a rename map is read: what a page keeps by a photo's path moves through this.
 */
export function renamedBy(renames) {
    const moves = new Map(Object.entries(renames || {}).map(([from, to]) => [pathKey(from), to]));
    return (path) => (moves.has(pathKey(path)) ? moves.get(pathKey(path)) : path);
}

/**
 * `held`, kept by photo path (a folder's suggestions), under the paths `now` (renamedBy) gives: a new object. An entry
 * that stays is written first, so a photo that moved onto its name wins over a stale entry there (a deleted photo's).
 */
export function rekeyed(held, now) {
    const entries = Object.entries(held);
    const moved = ([path]) => now(path) !== path;
    return Object.fromEntries([
        ...entries.filter(entry => !moved(entry)),
        ...entries.filter(moved).map(([path, value]) => [now(path), value]),
    ]);
}
