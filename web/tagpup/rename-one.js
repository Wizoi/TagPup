// TagPup's page: Smart Rename of the open photo alone -- the box beside its file name, filled with the grouping its name
// holds (GET /api/photo/smart-rename-parts), and its button (POST /api/photo/smart-rename). The photo keeps its number
// (DECISIONS 2026-11-05). Not in rename.js, the folder's Smart Rename: photo.js fills the box, and rename.js imports
// grid.js, which imports photo.js.
import { api } from './common/api.js';
import { ruleProblem } from './common/validate.js';
import { baseName, samePath } from './common/paths.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import { btnSmartRenameOne, inputSmartRenameOne } from './elements.js';
import { flagField, setStatus } from './status.js';
import { recordsWritten } from './cache.js';
import { whereWritten } from './write-queue.js';
import { hasUnsavedEdits, photoRenamed, queueWriteOf } from './edits.js';
import { takeStamp } from './common/photo-write.js';
import { refetchCards } from './library-source.js';

export const SAVE_EDITS_FIRST = 'Save or discard the changes typed on this photo first: then Smart Rename it.';

/**
 * The box shows the grouping of `path`'s name. The same photo shown again (a refresh) keeps what is typed in it; another
 * photo empties it at once and fills it when the server answers, unless the page has moved on or the box was typed in.
 */
export function showSmartRenameParts(path) {
    const shown = state.smartRenameOne;
    if (!inputSmartRenameOne || (shown.path !== null && path === shown.path)) return;
    const asked = ++shown.asked;
    shown.path = path;
    shown.filled = '';
    inputSmartRenameOne.value = '';
    // A quoted string, not a template: the page tests read a template's words as names (harness.mjs).
    api.json('/api/photo/smart-rename-parts?path=' + encodeURIComponent(path))
        .then(data => {
            if (asked !== shown.asked || inputSmartRenameOne.value !== '') return;
            shown.filled = (data && typeof data.grouping === 'string') ? data.grouping : '';
            inputSmartRenameOne.value = shown.filled;
        })
        .catch(err => console.error('Could not read the grouping of the photo\'s name:', err));
}

/**
 * Smart Rename the open photo by the grouping in the box. Refused, with nothing sent, for a grouping that may not be used
 * and while text typed on the photo is unsaved (its save could rename the photo too). It waits behind the photo's other
 * writes and names the photo as it is called when it runs. Resolves true when the photo holds its new name (or had it).
 */
export function smartRenameOpenPhoto() {
    const photo = state.activePhotoPath && state.folderPhotos.find(p => samePath(p.path, state.activePhotoPath));
    if (!photo) return Promise.resolve(false);
    const grouping = inputSmartRenameOne.value.trim();
    const refused = ruleProblem('grouping', grouping);
    if (refused) {
        flagField(inputSmartRenameOne, refused);
        return Promise.resolve(false);
    }
    if (hasUnsavedEdits()) {
        setStatus('error', SAVE_EDITS_FIRST, { transient: false });
        return Promise.resolve(false);
    }
    return queueWriteOf(photo.path, async (entry) => {
        const from = photo.path;
        setStatus('busy', `Renaming ${baseName(from)}...`);
        let data;
        try {
            const res = await api.fetch('/api/photo/smart-rename', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: from, grouping }),
            });
            data = await res.json();
            if (!res.ok || !data.success) throw new Error(data.error || 'the rename failed');
        } catch (err) {
            console.error(err);
            entry.error = err.message;
            setStatus('error', `Not renamed: ${err.message}`, { transient: false });
            return false;
        }
        takeStamp(photo, data);
        if (data.renamed) photoRenamed(photo, data.new_path);
        // The file is renamed: a failure redrawing the page is logged, not said as a rename that failed.
        try {
            recordsWritten([photo], { [from]: photo.path });
            // `from !== photo.path`: the spelling changed (a rename, even of case only), exact on purpose.
            if (from !== photo.path && samePath(state.activePhotoPath, from)) {
                state.activePhotoPath = photo.path;
                upper.selectPhoto(photo.path);
            }
            upper.renderFileList();
            upper.renderThumbnails();
            // A library view's card holds the name it was read with: asked for again.
            if (data.renamed && state.library && photo.id !== undefined) refetchCards([photo.id]);
        } catch (err) {
            console.error('Renamed, but redrawing the page failed:', err);
        }
        const said = data.renamed
            ? `Renamed to ${baseName(photo.path)}.${whereWritten(data)}`
            : `${baseName(photo.path)} already has that name.`;
        setStatus('ready', said.replace(/\.$/, ''));
        return true;
    }, `Smart Rename ${baseName(photo.path)}`);
}

export function wireSmartRenameOne() {
    if (!btnSmartRenameOne || !inputSmartRenameOne) return;
    btnSmartRenameOne.addEventListener('click', () => smartRenameOpenPhoto());
    inputSmartRenameOne.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') smartRenameOpenPhoto();
    });
}
