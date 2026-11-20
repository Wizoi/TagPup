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
//
// The open photo's own removals (its pills, a face's person: removePhotoTags) enter the map too, first in line (takeChunk), and
// are answered at the click, and a write of them that fails puts them back rather than waiting for Retry. Any other change
// waiting for a photo makes its panel read-only until it is written (pendingOf; stale.js offerWrites). The record follows
// the reply, not the map; what the page shows of a photo's tags, and decides by, is the record with the map's changes for
// it applied (shownTags), redrawn whenever the map changes for a photo of the page (pageFollows); a record read from the
// library while a write is out takes the replies that came back meanwhile (readHeld).
import { api } from './common/api.js';
import { pathKey, samePath } from './common/paths.js';
import { leafOf } from './common/vocabulary.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import { setStatus } from './status.js';
import { changeCachedPhotos, forgetCachedFolder, saveToLocalStorageCache } from './cache.js';
import { leavePhotoThen, queuePhotoWrite, redrawIfShowing } from './edits.js';
import { takeWritten } from './common/photo-write.js';
import { renderWriteQueue, whereWritten } from './write-queue.js';
import { namesAPerson, normalizeTag, updatePeopleDatalist, updateTagsDatalist } from './tags.js';
import { offerWrites } from './stale.js';

/** Files a request: the server's group of one write (batches.CHUNK, the route's limit with a `group`). */
const CHUNK = 25;

const photosWord = (n) => `${n.toLocaleString()} ${n === 1 ? 'photo' : 'photos'}`;

const STOPPED = 'Tag changes wait for the failed write: Retry or Cancel it (bottom right).';

/** Wait to write `add` and `remove` to each of `paths`; resolves {written, failed, skipped} once every file is settled. */
export function pendTags(paths, { add = [], remove = [], people = false } = {}) {
    return pendChanges(paths.map(path => ({ path, add, remove })), { people });
}

/**
 * Wait to write each `{path, add, remove}` of `list`: one owner action, one group token. `people`: the tags name people,
 * whose leaves the records' `people` follow. `panel`: the open photo's own removal (removePhotoTags), first in line.
 * Resolves {written, failed, skipped}, counted in files.
 */
export function pendChanges(list, { people = false, panel = false } = {}) {
    const pending = state.pending;
    let resolve;
    const settled = new Promise(r => { resolve = r; });
    const action = { left: new Set(), written: 0, failed: 0, skipped: 0, resolve, panel };
    // getRandomValues, not randomUUID: a page opened over plain http from another computer (`--listen lan`) is not a
    // secure context and has no randomUUID (#3097).
    const token = Array.from(crypto.getRandomValues(new Uint32Array(4)), n => n.toString(16).padStart(8, '0')).join('');
    for (const { path, add = [], remove = [] } of list) {
        const key = pathKey(path);
        let entry = pending.files.get(key);
        // A file already waiting keeps the token of the action that put it here first (the plan's cut 3 rule), and the
        // folder it was clicked in, whose records this browser keeps (cache.js) if the folder is left before it is written.
        if (!entry) {
            entry = { path, add: new Set(), remove: new Set(), panel: new Set(), token, actions: new Set(), folder: state.scannedFolder || null };
            pending.files.set(key, entry);
        }
        remove.forEach(tag => { entry.add.delete(tag); entry.remove.add(tag); });
        add.forEach(tag => { entry.remove.delete(tag); entry.add.add(tag); });
        // The panel's tags are those whose last click was the panel's; a click elsewhere on one makes it a change it does not draw.
        [...add, ...remove].forEach(tag => (panel ? entry.panel.add(tag) : entry.panel.delete(tag)));
        if (people) [...add, ...remove].forEach(tag => pending.people.add(tag));
        entry.actions.add(action);
        action.left.add(key);
    }
    if (!action.left.size) resolve({ written: 0, failed: 0, skipped: 0 });
    if (pending.stopped && !panel) say('error', STOPPED);
    pageFollows(new Set(list.map(({ path }) => pathKey(path))));
    drain();
    return settled;
}

/**
 * Take tags off a photo of the page: the click is answered at once (its pills are no longer drawn) and the write goes first
 * in line, with any pill clicked before its turn. `tags` as the photo spells them. Resolves true once its file is written,
 * false when the write fails (the tags are drawn again: the file still holds them) or is dropped.
 */
