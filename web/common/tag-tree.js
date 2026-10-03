/**
 * The tag editor's tree: its nodes, in alphabetical order, and the elements that show
 * them, kept in step by small patches instead of a rebuild.
 *
 * The editor (web/common/tag-editor.js) used to empty the tree and build every node again
 * after each action, from the server's list, which comes in the order the nodes were
 * made. So a rename collapsed the tree, sent the scroll to the top and dropped the focus,
 * and showed nothing until the server had rewritten the photos. Now:
 *
 *   - Every level is in alphabetical order: case and accents ignored, digits as numbers
 *     ("Trip 3" before "Trip 10"), ties broken by the exact name and then the id, so the
 *     order is total and a reload does not reshuffle it. It is the display's only; the
 *     stored order and the server's are not touched.
 *   - What the person has open -- which nodes are expanded, the scroll, the focus, the
 *     search -- belongs to the tree and survives render().
 *   - An action is a patch: rename(), add(), remove(), setFlags() change the node list
 *     first and then only the elements that depend on it (a node's row, its branch's tag
 *     paths, its place among its siblings, its parent's expander). reconcile() brings the
 *     tree in step with a list read from the server the same way, by what differs.
 *
 * It knows nothing of the server. The editor passes in what a click on a row asks for:
 *   handlers.add(id), .rename(id), .remove(id), .flag(id, fields)
 * and keeps what is in flight with setBusy(id, text), a marker on the node's row.
 *
 * A node is { id, tag, name, parent_id, has_face, hidden_from_autocomplete, usage_count }
 * as /api/taxonomy/tree answers; a node the editor has made and the server has not yet
 * answered has a text id ('new-1') and `temp: true`.
 */
import { joinTag } from './vocabulary.js';

const NAME_ORDER = new Intl.Collator(undefined, { sensitivity: 'base', numeric: true });
const FLASH_MS = 1600;

/** Numbers (the server's ids) before text (the editor's own), each in its own order. */
function compareIds(a, b) {
    if (typeof a === 'number' && typeof b === 'number') return a - b;
    if (typeof a === 'number') return -1;
    if (typeof b === 'number') return 1;
    return a < b ? -1 : a > b ? 1 : 0;
}

/** The order tags are listed in: alphabetical, then the exact spelling, so it is total. */
export function compareTagNames(a, b) {
    const left = String(a ?? '');
    const right = String(b ?? '');
    return NAME_ORDER.compare(left, right) || (left < right ? -1 : left > right ? 1 : 0);
}

/** Siblings' order: by name, then by id. */
export function compareNodes(a, b) {
    return compareTagNames(a.name, b.name) || compareIds(a.id, b.id);
}

function element(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
}

function setText(el, text) {
    if (el.textContent !== text) el.textContent = text;
}

/** Whether two names are one to the server: trimmed, case ignored (tagpup.core.vocabulary.key). */
export function sameName(a, b) {
    return String(a ?? '').trim().toLowerCase() === String(b ?? '').trim().toLowerCase();
}

const FIELDS = ['tag', 'name', 'has_face', 'hidden_from_autocomplete', 'usage_count'];

/**
 * The tree inside `container`. Returns what the editor drives it with; see the header.
 */
