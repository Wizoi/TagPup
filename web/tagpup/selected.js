// TagPup's page: the selection of photos -- the one owner of what is selected.
//
// `state.selectedThumbnails` is the selection as every other module reads it: the paths, in the
// order they were picked (bulk tags, rename, Suggest read it, or copy it with slice()). A grid
// that draws only the cards on screen cannot ask the cards what is selected, and an array
// searched with includes() once per card is O(n^2) for Select All, so the same photos are held
// again by pathKey in `state.selectedKeys`, a Set. The two change together, here and nowhere
// else (tests/frontend/selected-single-owner.test.mjs): nothing else assigns, pushes to or
// splices `state.selectedThumbnails`.
//
// A LIBRARY VIEW (docs/ARCHITECTURE.md, phase 9d-2) holds up to 200,000 photos and the page holds only the cards near the
// window, so its selection is not paths: it is photo ids, in one of two shapes (`state.library.sel`), and
// `state.selectedThumbnails` stays empty while it is open.
//
//   mode 'ids'     `ids`: the photos picked, one by one or in ranges;
//   mode 'source'  every photo of the view's source but `excluded` (Select all is this, with nothing excluded).
//
// So Select all of 68,000 photos is one assignment, a Shift range is a loop over the view's order, and a request carries
// the source and a few ids, never 68,000 paths. The server takes at most LIST_LIMIT ids or excluded ids in a request
// (tagpup.services.selection.MAX_LISTED); selectionRequest() says in a sentence, before any request, what cannot be sent.
import { pathKey } from './common/paths.js';
import { state } from './state.js';

/** A bulk write over more photos than this names itself and asks first (selection.js). */
export const BULK_CONFIRM_ABOVE = 200;
/** The most photos one bulk write may name: the server refuses more (tagpup.web.tagpup_routes.BULK_LIMIT). */
export const BULK_LIMIT = 5000;
/** The most ids, or excluded ids, a request may list (tagpup.services.selection.MAX_LISTED). */
export const LIST_LIMIT = 20000;
/** The most photos one bulk edit may hold, however they are named (tagpup.services.selection.MAX_SELECTED). */
export const JOB_LIMIT = 200000;

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

/** Keep only the selected photos that are among these paths (the same folder, scanned again). */
export function keepOnly(paths) {
    const present = new Set();
    for (const path of paths) present.add(pathKey(path));
    state.selectedThumbnails = state.selectedThumbnails.filter(path => present.has(pathKey(path)));
    state.selectedKeys = new Set(state.selectedThumbnails.map(pathKey));
    if (state.lastSelectedPath && !present.has(pathKey(state.lastSelectedPath))) state.lastSelectedPath = null;
}

export function clearSelection() {
    state.selectedThumbnails = [];
    state.selectedKeys = new Set();
    if (state.library) clearIdSelection();
}

// ---- A library view's selection, by photo id -----------------------------------------------------------------------

/** An empty selection of a library view: the shape `state.library.sel` has. `version` changes with every change. */
export function newIdSelection() {
    return { mode: 'ids', ids: new Set(), excluded: new Set(), version: 0 };
}

function bumpVersion(sel) {
    sel.version += 1;
}

/** Is the photo with this id selected? O(1). False outside a library view. */
export function isIdSelected(id) {
    const lib = state.library;
    if (!lib) return false;
    return lib.sel.mode === 'ids' ? lib.sel.ids.has(id) : !lib.sel.excluded.has(id);
}

/** Is this card's photo selected: by id in a library view, by path in a folder's. */
export function isPhotoSelected(photo) {
    return state.library ? isIdSelected(photo.id) : isSelected(photo.path);
}

/** How many photos are selected: the folder's paths, or the library view's ids (or its total less the excluded). */
export function selectionCount() {
    const lib = state.library;
    if (!lib) return state.selectedThumbnails.length;
    return lib.sel.mode === 'ids' ? lib.sel.ids.size : Math.max(0, lib.total - lib.sel.excluded.size);
}

/** Select, or deselect, the photo with this id. */
export function setIdSelected(id, on) {
    const lib = state.library;
    if (!lib || id === undefined || id === null) return;
    setIdSelectedQuietly(lib.sel, id, on);
    bumpVersion(lib.sel);
}

/** Select, or deselect, every photo from index `from` to index `to` of the view's order (either way round, both included). */
export function setIdRange(from, to, on) {
    const lib = state.library;
    if (!lib) return;
    const low = Math.max(0, Math.min(from, to));
    const high = Math.min(lib.ids.length - 1, Math.max(from, to));
    for (let at = low; at <= high; at++) setIdSelectedQuietly(lib.sel, lib.ids[at], on);
    bumpVersion(lib.sel);
}

function setIdSelectedQuietly(sel, id, on) {
    if (sel.mode === 'ids') {
        if (on) sel.ids.add(id);
        else sel.ids.delete(id);
    } else if (on) sel.excluded.delete(id);
    else sel.excluded.add(id);
}

/** Select every photo of the view: its source, nothing excluded. No request, no ids. */
export function selectAllInView() {
    const lib = state.library;
    if (!lib) return;
    lib.sel.mode = 'source';
    lib.sel.ids = new Set();
    lib.sel.excluded = new Set();
    bumpVersion(lib.sel);
}