export function removePhotoTags(path, tags) {
    const photo = path && state.folderPhotos.find(p => samePath(p.path, path));
    if (!photo) return Promise.resolve(false);
    tags.filter(namesAPerson).forEach(tag => state.pending.people.add(tag));
    return pendChanges([{ path: photo.path, remove: tags }], { panel: true }).then(({ written }) => written > 0);
}

/** The entries of `photo`'s file: waiting in the map, and in the chunk out. */
function entriesOf(photo) {
    if (!photo) return [];
    const key = pathKey(photo.path);
    return [state.pending.files.get(key), ...(state.pending.out || []).filter(entry => pathKey(entry.path) === key)].filter(Boolean);
}

/** How many tag changes wait for `photo` that its panel does not draw: any but its own removals. Over 0, it is read-only. */
export function pendingOf(photo) {
    return entriesOf(photo).reduce((n, entry) => n + [...entry.add, ...entry.remove].filter(tag => !entry.panel.has(tag)).length, 0);
}

/**
 * The tags `photo` shows: its record (what its file held at the last reply) with the changes not written yet applied, the
 * chunk out's then the map's (the later op wins), compared as tags are (normalizeTag). The one answer for whatever draws a
 * photo's tags or decides by them: its pills, the list's count, the suggestions not yet on it, Ctrl+D's copy, its face
 * cards, a folder's selection chips. A change a failed chunk gave back is waiting like any other (its Retry writes it), and
 * is shown: the panel is read-only saying so, and Cancel draws the record again. A write builds on the record, which is
 * what the file holds.
 */
export function shownTags(photo) {
    let tags = ((photo && photo.tags) || []).slice();
    for (const entry of entriesOf(photo).reverse()) {
        const off = new Set([...entry.remove].map(normalizeTag));
        tags = tags.filter(tag => !off.has(normalizeTag(tag)));
        const have = new Set(tags.map(normalizeTag));
        entry.add.forEach(tag => { if (!have.has(normalizeTag(tag))) tags.push(tag); });
    }
    return tags;
}

/**
 * The records the page holds of photos, the one lookup of them: the folder's (a library view's: the open photo's), and a
 * library view's photo before the open one, read from the library for Ctrl+D (photo.js readPreviousOf). A reply reaches
 * each of them (takeReply), and the page follows a change for any of them (pageFollows).
 */
function heldRecords() {
    const previous = state.library && state.library.previous;
    return previous ? [...state.folderPhotos, previous.record] : state.folderPhotos;
}

/**
 * A record read from the library while a write may be out (photo.js: a library view's open photo, and the one before it for
 * Ctrl+D): the one place such a read is held against the writes' replies. The server may have read the file before a write
 * whose reply came back while the read was out, and that reply found no record of it to reach (heldRecords); so every reply
 * taken meanwhile is applied to the record the read answers, in the order taken. `read` gives a promise of the record or null.
 */
export function readHeld(read) {
    const replies = [];
    state.pending.reads.add(replies);
    return read().then(record => {
        if (record) replies.filter(reply => reply.key === pathKey(record.path)).forEach(reply => reply.take(record));
        return record;
    }).finally(() => state.pending.reads.delete(replies));
}

/**
 * The page follows a change of the map for any of `keys`: the list (its counts are of the tags shown), a folder's selection
 * (a view's is counted by the server, after the file is written: takeReply), Ctrl+D's offer, and the open photo's pills,
 * suggestions, face cards, and whether it is read-only.
 */
function pageFollows(keys) {
    if (!heldRecords().some(p => keys.has(pathKey(p.path)))) return;
    upper.renderFileList();
    if (!state.library && state.selectedThumbnails.length) upper.updateSelectedThumbnailsCount();
    upper.updateCarryForwardState();
    const path = state.activePhotoPath;
    if (!path || !keys.has(pathKey(path))) return;
    const photo = state.folderPhotos.find(p => samePath(p.path, path));
    if (!photo) return;
    upper.renderTags();
    upper.renderSuggestionsPanel(photo.path);
    upper.renderPhotoFacesHere(photo.path, { readStands: true });
    offerWrites(photo, pendingOf(photo));
}

/** The files whose changes are not sent yet. */
function unsentCount() {
    return state.pending.files.size;
}

