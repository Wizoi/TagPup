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

/** Whether two spellings name the same file or folder. */
export function samePath(a, b) {
    if (!a || !b) return false;
    return pathKey(a) === pathKey(b);
}
