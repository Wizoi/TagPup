// TagPup's page: the tag changes waiting to be written to photo files, per file, and the drain that writes them
// (docs/plans/one-queue-for-tag-changes.md).
//
// A click that adds or takes off tags on many photos puts each file's change here, keyed by the file: the last op on a
// tag wins, so a removal then an add nets to the add, an add then a removal to the removal. The drain sends CHUNK files a
// request, each request one journaled change carrying its owner action's group token (History shows the action as one
// line, Undo takes it back whole), in the photo write queue (edits.js queuePhotoWrite) behind any single save queued
// meanwhile. A tag clicked while a 5,000-photo change drains joins the files not yet sent and rides their chunk; a file
// already written gets it in a later chunk. A chunk that fails gives its files' ops back and stops the drain until Retry
// or Cancel: it never loops by itself. The map is this page's memory only (closing the page loses what is not sent).
import { api } from './common/api.js';
import { pathKey, samePath } from './common/paths.js';
import { leafOf } from './common/vocabulary.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import { setStatus } from './status.js';
import { changeCachedPhotos, forgetCachedFolder, saveToLocalStorageCache } from './cache.js';
import { leavePhotoThen, queuePhotoWrite, redrawIfShowing, takeWritten } from './edits.js';
import { renderWriteQueue, whereWritten } from './write-queue.js';
import { updatePeopleDatalist, updateTagsDatalist } from './tags.js';

/** Files a request: the server's group of one write (batches.CHUNK, the route's limit with a `group`). */
const CHUNK = 25;

const photosWord = (n) => `${n.toLocaleString()} ${n === 1 ? 'photo' : 'photos'}`;

/** Wait to write `add` and `remove` to each of `paths`; resolves {written, failed, skipped} once every file is settled. */
export function pendTags(paths, { add = [], remove = [], people = false } = {}) {
    return pendChanges(paths.map(path => ({ path, add, remove })), { people });
}

/**
 * Wait to write each `{path, add, remove}` of `list`: one owner action, one group token. `people`: the tags name people,
 * whose leaves the records' `people` follow. Resolves {written, failed, skipped}, counted in files.
 */
export function pendChanges(list, { people = false } = {}) {
    const pending = state.pending;
    let resolve;
    const settled = new Promise(r => { resolve = r; });
    const action = { left: new Set(), written: 0, failed: 0, skipped: 0, resolve };
    const token = crypto.randomUUID();
    for (const { path, add = [], remove = [] } of list) {
        const key = pathKey(path);
        let entry = pending.files.get(key);
        // A file already waiting keeps the token of the action that put it here first (the plan's cut 3 rule), and the
        // folder it was clicked in, whose records this browser keeps (cache.js) if the folder is left before it is written.
        if (!entry) {
            entry = { path, add: new Set(), remove: new Set(), token, actions: new Set(), folder: state.scannedFolder || null };
            pending.files.set(key, entry);
        }
        remove.forEach(tag => { entry.add.delete(tag); entry.remove.add(tag); });
        add.forEach(tag => { entry.remove.delete(tag); entry.add.add(tag); });
        if (people) [...add, ...remove].forEach(tag => pending.people.add(tag));
        entry.actions.add(action);
        action.left.add(key);
    }
    if (!action.left.size) resolve({ written: 0, failed: 0, skipped: 0 });
    if (pending.stopped) setStatus('error', 'Tag changes wait for the failed write: Retry or Cancel it (bottom right).', { transient: false });
    drain();
    return settled;
}

/** The files whose changes are not sent yet. */
function unsentCount() {
    return state.pending.files.size;
}

/** Queue the next chunk, unless one is queued, the drain is stopped by a failure, or nothing waits. */
function drain() {
    const pending = state.pending;
    if (pending.queued || pending.stopped || !pending.files.size) return;
    pending.queued = true;
    queuePhotoWrite(sendChunk, 'Write tag changes', () => resume());
}

/** Retry: the drain goes on from the files a failed chunk gave back. */
function resume() {
    state.pending.stopped = false;
    drain();
}

/**
 * Drop every change not sent yet; a chunk out finishes as a normal write, but neither gives back nor says Ready. A failed
 * chunk's row loses its Retry and Cancel: the drain they named is gone, and a Cancel there would drop later changes.
 */
function dropPending() {
    const pending = state.pending;
    const dropped = [...pending.files.values()];
    pending.files.clear();
    pending.stopped = false;
    pending.drops += 1;
    state.writeQueue.entries.forEach(row => { if (row.cancel === cancelPending) { row.retry = null; row.cancel = null; } });
    renderWriteQueue();
    dropped.forEach(entry => settle(entry, 'failed'));
    return dropped.length;
}

/** Cancel: drop the changes not sent, and say how many photos it leaves unwritten. */
function cancelPending() {
    const n = dropPending();
    if (n) setStatus('error', `${n.toLocaleString()} ${n === 1 ? "photo's" : "photos'"} tag changes were not written.`, { transient: false });
}

