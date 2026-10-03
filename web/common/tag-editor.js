/**
 * The tag editor: the tag tree, its search, and adding, renaming, flagging and deleting
 * its nodes. Both pages open it from their gear (web/common/gear.js), over the tree's
 * routes, which both apps serve (tagpup/web/taxonomy_routes.py).
 *
 * It was TagPup's Manage Tags dialog -- web/tagpup/taxonomy.js and its markup in
 * TagPup's index.html -- and TagTuner had no way to edit the tree (docs/ARCHITECTURE.md,
 * phase 7.6). The markup is built here, once, so neither page carries a copy; its look
 * is web/common/tag-editor.css, which each page links.
 *
 * What differs between the pages is handed in to wireTagEditor:
 *   nodes()     the tree as the page holds it; without it the editor fetches its own
 *               each time it opens.
 *   reload()    reads the tree again (a promise); by default the editor's own fetch.
 *   status(text, busy)   the page's status line; without it, the editor's footer.
 *   edited({ treeChanged, photosChanged })   after an edit: the tree was read again,
 *               and/or photos were rewritten, so what the page shows of them is old.
 */
import { api } from './api.js';
import { joinTag, nameProblem, tagProblem } from './vocabulary.js';
import { createTagTree } from './tag-tree.js';

/**
 * The page's hooks and the editor's elements, once wireTagEditor has run. `view` is the
 * tree (web/common/tag-tree.js), which holds the nodes as shown, the open branches, and
 * the patches; it is made on the first opening and kept, so what was open is open still.
 */
/** What makes a name more than one level, as the server reads it (tagpup.core.vocabulary.segments). */
const PATH_SEPARATORS = /[/|\\]/;
const NOTICE_MS = 6000;

const tagEditor = {
    hooks: null,
    nodes: [],
    view: null,
    scrolled: 0,
    notice: null,
    noticeTimer: null,
    tail: Promise.resolve(),
    inflight: 0,
    failed: false,
    made: 0,
    overlay: null,
    search: null,
    tree: null,
    footerStatus: null,
    opener: null,
    conflictOpen: false,
};

function editorNodes() {
    return tagEditor.hooks.nodes ? tagEditor.hooks.nodes() || [] : tagEditor.nodes;
}

function reloadTree() {
    if (tagEditor.hooks.reload) return Promise.resolve(tagEditor.hooks.reload());
    return api.json('/api/taxonomy/tree')
        .then(data => { tagEditor.nodes = Array.isArray(data) ? data : []; })
        .catch(err => {
            console.error('Could not load the taxonomy:', err);
            tagEditor.nodes = [];
        });
}

function editorSay(text, busy = false) {
    if (tagEditor.hooks.status) {
        tagEditor.hooks.status(text, busy);
    } else if (tagEditor.footerStatus) {
        tagEditor.footerStatus.textContent = text === 'Ready' ? '' : text;
    }
}

/**
 * Bring the tree shown in step with the tree the page holds: by what differs, so what
 * is open, scrolled to and focused stays. A read that failed answers an empty list,
 * which is not an empty tree: it is not shown.
 */
function syncTree() {
    const list = editorNodes();
    if (list.length === 0 && tagEditor.view.size() > 0) return;
    tagEditor.view.reconcile(list);
}


function editorElement(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
}

