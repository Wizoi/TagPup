// Both pages' write of one photo's title, tags and Date Taken (POST /api/photo/save-metadata), and the stamp and base it
// names: the file as the page read it, which the server compares under its lock and refuses a write over when it
// changed (docs/plans/phone-viewer.md, moved from web/tagpup/edits.js). What a page shows of a refusal is its own.
import { api } from './api.js';

/**
 * The stamp of the file a record was built from -- its modified time and size -- which a write names: the server
 * refuses a write whose file has changed since (409, `changed_on_disk`), because the whole tag list a save sends
 * may be missing what another program put in the file. A record with no stamp names none (findings #533).
 */
export function stampOf(photo) {
    return photo && photo.mtime > 0 && Number.isFinite(photo.size) ? { mtime: photo.mtime, size: photo.size } : undefined;
}

/**
 * What the page read of the tags and caption a save writes over, kept with the record: `base`, {tags, title}. A
 * save sends it, and the server compares it with the file under its lock and refuses on any difference -- a stamp
 * can be kept by a copy, or by a rename of a tag to one of the same length with the time put back (findings #551,
 * #552). Taken from the record the first time it is needed, and from the server's reply after each write. A photo
 * ExifTool could not read when it was opened has none: null, which the server refuses a save from.
 */
export function baseOf(photo) {
    if (!photo) return undefined;
    if (photo.unreadable) return null;
    if (!photo.base) photo.base = { tags: (photo.tags || []).slice(), title: photo.title || '' };
    return photo.base;
}

export const UNREADABLE_SAVE = 'This photo could not be read just now: reopen it.';

/**
 * Both pages' one write at a time: run `job` after every job put in turn before it on `holder[key]`, which holds the tail
 * of the line (null when nothing is out). Resolves what `job` resolves and rejects with its failure; the line goes on
 * either way, and `idle()` runs when the last job in it settles. What a failure means is the caller's: the phone says it
 * on the sheet, the PC's queue (edits.js queuePhotoWrite) marks its entry failed and resolves false.
 */
export function inTurn(holder, key, job, idle = () => {}) {
    const run = (holder[key] || Promise.resolve()).then(() => job());
    const tail = run.then(() => {}, () => {});
    holder[key] = tail;
    tail.then(() => {
        if (holder[key] !== tail) return;
        holder[key] = null;
        idle();
    });
    return run;
}

/** After a write: the record is of the file as it is now, and the next write names that. */
export function takeStamp(photo, stamp) {
    if (!photo || !stamp) return;
    if (Number.isFinite(stamp.mtime) && Number.isFinite(stamp.size)) {
        photo.mtime = stamp.mtime;
        photo.size = stamp.size;
    }
    if (stamp.base && Array.isArray(stamp.base.tags)) {
        photo.base = { tags: stamp.base.tags.slice(), title: stamp.base.title || '' };
    }
}

/** A bulk write (or its undo) left the file holding `tags`: the record's base says so, and its stamp is the file's. */
export function takeWritten(photo, stamp, tags) {
    takeStamp(photo, stamp);
    if (photo && Array.isArray(tags)) {
        photo.base = { tags: tags.slice(), title: photo.base ? photo.base.title : (photo.title || '') };
    }
}

/**
 * POST a photo's title and tags -- as they are now, unless given -- and check the reply. A refusal rejects with the
 * server's sentence; one because the file changed since it was read (409) is marked `changedOnDisk` and handed to
 * `changed(photo)` first, the page's own way of reading it again. Nothing is retried or merged. `request` is of the
 * request, never its body: the phone's Save goes with keepalive, so the page going does not cancel it.
 */
export async function postPhotoMetadata(photo, { title = photo.title, tags = photo.tags || [], ...extra } = {}, changed = () => {},
    request = {}) {
    if (photo.unreadable) throw new Error(UNREADABLE_SAVE);
    const res = await api.fetch('/api/photo/save-metadata', {
        ...request,
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: photo.path, title, tags, stamp: stampOf(photo), base: baseOf(photo), ...extra })
    });
    const data = await res.json();
    if (!data.success) {
        if (data.changed_on_disk) changed(photo);
        const failure = new Error(data.error || 'Failed to save');
        failure.changedOnDisk = Boolean(data.changed_on_disk);
        throw failure;
    }
    takeStamp(photo, data);
    return data;
}
