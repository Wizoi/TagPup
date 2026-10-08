// TagPup's page: what the selected photos of a library view carry -- their tags and people, each with how many of the photos hold it --
// counted by the server (POST /api/library/selection/tally), because the page holds only the cards near the window and a count of a
// part would read as the whole (docs/ARCHITECTURE.md, phase 9d-2; 9d-1 for the route). Asked 250 ms after the selection stops changing,
// and a request the selection has left behind is aborted and its answer dropped. While it is out the lists say "counting...".
// The same answer names the folders the selection is in (#675): ten or fewer are listed by name, each a button that opens it in
// Organize (library-moves.js openInOrganize, through `upper`); more are counted, and the owner is asked to narrow the selection.
// Names are text, never markup. A folder view's panel is selection.js's own and is not touched.
import { api } from './common/api.js';
import { buildElement, replaceContent } from './common/dom.js';
import { sortedTags } from './common/vocabulary.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import { selectionFoldersList, selectionPeopleList, selectionTagsList } from './elements.js';
import { selectionCount, tallyRequest } from './selected.js';
import { editByPill } from './bulk-edit.js';
import { lockBulkControls } from './bulk-job.js';
import { drawJumpNote, drawJumps } from './selection-panel.js';

/** How long the selection must stand still before it is counted. */
export const TALLY_DELAY_MS = 250;

const QUIET = 'color: var(--text-muted); font-size: 12px; padding: 4px 0;';

function note(text) {
    return buildElement('span', { style: QUIET, text });
}

function chip({ label, title, count, total, kind, name }) {
    const element = buildElement('span', { className: 'selection-summary-chip', text: `${label} (${count.toLocaleString()})`, title });
    if (count < total) {
        const apply = buildElement('span', { className: 'selection-summary-chip-apply', text: ' ➡️', title: `Apply "${label}" to all selected photos` });
        apply.addEventListener('click', (event) => {
            event.stopPropagation();
            if (apply.getAttribute('aria-disabled') === 'true') return;
            editByPill({ kind, name, remove: false });
        });
        element.appendChild(apply);
    }
    const remove = buildElement('span', { className: 'selection-summary-chip-remove', text: ' ×', title: `Remove "${label}" from all selected photos` });
    remove.addEventListener('click', (event) => {
        event.stopPropagation();
        if (remove.getAttribute('aria-disabled') === 'true') return;
        editByPill({ kind, name, remove: true });
    });
    element.appendChild(remove);
    return element;
}

function listOf(entries, more, total, kind, key) {
    if (!entries.length && !more) return [note('None')];
    const shown = sortedTags(entries, each => each[key]).map(each => chip({
        label: each[key], title: each[key], count: each.count, total, kind, name: each[key],
    }));
    if (more) shown.push(note(`${more.toLocaleString()} more, not listed`));
    return shown;
}

/** The most folders listed by name: the server's MAX_FOLDERS_LISTED (tagpup.services.selection). */
export const FOLDERS_LISTED = 10;

/** The last two parts of a path, for two listed folders of one name ("2020\Event 01" beside "2021\Event 01"). */
function twoParts(path) {
    const parts = String(path).split(/[\\/]+/).filter(Boolean);
    return parts.slice(-2).join('\\');
}

/** Folders to Organize: each listed folder a button opening it in Organize, or how many there are and to narrow the selection. */
function foldersOf(folders) {
    if (!folders || !Number.isFinite(folders.count)) return [note('')];
    if (folders.count === 0) return [note('None')];
    const listed = Array.isArray(folders.listed) ? folders.listed : [];
    if (folders.count > FOLDERS_LISTED || !listed.length) {
        return [note(`These photos are in ${folders.count.toLocaleString()} folders. Narrow the selection to ${FOLDERS_LISTED} folders or fewer to open one in Organize.`)];
    }
    const names = new Map();
    for (const each of listed) names.set(each.name, (names.get(each.name) || 0) + 1);
    return listed.map(each => {
        const label = names.get(each.name) > 1 ? twoParts(each.path) : String(each.name || each.path);
        const open = buildElement('button', {
            className: 'selection-folder-link', text: label, title: `Open ${each.path} in Organize`, attrs: { type: 'button' },
        });
        open.addEventListener('click', (event) => {
            event.stopPropagation();
            upper.openInOrganize(each.path);
        });
        const photos = Number(each.photos) || 0;
        return buildElement('span', {}, [open, ' ', buildElement('span', {
            className: 'selection-folder-count', text: `(${photos.toLocaleString()} ${photos === 1 ? 'photo' : 'photos'})`,
        })]);
    });
}

