// Names to review (docs/ARCHITECTURE.md, "People by id, stage 2", "Names to review"; docs/findings.md, #985): a name that faces or
// photos' people hold and no person's tag is. A first row of Review People, shown only when something waits, opens this
// dialog; each entry offers four choices -- make a person under a group the owner picks, link the name to a person, unname the
// faces, set it aside -- and none runs by itself or on opening: a choice is rehearsed (the sentence says what would change and
// how many), and only Apply writes it, as one journaled change that History can undo. The result of each is stated, and the
// entry leaves the list. One choice at a time (a second press while one is on its way does nothing), and everything is read
// again under the server's write lock, so a name settled in another window, a person merged meanwhile or a group removed is
// answered in words, never half done (404: the page says so and reads the list again).
import { api } from './common/api.js';
import { buildElement, replaceContent } from './common/dom.js';
import { personLabel, personTitle } from './common/vocabulary.js';
import { state } from './state.js';
import { upper } from './hooks.js';
import { modeSelect, photoList } from './elements.js';
import { fetchKnownPeople } from './shared.js';

const JSON_HEADERS = { 'Content-Type': 'application/json' };

/** What each reason says to the owner. */
const WHY = {
    none: 'No person tag has this name.',
    several: 'Two or more people are called this: say which one it is.',
    one: 'One person is called this, but these rows are linked to nobody.',
    branch: 'Only a group of people is called this, and a group is not a person.',
};

function plural(count, one, many) {
    return `${count} ${count === 1 ? one : many}`;
}

// ---- The first row of Review People -------------------------------------------------------

/**
 * Read how many names wait, for the row: the count alone, never the names. A server that does not have the route (an older one)
 * is not a failure -- there is nothing to show; any other failure is shown by the row itself, which says the count could not
 * be read and opens the dialog to try again.
 */
export function loadNamesCount() {
    return api.fetch('/api/names-to-review?count=1').then(res => {
        if (res.status === 404) {
            state.names.available = false;
            state.names.count = 0;
            state.names.unreadable = false;
            return null;
        }
        if (!res.ok) throw new Error(`The server answered ${res.status}`);
        return res.json().then(data => {
            state.names.available = true;
            state.names.unreadable = false;
            state.names.count = Number(data && data.count) || 0;
            return data;
        });
    }).catch(err => {
        console.error('Could not read how many names wait to be reviewed:', err);
        state.names.unreadable = true;
        return null;
    }).then(data => {
        showNamesRow();
        return data;
    });
}

/**
 * The row above the people: "Names to review (5)", only in Review People and only when something waits (or the count could not
 * be read). Put back after every drawing of the list, which empties it.
 */
export function showNamesRow() {
    const old = photoList.querySelector('.names-to-review-row');
    if (old) old.remove();
    if (modeSelect.value !== 'face-matching' || !state.names.available) return;
    const count = state.names.count;
    if (!state.names.unreadable && !count) return;
    const text = state.names.unreadable ? 'Names to review (could not be read)' : `Names to review (${count})`;
    const row = buildElement('li', {
        className: 'names-to-review-row', text,
        title: 'Names on faces and photos that no person\'s tag is: settle each by hand, one at a time',
        attrs: { role: 'button', tabindex: '0' },
    });
    row.addEventListener('click', () => openNamesReview());
    row.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            openNamesReview();
        }
    });
    photoList.insertBefore(row, photoList.firstChild);
}

// ---- The dialog -------------------------------------------------------------------------------

function say(text) {
    if (state.names.status) state.names.status.textContent = text || '';
}

function whyText(entry) {
    return WHY[entry.why] || '';
}

function countsText(entry) {
    const parts = [];
    if (entry.faces) {
        parts.push(`${plural(entry.faces, 'face', 'faces')}${entry.faces_by_hand ? ` (${entry.faces_by_hand} decided by hand)` : ''}`);
    }
    if (entry.listed) parts.push(`listed on ${plural(entry.listed, 'photo', 'photos')} from a face`);
    if (entry.keyword_photos) {
        parts.push(`${plural(entry.keyword_photos, 'photo lists', 'photos list')} it from a keyword (the keyword's path says who it is)`);
    }
    return parts.join('; ');
}

function post(body) {
    return api.fetch('/api/names-to-review/resolve', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body) })
        .then(res => res.json().catch(() => ({})).then(data => ({ status: res.status, ok: res.ok, data })));
}