/** The editor's markup, built once and put at the end of the page's body. */
function buildTagEditor() {
    const overlay = editorElement('div', 'tag-editor');
    overlay.id = 'taxonomy-modal';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', 'Tag editor');

    const dialog = editorElement('div', 'tag-editor-dialog');
    const header = editorElement('div', 'tag-editor-header');
    header.appendChild(editorElement('h2', null, 'Tags Taxonomy Tree Manager'));
    const btnClose = editorElement('button', 'tag-editor-close', '×');
    btnClose.id = 'btn-close-taxonomy';
    btnClose.title = 'Close';
    btnClose.setAttribute('aria-label', 'Close');
    header.appendChild(btnClose);

    const body = editorElement('div', 'tag-editor-body');
    const controls = editorElement('div', 'taxonomy-controls');
    const search = editorElement('input', 'taxonomy-search-input');
    search.id = 'taxonomy-search-input';
    search.type = 'text';
    search.placeholder = 'Search taxonomy tags...';
    const btnAddRoot = editorElement('button', 'btn btn-primary', '➕ Add Root Category');
    btnAddRoot.id = 'btn-taxonomy-add-root';
    controls.append(search, btnAddRoot);
    const note = editorElement('div', 'tag-editor-notice');
    note.setAttribute('role', 'status');
    tagEditor.notice = note;
    const tree = editorElement('div', 'tag-editor-tree');
    tree.id = 'taxonomy-tree-container';
    body.append(controls, note, tree);

    const footer = editorElement('div', 'tag-editor-footer');
    if (!tagEditor.hooks.status) {
        tagEditor.footerStatus = editorElement('span', 'tag-editor-status');
        tagEditor.footerStatus.setAttribute('role', 'status');
        footer.appendChild(tagEditor.footerStatus);
    }
    const btnCloseFooter = editorElement('button', 'btn btn-secondary', 'Close');
    btnCloseFooter.id = 'btn-close-taxonomy-footer';
    footer.appendChild(btnCloseFooter);

    dialog.append(header, body, footer);
    overlay.appendChild(dialog);
    document.body.appendChild(overlay);

    btnClose.addEventListener('click', closeTagEditor);
    btnCloseFooter.addEventListener('click', closeTagEditor);
    btnAddRoot.addEventListener('click', () => {
        const name = prompt("Enter name of new root category:");
        if (name && name.trim()) {
            const hasFace = confirm(`Enable Face Matching for "${name}" (e.g. for people or pets)?`);
            createTaxonomyNode(name.trim(), null, hasFace ? 1 : 0);
        }
    });
    search.addEventListener('input', () => {
        tagEditor.view.applyFilter(search.value);
    });
    overlay.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape' || tagEditor.conflictOpen) return;
        e.preventDefault();
        e.stopPropagation();
        closeTagEditor();
    });
    // The pages' own keys -- TagPup's arrows, TagTuner's sidebar -- are left alone
    // while the editor is open, wherever its focus is: the pages ask dialogOpen()
    // (web/common/dialog.js), which the open editor answers.

    tagEditor.overlay = overlay;
    tagEditor.search = search;
    tagEditor.tree = tree;
    tagEditor.view = createTagTree(tree, {
        add: askNewChild,
        rename: askNewName,
        remove: id => deleteTaxonomyNode(id, tagEditor.view.node(id).tag),
        flag: updateTaxonomyNode,
    });
}

/** The Add button of a node: ask for the name, then make it. */
function askNewChild(id) {
    const node = tagEditor.view.node(id);
    const childName = prompt(`Enter name of new subtag under "${node.tag}":`);
    if (childName && childName.trim()) createTaxonomyNode(childName.trim(), id);
}

/** The Rename button of a node: ask for the name, then change it. */
function askNewName(id) {
    const node = tagEditor.view.node(id);
    const newName = prompt(`Enter new name for tag "${node.name}":`, node.name);
    if (newName && newName.trim() && newName.trim() !== node.name) renameTaxonomyNode(id, newName.trim());
}

/**
 * Build the editor into the page, with the page's hooks (above). Returns what the
 * page's gear calls: { open, close, isOpen }.
 */
export function wireTagEditor(hooks = {}) {
    tagEditor.hooks = { edited: () => {}, ...hooks };
    buildTagEditor();
    return { open: openTagEditor, close: closeTagEditor, isOpen: () => tagEditor.overlay.classList.contains('active') };
}

/** Show the editor. The focus goes back to what had it when the editor closes. */
export function openTagEditor() {
    if (!tagEditor.overlay) return;
    tagEditor.opener = document.activeElement;
    tagEditor.overlay.classList.add('active');
    const view = tagEditor.view;
    if (view.size() === 0) {
        view.load(editorNodes());
        view.applyFilter(tagEditor.search.value);
    } else {
        syncTree();
    }
    tagEditor.tree.scrollTop = tagEditor.scrolled;
    tagEditor.search.focus();
    // A page that keeps the tree has it already; otherwise it is read now.
    if (!tagEditor.hooks.nodes) reloadTree().then(syncTree);
}

export function closeTagEditor() {
    if (!tagEditor.overlay) return;
    tagEditor.scrolled = tagEditor.tree.scrollTop;
    tagEditor.overlay.classList.remove('active');
    const opener = tagEditor.opener;
    tagEditor.opener = null;
    if (opener && typeof opener.focus === 'function' && document.contains(opener)) opener.focus();
}

/**
 * Settle the tree's own state after the server answered, then the page's: read the tree
 * again, show what differs from what is shown, and tell the page. `view` is the tree the
 * action was made on.
 */
function afterTreeEdit(photosChanged, view = tagEditor.view) {
    return reloadTree().then(() => {
        if (view === tagEditor.view) syncTree();
        tagEditor.hooks.edited({ treeChanged: true, photosChanged });
    });
}

