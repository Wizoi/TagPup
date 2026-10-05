// TagPup's page: searching the library (docs/ARCHITECTURE.md, phase 9e-2) -- the box at the top of the Library pane, as Windows
// Live Photo Gallery's: words in one box, and the filters beside it. A search is a view like any other (library-source.js, kind
// `search`): it opens in the same grid, header, Sort by, selection, tally, bulk edits and Delete, and the address holds it
// (`?view=search&value=<its JSON>&order=`), so a bookmark, Back and Forward restore it -- the box's words and the chips with it.
//
// WORDS ARE SEARCHED ON ENTER (or the Search button), never as they are typed: each search is a new view -- the grid rebuilt,
// the selection cleared, the address changed -- and a term is matched as a prefix, so a word half typed finds a broad set;
// searching on a pause would clear a selection and redraw the grid while the person is still typing.
//
// THE FILTERS: three lists, All of, Any of, None of, each of tags and people picked from the library's own names (the
// navigator's, search-model.js) by a combobox that completes as it is typed -- arrow keys, Enter to add, Escape, Backspace
// on an empty box to take the last chip off. A chip added or taken off searches at once (a click on a navigator row does
// too); so does ticking "Within the sidebar's selection" -- under the box while rows of the navigator are the view open --
// when there is something to search: the navigator's selection becomes the first member of All of (the contract of 9e-1),
// shown as a chip "Within ..." from then on.
//
// THE HISTORY: a search opened from another view is a new place (Back returns to that view); changing the search while one
// is open replaces it, so Back from any search returns to the view before it. Clear (the x, or Enter on an empty box) returns
// to that view (library-view.js keeps how many places back it is), or, for a search opened by its address, closes the view.
//
// WHAT FAILS: a list of more than 1,000, or a search whose address would be longer than the page can be reloaded by
// (MAX_ADDRESS), is a sentence by the box and nothing is sent; words with no letter or digit are left out and said so; a
// search the server refuses (400) says the server's sentence by the box; a library still making its word index (503) says
// so in the strip and is asked again for about a minute (library-source.js). A later search wins: the view it replaces asks
// nothing more and its answer is dropped. Everything the box holds is `state.search`.
import { buildElement, replaceContent } from './common/dom.js';
import { state } from './state.js';
import {
    btnLibrarySearch, btnLibrarySearchClear, btnLibrarySearchFilters, librarySearchFilters, librarySearchNote, librarySearchWithin,
    librarySearchWithinName, librarySearchWithinRow, librarySearchWords
} from './elements.js';
import { closeViewAsNewPlace, openLibraryView } from './library-view.js';
import {
    MAX_MEMBERS, MAX_WORDS, SEARCH_LISTS, addressTooLong, searchAsksSomething, searchValue, searchWords, termSaysSomething,
    viewLabel
} from './library-source.js';
import { readSectionIndex } from './navigator.js';
import { chipKnown, chipLabel, matchNames, memberKey, pickerNames } from './search-model.js';

const LIST_NAMES = { all_of: 'All of', any_of: 'Any of', none_of: 'None of' };
const LIST_HINTS = {
    all_of: 'Photos that have every one of these',
    any_of: 'Photos that have at least one of these',
    none_of: 'Photos that have none of these',
};
/** The chips a list draws; more (a bookmark of a long list) are counted in a line. */
export const MAX_CHIPS_SHOWN = 50;

function emptyLists() {
    return { all_of: [], any_of: [], none_of: [] };
}

function rowOf(list) {
    return librarySearchFilters.querySelector(`.library-search-list[data-list="${list}"]`);
}

function pickOf(list) {
    return rowOf(list).querySelector('.library-search-pick');
}

function optionsOf(list) {
    return rowOf(list).querySelector('.library-search-options');
}

/** A text cut short for a sentence. */
function clip(text, most = 60) {
    return text.length > most ? `${text.slice(0, most - 1)}…` : text;
}

/** The view open, when it is a search. */
function openSearch() {
    const lib = state.library;
    return lib && !lib.invalid && lib.kind === 'search' ? lib : null;
}

/** The sidebar's selection a search may look within: the source of the view open, when the navigator's rows are it. */
function withinSource() {
    const lib = state.library;
    if (!lib || lib.invalid || lib.kind === 'search' || lib.kind === 'all') return null;
    return state.nav.followed;
}

