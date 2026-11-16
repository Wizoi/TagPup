// Taking tags off the open photo: the click is answered at once, the write waits its turn (edits.js queueWriteOf).
import { samePath } from './common/paths.js';
import { state } from './state.js';
import { setStatus } from './status.js';
import { saveToLocalStorageCache } from './cache.js';
import { normalizeTag, updateTagsDatalist } from './tags.js';
import { baseOf, postPhotoMetadata, queueWriteOf, redrawIfShowing } from './edits.js';

/**
 * The tags of each photo taken off on this page and not written yet ({photo: Map(normalized tag: removals)}): renderTags
 * does not draw them, whatever a write queued before them answers in between (its reply sets the tags it was asked with).
 */
const goingOff = new WeakMap();

/** The tags of the photo taken off and not written yet, normalized (a Map of tag to removals), or none. */
export function goingOffOf(photo) {
    return photo ? goingOff.get(photo) : undefined;
}

function markGoingOff(photo, keys, by) {
    const marks = goingOff.get(photo) || new Map();
    for (const key of keys) {
        const left = (marks.get(key) || 0) + by;
        if (left > 0) marks.set(key, left);
        else marks.delete(key);
    }
    if (marks.size) goingOff.set(photo, marks);
    else goingOff.delete(photo);
}

/** A batch's tags drawn again by renderTags (written, or put back): once, whichever comes first. */
function unmarkBatch(photo, batch) {
    if (batch.marked) markGoingOff(photo, batch.keys, -1);
    batch.marked = false;
}

/** {photo: the removal of it queued and not started yet}: a pill clicked meanwhile joins it, one write for the lot. */
const removalWaiting = new WeakMap();

// The removals asked for and not answered yet, of every photo, and whether one failed: state.removals (state.js).

/**
 * Take tags off a photo, through the page's own save of its keywords (its queue, its stamp, its refusal of a file changed on
 * disk). `tagsToRemove` as the photo spells them. Resolves true when they are off, or were not there.
 *
 * The click is answered at once, as naming a face is (face-boxes.js nameFaceAs): the tags leave the page's copy and the pills
 * now, and the write waits its turn behind the photo's others (a write of a file on a share takes seconds); the pills clicked
 * while it waits are written with it. If it fails, the tags it took are put back, and only those.
 */
export function removePhotoTags(path, tagsToRemove) {
    const photo = path && state.folderPhotos.find(p => samePath(p.path, path));
    if (!photo) return Promise.resolve(false);
    const keys = tagsToRemove.map(normalizeTag);
    // What the file held is taken before the page's copy changes: the write names the file as it was read (baseOf).
    baseOf(photo);
    const taken = (photo.tags || []).filter(t => keys.includes(normalizeTag(t)));
    markGoingOff(photo, keys, 1);
    if (taken.length) {
        photo.tags = photo.tags.filter(t => !keys.includes(normalizeTag(t)));
        redrawIfShowing(photo);
    }
    const waiting = removalWaiting.get(photo);
    if (waiting) {
        waiting.keys.push(...keys);
        waiting.taken.push(...taken);
        return waiting.run;
    }
    const batch = { keys: keys.slice(), taken: taken.slice(), marked: true };
    removalWaiting.set(photo, batch);
    state.removals.open += 1;
    if (!state.removals.failed) setStatus('busy', 'Deleting tag...');
    batch.run = queueWriteOf(photo.path, (entry) => writeRemoval(photo, batch, entry)).then((done) => {
        state.removals.open -= 1;
        if (!state.removals.open) state.removals.failed = false;
        unmarkBatch(photo, batch);
        return done;
    });
    return batch.run;
}

/**
 * A removal's turn (or its Retry): the photo's tags as they are now, without the batch's, written -- whatever was added or
 * taken off since the clicks, so that neither undoes the other. Nothing is written when the batch took nothing off the
 * page's copy and the copy holds none of them (an earlier removal wrote it, or it was never on the photo).
 */
async function writeRemoval(photo, batch, entry) {
    if (removalWaiting.get(photo) === batch) removalWaiting.delete(photo);
    const gone = new Set(batch.keys);
    const isGone = (tag) => gone.has(normalizeTag(tag));
    const current = photo.tags || [];
    const updatedTags = current.filter(t => !isGone(t));
    if (!batch.taken.length && updatedTags.length === current.length) {
        unmarkBatch(photo, batch);
        if (state.removals.open <= 1 && !state.removals.failed) setStatus('ready', 'Ready');
        return true;
    }

    if (!state.removals.failed) setStatus('busy', 'Deleting tag...');
    let saved;
    try {
        saved = await postPhotoMetadata(photo, { tags: updatedTags });
    } catch (err) {
        console.error(err);
        unmarkBatch(photo, batch);
        const holds = photo.tags || [];
        const back = batch.taken.filter((t, at) => batch.taken.indexOf(t) === at
            && !holds.some(each => normalizeTag(each) === normalizeTag(t)));
        photo.tags = [...holds, ...back];
        redrawIfShowing(photo);
        if (entry) entry.error = err.message;
        state.removals.failed = true;
        setStatus('error', `Not taken off${back.length ? ' ' + back.join(', ') : ''}: ${err.message}`, { transient: false });
        alert("Error deleting tag: " + err.message);
        return false;
    }
    unmarkBatch(photo, batch);
    // The page's copy as it is now (a pill clicked while this was out is already off it), without what this wrote off.
    photo.tags = (photo.tags || []).filter(t => !isGone(t));
    // The faces follow the reply (facesFollowReply): a person taken off the photo is taken off its faces by the server (#908).
    redrawIfShowing(photo, saved);
    updateTagsDatalist();
    if (state.removals.open <= 1 && !state.removals.failed) setStatus('ready', 'Ready');
    saveToLocalStorageCache();
    return true;
}