/** A failed or refused action: the tree is read again, quietly, so what is shown is the server's. */
function settleAfterFailure(view) {
    return reloadTree().then(() => {
        if (view === tagEditor.view) syncTree();
    });
}

/** Say something to the person, in the editor, where they are looking. Cleared after a while. */
function notice(text) {
    if (!tagEditor.notice) return;
    tagEditor.notice.textContent = text;
    clearTimeout(tagEditor.noticeTimer);
    if (text) tagEditor.noticeTimer = setTimeout(() => { tagEditor.notice.textContent = ''; }, NOTICE_MS);
}

/** Back to Ready when nothing is in flight. */
function settled() {
    if (tagEditor.inflight === 0 && !tagEditor.failed) editorSay('Ready');
}

/**
 * Run `job` after the ones asked before it. The server rewrites photo files for a rename
 * or a delete, and two of those running at once -- a branch and a node in it -- would
 * write the same file twice at the same time; so they go one at a time, in the order
 * they were asked. What is shown does not wait: each action shows its result at once.
 */
function enqueue(job) {
    if (tagEditor.inflight === 0) tagEditor.failed = false;
    tagEditor.inflight += 1;
    const run = tagEditor.tail.then(job).catch((err) => {
        console.error(err);
        tagEditor.failed = true;
        editorSay('Error');
    }).then(() => {
        tagEditor.inflight -= 1;
        settled();
    });
    tagEditor.tail = run;
    return run;
}

/** A second action on a node still being changed is refused, in words, and its row repainted. */
function refuseIfBusy(id) {
    if (!tagEditor.view.isBusy(id)) return false;
    notice('That tag is still being changed; try again when it has finished.');
    tagEditor.view.repaint(id);
    return true;
}

/** Flags of a node: set at once, written by the server, put back if it refuses. */
export function updateTaxonomyNode(id, fields) {
    const view = tagEditor.view;
    const node = view.node(id);
    if (!node || refuseIfBusy(id)) return Promise.resolve();
    notice('');
    const saved = view.flagsOf(id);
    view.setFlags(id, fields);
    view.setBusy(id, 'saving…');
    editorSay('Updating taxonomy...', true);

    return enqueue(async () => {
        let data;
        try {
            data = await api.json('/api/taxonomy/update', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: node.id, ...fields }),
            });
        } catch (err) {
            console.error(err);
            view.setBusy(id, null);
            view.restoreFlags(saved);
            editorSay('Error');
            tagEditor.failed = true;
            return settleAfterFailure(view);
        }
        view.setBusy(id, null);
        if (data.success) return afterTreeEdit(false, view);
        view.restoreFlags(saved);
        alert("Error updating tag: " + data.error);
        return settleAfterFailure(view);
    });
}

/**
 * A new tag: shown at once, under its parent, in its order, with a marker until the
 * server answers; taken out again if it refuses. A name that is a path ("A/B") makes
 * several nodes the server alone names, so it is shown when the server has answered.
 */
export function createTaxonomyNode(name, parentId = null, hasFace = 0) {
    const problem = tagProblem(name);
    if (problem) {
        alert(problem);
        return Promise.resolve();
    }
    const view = tagEditor.view;
    const parent = parentId === null ? null : view.node(parentId);
    if (parentId !== null && !parent) return Promise.resolve();
    notice('');

    let temp = null;
    if (!PATH_SEPARATORS.test(name)) {
        const already = view.siblingNamed(parentId, name);
        if (already) {
            view.reveal(already.id);
            notice(`"${already.tag}" is already there.`);
            return Promise.resolve();
        }
        tagEditor.made += 1;
        temp = view.add({
            id: `new-${tagEditor.made}`, temp: true, name, parent_id: parentId,
            tag: parent ? joinTag(parent.tag, name) : name,
            has_face: parent ? parent.has_face : hasFace,
            hidden_from_autocomplete: parent ? parent.hidden_from_autocomplete : 0,
            usage_count: 0,
        }, { show: true });
        view.setBusy(temp.id, 'adding…');
    }
    editorSay('Creating tag...', true);

    const undo = () => {
        if (temp && view.node(temp.id) === temp) view.remove(temp.id);
    };
    return enqueue(async () => {
        if (parent && view.node(parent.id) !== parent) {
            undo();
            notice('Not added: the tag it was to go under is gone.');
            return;
        }
        let data;
        try {
            data = await api.json('/api/taxonomy/create', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, parent_id: parent ? parent.id : null, has_face: hasFace }),
            });
        } catch (err) {
            console.error(err);
            undo();
            editorSay('Error');
            tagEditor.failed = true;
            return settleAfterFailure(view);
        }
        if (!data.success) {
            undo();
            alert("Error creating tag: " + data.error);
            return;
        }
        if (temp && view.node(temp.id) === temp) {
            view.setBusy(temp.id, null);
            if (view.has(data.id)) {
                // The tag was there already, or is the parent's own name: nothing was made.
                view.remove(temp.id);
                view.reveal(data.id);
            } else {
                view.rekey(temp.id, data.id, { tag: data.tag });
            }
        }
        await afterTreeEdit(false, view);
        if (!temp) view.reveal(data.id);
    });
}

