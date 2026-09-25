// TagPup's page: undoing the last bulk write.
import { api } from './common/api.js';
import { state } from './state.js';
import { btnUndo } from './elements.js';
import { setStatus } from './status.js';
import { saveToLocalStorageCache } from './cache.js';
import { renderFileList } from './folder.js';
import { renderTags } from './photo.js';
import { renderThumbnails } from './grid.js';

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
 * Put back the values captured before the last bulk write.
 *
 * Each photo is restored to the tags and title it had, rather than the change
 * being reversed field by field: a snapshot cannot be confused about what an
 * addition or a removal was, and the write path is the one already trusted.
 */
export function undoLastOperation() {
    if (!state.lastUndoable) {
        setStatus('ready', 'Nothing to undo');
        return;
    }
    const entry = state.lastUndoable;
    state.lastUndoable = null;
    updateUndoButton();

    setStatus('busy', `Undoing: ${entry.label}...`);
    const writes = entry.photos.map(snapshot =>
        api.json('/api/photo/save-metadata', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                path: snapshot.path,
                title: snapshot.title,
                tags: snapshot.tags,
            })
        })
        .then(data => {
            if (!data.success) throw new Error(data.error || 'failed');
            const photo = state.folderPhotos.find(p => p.path === snapshot.path);
            if (photo) {
                photo.tags = snapshot.tags.slice();
                photo.title = snapshot.title;
            }
        })
    );

    Promise.allSettled(writes).then(results => {
        const failed = results.filter(r => r.status === 'rejected').length;
        renderFileList();
        renderThumbnails();
        if (state.activePhotoPath) {
            const photo = state.folderPhotos.find(p => p.path === state.activePhotoPath);
            if (photo) renderTags(photo.tags || []);
        }
        saveToLocalStorageCache();

        if (failed) {
            // Partly restored is worth saying plainly: the rest is as it was.
            setStatus('error',
                `Undo restored ${results.length - failed} of ${results.length} photo(s)`,
                { transient: false });
        } else {
            setStatus('ready', `Undone: ${entry.label}`);
        }
    });
}

/** Snapshot the photos a bulk write is about to change. */
export function snapshotPhotos(paths) {
    return paths
        .map(path => state.folderPhotos.find(p => p.path === path))
        .filter(Boolean)
        .map(photo => ({
            path: photo.path,
            tags: (photo.tags || []).slice(),
            title: photo.title || '',
        }));
}
