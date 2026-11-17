// TagPup's page: undoing the last bulk write.
import { state } from './state.js';
import { btnUndo } from './elements.js';
import { setStatus } from './status.js';
import { pendChanges } from './pending.js';

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
 * Take back what the last bulk write changed, as tag changes waiting their turn (pending.js): after every write
 * queued before it.
 *
 * Each photo's entry holds its tags before the write and after it, and the undo is the difference: the tags the
 * write added are taken off, the ones it took off are put back, starting from what each file holds. It posted each
 * photo's whole tag list from before, at once and beside the queue, so a write queued after the one being undone --
 * a tag clicked while Apply All was still writing -- was overwritten without a word.
 */
export async function undoLastOperation() {
    if (!state.lastUndoable) {
        setStatus('ready', 'Nothing to undo');
        return;
    }
    // Undo re-writes the earlier tags, so it works for an edit of files only as for any other.
    const entry = state.lastUndoable;
    state.lastUndoable = null;
    updateUndoButton();
    const changes = entry.photos.map(photo => {
        const before = photo.before || [];
        const after = photo.after || [];
        return { path: photo.path, add: before.filter(t => !after.includes(t)), remove: after.filter(t => !before.includes(t)) };
    }).filter(change => change.add.length || change.remove.length);
    setStatus('busy', `Undoing: ${entry.label}...`);
    const { written, failed, skipped } = await pendChanges(changes);
    if (failed || skipped) {
        // Partly restored is worth saying plainly: the rest is as it was.
        setStatus('error', `Undo restored ${written} of ${written + failed + skipped} photo(s)`, { transient: false });
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
