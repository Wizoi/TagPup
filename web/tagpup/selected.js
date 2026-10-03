// TagPup's page: the selection of photos -- the one owner of what is selected.
//
// `state.selectedThumbnails` is the selection as every other module reads it: the paths, in the
// order they were picked (bulk tags, rename, Suggest read it, or copy it with slice()). A grid
// that draws only the cards on screen cannot ask the cards what is selected, and an array
// searched with includes() once per card is O(n^2) for Select All, so the same photos are held
// again by pathKey in `state.selectedKeys`, a Set. The two change together, here and nowhere
// else (tests/frontend/selected-single-owner.test.mjs): nothing else assigns, pushes to or
// splices `state.selectedThumbnails`.
import { pathKey } from './common/paths.js';
import { state } from './state.js';

/** Is this photo selected? O(1), whatever its spelling of the path. */
export function isSelected(path) {
    return state.selectedKeys.has(pathKey(path));
}

/** Replace the selection with these paths (kept in order, once each). */
export function setSelection(paths) {
    state.selectedThumbnails = [];
    state.selectedKeys = new Set();
    addToSelection(paths);
}

/** Select these paths too: those already selected keep their place, the rest go on the end. */
export function addToSelection(paths) {
    for (const path of paths) {
        const key = pathKey(path);
        if (state.selectedKeys.has(key)) continue;
        state.selectedKeys.add(key);
        state.selectedThumbnails.push(path);
    }
}

/** Take these paths out of the selection. One path is found by a native search; many by one pass. */
export function removeFromSelection(paths) {
    const gone = new Set();
    for (const path of paths) {
        const key = pathKey(path);
        if (state.selectedKeys.delete(key)) gone.add(key);
    }
    if (!gone.size) return;
    if (gone.size === 1) {
        const [only] = paths;
        const at = state.selectedThumbnails.indexOf(only);
        if (at !== -1) {
            state.selectedThumbnails.splice(at, 1);
            return;
        }
    }
    state.selectedThumbnails = state.selectedThumbnails.filter(path => !gone.has(pathKey(path)));
}

export function clearSelection() {
    state.selectedThumbnails = [];
    state.selectedKeys = new Set();
}

/** A photo renamed by a save keeps its place in the selection, under its new path. */
export function renameInSelection(oldPath, newPath) {
    const oldKey = pathKey(oldPath);
    if (!state.selectedKeys.has(oldKey)) return;
    state.selectedKeys.delete(oldKey);
    state.selectedKeys.add(pathKey(newPath));
    const at = state.selectedThumbnails.findIndex(path => pathKey(path) === oldKey);
    if (at !== -1) state.selectedThumbnails[at] = newPath;
    if (state.lastSelectedPath && pathKey(state.lastSelectedPath) === oldKey) state.lastSelectedPath = newPath;
}
