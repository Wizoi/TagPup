// TagPup's page: a navigator section's rows on the page, and the keys that move through them (docs/ARCHITECTURE.md,
// phase 9c). The rows are navigator-model.js's; this draws them as the ARIA tree (or listbox) pattern asks and
// keeps what the person has: the focus, the scroll, an element for a row that stays.
//
// A tree is one tab stop (roving tabindex): the row that is current has tabindex 0, the others -1; the arrow keys
// move the current row (Up/Down through what is drawn, Right opens a closed row or goes to its first child, Left
// closes an open row or goes to its parent, Home/End), Enter or Space opens the row's source. A row is an element
// of its own for as long as its id is drawn: a count that changes, a row opened above it, a section loaded again
// patch the elements that are there, and the focused one is never taken from under the person.
import { buildElement } from './common/dom.js';

const drawn = new WeakMap();   // the container -> Map(row id -> its element): bookkeeping of the draw, not the page's state

function makeRow(role) {
    return buildElement('div', { className: 'nav-row', attrs: { role, tabindex: '-1' } }, [
        buildElement('span', { className: 'nav-twisty', attrs: { 'aria-hidden': 'true' } }),
        buildElement('span', { className: 'nav-label' }),
        buildElement('span', { className: 'nav-hint' }),
        buildElement('span', { className: 'nav-count' }),
    ]);
}

function setText(el, text) {
    if (el.textContent !== text) el.textContent = text;
}

function patchRow(el, row, selected, current, tree) {
    el.dataset.row = row.id;
    el.style.setProperty('--nav-level', String(row.level));
    if (tree) el.setAttribute('aria-level', String(row.level));
    if (row.expandable) el.setAttribute('aria-expanded', row.expanded ? 'true' : 'false');
    else el.removeAttribute('aria-expanded');
    el.setAttribute('aria-selected', selected ? 'true' : 'false');
    el.setAttribute('aria-label', row.aria);
    el.tabIndex = current ? 0 : -1;
    el.classList.toggle('current', selected);
    el.classList.toggle('grouping', !row.spec);
    if (el.title !== row.title) el.title = row.title;
    const [twisty, label, hint, count] = el.children;
    setText(twisty, row.expandable ? (row.expanded ? '▾' : '▸') : '');
    twisty.classList.toggle('empty', !row.expandable);
    setText(label, row.label);
    setText(hint, row.hint || '');
    hint.classList.toggle('hidden', !row.hint);
    setText(count, row.count.toLocaleString());
}

/**
 * Draw `rows` in `container`: an element kept for each id that stays, made for a new one, dropped for one that
 * went; the DOM in the rows' order, moving only what is out of place (a moved element loses its focus).
 * `selectedId` is the row that is the view open; `currentId` the one the arrow keys are on (the tab stop), or
 * else the selected one, else the first. `tree`: the container is a tree (levels), else a listbox.
 */
export function paintRows(container, rows, { selectedId = null, currentId = null, tree = true } = {}) {
    let held = drawn.get(container);
    if (!held) {
        held = new Map();
        drawn.set(container, held);
    }
    const role = tree ? 'treeitem' : 'option';
    const stop = rows.some(r => r.id === currentId) ? currentId
        : rows.some(r => r.id === selectedId) ? selectedId : (rows[0] ? rows[0].id : null);
    const next = new Map();
    for (const row of rows) {
        const el = held.get(row.id) || makeRow(role);
        patchRow(el, row, row.id === selectedId, row.id === stop, tree);
        next.set(row.id, el);
    }
    for (const [id, el] of held) if (!next.has(id)) el.remove();
    let cursor = container.firstChild;
    for (const el of next.values()) {
        if (cursor === el) cursor = cursor.nextSibling;
        else container.insertBefore(el, cursor);
    }
    while (cursor) {
        const following = cursor.nextSibling;
        container.removeChild(cursor);
        cursor = following;
    }
    drawn.set(container, next);
    return next;
}

/** The element drawn for a row id in a container, or null. */
export function rowElement(container, id) {
    const held = drawn.get(container);
    return held && held.get(id) ? held.get(id) : null;
}

/**
 * What a key does in a tree (or listbox) whose rows are `rows` and whose current row is `currentId`:
 * { focus: id } | { open: id } | { close: id } | { activate: id } | null for a key that is not the tree's.
 * Pure, so that the keys can be tested without a page.
 */
export function treeKeyAction(key, rows, currentId, tree = true) {
    if (!rows.length) return null;
    const at = rows.findIndex(r => r.id === currentId);
    const row = at >= 0 ? rows[at] : null;
    const last = rows.length - 1;
    switch (key) {
        case 'ArrowDown':
            return { focus: rows[at < 0 ? 0 : Math.min(at + 1, last)].id };
        case 'ArrowUp':
            return { focus: rows[at < 0 ? 0 : Math.max(at - 1, 0)].id };
        case 'Home':
            return { focus: rows[0].id };
        case 'End':
            return { focus: rows[last].id };
        case 'ArrowRight':
            if (!tree || !row) return null;
            if (row.expandable && !row.expanded) return { open: row.id };
            if (row.expandable && row.expanded && at < last && rows[at + 1].level > row.level) return { focus: rows[at + 1].id };
            return null;
        case 'ArrowLeft': {
            if (!tree || !row) return null;
            if (row.expandable && row.expanded) return { close: row.id };
            for (let i = at - 1; i >= 0; i--) {
                if (rows[i].level < row.level) return { focus: rows[i].id };
            }
            return null;
        }
        case 'Enter':
        case ' ':
            return row ? { activate: row.id } : null;
        default:
            return null;
    }
}

/** The tab to go to for an arrow key in a tab list of `count` tabs from tab `at`: an index, or -1 for none. */
export function tabKeyTarget(key, at, count) {
    if (key === 'ArrowRight') return (at + 1) % count;
    if (key === 'ArrowLeft') return (at - 1 + count) % count;
    if (key === 'Home') return 0;
    if (key === 'End') return count - 1;
    return -1;
}
