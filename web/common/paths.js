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