/**
 * Leaving with changes waiting, the one place that asks it: another library (`to` null) or another folder opened (`to`
 * its path; from a library view too). Every question comes before anything is dropped or discarded: the tag changes not
 * sent for a folder other than `to`, then the open photo's edits (edits.js leavePhotoThen: Save, Discard or Cancel). Only
 * when both are yes are the changes dropped and `go` run; else `stay`. A chunk already out is a normal write and does not
 * ask (its reply reaches the records kept of a folder left, takeReply).
 */
export function leaveThen(go, stay = () => {}, to = null) {
    const n = [...state.pending.files.values()].filter(entry => !(to && entry.folder && samePath(entry.folder, to))).length;
    if (n && !confirm(`Tag changes for ${photosWord(n)} are not written yet. Leave without writing them?`)) return stay();
    return leavePhotoThen(() => {
        if (n) dropPending();
        go();
    }, { onStay: stay });
}

/** Closing the page asks while changes are not sent (a drain stopped by a failure has no write waiting). */
export function wirePending() {
    window.addEventListener('beforeunload', (e) => {
        if (!unsentCount()) return;
        e.preventDefault();
        e.returnValue = '';
    });
    // A page left with a chunk out abandons its fetch, written or not: the folders it came from forget the records kept of
    // them (their stamps may be older than the files), so opened again they are scanned rather than refused a save.
    window.addEventListener('pagehide', () => {
        new Set((state.pending.out || []).map(item => item.folder).filter(Boolean)).forEach(forgetCachedFolder);
    });
}

function settle(entry, how) {
    const key = pathKey(entry.path);
    for (const action of entry.actions) {
        if (!action.left.delete(key)) continue;
        action[how] += 1;
        if (!action.left.size) action.resolve({ written: action.written, failed: action.failed, skipped: action.skipped });
    }
}

/**
 * The next chunk, taken when its turn comes (not when it was queued): files in the order they came, those carrying the
 * first one's token, at most CHUNK. Every op is sent, whatever the page's record holds: the record may be older than the
 * file, and the server skips a file that already holds it all.
 */
function takeChunk() {
    const files = state.pending.files;
    const chunk = [];
    for (const [key, entry] of files) {
        if (chunk.length >= CHUNK) break;
        if (chunk.length && entry.token !== chunk[0].token) continue;
        files.delete(key);
        chunk.push(entry);
    }
    return chunk;
}

/** What a chunk's entry in the write queue says: "Add Trips/Coast to 25 photos, 4,975 more pending". */
function chunkLabel(chunk) {
    const adds = new Set(chunk.flatMap(e => [...e.add]));
    const removes = new Set(chunk.flatMap(e => [...e.remove]));
    const named = (tags) => (tags.size === 1 ? [...tags][0] : `${tags.size} tags`);
    const what = !removes.size ? `Add ${named(adds)} to` : !adds.size ? `Remove ${named(removes)} from` : 'Change tags on';
    const more = unsentCount();
    return `${what} ${photosWord(chunk.length)}${more ? `, ${more.toLocaleString()} more pending` : ''}`;
}

/** The queue's job: send the next chunk and take its reply; the drain goes on behind whatever was queued meanwhile. */
async function sendChunk(entry) {
    try {
        const drops = state.pending.drops;
        const chunk = takeChunk();
        state.pending.out = chunk;
        if (!chunk.length) {
            entry.label = 'Tag changes: nothing left to write';
            return true;
        }
        entry.label = chunkLabel(chunk);
        entry.cancel = cancelPending;
        renderWriteQueue();
        setStatus('busy', 'Writing tags...');
        const changes = Object.fromEntries(chunk.map(e => [e.path, { add: [...e.add], remove: [...e.remove] }]));
        let data;
        try {
            data = await api.json('/api/photos/bulk-tags', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ changes, group: chunk[0].token }),
            });
        } catch (err) {
            data = { success: false, error: err.message };
        }
        return takeReply(chunk, data, entry, drops !== state.pending.drops);
    } finally {
        state.pending.out = null;
        state.pending.queued = false;
        drain();
    }
}

/**
 * The reply into the records: each file written takes the stamp and the tags its file holds now, in any record of it
 * the page shows and, its folder left since, in the records this browser kept of that folder (whatever the page shows
 * meanwhile: a library view may show one of its photos); a file gone was skipped and keeps its record. A failed chunk's
 * unwritten files go back to the front of the map and the drain stops, unless the changes were dropped while it was out
 * (`dropped`): then they are not written, and Cancel's message stands.
 */
