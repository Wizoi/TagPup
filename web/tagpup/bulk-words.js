// TagPup's page: what a bulk edit of a library view asks before it is written, and the words it names itself in, as plain functions
// of numbers and names. How it goes and how it ends is the shared strip's (web/common/batch-strip.js).
import { howLong, photosOf } from './common/batch-strip.js';
import { kindsSaid } from './folder.js';

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

/**
 * `count` of what a selection holds, by kind (#2645): `kinds` is the selection's `{ photos, videos }` (selected.js selectionKinds),
 * or null. A count that is the selection's is said as kindsSaid says one ("2 photos, 1 video"); a count the server answered
 * otherwise (photos that came into the view since, a delete that failed for some) by the kinds the selection held.
 */
export function itemsOf(count, kinds) {
    if (!kinds || !kinds.videos) return photosOf(count);
    if (kinds.photos + kinds.videos === count) return kindsSaid(kinds);
    const n = count.toLocaleString();
    if (!kinds.photos) return `${n} ${count === 1 ? 'video' : 'videos'}`;
    return `${n} ${count === 1 ? 'photo or video' : 'photos and videos'}`;
}

/**
 * What the edit is, in words: `{ op, verb, past, what, prep, kinds }` for Add or Remove of tags or people; `kinds` the selection's
 * by kind, which its counts are said in (itemsOf).
 */
export function describeTags({ op, add = [], remove = [], kinds = null }) {
    const names = (add.length ? add : remove).join(', ');
    if (add.length && remove.length) {
        return { op, verb: 'Change', past: 'Changed', what: `${add.join(', ')} and ${remove.join(', ')}`, prep: 'on', kinds };
    }
    if (add.length) {
        return { op, verb: 'Add', past: 'Added', what: names, prep: 'to', kinds };
    }
    return { op, verb: 'Remove', past: 'Removed', what: names, prep: 'from', kinds };
}

/** "Delete 3,412 photos", "Added Trips/Coast to 1 photo, 2 videos": the words of `desc` for `count`, `form` 'verb' or 'past'. */
function phrase(desc, form, count, after = '') {
    return [desc[form], desc.what, desc.prep, itemsOf(count, desc.kinds)].filter(Boolean).join(' ') + after;
}

/**
 * What a Delete of the selection is, in words (#674): `where` is the server's answer of where the files would go
 * (/api/library/selection/delete-check: `through_this_pc`, the photos in places with no Recycle Bin), `count` the photos. How many
 * went for good is the reply's (doneWords), never the question's: a file the warning named may have failed to go.
 */
export function describeDelete(where, count, kinds = null) {
    const through = Math.min((where && where.through_this_pc) || 0, count);
    const binned = !through ? ' to the Recycle Bin'
        : through >= count ? " through this PC's Recycle Bin" : ", those from places with no Recycle Bin through this PC's";
    return { op: 'delete', verb: 'Delete', past: 'Deleted', what: '', prep: '', binned, throughThisPc: through, kinds };
}

/** `2,345 MB`: bytes as the question says them, rounded up. */
function megabytes(bytes) {
    return `${Math.max(1, Math.ceil((Number(bytes) || 0) / (1024 * 1024))).toLocaleString()} MB`;
}

/**
 * Why a file is deleted for good rather than moved to a Recycle Bin, as the first question says it (the second, forGoodQuestion,
 * names the files). A clip over MOST_CLIP_BYTES (tagpup/files/recycle_bin.py, 2 GiB) is deleted for good on ANY drive: a copy of it
 * through this PC is gigabytes over the share (DECISIONS 2026-11-02 item 7). `bigClip` is the page's test of that size.
 */
export const MOST_CLIP_BYTES = 2 * 1024 * 1024 * 1024;
const GOOD_WHY = "a video over 2 GiB is too large for TagPup to copy to a Recycle Bin, and a file larger than its drive's Recycle Bin "
    + 'keeps, or on a Recycle Bin set to delete files at once, is not kept by it';