/**
 * Take a tag out. Asked first, as it was, and the node stays, marked, until the server
 * has done it: unlike a rename it can fail part-way (a photo that cannot be rewritten
 * keeps the tag, and the tree keeps the node), and a node removed at once would have to
 * come back. Its place, what is open and the scroll are kept either way.
 */
export function deleteTaxonomyNode(id, tagPath) {
    const view = tagEditor.view;
    const node = view.node(id);
    if (!node || refuseIfBusy(id)) return Promise.resolve();
    notice('');
    view.setBusy(id, 'checking…');
    editorSay('Checking usage...', true);

    return (async () => {
        try {
            const data = await api.json('/api/taxonomy/delete-check', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tag_id: node.id }),
            });
            if (!data.success) {
                alert("Error checking tag usage: " + data.error);
                return;
            }

            let confirmResult = { action: 'remove' };
            if (data.used) {
                const possibleTargets = view.ordered()
                    .filter(n => !n.temp && n.id !== id && !n.tag.startsWith(tagPath + "/"))
                    .map(n => n.tag);
                confirmResult = await showDeleteConflictModal(tagPath, data.count, possibleTargets);
                if (!confirmResult) return;
            } else if (!confirm(`Are you sure you want to remove tag "${tagPath}"?`)) {
                return;
            }

            view.setBusy(id, data.used ? 'deleting… the photos are being rewritten' : 'deleting…');
            editorSay('Deleting tag...', true);
            await enqueue(async () => {
                let resData;
                try {
                    resData = await api.json('/api/taxonomy/delete-confirm', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            tag_id: node.id,
                            action: confirmResult.action,
                            target_tag: confirmResult.target_tag,
                        }),
                    });
                } catch (err) {
                    console.error(err);
                    editorSay('Error');
                    tagEditor.failed = true;
                    tagEditor.hooks.edited({ treeChanged: false, photosChanged: true });
                    return settleAfterFailure(view);
                }
                if (resData.success) {
                    const next = view.neighbour(id);
                    view.remove(id);
                    // The row that had the focus is gone: it goes to the one beside it.
                    if (next !== null && !tagEditor.overlay.contains(document.activeElement)) {
                        view.focusOn(next, 'delete');
                    }
                    return afterTreeEdit(Boolean(data.used), view);
                }
                alert("Error deleting tag: " + resData.error);
                // A partial delete rewrote some photos; show them as they are now.
                if (resData.photos_rewritten) tagEditor.hooks.edited({ treeChanged: false, photosChanged: true });
            });
        } catch (err) {
            console.error(err);
            editorSay('Error');
            tagEditor.failed = true;
        } finally {
            if (view.node(id) === node) view.setBusy(id, null);
            settled();
        }
    })();
}

/**
 * A new name: shown at once, in its alphabetical place, the node marked while the server
 * rewrites the photos. Answered, the marker goes and nothing else moves; refused, the node
 * goes back to its old name and place and the reason is shown. Warned ("some photos not
 * rewritten") the new name stays: the tree has it, and the warning says what does not.
 */
export function renameTaxonomyNode(tagId, newName) {
    // A node's own name is one level: no "/" either.
    const problem = nameProblem(newName);
    if (problem) {
        alert(problem);
        return Promise.resolve();
    }
    const view = tagEditor.view;
    const node = view.node(tagId);
    if (!node || refuseIfBusy(tagId) || node.name === newName) return Promise.resolve();
    notice('');
    const oldName = node.name;
    view.rename(tagId, newName);
    view.setBusy(tagId, node.usage_count > 0 ? 'renaming… the photos are being rewritten' : 'renaming…');
    editorSay('Renaming tag...', true);

    const back = () => {
        view.setBusy(tagId, null);
        if (view.node(tagId) === node) view.rename(tagId, oldName);
    };
    return enqueue(async () => {
        let data;
        try {
            data = await api.json('/api/taxonomy/rename', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tag_id: node.id, new_name: newName }),
            });
        } catch (err) {
            // Nothing says whether the server did it: show the old name, then read what it holds.
            console.error(err);
            back();
            editorSay('Error');
            tagEditor.failed = true;
            tagEditor.hooks.edited({ treeChanged: false, photosChanged: true });
            return settleAfterFailure(view);
        }
        if (!data.success) {
            back();
            alert("Error renaming tag: " + data.error);
            return settleAfterFailure(view);
        }
        view.setBusy(tagId, null);
        // Some photos may not have been rewritten; they still carry the old tag.
        if (data.warning) alert("Renamed, but " + data.warning);
        return afterTreeEdit(true, view);
    });
}