// ---- What is said ---------------------------------------------------------------------------

function say(text, problem = false) {
    state.search.note = text;
    state.search.problem = problem;
    paintNote();
}

function paintNote() {
    const { note, problem } = state.search;
    if (librarySearchNote.textContent !== note) librarySearchNote.textContent = note;
    librarySearchNote.classList.toggle('hidden', !note);
    librarySearchNote.classList.toggle('library-search-problem', problem);
}

/** "Within the sidebar's selection" is offered while the navigator's rows are the view open, and names them. */
function paintWithin() {
    const source = withinSource();
    librarySearchWithinRow.classList.toggle('hidden', !source);
    librarySearchWithin.disabled = !source;
    librarySearchWithin.checked = Boolean(source) && state.search.within;
    const label = source ? viewLabel(source) : '';
    if (librarySearchWithinName.textContent !== label) librarySearchWithinName.textContent = label;
    librarySearchWithinName.title = label;
}

function paintClear() {
    const s = state.search;
    const any = Boolean(openSearch()) || Boolean(librarySearchWords.value) || SEARCH_LISTS.some(list => s.lists[list].length);
    btnLibrarySearchClear.classList.toggle('hidden', !any);
}

function paintFilters() {
    const open = state.search.filtersOpen;
    librarySearchFilters.classList.toggle('hidden', !open);
    btnLibrarySearchFilters.setAttribute('aria-expanded', open ? 'true' : 'false');
    const held = SEARCH_LISTS.reduce((sum, list) => sum + state.search.lists[list].length, 0);
    btnLibrarySearchFilters.textContent = held ? `Filters (${held.toLocaleString()})` : 'Filters';
}

// ---- The chips ------------------------------------------------------------------------------

/** Read the keywords or the people for the chips' marks, once, when a chip names one and the section was never read. */
function readForChips(name) {
    const sec = state.nav.sections[name];
    if (sec.index || sec.pending || sec.status === 'error') return;
    readSectionIndex(name).then(() => paintChips());
}

function paintChips() {
    const s = state.search;
    const keywords = state.nav.sections.keywords.index;
    const people = state.nav.sections.people.index;
    for (const list of SEARCH_LISTS) {
        const members = s.lists[list];
        const chips = [];
        members.slice(0, MAX_CHIPS_SHOWN).forEach((member, at) => {
            const { text, title } = chipLabel(member);
            if (member.kind === 'person' && !people) readForChips('people');
            if ((member.kind === 'keyword' || member.kind === 'keyword_only') && !keywords) readForChips('keywords');
            const known = chipKnown(member, keywords, people);
            const why = known === false
                ? (member.kind === 'person' ? ' -- no photo names them now: this finds nothing' : ' -- the library has no such tag now: this finds nothing')
                : '';
            chips.push(buildElement('span', {
                className: `library-search-chip${known === false ? ' library-search-chip-unknown' : ''}`,
                attrs: { role: 'listitem' }, title: `${title}${why}`,
            }, [
                buildElement('span', { className: 'library-search-chip-text', text }),
                buildElement('button', {
                    className: 'library-search-chip-remove', text: '×',
                    attrs: { type: 'button', 'aria-label': `Take ${text} off ${LIST_NAMES[list]}` }, data: { list, at },
                }),
            ]));
        });
        if (members.length > MAX_CHIPS_SHOWN) {
            chips.push(buildElement('span', {
                className: 'library-search-more', attrs: { role: 'listitem' },
                text: `and ${(members.length - MAX_CHIPS_SHOWN).toLocaleString()} more`,
            }));
        }
        replaceContent(rowOf(list).querySelector('.library-search-chips'), ...chips);
    }
    paintFilters();
    paintClear();
}

/** The chips of a search's value, as the lists hold them. */
function listsOf(value) {
    const lists = emptyLists();
    for (const list of SEARCH_LISTS) lists[list] = Array.isArray(value && value[list]) ? [...value[list]] : [];
    return lists;
}