// Why, when this PC's Recycle Bin -- which keeps the copies of files from places with no Recycle Bin of their own -- is full
// (the delete-check's `bin_full`; owner, 2026-11-12).
const BIN_FULL_WHY = "a Recycle Bin that would keep it is full, or full by the time it comes to it: the one of its drive, or, for a "
    + "file from a place with no Recycle Bin of its own (a network share), this PC's Recycle Bin, which keeps its copy";
export function bigClip(size) {
    return Number(size) > MOST_CLIP_BYTES;
}

/**
 * The question before a Delete: how many, where the files go -- the Recycle Bin, or, for photos in places with none (a network
 * share, a removable drive), through this PC (#694): copied to it, the copies to its Recycle Bin, then the originals deleted, and a
 * copy restored goes to the folder under Downloads, not back to the share -- how long, and that the rows go too. `selected`, a
 * folder's (Organize names its photos by path): how many were selected, of which the server's `missing` have no row in the
 * library (copied in, not indexed yet) and are left where they are, said after the count.
 */
export function deleteQuestion(where, count, kinds = null, selected = null) {
    const missing = selected === null ? 0 : Math.min((where && where.missing) || 0, selected);
    const head = `Delete ${itemsOf(count, kinds)}?` + (!missing ? '' : ` ${missing.toLocaleString()} of the ${selected.toLocaleString()} `
        + `selected ${missing === 1 ? 'is' : 'are'} not in the library (not indexed yet) and ${missing === 1 ? 'is left where it is' : 'are left where they are'}.`);
    const rows = count === 1 ? ' Its row leaves the library, with its faces.' : ' Their rows leave the library, with their faces.';
    const through = Math.min((where && where.through_this_pc) || 0, count);
    const seconds = count / DELETE_PER_SECOND + (through ? ((where && where.copy_bytes) || 0) / COPY_BYTES_PER_SECOND : 0);
    const takes = through ? `at least ${howLong(Math.max(seconds, 60))} (copies from a share take as long as the share does)` : howLong(seconds);
    const job = ` It takes ${takes}; TagPup is busy until it is done, and saving a photo elsewhere waits for the photos being deleted `
        + 'at that moment. It cannot be undone in TagPup.';
    // Files deleted for good (the delete-check's `for_good`, as many as the server counts): not copied, not sent to any Recycle Bin,
    // and said so here, before the second question names them (a start that did not confirm that deletes none of them).
    const good = Math.min((where && where.for_good && where.for_good.count) || 0, count - through);
    // Those there only because this PC's Recycle Bin is full (`bin_full`) are said so.
    const full = Math.min((where && where.bin_full) || 0, good);
    const goodWhy = !full ? GOOD_WHY : full >= good ? BIN_FULL_WHY : `${GOOD_WHY}; or ${BIN_FULL_WHY}`;
    const goodWords = !good ? '' : good >= count
        ? ` ${count === 1 ? 'It is' : 'They are'} deleted for good, not moved to a Recycle Bin and not copied to this PC (${goodWhy}); `
            + 'you are asked next, by name.'
        : ` ${good.toLocaleString()} of them ${good === 1 ? 'is' : 'are'} deleted for good, not moved to a Recycle Bin and not copied to `
            + `this PC (${goodWhy}); you are asked next, by name.`;
    if (!through) {
        if (good >= count) return `${head}${goodWords}${rows}${job}`;
        const bin = good
            ? ` The other ${(count - good).toLocaleString()} ${count - good === 1 ? 'goes' : 'go'} to the Recycle Bin, where ${count - good === 1 ? 'it' : 'they'} can be restored.`
            : (count === 1 ? ' The file goes to the Recycle Bin, where it can be restored.' : ' The files go to the Recycle Bin, where they can be restored.');
        return `${head}${goodWords}${bin}${rows}${job}`;
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
    const rest = count - through - good;
    const others = rest > 0 ? ` The other ${rest.toLocaleString()} ${rest === 1 ? 'goes' : 'go'} to the Recycle Bin.` : '';
    return `${head} ${through.toLocaleString()} of them ${one ? 'is' : 'are'} where there is no Recycle Bin (${why}): ${copied}${goodWords}${others}${rows}${job}`;
}

/**
 * The warning before files are deleted for good (DECISIONS 2026-11-08; a video over 2 GiB, 2026-11-02 item 7, whose copy through
 * this PC would be gigabytes over the share): `names` the file names the server gave (the delete-check's
 * `for_good.names`, as many as it names: selection.MOST_FOR_GOOD_NAMED; or the one file of the panel's delete), `count` how many
 * in all, the rest counted. The owner's OK is the only way the server deletes one for good; Cancel deletes nothing. `binFull`:
 * how many of them only because this PC's Recycle Bin, which keeps the copies of files from a share, is full (the reply's
 * `bin_full`): the question then asks in those words (owner, 2026-11-12).
 */
export function forGoodQuestion(names, count, binFull = 0) {
    const shown = names || [];
    const total = Math.max(count || 0, shown.length);
    const more = total - shown.length;
    const one = total === 1;
    const list = shown.map(name => `  ${name}`).join('\n') + (more > 0 ? `\n  and ${more.toLocaleString()} more` : '');
    const full = Math.min(binFull || 0, total);
    if (full) {
        const which = full >= total ? (one ? 'This file is' : 'These files are')
            : `${full.toLocaleString()} of these ${total.toLocaleString()} files ${full === 1 ? 'is' : 'are'}`;
        const others = full >= total ? '' : ` The other ${(total - full).toLocaleString()} would not be kept by any Recycle Bin `
            + '(larger than its drive\'s Recycle Bin keeps, a Recycle Bin set to delete files at once, or a video too large for '
            + 'TagPup to copy to one).';
        return 'The Recycle Bin is full: delete permanently instead?\n\n'
            + `${which} headed for a Recycle Bin that is full: the one of the file's own drive, or, for a file in a place with no `
            + "Recycle Bin of its own (a network share), this PC's Recycle Bin, which keeps its copy. "
            + `Deleted permanently, ${full === 1 ? 'it' : 'they'} cannot be restored.${others}`
            + `\n\n${list}\n\nOK deletes ${one ? 'it' : 'them'} permanently; Cancel deletes nothing.`;
    }
    return `${one ? 'This file' : 'These files'} would be deleted for good, not moved to the Recycle Bin: no Recycle Bin would `
        + `keep ${one ? 'it' : 'them'} (larger than ${one ? 'its' : 'their'} drive's Recycle Bin keeps, a Recycle Bin set to delete `
        + 'files at once, or a video too large for TagPup to copy to one).'
        + `\n\n${list}\n\nOK deletes ${one ? 'it' : 'them'} for good; Cancel deletes nothing.`;
}

/** The question before a bulk edit: it names the write and the count, what it touches, how long, and how to undo it. */
export function confirmSentence(desc, count) {
    const head = `${desc.verb} ${desc.what} ${desc.prep} ${itemsOf(count, desc.kinds)}?`;
    return `${head} This changes the photo files and takes ${howLong(count / ASSUMED_PER_SECOND)}. `
        + 'History lists it as one change and can undo it.';
}

/**
 * What an edit that is done says first: "Added Trips/Coast to 3 photos", "Deleted 2 photos to the Recycle Bin". `count` and, of a
 * Delete, `forGood` are the server's reply (`changed`, `for_good`: the files really removed for good), what was done, not asked.
 */
export function doneWords(desc, count, forGood = 0) {
    if (desc.op !== 'delete') return phrase(desc, 'past', count);
    const gone = Math.min(forGood || 0, count);
    // A file deleted for good is never said to have gone to a Recycle Bin.
    const after = !gone ? desc.binned : gone >= count ? ' for good' : `${desc.binned}, ${gone.toLocaleString()} of them for good`;
    return phrase(desc, 'past', count, after);
}