/** One entry's confirm area: what the rehearsal says would change, with Apply and Cancel. */
function showConfirm(entry, row, ask, sentence) {
    const area = row.querySelector('.names-confirm');
    const apply = buildElement('button', { className: 'btn btn-primary btn-sm names-apply', text: 'Apply', attrs: { type: 'button' } });
    const cancel = buildElement('button', { className: 'btn btn-secondary btn-sm names-cancel', text: 'Cancel', attrs: { type: 'button' } });
    replaceContent(area, buildElement('p', { className: 'names-sentence', text: sentence }), apply, cancel);
    area.classList.remove('hidden');
    cancel.addEventListener('click', () => {
        area.classList.add('hidden');
        replaceContent(area);
    });
    apply.addEventListener('click', () => choose(entry, row, ask, true));
}

/** The server's refusal, said; a 404 or a settled name also reads the list again, since what the page held is out of date. */
function refused(entry, outcome) {
    const message = (outcome.data && outcome.data.error) || `The server answered ${outcome.status}.`;
    say(message);
    state.names.results.prepend(buildElement('p', { className: 'names-result names-result-error', text: message }));
    if (outcome.status === 404 || outcome.status === 400) return loadNames(true);
    return null;
}

/** Say what a choice did, in the dialog and as a line that stays until it is closed. */
function stated(entry, ask, data) {
    let text;
    if (ask.action === 'dismiss') text = `${entry.name} is set aside until it holds more rows than ${entry.rows}.`;
    else if (ask.action === 'restore') text = `${entry.name} is shown again.`;
    else if (ask.action === 'rebuild') text = `Rebuilt the lists of ${plural(data.changed, 'photo', 'photos')} from their keywords. Derived rows: not journaled.`;
    else if (ask.action === 'unname') text = `Unnamed ${plural(data.changed, 'face', 'faces')} called ${entry.name}.`;
    else if (ask.action === 'make') text = `Made ${data.person_tag || entry.name}: ${plural(data.changed, 'face', 'faces')} named ${entry.name} are theirs.`;
    else text = `Linked ${entry.name} to ${ask.label}: ${plural(data.faces, 'face', 'faces')} and ${plural(data.listed, 'listed person', 'listed people')}.`;
    if (data.keywords_kept) {
        text += ` ${plural(data.keywords_kept, 'photo keeps', 'photos keep')} the old keyword in ${data.keywords_kept === 1 ? 'its' : 'their'} file${
            data.keywords_kept === 1 ? '' : 's'}: merge the tag in the tag editor to rewrite ${data.keywords_kept === 1 ? 'it' : 'them'}.`;
    }
    if (data.change !== null && data.change !== undefined) text += ` History can undo it (change ${data.change}).`;
    return text;
}

/**
 * Make one choice for one entry: rehearsed first (`apply` false: the sentence and counts, nothing written), written on Apply.
 * One at a time.
 */
export function choose(entry, row, ask, apply) {
    if (state.names.busy) return Promise.resolve(null);
    state.names.busy = true;
    row.classList.add('is-busy');
    const body = { key: entry.key, action: ask.action, apply };
    if (ask.person_id !== undefined) body.person_id = ask.person_id;
    if (ask.group_id !== undefined) body.group_id = ask.group_id;
    return post(body).then(outcome => {
        if (!outcome.ok || !outcome.data || outcome.data.success === false) return refused(entry, outcome);
        if (!apply) {
            showConfirm(entry, row, ask, outcome.data.sentence || '');
            return outcome.data;
        }
        const text = stated(entry, ask, outcome.data);
        say(text);
        state.names.results.prepend(buildElement('p', { className: 'names-result', text }));
        row.remove();
        state.names.entries = state.names.entries.filter(each => each.key !== entry.key);
        // The people and counts the rest of the page shows have changed (faces were named, a person was made).
        if (ask.action === 'make' || ask.action === 'link' || ask.action === 'unname') {
            upper.fetchPeopleWithCounts(true, true);
            fetchKnownPeople();
        }
        return loadNames(true).then(() => outcome.data);
    }).catch(err => {
        console.error('Could not settle the name:', err);
        say('Could not settle it: the server did not answer. Nothing was changed that the list does not show.');
        return null;
    }).then(done => {
        state.names.busy = false;
        row.classList.remove('is-busy');
        return done;
    });
}

function crops(entry) {
    return buildElement('div', { className: 'names-crops' }, (entry.face_ids || []).map(id => buildElement('img', {
        className: 'names-crop', attrs: { src: api.image(`/api/face-crop?id=${id}`), alt: '', loading: 'lazy' },
    })));
}