/** Take a chip off a list, and search what is left. */
function takeOff(list, at) {
    const s = state.search;
    const removed = s.lists[list][at];
    if (!removed) return;
    s.lists[list] = s.lists[list].filter((_member, index) => index !== at);
    paintChips();
    const next = rowOf(list).querySelectorAll('.library-search-chip-remove')[Math.min(at, s.lists[list].length - 1)];
    (next || pickOf(list)).focus();
    runSearch();
}

// ---- The box shows the view that is open ------------------------------------------------------

/**
 * The box shows the search that is open: called as a view opens, changes or closes (library-view.js, through `upper`). A search
 * opened -- by the box, a bookmark, Back or Forward -- puts its words and its chips in the box; leaving a search empties them;
 * a move between two views that are not searches leaves what is typed alone (a search within the next selection). `force`
 * shows the open view's chips again (a search refused, or a person who stayed on a photo with unsaved edits). A search the
 * library refused (400) says its sentence by the box.
 */
export function searchFollows({ force = false } = {}) {
    const s = state.search;
    const lib = state.library;
    const token = lib ? lib.token : 0;
    const search = openSearch();
    const fresh = token !== s.shownToken;
    if (fresh || force) {
        if (search) {
            s.lists = listsOf(search.value);
            if (fresh) {
                librarySearchWords.value = search.value.words || '';
                s.within = false;   // the selection is in the search now, as a chip of All of (#769: only once it has opened)
            }
            if (SEARCH_LISTS.some(list => s.lists[list].length)) s.filtersOpen = true;
        } else {
            s.lists = emptyLists();
            if (fresh && s.showing) librarySearchWords.value = '';
        }
        if (fresh) {
            s.note = '';
            s.problem = false;
            closePicker();
        }
        s.shownToken = token;
        s.showing = Boolean(search);
        paintChips();
    }
    if (search && search.status === 'error' && search.failure && search.failure.status === 400 && s.note !== search.message) {
        s.note = search.message;   // the server's own sentence: a part it does not know, too many words
        s.problem = true;
    }
    paintNote();
    paintWithin();
    paintClear();
}

// ---- Searching ------------------------------------------------------------------------------

/** Refused before anything is sent: the chips go back to the search that is open, and the box says why. */
function refuse(sentence) {
    searchFollows({ force: true });
    say(sentence, true);
}

/**
 * Search what the box holds: its words, the three lists, and the sidebar's selection when "Within" is ticked. A search open is
 * replaced in the history, any other view is left as a new place. Nothing asked clears the search.
 */
export function runSearch() {
    const s = state.search;
    const typed = searchWords(librarySearchWords.value);
    const inSearch = Boolean(openSearch());
    let words = typed;
    let wordsNote = '';
    if (typed && !typed.split(' ').some(termSaysSomething)) {
        words = '';
        wordsNote = `“${clip(typed)}” has no letter or digit: there is nothing in it to look for.`;
    }
    if (typed.length > MAX_WORDS) {
        refuse(`A search's words are at most ${MAX_WORDS.toLocaleString()} characters; that is ${typed.length.toLocaleString()}.`);
        return;
    }
    const within = s.within ? withinSource() : null;
    const value = searchValue({
        all_of: within ? [within, ...s.lists.all_of] : s.lists.all_of, any_of: s.lists.any_of, none_of: s.lists.none_of, words,
    });
    if (!searchAsksSomething(value)) {
        if (wordsNote) say(`${wordsNote} Type a word, or add a tag or a person under Filters.`, true);
        else if (inSearch) clearSearch();
        else say('Type words to look for, or add a tag or a person under Filters.');
        return;
    }
    const tooMany = SEARCH_LISTS.find(list => (value[list] || []).length > MAX_MEMBERS);
    if (tooMany) {
        refuse(`${LIST_NAMES[tooMany]} holds at most ${MAX_MEMBERS.toLocaleString()} tags and people at once.`);
        return;
    }
    const spec = { kind: 'search', value, recursive: false };
    if (addressTooLong({ ...spec, order: state.nav.order })) {
        // The address holds the search, and one this long could not be reloaded or bookmarked (#696).
        refuse('This search is too long to name in the page’s address: take some tags or people off, or search within fewer rows.');
        return;
    }
    // Within and the words are left as they are until the view opens (searchFollows): a person who stays on a photo with
    // unsaved edits keeps what they typed and ticked (#769).
    openLibraryView(spec, { history: inSearch ? 'replace' : 'push' });
    if (wordsNote) say(`${wordsNote} It was left out of the search.`, true);
}