export function createTagTree(container, handlers) {
    const nodes = new Map();      // id -> node
    const kids = new Map();       // parent id (null: the roots) -> its nodes, in order
    const lis = new Map();        // id -> its <li>
    const expanded = new Set();   // ids of the nodes shown open
    const busy = new Map();       // id -> what is being done to it, in words
    const flashes = new Map();    // id -> timer clearing its highlight
    const view = { filter: '' };

    const rootList = element('ul', 'taxonomy-tree-list');
    const emptyNote = element('div', 'tag-editor-empty',
        'No tags match your search or taxonomy is empty.');
    emptyNote.hidden = true;
    container.replaceChildren(rootList, emptyNote);

    // ---- the node list ------------------------------------------------------------

    function keyUnder(node) {
        return node.parent_id !== null && nodes.has(node.parent_id) ? node.parent_id : null;
    }

    function link(node) {
        node.placedUnder = keyUnder(node);
        let list = kids.get(node.placedUnder);
        if (!list) kids.set(node.placedUnder, list = []);
        let i = list.length;
        while (i > 0 && compareNodes(list[i - 1], node) > 0) i--;
        list.splice(i, 0, node);
    }

    function unlink(node) {
        const list = kids.get(node.placedUnder);
        if (!list) return;
        const i = list.indexOf(node);
        if (i >= 0) list.splice(i, 1);
        if (list.length === 0) kids.delete(node.placedUnder);
    }

    function childrenOf(id) {
        return kids.get(id) || [];
    }

    function parentOf(node) {
        return node.placedUnder === null ? null : nodes.get(node.placedUnder) || null;
    }

    function* branch(node) {
        yield node;
        for (const child of childrenOf(node.id)) yield* branch(child);
    }

    /** Every node, in the order they are shown. */
    function ordered() {
        const found = [];
        for (const root of childrenOf(null)) found.push(...branch(root));
        return found;
    }

    function underBusy(node) {
        for (let at = node; at; at = parentOf(at)) {
            if (busy.has(at.id)) return true;
        }
        return false;
    }

    // ---- focus and scroll ---------------------------------------------------------

    function focusKey() {
        const active = document.activeElement;
        if (!active || !container.contains(active)) return null;
        const li = active.closest('li.taxonomy-node');
        if (!li) return null;
        return { id: li.tagId, control: active.dataset.control || null, el: active };
    }

    function restoreFocus(key) {
        if (!key || document.activeElement === key.el) return;
        const li = lis.get(key.id);
        if (!li || !key.control) return;
        const target = li.firstElementChild.querySelector(`[data-control="${key.control}"]`);
        if (target) target.focus({ preventScroll: true });
    }

    function scrollTo(id) {
        const li = lis.get(id);
        const row = li && li.firstElementChild;
        if (row && typeof row.scrollIntoView === 'function') row.scrollIntoView({ block: 'nearest' });
    }

    function flash(id) {
        const li = lis.get(id);
        if (!li) return;
        const row = li.firstElementChild;
        row.classList.add('taxonomy-flash');
        clearTimeout(flashes.get(id));
        flashes.set(id, setTimeout(() => {
            row.classList.remove('taxonomy-flash');
            flashes.delete(id);
        }, FLASH_MS));
    }

    // ---- the elements -------------------------------------------------------------

    function toggle(id) {
        const node = nodes.get(id);
        const li = lis.get(id);
        if (!node || !li || !li.sublist) return;
        if (expanded.has(id)) expanded.delete(id);
        else expanded.add(id);
        li.sublist.classList.toggle('hidden', !expanded.has(id));
        paintExpander(node);
    }

    function control(row, tag, className, text, name) {
        const el = element(tag, className, text);
        el.dataset.control = name;
        row.appendChild(el);
        return el;
    }

    /** One node's <li>, its row built to be repainted: every part is always there. */
    function buildRow(node) {
        const li = element('li', 'taxonomy-node');
        li.tagId = node.id;
        li.setAttribute('data-id', node.id);
        li.sublist = null;
        const content = element('div', 'taxonomy-node-content');

        const expander = element('span', 'taxonomy-node-expander');
        expander.onclick = (e) => {
            e.stopPropagation();
            toggle(li.tagId);
        };
        const name = element('span', 'taxonomy-node-name');
        const meta = element('span', 'taxonomy-node-meta');
        const marker = element('span', 'taxonomy-node-busy');
        marker.setAttribute('role', 'status');
        marker.hidden = true;
        content.append(expander, name, meta, marker);

        const actions = element('div', 'taxonomy-node-actions');

        const faceLabel = element('label', 'taxonomy-face-label');
        faceLabel.style.cssText = 'display: inline-flex; align-items: center; gap: 6px; font-size: 12px; margin-right: 12px;';
        faceLabel.title = 'Enable face matching for this category (People/Pets)';
        const faceSwitch = element('label', 'switch');
        const face = element('input');
        face.type = 'checkbox';
        face.dataset.control = 'face';
        face.onchange = (e) => handlers.flag(li.tagId, { has_face: e.target.checked ? 1 : 0 });
        faceSwitch.append(face, element('span', 'slider'));
        faceLabel.append(element('span', null, 'Face Matching:'), faceSwitch);

        const badge = element('span', 'taxonomy-node-badge badge-people', 'Face Match');

        const hideLabel = element('label');
        hideLabel.style.cssText = 'display: inline-flex; align-items: center; gap: 4px; font-size: 12px; margin-right: 8px;';
        hideLabel.title = 'Hide this tag and its sub-tags from autocomplete popups for new images';
        const hide = element('input');
        hide.type = 'checkbox';
        hide.dataset.control = 'hide';
        hide.onchange = (e) => handlers.flag(li.tagId, { hidden_from_autocomplete: e.target.checked ? 1 : 0 });
        hideLabel.append(hide, element('span', null, 'Hide'));

        actions.append(faceLabel, badge, hideLabel);
        const small = (name, text, onclick) => {
            const button = control(actions, 'button', 'btn btn-secondary btn-sm', text, name);
            button.style.padding = '2px 6px';
            button.style.fontSize = '11px';
            button.onclick = (e) => {
                e.stopPropagation();
                onclick(li.tagId);
            };
            return button;
        };
        small('add', '➕ Add', (id) => handlers.add(id));
        small('rename', '✏️ Rename', (id) => handlers.rename(id));
        const del = small('delete', '🗑️', (id) => handlers.remove(id));
        del.style.backgroundColor = 'rgba(239, 68, 68, 0.1)';
        del.style.color = '#f87171';
        del.style.borderColor = 'rgba(239, 68, 68, 0.2)';

        content.appendChild(actions);
        li.appendChild(content);
        li.parts = { expander, name, meta, marker, faceLabel, badge, face, hide, del };
        return li;
    }

    function paintExpander(node) {
        const li = lis.get(node.id);
        if (!li) return;
        const { expander } = li.parts;
        const has = childrenOf(node.id).length > 0;
        setText(expander, has ? (expanded.has(node.id) ? '▼' : '▶') : '•');
        expander.style.cursor = has ? '' : 'default';
    }

    /** Make a node's row say what the node holds now. Touches only what differs. */
    function paintRow(node) {
        const li = lis.get(node.id);
        if (!li) return;
        const p = li.parts;
        const root = node.parent_id === null;
        setText(p.name, node.name);
        setText(p.meta, `${node.usage_count} ${node.usage_count === 1 ? 'img' : 'imgs'}`);
        p.faceLabel.hidden = !root;
        p.badge.hidden = root || node.has_face !== 1;
        p.face.checked = node.has_face === 1;
        p.hide.checked = node.hidden_from_autocomplete === 1;
        p.del.title = `Delete ${node.tag}`;
        const doing = busy.get(node.id);
        p.marker.hidden = !doing;
        setText(p.marker, doing || '');
        li.classList.toggle('taxonomy-busy', Boolean(doing));
        if (doing) li.setAttribute('aria-busy', 'true');
        else li.removeAttribute('aria-busy');
        paintExpander(node);
    }

    function ensureSublist(node) {
        const li = lis.get(node.id);
        if (!li.sublist) {
            li.sublist = element('ul', 'taxonomy-sublist' + (expanded.has(node.id) ? '' : ' hidden'));
            li.appendChild(li.sublist);
        }
        return li.sublist;
    }

    function dropSublistIfEmpty(node) {
        const li = lis.get(node.id);
        if (!li || !li.sublist || childrenOf(node.id).length > 0) return;
        li.sublist.remove();
        li.sublist = null;
        paintExpander(node);
    }

    /**
     * Put a node's <li> where its order puts it, under its parent. True if it had to move.
     * Builds nothing: the <li> is there, in the right list or in none.
     */
    function place(node) {
        const li = lis.get(node.id);
        const into = node.placedUnder === null ? rootList : ensureSublist(nodes.get(node.placedUnder));
        const list = kids.get(node.placedUnder);
        const after = list[list.indexOf(node) + 1];
        const before = after ? lis.get(after.id) : null;
        if (li.parentNode === into && li.nextElementSibling === before) return false;
        into.insertBefore(li, before);
        return true;
    }

    function buildBranch(node) {
        const li = buildRow(node);
        lis.set(node.id, li);
        const list = childrenOf(node.id);
        if (list.length > 0) {
            const sublist = ensureSublist(node);
            for (const child of list) sublist.appendChild(buildBranch(child));
        }
        paintRow(node);
        return li;
    }

    /** Every row but the nodes' own state is gone and made again; used when the list is replaced. */
    function render() {
        const key = focusKey();
        const top = container.scrollTop;
        lis.clear();
        const built = document.createDocumentFragment();
        for (const root of childrenOf(null)) built.appendChild(buildBranch(root));
        rootList.replaceChildren(built);
        applyFilter(view.filter);
        container.scrollTop = top;
        restoreFocus(key);
    }

    // ---- the search ---------------------------------------------------------------

    function filterBranch(node, text) {
        let shown = !text || node.tag.toLowerCase().includes(text);
        for (const child of childrenOf(node.id)) {
            if (filterBranch(child, text)) shown = true;
        }
        lis.get(node.id).hidden = !shown;
        return shown;
    }

    function paintEmpty() {
        emptyNote.hidden = [...rootList.children].some(li => !li.hidden);
    }

    /** Show only the nodes whose tag holds `text`, and the ones above them. */
    function applyFilter(text) {
        view.filter = String(text || '').toLowerCase().trim();
        for (const root of childrenOf(null)) filterBranch(root, view.filter);
        paintEmpty();
    }

    function unhide(id) {
        for (let at = nodes.get(id); at; at = parentOf(at)) {
            const li = lis.get(at.id);
            if (li) li.hidden = false;
        }
        paintEmpty();
    }

    // ---- patches ------------------------------------------------------------------

    /** The tree's list replaced by `list`: every node built, the tree shown. */
    function load(list) {
        nodes.clear();
        kids.clear();
        for (const node of list) nodes.set(node.id, { ...node });
        for (const node of nodes.values()) link(node);
        render();
    }

    /** Open the nodes above `id` so it shows, bring it into view, and mark it. */
    function reveal(id, { mark = true } = {}) {
        const node = nodes.get(id);
        if (!node) return;
        for (let at = parentOf(node); at; at = parentOf(at)) {
            expanded.add(at.id);
            const li = lis.get(at.id);
            if (li && li.sublist) li.sublist.classList.remove('hidden');
            paintExpander(at);
        }
        unhide(id);
        scrollTo(id);
        if (mark) flash(id);
    }

    function retag(node) {
        const parent = parentOf(node);
        node.tag = parent ? joinTag(parent.tag, node.name) : node.name;
        for (const child of childrenOf(node.id)) retag(child);
    }

    /**
     * Give a node a new name: its row, the tag paths of its branch, and its place among
     * its siblings, which is where the new name sorts. Returns whether it moved. The
     * focus stays on the control it was on.
     */
    function rename(id, name) {
        const node = nodes.get(id);
        if (!node) return false;
        const key = focusKey();
        unlink(node);
        node.name = name;
        link(node);
        retag(node);
        for (const member of branch(node)) paintRow(member);
        const moved = place(node);
        restoreFocus(key);
        if (moved) {
            scrollTo(id);
            flash(id);
        }
        return moved;
    }

    /** A new node, under its parent in its order. The parent opens when `show` is set. */
    function add(node, { show = false } = {}) {
        const key = focusKey();
        const mine = { ...node };
        nodes.set(mine.id, mine);
        link(mine);
        const li = buildRow(mine);
        lis.set(mine.id, li);
        paintRow(mine);
        place(mine);
        const parent = parentOf(mine);
        if (parent) paintExpander(parent);
        if (view.filter && !mine.tag.toLowerCase().includes(view.filter) && !show) li.hidden = true;
        else unhide(mine.id);
        restoreFocus(key);
        if (show) reveal(mine.id);
        return mine;
    }

    /** Take a node and its branch out. Returns the ids gone. */
    function remove(id) {
        const node = nodes.get(id);
        if (!node) return [];
        const key = focusKey();
        const parent = parentOf(node);
        const gone = [...branch(node)];
        unlink(node);
        for (const member of gone) {
            nodes.delete(member.id);
            kids.delete(member.id);
            expanded.delete(member.id);
            busy.delete(member.id);
            clearTimeout(flashes.get(member.id));
            flashes.delete(member.id);
        }
        const li = lis.get(id);
        for (const member of gone) lis.delete(member.id);
        if (li) li.remove();
        if (parent) dropSublistIfEmpty(parent);
        paintEmpty();
        restoreFocus(key);
        return gone.map(member => member.id);
    }

    /** Set a node's flags, and its branch's, as the server does; fields not given stay. */
    function setFlags(id, fields) {
        const node = nodes.get(id);
        if (!node) return;
        for (const member of branch(node)) {
            if (fields.has_face !== undefined) member.has_face = fields.has_face;
            if (fields.hidden_from_autocomplete !== undefined) {
                member.hidden_from_autocomplete = fields.hidden_from_autocomplete;
            }
            paintRow(member);
        }
    }

    /** The flags of a node and its branch, to put back if the server refuses. */
    function flagsOf(id) {
        const node = nodes.get(id);
        const found = new Map();
        if (!node) return found;
        for (const member of branch(node)) {
            found.set(member.id, {
                has_face: member.has_face, hidden_from_autocomplete: member.hidden_from_autocomplete,
            });
        }
        return found;
    }

    function restoreFlags(saved) {
        for (const [id, flags] of saved) {
            const node = nodes.get(id);
            if (!node) continue;
            Object.assign(node, flags);
            paintRow(node);
        }
    }

    function setBusy(id, text) {
        if (text) busy.set(id, text);
        else busy.delete(id);
        const node = nodes.get(id);
        if (node) paintRow(node);
    }

    /** A node the editor made takes the id the server gave it. */
    function rekey(oldId, newId, fields = {}) {
        const node = nodes.get(oldId);
        if (!node || nodes.has(newId)) return node;
        const li = lis.get(oldId);
        nodes.delete(oldId);
        node.id = newId;
        delete node.temp;
        Object.assign(node, fields);
        nodes.set(newId, node);
        lis.delete(oldId);
        lis.set(newId, li);
        li.tagId = newId;
        li.setAttribute('data-id', newId);
        if (busy.has(oldId)) {
            busy.set(newId, busy.get(oldId));
            busy.delete(oldId);
        }
        if (expanded.delete(oldId)) expanded.add(newId);
        const list = kids.get(oldId);
        if (list) {
            kids.delete(oldId);
            kids.set(newId, list);
            for (const child of list) {
                child.parent_id = newId;
                child.placedUnder = newId;
            }
        }
        paintRow(node);
        return node;
    }

    /**
     * Bring the tree in step with `list`, a reading of the server's, by what differs:
     * nodes it has that we do not are added, ours it lacks are removed, and the ones that
     * changed are patched, a rename moving the node. A node with something in flight, and
     * the branch under it, is left as the editor shows it; the answer settles it. Returns
     * how many nodes it touched.
     */
    function reconcile(list) {
        const key = focusKey();
        const incoming = new Map(list.map(node => [node.id, node]));
        let touched = 0;

        // A node the editor made, whose answer is in, takes the id the server holds it by.
        for (let progress = true; progress;) {
            progress = false;
            for (const node of [...nodes.values()]) {
                if (!node.temp || busy.has(node.id) || typeof node.parent_id === 'string') continue;
                const match = list.find(c => c.parent_id === node.parent_id && sameName(c.name, node.name));
                if (!match) continue;
                if (nodes.has(match.id)) remove(node.id);
                else rekey(node.id, match.id);
                touched++;
                progress = true;
            }
        }

        for (const node of [...nodes.values()]) {
            if (!nodes.has(node.id) || underBusy(node)) continue;
            const real = incoming.get(node.id);
            if (!real || real.parent_id !== node.parent_id) {
                touched += remove(node.id).length;
            }
        }

        // Parents first; one whose parent never comes is shown as a root, as it always was.
        let waiting = list.filter(node => !nodes.has(node.id));
        while (waiting.length > 0) {
            const ready = waiting.filter(node => node.parent_id === null || nodes.has(node.parent_id));
            const next = ready.length > 0 ? ready : waiting;
            for (const node of next) {
                add(node);
                if (view.filter) applyFilterTo(node.id);
                touched++;
            }
            waiting = waiting.filter(node => !nodes.has(node.id));
        }

        for (const real of list) {
            const node = nodes.get(real.id);
            if (!node || underBusy(node)) continue;
            const differs = FIELDS.some(field => node[field] !== real[field]);
            if (!differs) continue;
            const renamed = node.name !== real.name;
            if (renamed) unlink(node);
            for (const field of FIELDS) node[field] = real[field];
            if (renamed) {
                link(node);
                place(node);
            }
            paintRow(node);
            touched++;
        }
        paintEmpty();
        restoreFocus(key);
        return touched;
    }

    /** A node reconcile added, under the search that is typed: shown only if it or what is under it matches. */
    function applyFilterTo(id) {
        const node = nodes.get(id);
        const li = lis.get(id);
        if (!node || !li) return;
        const matches = node.tag.toLowerCase().includes(view.filter);
        li.hidden = !matches;
        if (matches) unhide(id);
    }

    /** Where focus goes when `id` is removed: the next node beside it, else the one before, else its parent. */
    function neighbour(id) {
        const node = nodes.get(id);
        if (!node) return null;
        const list = childrenOf(node.placedUnder);
        const i = list.indexOf(node);
        const found = list[i + 1] || list[i - 1] || parentOf(node);
        return found ? found.id : null;
    }

    function focusOn(id, controlName) {
        const li = lis.get(id);
        const target = li && li.firstElementChild.querySelector(`[data-control="${controlName}"]`);
        if (target) target.focus({ preventScroll: true });
    }

    return {
        load, render, reconcile, applyFilter, reveal, rename, add, remove, setFlags, flagsOf,
        restoreFlags, setBusy, rekey, ordered, neighbour, focusOn, flash,
        node: (id) => nodes.get(id),
        has: (id) => nodes.has(id),
        isBusy: (id) => busy.has(id),
        busyCount: () => busy.size,
        repaint: (id) => { const node = nodes.get(id); if (node) paintRow(node); },
        siblingNamed: (parentId, name) =>
            childrenOf(nodes.has(parentId) ? parentId : null).find(n => sameName(n.name, name)) || null,
        size: () => nodes.size,
    };
}