/** The people an entry can be linked to: those it could mean first, then everyone, each by label with the tag as its hover. */
function personSelect(entry) {
    const select = buildElement('select', { className: 'names-person', attrs: { 'aria-label': `The person ${entry.name} is` } });
    select.append(buildElement('option', { text: 'Choose a person...', attrs: { value: '' } }));
    const seen = new Set();
    const group = (label, people) => {
        const options = people.filter(person => person && !seen.has(person.id)).map(person => {
            seen.add(person.id);
            return buildElement('option', { text: personLabel(person), title: personTitle(person), attrs: { value: String(person.id) } });
        });
        if (options.length) select.append(buildElement('optgroup', { attrs: { label } }, options));
    };
    group('It could be', entry.candidates || []);
    group('Everyone', state.people.all());
    return select;
}

function groupSelect(groups) {
    const select = buildElement('select', { className: 'names-group', attrs: { 'aria-label': 'The group the person goes under' } });
    // One group offers itself; with several, nothing is chosen for the owner.
    if (groups.length !== 1) select.append(buildElement('option', { text: 'Choose a group...', attrs: { value: '' } }));
    for (const group of groups) {
        select.append(buildElement('option', { text: group.tag, attrs: { value: String(group.id) } }));
    }
    return select;
}

function button(text, className, title) {
    return buildElement('button', { className: `btn btn-secondary btn-sm ${className}`, text, title, attrs: { type: 'button' } });
}

function entryRow(entry) {
    const row = buildElement('li', { className: 'names-entry', data: { key: entry.key, why: entry.why } });
    row.append(
        buildElement('div', { className: 'names-head' }, [
            buildElement('strong', { className: 'names-name', text: entry.name }),
            buildElement('span', { className: 'names-why', text: ` ${whyText(entry)}` }),
        ]),
        buildElement('div', { className: 'names-counts', text: countsText(entry) }),
        crops(entry),
    );
    const choices = buildElement('div', { className: 'names-choices' });
    if (entry.dismissed) {
        const show = button('Show again', 'names-restore', 'Put this name back in the list');
        show.addEventListener('click', () => choose(entry, row, { action: 'restore' }, true));
        choices.append(show);
    } else {
        // Link to a person.
        const people = personSelect(entry);
        const link = button('Link to this person', 'names-link', 'The faces and listed people called this become that person');
        link.addEventListener('click', () => {
            const id = Number(people.value);
            if (!people.value || !Number.isInteger(id)) return say('Choose the person first.');
            const chosen = people.selectedOptions[0];
            return choose(entry, row, { action: 'link', person_id: id, label: chosen ? chosen.textContent : '' }, false);
        });
        choices.append(buildElement('div', { className: 'names-choice' }, [people, link]));
        // Make a person.
        if (entry.why === 'none') {
            const groups = groupSelect(state.names.groups);
            const make = button('Make a person', 'names-make', 'Make a person tag of this name under the group, and link the name to it');
            make.addEventListener('click', () => {
                const id = Number(groups.value);
                if (!groups.value || !Number.isInteger(id)) return say('Choose the group the person goes under first.');
                return choose(entry, row, { action: 'make', group_id: id }, false);
            });
            choices.append(buildElement('div', { className: 'names-choice' }, [groups, make]));
        }
        // Unname the faces.
        const rest = buildElement('div', { className: 'names-choice' });
        if (entry.faces) {
            const unname = button('Unname the faces', 'names-unname',
                'The faces called this become nameless again, to be identified; the photos\' keywords stay');
            unname.addEventListener('click', () => choose(entry, row, { action: 'unname' }, false));
            rest.append(unname);
        }
        if (entry.keyword_photos) {
            const rebuild = button('Rebuild these photos', 'names-rebuild',
                'Write the lists of the photos that list this name from a keyword again by the keywords\' paths (derived; not journaled)');
            rebuild.addEventListener('click', () => choose(entry, row, { action: 'rebuild' }, false));
            rest.append(rebuild);
        }
        const aside = button('Set aside', 'names-dismiss', 'Leave it as it is; it comes back if it gains rows');
        aside.addEventListener('click', () => choose(entry, row, { action: 'dismiss' }, false));
        rest.append(aside);
        choices.append(rest);
    }
    row.append(choices, buildElement('div', { className: 'names-confirm hidden', attrs: { role: 'status' } }));
    return row;
}

function render() {
    const entries = state.names.entries;
    if (!entries.length) {
        replaceContent(state.names.list, buildElement('p', { className: 'names-empty',
            text: 'No names wait to be settled.' }));
    } else {
        replaceContent(state.names.list, buildElement('ul', { className: 'names-list' }, entries.map(entryRow)));
    }
    if (state.names.toggle) {
        state.names.toggle.classList.toggle('hidden', !state.names.dismissed && !state.names.showDismissed);
        state.names.toggleLabel.textContent = `Show the ${plural(state.names.dismissed, 'name', 'names')} set aside`;
    }
}