/**
 * Clear the search: back to the view before it, or, for a search opened by its address, no view -- the box emptied as that view
 * opens (searchFollows), so a person who stays on a photo with unsaved edits keeps the search and its words (#769). With no
 * search open, typed words alone are emptied.
 */
export function clearSearch() {
    const s = state.search;
    closePicker();
    if (!openSearch()) {
        librarySearchWords.value = '';
        s.within = false;
        s.lists = emptyLists();
        say('');
        paintChips();
        paintWithin();
        return;
    }
    const back = Number(window.history.state && window.history.state.searchBack);
    if (back > 0) window.history.go(-back);
    else closeViewAsNewPlace();
}

// ---- The picker -----------------------------------------------------------------------------

function closePicker() {
    const picker = state.search.picker;
    if (!picker.list) return;
    const list = picker.list;
    picker.list = null;
    picker.reading = false;
    picker.enterWaits = false;
    picker.options = [];
    picker.active = -1;
    picker.asked += 1;   // an answer on its way is for a picker no longer open
    const listbox = optionsOf(list);
    replaceContent(listbox);
    listbox.classList.add('hidden');
    const pick = pickOf(list);
    pick.setAttribute('aria-expanded', 'false');
    pick.removeAttribute('aria-activedescendant');
}

/** The names on offer, made once for the indexes they come from. */
function namesFrom(keywords, people) {
    const picker = state.search.picker;
    if (picker.from && picker.from[0] === keywords && picker.from[1] === people) return;
    picker.names = pickerNames(keywords, people);
    picker.from = [keywords, people];
}

function paintOptions(list, { reading = false } = {}) {
    const picker = state.search.picker;
    const pick = pickOf(list);
    const listbox = optionsOf(list);
    const typed = pick.value.trim();
    const found = matchNames(picker.names, typed);
    picker.options = found.options;
    picker.more = found.more;
    if (picker.active >= picker.options.length) picker.active = picker.options.length - 1;
    if (picker.active < 0 && picker.options.length) picker.active = 0;
    const items = picker.options.map((name, at) => buildElement('div', {
        className: `library-search-option${at === picker.active ? ' active' : ''}`, id: `library-search-option-${list}-${at}`,
        attrs: { role: 'option', 'aria-selected': at === picker.active ? 'true' : 'false' }, data: { at },
        title: name.hint ? `${name.label} (${name.hint})` : name.label,
    }, [
        buildElement('span', { className: 'library-search-option-label', text: name.label }),
        buildElement('span', {
            className: 'library-search-option-what',
            text: `${name.what}${name.hint ? `, ${name.hint}` : ''} · ${name.count.toLocaleString()}`,
        }),
    ]));
    const said = [];
    if (!picker.options.length && typed) said.push(reading ? 'Reading the library’s tags and people...' : `No tag or person of this library is called “${clip(typed)}”.`);
    else if (picker.more) said.push(`${picker.more.toLocaleString()} more: keep typing.`);
    if (picker.message) said.push(picker.message);
    for (const text of said) items.push(buildElement('div', { className: 'library-search-option-note', attrs: { role: 'presentation' }, text }));
    replaceContent(listbox, ...items);
    const open = Boolean(typed) && items.length > 0;
    listbox.classList.toggle('hidden', !open);
    pick.setAttribute('aria-expanded', open && picker.options.length ? 'true' : 'false');
    if (picker.active >= 0 && open) pick.setAttribute('aria-activedescendant', `library-search-option-${list}-${picker.active}`);
    else pick.removeAttribute('aria-activedescendant');
}

/** What the picker says of the sections the library could not answer: tags, people, or both (#772). */
function unreadSentence(keywordsSec, peopleSec) {
    const keywordsFailed = !keywordsSec.index && keywordsSec.status === 'error';
    const peopleFailed = !peopleSec.index && peopleSec.status === 'error';
    if (keywordsFailed && peopleFailed) return `Could not read the library’s tags and people: ${keywordsSec.message || peopleSec.message || 'no answer'}`;
    if (keywordsFailed) return `Could not read the library’s tags, so only people are offered: ${keywordsSec.message || 'no answer'}`;
    if (peopleFailed) return `Could not read the library’s people, so only tags are offered: ${peopleSec.message || 'no answer'}`;
    return '';
}