/** The files with tag changes not written yet, by key: waiting here, or in the chunk out. */
function unwritten() {
    const pending = state.pending;
    return new Set([...pending.files.keys(), ...(pending.out || []).map(entry => pathKey(entry.path))]);
}

/**
 * A Delete of `paths` waits for their tag changes (a folder's selection, bulk-edit.js, and the open photo's, photo.js
 * deleteActivePhoto): else a chunk would write, or give back the ops of, a file that is gone, and the page would call a
 * photo deleted on purpose missing. So for a single write of one of them queued or out (a save, a card's title, a rotate:
 * edits.js queueWriteOf, state.photoWrites), which would land on a file about to go or fail "file gone" after it. A library
 * view's selection is ids (every photo of the view, with Select all), which the page cannot match to the files waiting
 * without their cards: it is asked with no `paths`, and waits for every change and write (in a view only the open photo's,
 * and an Undo, written in a moment). Refused with a sentence naming what waits, never waited for: answers true while any
 * is not written; false when the delete may go on.
 */
export function deleteWaits(paths = null) {
    const among = (keys) => (paths ? paths.filter(path => keys.has(pathKey(path))).length : keys.size);
    const which = (n) => (!paths ? photosWord(n) : paths.length === 1 ? 'this photo' : `${n.toLocaleString()} of these photos`);
    const waiting = among(unwritten());
    if (waiting) {
        alert(`Tag changes for ${which(waiting)} are not written yet: wait for them, or cancel them in the list of writes, then delete.`);
        return true;
    }
    const saving = among(new Set(Object.keys(state.photoWrites)));
    if (!saving) return false;
    const one = saving === 1;
    alert(`${one ? 'A save' : 'Saves'} of ${which(saving)} ${one ? 'is' : 'are'} not written yet: wait for ${one ? 'it' : 'them'}`
        + ' (the list of writes, bottom right), then delete.');
    return true;
}

/**
 * Queue the next chunk, unless one is queued and has not had its turn, nothing waits, or the drain is stopped by a failure
 * and no photo's own removal waits (those go anyway: takeChunk). A click while a chunk is out queues the next at the click,
 * so a save clicked after it goes after it (one queue, in the order clicked); the drain's own next chunk is queued when the
 * one out is answered, behind whatever was queued meanwhile.
 */