/** Read the list again (the names, the groups, how many are set aside) and the first row's count with it. */
export function loadNames(quiet = false) {
    const token = ++state.names.token;
    if (!quiet) {
        replaceContent(state.names.list, buildElement('p', { className: 'names-loading',
            text: 'Reading the library\'s names...' }));
    }
    return api.fetch(`/api/names-to-review${state.names.showDismissed ? '?dismissed=1' : ''}`).then(res => res.json()
        .catch(() => ({})).then(data => ({ ok: res.ok, data }))).then(({ ok, data }) => {
        if (token !== state.names.token) return null;   // a newer reading has taken its place
        if (!ok || !data || data.success === false) {
            replaceContent(state.names.list, buildElement('p', { className: 'validation-error',
                text: (data && data.error) || 'Could not list the names to review.' }));
            return null;
        }
        state.names.entries = data.entries || [];
        state.names.groups = data.groups || [];
        state.names.dismissed = data.dismissed || 0;
        state.names.count = data.count || 0;
        state.names.unreadable = false;
        state.names.available = true;
        render();
        showNamesRow();
        if (data.stale_group_rows) {
            say(`${plural(data.stale_group_rows, 'listed row names', 'listed rows name')} a group of people; `
                + 'tools/doctor.py --rebuild-derived drops them.');
        }
        return data;
    }).catch(err => {
        if (token !== state.names.token) return null;
        console.error('Could not list the names to review:', err);
        replaceContent(state.names.list, buildElement('p', { className: 'validation-error',
            text: 'Could not list the names to review.' }));
        return null;
    });
}

function buildDialog() {
    // The multiplication sign, by its code: no escape in this file can be mangled in transit.
    const close = buildElement('button', { className: 'close-btn', text: String.fromCharCode(0xd7), title: 'Close',
        attrs: { type: 'button', 'aria-label': 'Close' } });
    const done = buildElement('button', { className: 'btn btn-secondary', text: 'Close', attrs: { type: 'button' } });
    state.names.list = buildElement('div', { className: 'modal-body names-body' });
    state.names.results = buildElement('div', { className: 'names-results', attrs: { 'aria-live': 'polite' } });
    state.names.status = buildElement('span', { className: 'names-status', attrs: { role: 'status' } });
    state.names.toggleLabel = buildElement('span', { text: '' });
    const checkbox = buildElement('input', { attrs: { type: 'checkbox' } });
    state.names.toggle = buildElement('label', { className: 'names-toggle hidden' }, [checkbox, state.names.toggleLabel]);
    checkbox.addEventListener('change', () => {
        state.names.showDismissed = checkbox.checked;
        loadNames();
    });
    const modal = buildElement('div', {
        id: 'names-modal',
        className: 'modal review-modal names-modal hidden',
        attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'names-title' },
    }, [
        buildElement('div', { className: 'modal-content review-content' }, [
            buildElement('div', { className: 'modal-header' }, [
                buildElement('h3', { id: 'names-title', text: 'Names to review' }), close]),
            buildElement('p', { className: 'review-about', text: 'Names that faces and photos hold and no person\'s tag is. '
                + 'Settle each one: nothing is changed until you choose, you see what would change first, and History can undo it.' }),
            state.names.results,
            state.names.list,
            buildElement('div', { className: 'modal-footer review-footer' }, [state.names.status, state.names.toggle, done]),
        ]),
    ]);
    document.body.append(modal);
    close.addEventListener('click', closeNamesReview);
    done.addEventListener('click', closeNamesReview);
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape' || modal.classList.contains('hidden')) return;
        e.preventDefault();
        e.stopPropagation();
        closeNamesReview();
    });
    state.names.modal = modal;
}

/** Open the dialog, the names read now. */
export function openNamesReview() {
    if (!state.names.modal) buildDialog();
    state.names.opener = document.activeElement;
    state.names.modal.classList.remove('hidden');
    replaceContent(state.names.results);
    say('');
    // The dialog takes the focus (it is aria-modal) and gives it back on close.
    state.names.modal.setAttribute('tabindex', '-1');
    state.names.modal.focus();
    return loadNames();
}

export function closeNamesReview() {
    if (!state.names.modal) return;
    state.names.modal.classList.add('hidden');
    // Back to what opened it; the first row is drawn again while the dialog is open, so the opener may be gone: the row that took its place.
    const opener = state.names.opener;
    const row = photoList.querySelector('.names-to-review-row');
    if (opener && opener.isConnected && typeof opener.focus === 'function') opener.focus();
    else if (row) row.focus();
}

/** Opened with `?names-to-review=1` -- the Activity page's link -- the page opens the dialog as it starts. */
export function wireNamesReview() {
    if (new URLSearchParams(window.location.search).get('names-to-review') === '1') openNamesReview();
}