export function showDeleteConflictModal(tagName, count, targetTagsOptions) {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.className = 'tag-editor active tag-editor-conflict';
        tagEditor.conflictOpen = true;

        // Built from text: a tag is the person's to name, and one holding a quote once
        // ended its option's value early, so "move to" sent a tag other than the one shown.
        const dialog = editorElement('div', 'tag-editor-dialog');
        dialog.style.maxWidth = '500px';

        const header = editorElement('div', 'tag-editor-header');
        header.appendChild(editorElement('h2', null, 'Tag Removal Check'));
        const btnClose = editorElement('button', 'tag-editor-close', '×');
        btnClose.title = 'Close';
        btnClose.setAttribute('aria-label', 'Close');
        header.appendChild(btnClose);

        const body = editorElement('div', 'tag-editor-body');
        const said = editorElement('p');
        said.style.marginBottom = '16px';
        said.style.lineHeight = '1.5';
        said.style.fontSize = '14px';
        const tagShown = editorElement('strong', null, tagName);
        tagShown.style.color = 'var(--accent)';
        said.append('The tag ', tagShown, ' is used by ', editorElement('strong', null, String(count)),
            ' photos. Removing it requires clean up. Please choose how you want to handle these photos:');

        const choices = editorElement('div', 'placement-options');
        [['remove', 'Remove this tag from all affected photos'],
         ['move', 'Move affected photos to another tag']].forEach(([value, text]) => {
            const label = editorElement('label', 'placement-option-label');
            const radio = editorElement('input');
            radio.type = 'radio';
            radio.name = 'delete-opt';
            radio.value = value;
            radio.checked = value === 'remove';
            label.append(radio, editorElement('span', null, text));
            choices.appendChild(label);
        });

        const dropdownContainer = editorElement('div');
        dropdownContainer.id = 'move-tag-dropdown-container';
        dropdownContainer.style.display = 'none';
        dropdownContainer.style.paddingLeft = '24px';
        dropdownContainer.style.marginTop = '8px';
        const select = editorElement('select', 'taxonomy-search-input');
        select.id = 'move-target-select';
        select.style.width = '100%';
        const none = editorElement('option', null, '-- Select Target Tag --');
        none.value = '';
        select.appendChild(none);
        targetTagsOptions.forEach(tag => {
            const option = editorElement('option', null, tag);
            option.value = tag;
            select.appendChild(option);
        });
        dropdownContainer.appendChild(select);
        body.append(said, choices, dropdownContainer);

        const footer = editorElement('div', 'tag-editor-footer');
        footer.append(editorElement('button', 'btn btn-secondary btn-cancel', 'Cancel'),
                      editorElement('button', 'btn btn-primary btn-confirm', 'Confirm'));

        dialog.append(header, body, footer);
        overlay.appendChild(dialog);
        document.body.appendChild(overlay);

        overlay.querySelectorAll('input[name="delete-opt"]').forEach(rad => {
            rad.addEventListener('change', (e) => {
                dropdownContainer.style.display = e.target.value === 'move' ? 'block' : 'none';
            });
        });

        const close = (value) => {
            overlay.className = 'tag-editor tag-editor-conflict';
            tagEditor.conflictOpen = false;
            setTimeout(() => overlay.remove(), 300);
            resolve(value);
        };

        overlay.querySelector('.tag-editor-close').onclick = () => close(null);
        overlay.querySelector('.btn-cancel').onclick = () => close(null);
        overlay.addEventListener('keydown', (e) => {
            if (e.key !== 'Escape') return;
            e.preventDefault();
            e.stopPropagation();
            close(null);
        });

        overlay.querySelector('.btn-confirm').onclick = () => {
            const selected = overlay.querySelector('input[name="delete-opt"]:checked').value;
            if (selected === 'move') {
                const target = select.value;
                if (!target) {
                    alert("Please select a target tag.");
                    return;
                }
                close({ action: 'move', target_tag: target });
            } else {
                close({ action: 'remove' });
            }
        };
    });
}
