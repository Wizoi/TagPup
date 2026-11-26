// Both pages' write of one photo's title, tags and Date Taken (POST /api/photos/write, op `set`), and the stamp and base it
// names: the file as the page read it, which the server compares under its lock and refuses a write over when it
// changed (docs/plans/phone-viewer.md, moved from web/tagpup/edits.js); and both pages' one rule for that refusal, read
// again and sent once more (postOrSendAgain). What a page shows of a refusal is its own.
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
 * ExifTool could not read when it was opened is never saved (postPhotoMetadata refuses it, UNREADABLE_SAVE), so it is
 * never asked for one.
 */
export function baseOf(photo) {
    if (!photo) return undefined;
    if (!photo.base) photo.base = { tags: (photo.tags || []).slice(), title: photo.title || '' };
    return photo.base;
}

// The one sentence of a save refused because the photo could not be read when it was opened: the page's own, said before
// anything is sent (#3298).
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

/** Photo `id` as the library holds it now: its record, or null when the library no longer has it (404). A read that fails rejects. */
export async function readById(id) {
    const res = await api.fetch(`/api/library/photo?id=${id}`);
    if (res.status === 404) return null;
    const body = await api.answered(res, 'it could not be read again.');
    return body.photo;
}

/**
 * Both pages' one rule for a write that names its photos by id (POST /api/photos/write), refused whole because one of them
 * is not at the path it names -- renamed, moved or deleted since the page read it (409 `changed_on_disk`), nothing
 * written: each photo named by id is read again by it, once, and the write is sent again naming each where it is now; one
 * the library no longer has is gone, as a file gone in Explorer is. Refused again after that read, it is the write's
 * failure (a Retry of it reads them again: the caller clears `readAgain`). `items` are the write's photos, {id: photo id
 * or null}. Resolves null when `reply` is not that refusal or they were read again once already (an item's `readAgain`);
 * else, in their order, each one's record as the library holds it now, null for one it no longer has, undefined for one
 * named by its path alone, each item marked `readAgain`. A read that fails rejects. Where and when the write goes again
 * (at once, or given back to the page's queue) is the caller's.
 */
export async function readMoved(reply, items) {
    if (!(reply && reply.changed_on_disk) || items.some(item => item.readAgain)) return null;
    const found = await Promise.all(items.map(item => (item.id === null || item.id === undefined ? undefined : readById(item.id))));
    items.forEach(item => { item.readAgain = true; });
    return found;
}

/**
 * POST a photo's title and tags -- as they are now, unless given -- to the one entry point (op `set`, the photo named by
 * its path and, when the library holds it, its id) and check the reply. A refusal rejects with the server's sentence; one
 * because the file changed since it was read, or the id's photo is no longer at this path (409), is marked
 * `changedOnDisk` and handed to `changed(photo)` first, the page's own way of reading it again. Nothing is retried or
 * merged. Resolves the reply with the photo's own part of it (`written`, its stamp, base, `new_path`, `taken`,
 * `rename_failed`) at the top, beside what the reply says of the whole write (`file_only`, `with_rows`, the faces it
 * changed, `pictures_refused`). `request` is of the request, never its body: the phone's Save goes with keepalive, so
 * the page going does not cancel it.
 */
export async function postPhotoMetadata(photo, { title = photo.title, tags = photo.tags || [], ...extra } = {}, changed = () => {},
    request = {}) {
    if (photo.unreadable) throw new Error(UNREADABLE_SAVE);
    const path = photo.path;
    const own = { id: Number.isInteger(photo.id) ? photo.id : null, title, tags, stamp: stampOf(photo), base: baseOf(photo), ...extra };
    const res = await api.fetch('/api/photos/write', {
        ...request,
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ op: 'set', changes: { [path]: own } })
    });
    const data = await res.json();
    if (!data.success) {
        if (data.changed_on_disk) changed(photo);
        const failure = new Error(data.error || (data.errors && data.errors[0] && data.errors[0].why) || 'Failed to save');
        failure.changedOnDisk = Boolean(data.changed_on_disk);
        throw failure;
    }
    const saved = { ...data, ...data.written[path] };
    takeStamp(photo, saved);
    return saved;
}

/**
 * postPhotoMetadata, and both pages' one rule for its refusal as changed on disk (DECISIONS 2026-11-26): the photo is read
 * again by its id (found by its path for a folder's photo, which the scan gives none) and the same save -- the title and
 * tags it sent, resolved before the first send -- is sent once more naming the file as it is now (its stamp) and, as its
 * base, what the page read before the first send. Whether it may be written is the server's to say, under its lock
 * (tagpup/services/tagging.py _differs_from_base): the file's tags (as a set) and caption as that base, the index's bake
 * having written its own fields only, it is written; others, someone else's edit, and it is refused again. Written,
 * `photo` takes the record read again (keeping its own id and path), with the write's stamp and base, so the page goes
 * on with the file as it is now, and the reply is marked `readAgain` for the page to say so.
 *
 * Not sent again, it is the refusal as before: a save that sends a Date Taken (its base holds no date, so the server could
 * not tell another program's date from the bake's), the library holding no photo at its path, its file gone (`missing`)
 * or one ExifTool could not read (`unreadable`), the read failing, or `stillListed(photo)` false (the page's own: does it
 * still hold the photo?). Then, or refused the second time, `changed(photo)` -- the page's way of reading it again to
 * show it -- and it rejects with the refusal (`changedOnDisk`); it is never sent a third time. Nothing is merged.
 * `request` is postPhotoMetadata's.
 */
export async function postOrSendAgain(photo, changes = {}, { changed = () => {}, stillListed = () => true, request = {} } = {}) {
    const asked = { title: photo.title, tags: (photo.tags || []).slice(), ...changes };
    try {
        return await postPhotoMetadata(photo, asked, () => {}, request);
    } catch (err) {
        if (!err.changedOnDisk) throw err;
        const before = { ...baseOf(photo) };
        const now = asked.date_taken ? null : await readAgain(photo).catch((why) => {
            console.error('Could not read the photo again:', why);
            return null;
        });
        if (!now || now.missing || now.unreadable || !stillListed(photo)) {
            changed(photo);
            throw err;
        }
        const record = { ...now, id: photo.id, path: photo.path, base: before };
        if (photo.id === undefined) delete record.id;    // a folder's photo: the page's record has none
        let reply;
        try {
            reply = await postPhotoMetadata(record, asked, () => {}, request);
        } catch (again) {
            if (again.changedOnDisk) changed(photo);
            throw again;
        }
        Object.assign(photo, record);
        return { ...reply, readAgain: true };
    }
}

/**
 * The photo as the library holds it now, read by its id -- found by its path for a folder's photo, which the scan gives
 * none -- or null when the library holds no photo there (a folder it does not hold). A read that fails rejects.
 */
async function readAgain(photo) {
    const id = Number.isInteger(photo.id) ? photo.id
        : (await api.json(`/api/library/find?path=${encodeURIComponent(photo.path)}`)).id;
    return Number.isInteger(id) ? readById(id) : null;
}