function drain() {
    const pending = state.pending;
    if (pending.queued || !pending.files.size || (pending.stopped && !ownRemovals().length)) return;
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
function dropPending(only = null) {
    const pending = state.pending;
    const dropped = [...pending.files].filter(([key]) => !only || only.has(key));
    dropped.forEach(([key]) => pending.files.delete(key));
    pending.stopped = false;
    pending.drops += 1;
    state.writeQueue.entries.forEach(row => { if (row.cancel === cancelPending) { row.retry = null; row.cancel = null; } });
    renderWriteQueue();
    dropped.forEach(([, entry]) => settle(entry, 'failed'));
    pageFollows(new Set(dropped.map(([key]) => key)));
    return dropped.length;
}

/** Cancel: drop the changes not sent, and say how many photos it leaves unwritten. */
function cancelPending() {
    const n = dropPending();
    if (n) say('error', `${n.toLocaleString()} ${n === 1 ? "photo's" : "photos'"} tag changes were not written.`, state.pending.failed);
}

/**
 * Leaving with changes waiting, the one place that asks it: another library (`to` null) or another folder opened (`to`
 * its path; from a library view too). Every question comes before anything is dropped or discarded: the tag changes not
 * sent for a folder other than `to`, then the open photo's edits (edits.js leavePhotoThen: Save, Discard or Cancel). Only
 * when both are yes are the changes dropped and `go` run; else `stay`. A chunk already out is a normal write and does not
 * ask (its reply reaches the records kept of a folder left, takeReply).
 */
export function leaveThen(go, stay = () => {}, to = null) {
    if (state.leavePrompt) return stay();   // a question about the photo is open: this route waits its turn (edits.js)
    const counted = new Set([...state.pending.files]
        .filter(([, entry]) => !(to && entry.folder && samePath(entry.folder, to))).map(([key]) => key));
    const n = counted.size;
    if (n && !confirm(`Tag changes for ${photosWord(n)} are not written yet. Leave without writing them?`)) return stay();
    return leavePhotoThen(() => {
        if (n) dropPending(counted);   // only what was asked about: the changes for the folder gone to stay
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

/**
 * The drain's words on the status line. A failure (`failed`: its row in the list of writes) stands until that row is
 * dismissed or retried, or another failure is said: the next write's "Writing tags..." and "Ready" do not say over it.
 */
function say(kind, message, failed = null) {
    const standing = state.pending.failed;
    if (!failed && standing && state.writeQueue.entries.includes(standing)) return;
    state.pending.failed = failed;
    setStatus(kind, message, { transient: kind !== 'error' });
}

function settle(entry, how, which = () => true) {
    const key = pathKey(entry.path);
    for (const action of entry.actions) {
        if (!which(action) || !action.left.delete(key)) continue;
        action[how] += 1;
        if (!action.left.size) action.resolve({ written: action.written, failed: action.failed, skipped: action.skipped });
    }
}

/**
 * The files whose photo's own removals wait (a pill, a face's person), in the order clicked; while a failed write waits for
 * its Retry, only those holding nothing else (a file with other changes waits with them; its panel is read-only meanwhile).
 */
function ownRemovals() {
    const pending = state.pending;
    const ownOnly = (entry) => [...entry.add, ...entry.remove].every(tag => entry.panel.has(tag));
    return [...pending.files].filter(([, entry]) => entry.panel.size && (!pending.stopped || ownOnly(entry)));
}

/**
 * The next chunk, taken when its turn comes (not when it was queued), at most CHUNK files: the one place that says in what
 * order the changes are written. The rule: a write -- a chunk, or a save or a suggestion queued behind it -- never runs
 * against a record that is missing a change clicked before it, on any photo. A save is made only on a photo whose waiting
 * changes are all its own removals (any other makes it read-only: pendingOf), so the chunk queued ahead of it takes every
 * photo's own removals waiting, in the order clicked, whatever action each was: one write of them, under the first one's
 * group (the pills taken off while a write was out are one line of History). They go while a failed write waits for its
 * Retry, as their failure is answered at once (answerPanel). With none waiting, the files of the first action in the map,
 * which is in the order clicked (a failed chunk's files given back first). Every op is sent, whatever the page's record
 * holds: the record may be older than the file, and the server skips a file that already holds it all.
 */
function takeChunk() {
    const pending = state.pending;
    const own = ownRemovals();
    const first = pending.files.values().next().value;
    const taken = own.length ? own : pending.stopped ? [] : [...pending.files].filter(([, entry]) => entry.token === first.token);
    const chunk = taken.slice(0, CHUNK);
    chunk.forEach(([key]) => pending.files.delete(key));
    return chunk.map(([, entry]) => entry);
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
    let chunk = [];
    state.pending.queued = false;
    try {
        const drops = state.pending.drops;
        chunk = takeChunk();
        state.pending.out = chunk;
        if (!chunk.length) {
            // Queued at a click while the chunk before was out, and that one failed: the changes wait for its Retry.
            entry.label = state.pending.stopped ? 'Tag changes: waiting for the failed write' : 'Tag changes: nothing left to write';
            return true;
        }
        entry.label = chunkLabel(chunk);
        entry.cancel = cancelPending;
        renderWriteQueue();
        say('busy', 'Writing tags...');
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
        drain();
        pageFollows(new Set(chunk.map(item => pathKey(item.path))));
    }
}

/**
 * The reply into the records: each file written takes the stamp and the tags its file holds now, in any record of it
 * the page holds (heldRecords) and, its folder left since, in the records this browser kept of that folder (whatever the page shows
 * meanwhile: a library view may show one of its photos); a file gone was skipped and keeps its record. A failed chunk's
 * unwritten files go back to the front of the map and the drain stops, unless the changes were dropped while it was out
 * (`dropped`): then they are not written, and Cancel's message stands.
 */
function takeReply(chunk, data, entry, dropped) {
    const byKey = (obj) => new Map(Object.entries(obj || {}).map(([path, v]) => [pathKey(path), v]));
    const stamps = byKey(data.stamps);
    const held = byKey(data.written);
    const skipped = new Set((data.skipped || []).map(s => pathKey(s.path)));
    const photos = new Map();   // key -> the records the page holds of it
    heldRecords().forEach(p => photos.set(pathKey(p.path), [...(photos.get(pathKey(p.path)) || []), p]));
    const back = [];
    const written = [];
    const left = new Map();   // folder left since -> its items written
    const take = (photo, item) => {
        const key = pathKey(item.path);
        takeWritten(photo, stamps.get(key), held.get(key));
        applyOps(photo, item, held.get(key));
    };
    // A library read out meanwhile may answer with the file as it was before this write (readHeld).
    const forReads = (item) => state.pending.reads.forEach(replies => replies.push({ key: pathKey(item.path), take: (photo) => take(photo, item) }));
    for (const item of chunk) {
        const key = pathKey(item.path);
        if (skipped.has(key)) {
            settle(item, 'skipped');
        } else if (data.success || held.has(key)) {
            (photos.get(key) || []).forEach(photo => {
                take(photo, item);
                written.push(photo);
            });
            if (item.folder && !(state.scannedFolder && samePath(item.folder, state.scannedFolder))) {
                left.set(item.folder, [...(left.get(item.folder) || []), item]);
            }
            forReads(item);
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
    let kept = back;
    let putBack = [];
    if (!data.success && dropped) {
        back.forEach(item => settle(item, 'failed'));
    } else if (!data.success) {
        ({ kept, putBack } = answerPanel(back));
        giveBack(kept);
        state.pending.stopped = state.pending.stopped || kept.length > 0;   // stopped already: own removals sent meanwhile
    }
    if (written.length) {
        if (state.library) upper.updateSelectedThumbnailsCount();   // a folder's, and the list, follow in sendChunk's pageFollows
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
            say('error', `Tags not written to ${back.length.toLocaleString()} more ${back.length === 1 ? 'photo' : 'photos'}: ${entry.error}`, entry);
            return false;
        }
        if (!kept.length) {
            // Only the open photo's own removals: they are drawn again, and nothing waits for a Retry or a Cancel.
            entry.retry = null;
            entry.cancel = null;
            say('error', `Not taken off ${putBack.join(', ')}: ${entry.error}`, entry);
            alert(`Not taken off ${putBack.join(', ')}: ${entry.error}`);
            return false;
        }
        // The panel's own removals among them are drawn again now, and are not part of the Retry: said by name.
        const notOff = putBack.length ? ` Not taken off ${putBack.join(', ')}.` : '';
        say('error', `Tags not written to ${photosWord(kept.length)}.${notOff}`, entry);
        alert(`Tags not written to ${photosWord(kept.length)}: ${entry.error}.${notOff} Retry or Cancel it in the list of writes (bottom right).`);
        return false;
    }
    if (dropped) return true;
    if (state.pending.stopped) {
        say('error', STOPPED);
        return true;
    }
    // Photos missing on disk are said once, when each owner action they belong to is done (counted over its chunks).
    const finished = new Set(chunk.flatMap(item => [...item.actions].filter(action => !action.left.size)));
    const gone = [...finished].reduce((n, action) => n + action.skipped, 0);
    if (gone) {
        say('ready', `${gone.toLocaleString()} ${gone === 1 ? 'photo is' : 'photos are'} missing on disk and ${gone === 1 ? 'was' : 'were'} left out.${whereWritten(data)}`);
    } else {
        say('ready', data.file_only ? `Saved.${whereWritten(data)}` : 'Ready');
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

/**
 * A failed chunk's removals made in a photo's panel are answered now, not kept for Retry: their clicks are told it failed,
 * and with the ops gone from the map the panel draws the tags again (the file still holds them). The files' other changes
 * are what is given back (`kept`); `putBack`, the tags drawn again.
 */
function answerPanel(items) {
    const kept = [];
    const putBack = [];
    for (const item of items) {
        item.panel.forEach(tag => { item.add.delete(tag); item.remove.delete(tag); putBack.push(tag); });
        item.panel.clear();
        settle(item, 'failed', action => action.panel);
        if (item.add.size || item.remove.size) kept.push(item);
        else settle(item, 'failed');
    }
    return { kept, putBack };
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
            [...newer.add, ...newer.remove].forEach(t => (newer.panel.has(t) ? item.panel.add(t) : item.panel.delete(t)));
            newer.actions.forEach(a => item.actions.add(a));
            files.delete(key);
        }
        front.set(key, item);
    }
    state.pending.files = new Map([...front, ...files]);
}
