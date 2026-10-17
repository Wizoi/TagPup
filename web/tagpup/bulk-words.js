// TagPup's page: what a bulk edit of a library view asks before it is written, and the words it names itself in, as plain functions
// of numbers and names. How it goes and how it ends is the shared strip's (web/common/batch-strip.js).
import { howLong, photosOf } from './common/batch-strip.js';

/** Photos a second the page assumes of a bulk edit: 9d-1 measured 15 to 23, and a write now reads each file to its end (about 53 ms a photo). */
export const ASSUMED_PER_SECOND = 15;

/**
 * Photos a second a Delete deletes, to the Recycle Bin: measured 2026-10-04 on a sandbox copy of photo_index with throwaway files,
 * 15.7 and 16.0 a second (200 and 400 photos, 63.8 and 62.6 ms a photo, each the one-photo delete's own reads and writes) (#693).
 */
export const DELETE_PER_SECOND = 15;

/**
 * Bytes a second of what a photo deleted THROUGH THIS PC adds (#694, #705): the copy, a hash of the copy and of the original, the
 * rename. Measured 2026-10-04 on this PC's own disk, 100 throwaway files of 3.4 MB (photo_index's average): 204 and 210 MB a second.
 * From a share it is slower -- the original is read over the network twice and deleted there -- which was not measurable here, so
 * the question says "at least".
 */
export const COPY_BYTES_PER_SECOND = 200 * 1024 * 1024;

/** What the edit is, in words: `{ op, verb, past, what, prep }` for Add or Remove of tags or people. */
export function describeTags({ op, add = [], remove = [] }) {
    const names = (add.length ? add : remove).join(', ');
    if (add.length && remove.length) {
        return { op, verb: 'Change', past: 'Changed', what: `${add.join(', ')} and ${remove.join(', ')}`, prep: 'on' };
    }
    if (add.length) {
        return { op, verb: 'Add', past: 'Added', what: names, prep: 'to' };
    }
    return { op, verb: 'Remove', past: 'Removed', what: names, prep: 'from' };
}

/** "Delete 3,412 photos", "Added Trips/Coast to 1 photo": the words of `desc` for `count` photos, `form` 'verb' or 'past'. */
function phrase(desc, form, count) {
    return [desc[form], desc.what, desc.prep, photosOf(count)].filter(Boolean).join(' ') + (desc.after || '');
}

/**
 * What a Delete of the selection is, in words (#674): `where` is the server's answer of where the files would go
 * (/api/library/selection/delete-check: `through_this_pc`, the photos in places with no Recycle Bin), `count` the photos.
 */
export function describeDelete(where, count) {
    const through = Math.min((where && where.through_this_pc) || 0, count);
    const after = !through ? ' to the Recycle Bin'
        : through >= count ? " through this PC's Recycle Bin" : ", those from places with no Recycle Bin through this PC's";
    return { op: 'delete', verb: 'Delete', past: 'Deleted', what: '', prep: '', after, throughThisPc: through };
}

/** `2,345 MB`: bytes as the question says them, rounded up. */
function megabytes(bytes) {
    return `${Math.max(1, Math.ceil((Number(bytes) || 0) / (1024 * 1024))).toLocaleString()} MB`;
}

/**
 * The question before a Delete: how many, where the files go -- the Recycle Bin, or, for photos in places with none (a network
 * share, a removable drive), through this PC (#694): copied to it, the copies to its Recycle Bin, then the originals deleted, and a
 * copy restored goes to the folder under Downloads, not back to the share -- how long, and that the rows go too.
 */
export function deleteQuestion(where, count) {
    const head = `Delete ${photosOf(count)}?`;
    const rows = count === 1 ? ' Its row leaves the library, with its faces.' : ' Their rows leave the library, with their faces.';
    const through = Math.min((where && where.through_this_pc) || 0, count);
    const seconds = count / DELETE_PER_SECOND + (through ? ((where && where.copy_bytes) || 0) / COPY_BYTES_PER_SECOND : 0);
    const takes = through ? `at least ${howLong(Math.max(seconds, 60))} (copies from a share take as long as the share does)` : howLong(seconds);
    const job = ` It takes ${takes}; TagPup is busy until it is done, and saving a photo elsewhere waits for the photos being deleted `
        + 'at that moment. It cannot be undone in TagPup.';
    if (!through) {
        const bin = count === 1 ? 'The file goes to the Recycle Bin, where it can be restored.' : 'The files go to the Recycle Bin, where they can be restored.';
        return `${head} ${bin}${rows}${job}`;
    }
    const why = ((where && where.reasons) || []).map(each => `${Number(each.photos).toLocaleString()} ${each.reason}`).join(', ');
    const one = through === 1;
    const long = Math.min(where.too_long || 0, through);
    const left = long ? ` ${long.toLocaleString()} of them ${long === 1 ? 'has' : 'have'} a path too long to copy there (260 characters `
        + `or more) and ${long === 1 ? 'is' : 'are'} left where ${long === 1 ? 'it is' : 'they are'}, each an error.` : '';
    const copied = `${one ? 'it is' : 'they are'} copied to this PC (${megabytes(where.copy_bytes)}), ${one ? 'the copy goes' : 'the copies go'} `
        + `to this PC's Recycle Bin, and then ${one ? 'the original is' : 'the originals are'} deleted. Restored from the Recycle Bin, `
        + `a copy goes to ${where.restores_to || 'your Downloads folder'}, not back to where it was.${left}`;
    if (through >= count) {
        return `${head} ${one ? 'It is' : 'They are'} where there is no Recycle Bin (${why}): ${copied}${rows}${job}`;
    }
    const rest = count - through;
    return `${head} ${through.toLocaleString()} of them ${one ? 'is' : 'are'} where there is no Recycle Bin (${why}): ${copied} `
        + `The other ${rest.toLocaleString()} ${rest === 1 ? 'goes' : 'go'} to the Recycle Bin.${rows}${job}`;
}

/** The question before a bulk edit: it names the write and the count, what it touches, how long, and how to undo it. */
export function confirmSentence(desc, count) {
    const head = `${desc.verb} ${desc.what} ${desc.prep} ${photosOf(count)}?`;
    return `${head} This changes the photo files and takes ${howLong(count / ASSUMED_PER_SECOND)}. `
        + 'History lists it as one change and can undo it.';
}

/** What an edit that is done says first: "Added Trips/Coast to 3 photos", "Deleted 2 photos to the Recycle Bin". */
export function doneWords(desc, count) {
    return phrase(desc, 'past', count);
}