/**
 * Offer the names that match what is typed in `list`'s box: at once from what is held, and again when the keywords and the
 * people are read -- when they never were, or the library changed them since (navigator.js calls `searchVocabularyChanged`
 * on its pause after writes, #768; the tag editor's edits are writes). A section the library could not answer is not asked
 * again on each key, only when the box is focused again (`retry`, #772). An answer for a picker closed or typed in since is
 * dropped; an Enter pressed while the names were read adds the first one offered once they are here (#771).
 */
function refreshPicker(list, { retry = false } = {}) {
    const picker = state.search.picker;
    if (picker.list !== list) {
        closePicker();
        picker.list = list;
        picker.active = -1;
    }
    picker.asked += 1;
    const asked = picker.asked;
    const keywordsSec = state.nav.sections.keywords;
    const peopleSec = state.nav.sections.people;
    // Held and up to date and not being read again (a read under way has already said the section is not stale), or failed
    // and not to be asked again now.
    const settled = sec => !sec.pending && ((sec.index && !sec.stale) || (!sec.index && sec.status === 'error' && !retry));
    const toRead = ['keywords', 'people'].filter(name => !settled(state.nav.sections[name]));
    namesFrom(keywordsSec.index, peopleSec.index);
    picker.message = unreadSentence(keywordsSec, peopleSec);
    picker.reading = toRead.length > 0;
    paintOptions(list, { reading: picker.reading });
    if (!picker.reading) return;
    const reads = ['keywords', 'people'].map(name => (toRead.includes(name) ? readSectionIndex(name) : Promise.resolve(state.nav.sections[name].index)));
    Promise.all(reads).then(([keywords, people]) => {
        if (asked !== picker.asked || picker.list !== list) return;
        picker.reading = false;
        namesFrom(keywords, people);
        picker.message = unreadSentence(keywordsSec, peopleSec);
        paintOptions(list);
        paintChips();   // a chip's tag or person may be gone, or back
        if (picker.enterWaits) {
            picker.enterWaits = false;
            pickEntered(list);
        }
    });
}

/** Enter in a picker: the name the keys are on, or the first offered; a text that names none says so; an empty box searches. */
function pickEntered(list) {
    const picker = state.search.picker;
    const pick = pickOf(list);
    const typed = pick.value.trim();
    if (picker.list === list && picker.reading && typed) {
        // The names are being read (the first time, or after a change): the Enter waits for them (#771).
        picker.enterWaits = true;
        say('Reading the library’s tags and people...');
        return;
    }
    if (picker.list === list && picker.options.length && picker.active >= 0) {
        say('');
        choose(list, picker.options[picker.active]);
    } else if (typed) say(`No tag or person of this library is called “${clip(typed)}”: pick one from the list.`, true);
    else runSearch();
}

/** The tags and people of the library may have changed (a write, the tag editor): an open picker reads them again. */
export function searchVocabularyChanged() {
    const list = state.search.picker.list;
    if (list) refreshPicker(list);
}

/** A name picked: its chip joins the list, and the search runs. */
function choose(list, name) {
    const s = state.search;
    const pick = pickOf(list);
    pick.value = '';
    closePicker();
    if (s.lists[list].some(member => memberKey(member) === memberKey(name.member))) {
        say(`${name.label} is in ${LIST_NAMES[list]} already.`);
        return;
    }
    if (s.lists[list].length >= MAX_MEMBERS) {
        say(`${LIST_NAMES[list]} holds at most ${MAX_MEMBERS.toLocaleString()} tags and people at once.`, true);
        return;
    }
    s.lists[list] = [...s.lists[list], name.member];
    paintChips();
    runSearch();
}

