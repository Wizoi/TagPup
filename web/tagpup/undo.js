// TagPup's page: undoing the last bulk write.
import { api } from './common/api.js';
import { pathKey, samePath } from './common/paths.js';
import { state } from './state.js';
import { btnUndo } from './elements.js';
import { setStatus } from './status.js';
import { saveToLocalStorageCache } from './cache.js';
import { renderFileList } from './folder.js';
import { renderTags } from './photo.js';
import { renderThumbnails } from './grid.js';
import { queuePhotoWrite, takeWritten } from './edits.js';

export function recordUndo(entry) {
    state.lastUndoable = entry;
    updateUndoButton();
}

export function updateUndoButton() {
    if (!btnUndo) return;
    btnUndo.disabled = !state.lastUndoable;
    btnUndo.title = state.lastUndoable
        ? `Undo: ${state.lastUndoable.label}  (Ctrl+Z)`
        : 'Nothing to undo';
}

/**
 * Take back what the last bulk write changed, in the photo write queue (edits.js):
 * after every write queued before it.
 *
 * Each photo's entry holds its tags before the write and after it, and the undo is
 * the difference: the tags the write added are taken off, the ones it took off are
 * put back, through the bulk tag write, which starts from what each file holds. It
 * posted each photo's whole tag list from before, at once and beside the queue, so
 * a write queued after the one being undone -- a tag clicked while Apply All was
 * still writing -- was overwritten without a word.
 */
export function undoLastOperation() {
    if (!state.lastUndoable) {
        setStatus('ready', 'Nothing to undo');
        return;
    }
    // Undo re-writes the earlier tags, so it works for an edit of files only as for any other.
    const entry = state.lastUndoable;
    state.lastUndoable = null;
    updateUndoButton();
    return queuePhotoWrite((queued) => undoEntry(entry, queued), `Undo: ${entry.label}`);
}

/** The photos of `entry` grouped by what undoing it adds and takes off. */
export function undoGroups(entry) {
    const groups = new Map();
    for (const photo of entry.photos) {
        const before = photo.before || [];
        const after = photo.after || [];
        const added = after.filter(t => !before.includes(t));
        const removed = before.filter(t => !after.includes(t));
        if (!added.length && !removed.length) continue;
        const key = JSON.stringify([added, removed]);
        if (!groups.has(key)) groups.set(key, { added, removed, paths: [] });
        groups.get(key).paths.push(photo.path);
    }
    return [...groups.values()];
}

async function undoEntry(entry, queued) {
    setStatus('busy', `Undoing: ${entry.label}...`);
    let restored = 0;
    let failed = 0;
    for (const group of undoGroups(entry)) {
        let data;
        try {
            data = await api.json('/api/photos/bulk-tags', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ paths: group.paths, add_tags: group.removed, remove_tags: group.added })
            });
        } catch (err) {
            data = { success: false, error: err.message };
        }
        // The photos the undo wrote are of their files as they are now: the stamp and the tags a save names next.
        const stamps = new Map(Object.entries(data.stamps || {}).map(([path, stamp]) => [pathKey(path), stamp]));
        const heldTags = new Map(Object.entries(data.written || {}).map(([path, held]) => [pathKey(path), held]));
        // A write that stopped part-way names the photos it wrote.
        const done = data.success ? group.paths : Object.keys(data.written || {});
        for (const path of group.paths) {
            if (!done.some(d => samePath(d, path))) {
                failed += 1;
                continue;
            }
            restored += 1;
            const photo = state.folderPhotos.find(p => samePath(p.path, path));
            if (!photo) continue;
            const kept = (photo.tags || []).filter(t => !group.added.includes(t));
            photo.tags = kept.concat(group.removed.filter(t => !kept.includes(t)));
            takeWritten(photo, stamps.get(pathKey(path)), heldTags.get(pathKey(path)));
        }
        if (!data.success) {
            console.error('Undo:', data.error);
            queued.error = data.error;
        }
    }
    renderFileList();
    renderThumbnails();
    if (state.activePhotoPath) {
        const photo = state.folderPhotos.find(p => p.path === state.activePhotoPath);
        if (photo) renderTags(photo.tags || []);
    }
    saveToLocalStorageCache();

    if (failed) {
        // Partly restored is worth saying plainly: the rest is as it was.
        setStatus('error', `Undo restored ${restored} of ${restored + failed} photo(s)`, { transient: false });
        return false;
    }
    setStatus('ready', `Undone: ${entry.label}`);
    return true;
}

/** Snapshot the tags of the photos a bulk write is about to change: each entry's `before`. */
export function snapshotPhotos(paths) {
    return paths
        .map(path => state.folderPhotos.find(p => p.path === path))
        .filter(Boolean)
        .map(photo => ({ path: photo.path, before: (photo.tags || []).slice() }));
}
