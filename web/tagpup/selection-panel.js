// TagPup's page: the selection details of a library view, as two halves (docs/ARCHITECTURE.md, phase 9e-4; #781).
//
// NAVIGATION, above: Folders to Organize (tally.js), People jump and Keyword jump -- each person and each keyword the selected photos
// carry a link that opens the library view of it (the People view of that person, the Keywords view of that tag), as a click on the
// sidebar's row does: the address names the view, the navigator shows it selected, Back returns. The selection is left behind, as it
// is when any view is opened: a view has its own. The lists are the server's tally (tally.js; it counts the whole selection, not the
// cards the page holds), so a link is only ever offered for something a selected photo carries. Names are text, never markup.
//
// TAGGING, below: the people and tag editing tools the panel always had (9d-2's bulk add and take off, the chips' x), in a section that
// starts closed and whose choice this browser remembers. Organize's panel is not split: its tagging stays open, with Suggest's
// auto-apply, which a library view does not have.
import { buildElement, replaceContent } from './common/dom.js';
import { pathKey } from './common/paths.js';
import { samePerson, sortedTags } from './common/vocabulary.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import {
    btnSelectionTagging, selectionAutoApply, selectionKeywordJump, selectionNavigation, selectionPeopleJump, selectionTaggingBody
} from './elements.js';
import { viewSearch } from './library-source.js';

/** How many links a list shows before "and N more". */
export const JUMP_SHOWN = 12;

/** Where this browser keeps whether the Tagging section is open ('open' or 'closed'). */
export const TAGGING_KEY = 'tagpup.selectionTagging';

const QUIET = 'color: var(--text-muted); font-size: 12px; padding: 4px 0;';

// ---- The two halves ------------------------------------------------------------------------------------------

/** Show the panel as a library view has it (navigation, a Tagging section) or as Organize has it (tagging, open, with auto-apply). */
export function showPanelFor(inLibrary) {
    if (selectionNavigation) selectionNavigation.classList.toggle('hidden', !inLibrary);
    if (btnSelectionTagging) btnSelectionTagging.classList.toggle('hidden', !inLibrary);
    if (selectionAutoApply) selectionAutoApply.classList.toggle('hidden', inLibrary);
    showTagging(inLibrary);
}

function showTagging(inLibrary) {
    const open = !inLibrary || state.selectionPanel.taggingOpen;
    if (selectionTaggingBody) selectionTaggingBody.classList.toggle('hidden', !open);
    if (btnSelectionTagging) {
        btnSelectionTagging.setAttribute('aria-expanded', String(open));
        btnSelectionTagging.classList.toggle('open', open);
    }
}

function setTagging(open) {
    state.selectionPanel.taggingOpen = open;
    try {
        window.localStorage.setItem(TAGGING_KEY, open ? 'open' : 'closed');
    } catch (err) { /* the choice is just not kept */ }
    showTagging(Boolean(state.library));
}

/** The Tagging section's button, and what this browser remembered of it. */
export function wireSelectionPanel() {
    try {
        state.selectionPanel.taggingOpen = window.localStorage.getItem(TAGGING_KEY) === 'open';
    } catch (err) { /* closed, then */ }
    if (btnSelectionTagging) {
        btnSelectionTagging.addEventListener('click', () => setTagging(!state.selectionPanel.taggingOpen));
    }
    showPanelFor(Boolean(state.library));
}

// ---- The jump lists ------------------------------------------------------------------------------------------

function jumpNote(text) {
    return buildElement('span', { style: QUIET, text });
}

/** A plain click opens the view in this page; a click with a modifier, or a middle click, is the browser's (a new tab, from the address). */
function isPlainClick(event) {
    return event.button === 0 && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey;
}

function jumpLink(spec, label, count, title) {
    const anchor = buildElement('a', { className: 'selection-jump-link', text: label, title, attrs: { href: viewSearch({ ...spec, order: state.nav.order }) } });
    anchor.addEventListener('click', (event) => {
        if (event.defaultPrevented || !isPlainClick(event)) return;
        event.preventDefault();
        upper.openLibraryView(spec);
    });
    return buildElement('span', { className: 'selection-jump-item', attrs: { role: 'listitem' } }, [
        anchor, ' ', buildElement('span', { className: 'selection-folder-count', text: `(${count.toLocaleString()})` }),
    ]);
}