function takeReply(chunk, data, entry, dropped) {
    const byKey = (obj) => new Map(Object.entries(obj || {}).map(([path, v]) => [pathKey(path), v]));
    const stamps = byKey(data.stamps);
    const held = byKey(data.written);
    const skipped = new Set((data.skipped || []).map(s => pathKey(s.path)));
    const photos = new Map(state.folderPhotos.map(p => [pathKey(p.path), p]));
    const back = [];
    const written = [];
    const left = new Map();   // folder left since -> its items written
    const take = (photo, item) => {
        const key = pathKey(item.path);
        takeWritten(photo, stamps.get(key), held.get(key));
        applyOps(photo, item, held.get(key));
    };
    for (const item of chunk) {
        const key = pathKey(item.path);
        if (skipped.has(key)) {
            settle(item, 'skipped');
        } else if (data.success || held.has(key)) {
            const photo = photos.get(key);
            if (photo) {
                take(photo, item);
                written.push(photo);
            }
            if (item.folder && !(state.scannedFolder && samePath(item.folder, state.scannedFolder))) {
                left.set(item.folder, [...(left.get(item.folder) || []), item]);
            }
            settle(item, 'written');
        } else {
            back.push(item);
        }
    }
    for (const [folder, items] of left) {
        changeCachedPhotos(folder, (kept) => {
            const records = new Map(kept.map(p => [pathKey(p.path), p]));
            items.forEach(item => { const photo = records.get(pathKey(item.path)); if (photo) take(photo, item); });
            return kept;
        });
    }
    // The map first: a throw in drawing below must not lose the unwritten files' changes nor leave the drain running.
    if (!data.success && dropped) {
        back.forEach(item => settle(item, 'failed'));
    } else if (!data.success) {
        giveBack(back);
        state.pending.stopped = true;
    }
    if (written.length) {
        upper.updateSelectedThumbnailsCount();
        upper.renderFileList();
        upper.renderThumbnails();
        updateTagsDatalist();
        if (chunk.some(e => [...e.add, ...e.remove].some(t => state.pending.people.has(t)))) updatePeopleDatalist();
        saveToLocalStorageCache();
        written.forEach(photo => redrawIfShowing(photo, data));
    }
    if (!data.success) {
        entry.error = data.error || 'not written';
        if (dropped) {
            // Dropped while out: its row lost Retry and Cancel (dropPending) and stays failed until dismissed.
            setStatus('error', `Tags not written to ${back.length.toLocaleString()} more ${back.length === 1 ? 'photo' : 'photos'}: ${entry.error}`, { transient: false });
            return false;
        }
        setStatus('error', `Tags not written to ${photosWord(back.length)}`, { transient: false });
        alert(`Tags not written to ${photosWord(back.length)}: ${entry.error}. Retry or Cancel it in the list of writes (bottom right).`);
        return false;
    }
    if (dropped) return true;
    // Photos missing on disk are said once, when each owner action they belong to is done (counted over its chunks).
    const finished = new Set(chunk.flatMap(item => [...item.actions].filter(action => !action.left.size)));
    const gone = [...finished].reduce((n, action) => n + action.skipped, 0);
    if (gone) {
        setStatus('ready', `${gone.toLocaleString()} ${gone === 1 ? 'photo is' : 'photos are'} missing on disk and ${gone === 1 ? 'was' : 'were'} left out.${whereWritten(data)}`);
    } else {
        setStatus('ready', data.file_only ? `Saved.${whereWritten(data)}` : 'Ready');
    }
    return true;
}

/** What the file holds now: the server's list when it names one, else the ops on the record; `people` follows by leaf. */
function applyOps(photo, item, heldTags) {
    if (Array.isArray(heldTags)) {
        photo.tags = heldTags.slice();
    } else {
        photo.tags = (photo.tags || []).filter(t => !item.remove.has(t));
        item.add.forEach(t => { if (!photo.tags.includes(t)) photo.tags.push(t); });
    }
    const people = state.pending.people;
    const gone = [...item.remove].filter(t => people.has(t)).map(t => leafOf(t).toLowerCase());
    if (gone.length && photo.people) photo.people = photo.people.filter(p => !gone.includes(leafOf(p).toLowerCase()));
    const added = [...item.add].filter(t => people.has(t)).map(leafOf);
    if (added.length) {
        photo.people = photo.people || [];
        added.forEach(leaf => { if (!photo.people.includes(leaf)) photo.people.push(leaf); });
    }
}

/** A failed chunk's files back at the front of the map; a newer op on the same file and tag wins, the actions join. */
function giveBack(items) {
    const files = state.pending.files;
    const front = new Map();
    for (const item of items) {
        const key = pathKey(item.path);
        const newer = files.get(key);
        if (newer) {
            newer.add.forEach(t => { item.remove.delete(t); item.add.add(t); });
            newer.remove.forEach(t => { item.add.delete(t); item.remove.add(t); });
            newer.actions.forEach(a => item.actions.add(a));
            files.delete(key);
        }
        front.set(key, item);
    }
    state.pending.files = new Map([...front, ...files]);
}