function onPickKey(list, event) {
    const picker = state.search.picker;
    const pick = pickOf(list);
    const open = picker.list === list && picker.options.length > 0;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        event.stopPropagation();
        if (picker.list !== list) {
            refreshPicker(list);
            return;
        }
        if (!open) return;
        const step = event.key === 'ArrowDown' ? 1 : -1;
        picker.active = (picker.active + step + picker.options.length) % picker.options.length;
        paintOptions(list);
        const el = document.getElementById(`library-search-option-${list}-${picker.active}`);
        if (el) el.scrollIntoView({ block: 'nearest' });
    } else if (event.key === 'Enter') {
        event.preventDefault();
        event.stopPropagation();
        pickEntered(list);
    } else if (event.key === 'Escape') {
        if (picker.list === list && !optionsOf(list).classList.contains('hidden')) {
            event.preventDefault();
            event.stopPropagation();
            closePicker();
        } else if (pick.value) {
            event.preventDefault();
            event.stopPropagation();
            pick.value = '';
        }
    } else if (event.key === 'Backspace' && !pick.value && state.search.lists[list].length) {
        event.preventDefault();
        takeOff(list, state.search.lists[list].length - 1);
    } else if (event.key === 'Tab') {
        closePicker();
    }
}

// ---- Wiring ---------------------------------------------------------------------------------

function buildLists() {
    for (const list of SEARCH_LISTS) {
        const labelId = `library-search-label-${list}`;
        const pick = buildElement('input', {
            className: 'library-search-pick',
            attrs: {
                type: 'text', role: 'combobox', 'aria-autocomplete': 'list', 'aria-expanded': 'false',
                'aria-controls': `library-search-options-${list}`, 'aria-labelledby': labelId,
                placeholder: 'Add a tag or a person...', autocomplete: 'off', spellcheck: 'false',
            },
        });
        const listbox = buildElement('div', {
            className: 'library-search-options hidden', id: `library-search-options-${list}`,
            attrs: { role: 'listbox', 'aria-label': `Tags and people to add to ${LIST_NAMES[list]}` },
        });
        const row = buildElement('div', { className: 'library-search-list', data: { list }, title: LIST_HINTS[list] }, [
            buildElement('span', { className: 'library-search-label', id: labelId, text: LIST_NAMES[list] }),
            buildElement('div', { className: 'library-search-chips', attrs: { role: 'list', 'aria-labelledby': labelId } }),
            buildElement('div', { className: 'library-search-pick-wrap' }, [pick, listbox]),
        ]);
        librarySearchFilters.appendChild(row);
        pick.addEventListener('input', () => refreshPicker(list));
        pick.addEventListener('focus', () => { if (pick.value.trim()) refreshPicker(list, { retry: true }); });
        pick.addEventListener('keydown', (event) => onPickKey(list, event));
        pick.addEventListener('blur', () => { if (state.search.picker.list === list) closePicker(); });
        listbox.addEventListener('mousedown', (event) => {
            event.preventDefault();   // the box keeps the focus: the click picks, the blur does not close it first
            const option = event.target.closest('[role="option"]');
            const name = option ? state.search.picker.options[Number(option.dataset.at)] : null;
            if (name) choose(list, name);
        });
        row.querySelector('.library-search-chips').addEventListener('click', (event) => {
            const button = event.target.closest('.library-search-chip-remove');
            if (button) takeOff(button.dataset.list, Number(button.dataset.at));
        });
    }
}

export function wireSearch() {
    buildLists();
    librarySearchWords.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
            event.preventDefault();
            runSearch();
        } else if (event.key === 'Escape' && librarySearchWords.value) {
            event.preventDefault();
            event.stopPropagation();
            librarySearchWords.value = '';
            paintClear();
        }
    });
    librarySearchWords.addEventListener('input', paintClear);
    btnLibrarySearch.addEventListener('click', runSearch);
    btnLibrarySearchClear.addEventListener('click', () => {
        clearSearch();
        librarySearchWords.focus();
    });
    btnLibrarySearchFilters.addEventListener('click', () => {
        state.search.filtersOpen = !state.search.filtersOpen;
        paintFilters();
        if (state.search.filtersOpen) pickOf('all_of').focus();
    });
    librarySearchWithin.addEventListener('change', () => {
        state.search.within = librarySearchWithin.checked;
        const s = state.search;
        if (s.within && (searchWords(librarySearchWords.value) || SEARCH_LISTS.some(list => s.lists[list].length))) runSearch();
    });
    searchFollows({ force: true });
}