/** A name with no view to open: its text and its count, no link. */
function jumpPlain(label, count, title) {
    return buildElement('span', { className: 'selection-jump-item', title, attrs: { role: 'listitem' } }, [
        label, ' ', buildElement('span', { className: 'selection-folder-count', text: `(${count.toLocaleString()})` }),
    ]);
}

/** The first JUMP_SHOWN items, or all of them once "and N more" was pressed; the button that does it is the last child. */
function fillJumpList(container, key, items, left, what) {
    if (!items.length && !left) {
        replaceContent(container, jumpNote('None'));
        return;
    }
    const expanded = Boolean(state.selectionPanel.expanded[key]);
    const shown = expanded ? items : items.slice(0, JUMP_SHOWN);
    const children = shown.slice();
    if (items.length > JUMP_SHOWN) {
        const hidden = items.length - JUMP_SHOWN;
        const more = buildElement('button', {
            className: 'selection-jump-more', attrs: { type: 'button', 'aria-expanded': String(expanded) },
            text: expanded ? 'Show fewer' : `and ${hidden.toLocaleString()} more`,
            title: expanded ? `Show only the first ${JUMP_SHOWN} ${what}` : `Show all ${items.length.toLocaleString()} ${what}`,
        });
        more.addEventListener('click', () => {
            state.selectionPanel.expanded[key] = !expanded;
            drawJumps(state.tally.data);
        });
        children.push(more);
    }
    if (left) children.push(jumpNote(`${left.toLocaleString()} more, not listed`));
    replaceContent(container, ...children);
}

/** Is this tag one of the Folders to Organize (the folder's own path or name)? */
function namesAListedFolder(tag, folders) {
    const listed = folders && Array.isArray(folders.listed) ? folders.listed : [];
    return listed.some(folder => (folder.path && pathKey(folder.path) === pathKey(tag)) || (folder.name && folder.name === tag));
}

/**
 * Draw People jump and Keyword jump from what the server counted. `data` is the tally ({ people, tags, more_people, more_tags, total,
 * folders }); the tags leave out those that are the people listed above, and the folders offered to Organize.
 */
export function drawJumps(data) {
    if (!selectionPeopleJump || !selectionKeywordJump || !data) return;
    // Only a name that is one person node of the tree is a person to open a view of (the server says: `has_node`); a branch, a name
    // two nodes share or one no node has is listed, without a link (#866).
    const people = sortedTags(data.people, each => each.name).map(each => ({
        spec: { kind: 'person', value: each.name, recursive: false }, label: each.name, count: each.count, linked: each.has_node !== false,
    }));
    const tags = sortedTags(data.tags, each => each.tag)
        .filter(each => !data.people.some(person => person.has_node !== false && samePerson(each.tag, person.name))
            && !namesAListedFolder(each.tag, data.folders))
        .map(each => ({ spec: { kind: 'keyword', value: each.tag, recursive: false }, label: each.tag, count: each.count }));
    const item = (each, what) => (each.linked === false
        ? jumpPlain(each.label, each.count, `${each.label} is not a person of the tag tree: there is no People view of it`)
        : jumpLink(each.spec, each.label, each.count, `Open ${what} ${each.label}: its photos, not only these`));
    fillJumpList(selectionPeopleJump, 'people', people.map(each => item(each, 'the People view of')), data.more_people || 0, 'people');
    fillJumpList(selectionKeywordJump, 'keywords', tags.map(each => item(each, 'the Keywords view of')), data.more_tags || 0, 'keywords');
}

/** What the lists say while there is nothing to list: counting, or why it could not be. */
export function drawJumpNote(text) {
    if (selectionPeopleJump) replaceContent(selectionPeopleJump, jumpNote(text));
    if (selectionKeywordJump) replaceContent(selectionKeywordJump, jumpNote(text));
}