/** Select none of the view's photos. */
export function clearIdSelection() {
    const lib = state.library;
    if (!lib) return;
    lib.sel.mode = 'ids';
    lib.sel.ids = new Set();
    lib.sel.excluded = new Set();
    bumpVersion(lib.sel);
}

/**
 * Select what is not selected: the listed ids become the excluded ones and the other way round. Only while that list is
 * one a request can carry; { ok: false, sentence } otherwise, and nothing changes.
 */
export function invertIdSelection() {
    const lib = state.library;
    if (!lib) return { ok: false, sentence: '' };
    const sel = lib.sel;
    const listed = sel.mode === 'ids' ? sel.ids : sel.excluded;
    if (listed.size > LIST_LIMIT) {
        return { ok: false, sentence: 'Too many to invert: use Select all and deselect.' };
    }
    if (sel.mode === 'ids') {
        sel.excluded = sel.ids;
        sel.ids = new Set();
        sel.mode = 'source';
    } else {
        sel.ids = sel.excluded;
        sel.excluded = new Set();
        sel.mode = 'ids';
    }
    bumpVersion(sel);
    return { ok: true, sentence: '' };
}

/** These photos are gone from the view (deleted, or no longer in the library): they are not selected, nor excluded. */
export function forgetSelectedIds(ids) {
    const lib = state.library;
    if (!lib) return;
    const sel = lib.sel;
    let any = false;
    for (const id of ids) {
        if (sel.ids.delete(id)) any = true;
        if (sel.excluded.delete(id)) any = true;
    }
    if (any) bumpVersion(sel);
}

/** The view's order was read again: every id the selection names that the view no longer holds is let go. */
export function reconcileIdSelection() {
    const lib = state.library;
    if (!lib || (!lib.sel.ids.size && !lib.sel.excluded.size)) return;
    const present = new Set(lib.ids);
    const gone = [...lib.sel.ids, ...lib.sel.excluded].filter(id => !present.has(id));
    if (gone.length && lib.complete) forgetSelectedIds(gone);
}

/**
 * How the selection can be named in a request, decided from the counts alone (no list is built): { how, sentence } with
 * `how` 'ids' (the ids picked), 'source' (the source but the excluded ones), 'rest-ids' (the ids the excluded ones leave) or
 * 'rest-source' (the source but the ids the picked ones leave), or `how` null and a `sentence` saying why it cannot be sent.
 */
function planRequest({ job = true } = {}) {
    const lib = state.library;
    if (!lib) return { how: null, sentence: 'No library view is open.' };
    const count = selectionCount();
    if (count <= 0) return { how: null, sentence: '' };
    if (job && count > JOB_LIMIT) {
        return { how: null, sentence: `${count.toLocaleString()} photos are selected; a bulk edit takes at most ${JOB_LIMIT.toLocaleString()}. Narrow the selection.` };
    }
    const sel = lib.sel;
    // The other way round is exact only when the page holds the whole order.
    const whole = lib.complete && lib.ids.length === lib.total;
    if (sel.mode === 'ids') {
        if (sel.ids.size <= LIST_LIMIT) return { how: 'ids', sentence: '' };
        if (whole && lib.total - sel.ids.size <= LIST_LIMIT) return { how: 'rest-source', sentence: '' };
        return { how: null, sentence: tooScattered(sel.ids.size, lib.total - sel.ids.size) };
    }
    if (sel.excluded.size <= LIST_LIMIT) return { how: 'source', sentence: '' };
    if (whole && count <= LIST_LIMIT) return { how: 'rest-ids', sentence: '' };
    return { how: null, sentence: tooScattered(count, sel.excluded.size) };
}

/** The selection as the tally's request names it: as selectionRequest, but a tally is not limited to what a job takes. */
export function tallyRequest() {
    return selectionRequest({ job: false });
}

/** Why the selection cannot be sent as it is, in a sentence; '' when it can, or when nothing is selected. */
export function selectionProblem() {
    return planRequest().sentence;
}

/**
 * The selection as a request names it: { ok: true, body } with `{ids}` or `{source, excluded}` (the way that fits the server's
 * limit), or { ok: false, sentence } saying why it cannot be sent. Nothing is asked of the server here: this is the page's
 * own check, made before any request.
 */
export function selectionRequest({ job = true } = {}) {
    const plan = planRequest({ job });
    if (!plan.how) return { ok: false, sentence: plan.sentence || 'Nothing is selected.' };
    const lib = state.library;
    const sel = lib.sel;
    const source = { kind: lib.kind, value: lib.value, recursive: lib.recursive };
    if (plan.how === 'ids') return { ok: true, body: { ids: lib.ids.filter(id => sel.ids.has(id)) } };
    if (plan.how === 'source') return { ok: true, body: { source, excluded: [...sel.excluded] } };
    if (plan.how === 'rest-ids') return { ok: true, body: { ids: lib.ids.filter(id => !sel.excluded.has(id)) } };
    return { ok: true, body: { source, excluded: lib.ids.filter(id => !sel.ids.has(id)) } };
}

function tooScattered(selected, left) {
    return `${selected.toLocaleString()} photos are selected and ${left.toLocaleString()} are not: a bulk edit can name at most `
        + `${LIST_LIMIT.toLocaleString()} photos one by one, or everything in the view but ${LIST_LIMIT.toLocaleString()}. `
        + 'Select fewer, or Select all and deselect.';
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