/** Draw what the panel holds: counting, the tally, or why there is none. */
export function drawTally() {
    const tally = state.tally;
    if (!selectionPeopleList || !selectionTagsList) return;
    if (tally.status === 'counting' || tally.status === 'idle') {
        replaceContent(selectionPeopleList, note('counting…'));
        replaceContent(selectionTagsList, note('counting…'));
        if (selectionFoldersList) replaceContent(selectionFoldersList, note('counting…'));
        drawJumpNote('counting…');
    } else if (tally.status === 'error') {
        replaceContent(selectionPeopleList, note(tally.message));
        replaceContent(selectionTagsList, note(tally.message));
        if (selectionFoldersList) replaceContent(selectionFoldersList, note(tally.message));
        drawJumpNote(tally.message);
    } else if (tally.data) {
        const data = tally.data;
        replaceContent(selectionPeopleList, ...listOf(data.people, data.more_people, data.total, 'person', 'name'));
        replaceContent(selectionTagsList, ...listOf(data.tags, data.more_tags, data.total, 'tag', 'tag'));
        if (selectionFoldersList) replaceContent(selectionFoldersList, ...foldersOf(data.folders));
        drawJumps(data);
    }
    lockBulkControls();
}

function dropPending() {
    const tally = state.tally;
    window.clearTimeout(tally.timer);
    tally.timer = 0;
    if (tally.controller) tally.controller.abort();
    tally.controller = null;
    tally.asked += 1;
}

/**
 * A write to a photo finished (the details panel's save, a pill's edit, the queue's done path): what a selected photo carries may have
 * changed without the selection changing, so a view's selection is counted again (250 ms later, once for a run of writes).
 */
export function photosWritten() {
    const lib = state.library;
    if (!lib || selectionCount() === 0) return;
    state.tally.key = '';
    selectionTallied();
}

/** Nothing is selected, or the view closed: nothing is counted, and what was out is let go. */
export function clearTally() {
    dropPending();
    const tally = state.tally;
    tally.data = null;
    tally.key = '';
    tally.status = 'idle';
}

/**
 * The selection of the open library view may have changed: count it again, 250 ms after the last change. The same selection (the page's
 * `version` of it) that was counted is not counted again, unless `photosWritten` ran.
 */
export function selectionTallied() {
    const tally = state.tally;
    const lib = state.library;
    const key = lib ? `${lib.token}:${lib.sel.version}` : '';
    if (!lib || !key) {
        clearTally();
        return;
    }
    if (tally.key === key && (tally.status === 'ready' || tally.status === 'counting')) {
        drawTally();
        return;
    }
    dropPending();
    tally.key = key;
    const asked = tally.asked;
    const request = tallyRequest();
    if (!request.ok) {
        tally.status = 'error';
        tally.message = 'Not counted: see the note above.';
        drawTally();
        return;
    }
    tally.status = 'counting';
    drawTally();
    tally.timer = window.setTimeout(() => {
        tally.timer = 0;
        const controller = new AbortController();
        tally.controller = controller;
        api.fetch('/api/library/selection/tally', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ selection: request.body }), signal: controller.signal,
        })
            .then(res => res.json().catch(() => ({})).then(body => {
                if (!res.ok) throw new Error((body && body.error) || `TagPup answered ${res.status}`);
                return body;
            }))
            .then(body => {
                // Dropped when the selection moved on, or the view did.
                if (asked !== tally.asked || lib !== state.library) return;
                tally.controller = null;
                tally.data = {
                    total: Number.isFinite(body.total) ? body.total : 0,
                    tags: Array.isArray(body.tags) ? body.tags : [], more_tags: body.more_tags || 0,
                    people: Array.isArray(body.people) ? body.people : [], more_people: body.more_people || 0,
                    folders: body.folders && typeof body.folders === 'object' ? body.folders : null,
                };
                tally.status = 'ready';
                drawTally();
            })
            .catch(err => {
                if (err.name === 'AbortError' || asked !== tally.asked || lib !== state.library) return;
                tally.controller = null;
                tally.status = 'error';
                tally.message = `Could not count what these photos carry (${err.message}).`;
                tally.key = '';
                drawTally();
            });
    }, TALLY_DELAY_MS);
}
