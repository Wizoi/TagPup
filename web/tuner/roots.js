// Roots, from the gear: where this computer keeps each root of the library, and Change location
// (docs/ARCHITECTURE.md, "Roots and machines"; /api/roots).
//
// The library remembers a photo as a root and the path under it; this computer's map says
// where the root is. Moving a root edits that map and nothing else: no photo file and no row
// changes. A move is checked first -- a sample of what the new place holds -- and only then
// confirmed; a poor result needs the person to say they mean it. The page's state is
// state.js's; requests go through api.js; paths are the server's, shown as they come.
import { api } from './common/api.js';
import { buildElement, replaceContent } from './common/dom.js';
import { samePath } from './common/paths.js';
import { state } from './state.js';

const TIMES = String.fromCharCode(0xd7);

/** Put `nodes`, a list, in place of what `container` held. */
function show(container, nodes) {
    return replaceContent(container, ...nodes);
}

/** 12345 as "12,345". */
function count(value) {
    return Number(value || 0).toLocaleString('en-US');
}

function say(text) {
    if (state.roots.status) state.roots.status.textContent = text || '';
}

function post(path, body) {
    return api.json(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
}

/** What the owner is told before a move, since TagTuner sees only its own runs, and what is kept. */
export const BEFORE_MOVING = 'Before you move a root: TagTuner sees only its own runs and a Roots check. Stop Suggest in '
    + 'TagPup, any index you started from the command line, and a sync first. Only the current and the previous '
    + 'place are kept: moving again forgets the one before.';

/** What a sample of a place answered, as lines for a person: its summary, and what is wrong with it. */
export function verifyLines(verify) {
    const lines = [verify.summary];
    for (const why of verify.poor_why || []) {
        if (why !== verify.message) lines.push(why);
    }
    if (verify.outside_rows) {
        const groups = (verify.outside || []).slice(0, 3).map(g => `${g.group} (${count(g.count)})`).join(', ');
        lines.push(`${count(verify.outside_rows)} photo(s) are held under no root: ${groups}.`);
    }
    return lines;
}

function lines(element, texts, className) {
    show(element, texts.filter(Boolean).map(text => buildElement('p', { className, text })));
}

// ---- Change location ----------------------------------------------------------

/**
 * The panel under a root for moving it: a folder to name, then Check. Nothing is changed until Move
 * is pressed, and Move is pressed once: a second press while the first is on its way does nothing.
 */
function openPanel(entry, element) {
    const panel = element.querySelector('.roots-panel');
    const check = buildElement('div', { className: 'roots-check', attrs: { role: 'status' } });
    const override = buildElement('input', { attrs: { type: 'checkbox' } });
    const overrideLabel = buildElement('label', { className: 'roots-override hidden' },
        [override, ' I mean it: change it anyway']);
    const confirm = buildElement('button', { className: 'btn btn-primary btn-sm roots-confirm',
        text: 'Move it here', attrs: { type: 'button', disabled: true } });
    const cancel = buildElement('button', { className: 'btn btn-secondary btn-sm roots-cancel', text: 'Cancel',
        attrs: { type: 'button' } });
    const location = buildElement('input', { className: 'roots-location', attrs: { type: 'text', size: 48,
        placeholder: 'The full path of a folder on this computer, or of a share',
        'aria-label': 'The folder to move the root to' } });
    const browse = buildElement('button', { className: 'btn btn-secondary btn-sm roots-browse',
        text: 'Browse...', attrs: { type: 'button' } });
    const looks = buildElement('button', { className: 'btn btn-secondary btn-sm roots-look',
        text: 'Check this place', attrs: { type: 'button' } });
    browse.addEventListener('click', () => browseFolder(location, ready));
    looks.addEventListener('click', () => checkPlace());
    location.addEventListener('input', ready);
    location.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            checkPlace();
        }
    });
    show(panel, [
        buildElement('p', { className: 'roots-note', text: BEFORE_MOVING }),
        buildElement('p', { text: 'Move it to this folder on this computer:' }),
        buildElement('div', { className: 'roots-row' }, [location, browse, looks]),
        check, overrideLabel, buildElement('div', { className: 'roots-row' }, [confirm, cancel])]);
    panel.classList.remove('hidden');

    let checked = null;   // what the last check answered, for the place now typed
    function ready() {
        checked = null;
        confirm.disabled = true;
        overrideLabel.classList.add('hidden');
        override.checked = false;
        show(check, []);
    }

    function body(dryRun) {
        const asked = { root: entry.name, dry_run: dryRun, from: entry.active || undefined,
            location: location.value.trim() };
        if (!dryRun && override.checked) asked.override = true;
        return asked;
    }

    function ask(dryRun) {
        return post('/api/roots/change-location', body(dryRun));
    }

    function checkPlace() {
        if (!location.value.trim()) {
            lines(check, ['Name the folder first.'], 'validation-error');
            return Promise.resolve(null);
        }
        ready();
        lines(check, ['Looking at a sample of what is there...'], 'roots-wait');
        return ask(true).then(answer => {
            if (!answer) {
                lines(check, ['The server did not answer.'], 'validation-error');
                return answer;
            }
            if (answer.unchanged) {
                lines(check, [answer.message], '');
                return answer;
            }
            const found = answer.verify ? verifyLines(answer.verify) : [];
            if (!answer.success) {
                lines(check, [answer.error, found[0]], 'validation-error');
                // A poor result may be accepted, by someone who says so; nothing else may.
                if (answer.would_refuse) overrideLabel.classList.remove('hidden');
                checked = answer.would_refuse ? { poor: true } : null;
                return answer;
            }
            lines(check, [...found, answer.writes_to
                ? `If you go on: ${answer.writes_to} Nothing else changes: no photo file, no row.` : ''], '');
            checked = { poor: false };
            confirm.disabled = false;
            return answer;
        }).catch(err => {
            console.error('Could not check the place:', err);
            lines(check, ['Could not check it.'], 'validation-error');
            return null;
        });
    }

    override.addEventListener('change', () => { confirm.disabled = !(checked && (!checked.poor || override.checked)); });
    cancel.addEventListener('click', () => { panel.classList.add('hidden'); show(panel, []); });
    confirm.addEventListener('click', () => confirmMove(entry, ask, confirm, check, panel));
    return panel;
}

function browseFolder(input, changed) {
    return api.json('/api/browse-folder').then(data => {
        if (data && data.path) {
            input.value = data.path;
            changed();
        }
        return data;
    }).catch(err => {
        console.error('Could not browse:', err);
        return null;
    });
}

/** The second press: make the change, once. */
function confirmMove(entry, ask, button, check, panel) {
    if (state.roots.busy) return Promise.resolve(null);
    state.roots.busy = true;
    button.disabled = true;
    lines(check, ['Changing it...'], 'roots-wait');
    return ask(false).then(answer => {
        state.roots.busy = false;
        if (!answer || !answer.success) {
            lines(check, [(answer && answer.error) || 'Could not change it.'], 'validation-error');
            button.disabled = false;
            return answer;
        }
        panel.classList.add('hidden');
        return loadRoots().then(() => {
            say(answer.message || 'Changed.');
            return answer;
        });
    }).catch(err => {
        state.roots.busy = false;
        button.disabled = false;
        console.error('Could not change the location:', err);
        lines(check, ['Could not change it.'], 'validation-error');
        return null;
    });
}

// ---- The dialog -------------------------------------------------------------------------------

/** Are files written to the place the root is kept at? The server says where; the page compares paths. */
function writesToKeptPlace(entry) {
    return !entry.writes_to_place || samePath(entry.writes_to_place, entry.active);
}

/**
 * What is said of a root besides where it is kept. The earlier place is always in the open: it is
 * a separate copy that nothing written now reaches. Where files are written is folded away while it
 * is the place above (redundant), and open, with its sentence, when it is somewhere else.
 */
function moreAbout(entry) {
    const nodes = [];
    if (entry.writes_to) {
        const redundant = writesToKeptPlace(entry);
        const writes = buildElement('p', { className: 'roots-writes', text: entry.writes_to });
        nodes.push(redundant
            ? buildElement('details', { className: 'roots-more' },
                [buildElement('summary', { text: 'Where files are written' }), writes])
            : buildElement('details', { className: 'roots-more', attrs: { open: '' } },
                [buildElement('summary', { text: 'Files are written elsewhere' }), writes]));
    }
    if (entry.previous) {
        nodes.push(buildElement('p', { className: 'roots-previous',
            text: `Before that: ${entry.previous}. It is a separate copy: nothing written now goes there. `
                + 'Moving again forgets it.' }));
    }
    return nodes;
}

function rootRow(entry) {
    const move = buildElement('button', { className: 'btn btn-secondary btn-sm roots-move',
        text: 'Change location...', attrs: { type: 'button' } });
    const facts = [];
    if (entry.mapped) {
        facts.push(buildElement('p', { className: 'roots-place', text: `Kept at ${entry.active}` }));
        facts.push(...moreAbout(entry));
        if (entry.shared_with && entry.shared_with.length) {
            facts.push(buildElement('p', { className: 'roots-shared',
                text: `${entry.shared_with.join(', ')} also uses this root: moving it moves it for them too.` }));
        }
    } else {
        facts.push(buildElement('p', { className: 'roots-place validation-error',
            text: 'This computer does not place this root yet. Change location says where it is.' }));
    }
    const row = buildElement('li', { className: 'roots-root', data: { root: entry.name } }, [
        buildElement('div', { className: 'roots-title' }, [
            buildElement('strong', { text: entry.name }),
            buildElement('span', { className: 'roots-address', text: entry.address ? `  ${entry.address}` : '' }),
            buildElement('span', { className: 'roots-rows',
                text: entry.rows === null || entry.rows === undefined ? '' : `${count(entry.rows)} photos` }),
        ]),
        ...facts,
        buildElement('div', { className: 'roots-row' }, [move]),
        buildElement('div', { className: 'roots-panel hidden' }),
    ]);
    move.addEventListener('click', () => openPanel(entry, row));
    return row;
}

function renderRoots(data) {
    const body = state.roots.body;
    const problem = data.problem ? [buildElement('p', { className: 'validation-error', text: data.problem })] : [];
    if (!data.roots.length) {
        show(body, [
            ...problem,
            buildElement('p', { className: 'roots-empty',
                text: 'This library has no roots; nothing to change here.' }),
        ]);
        return;
    }
    show(body, [...problem, buildElement('ul', { className: 'roots-list' }, data.roots.map(rootRow))]);
    if (data.busy && data.busy.length) {
        say(`Moving a root waits while ${data.busy.join(' and ')}.`);
    }
}

/** Read the roots and show them. */
export function loadRoots() {
    const token = state.roots.token;
    return api.json('/api/roots').then(data => {
        if (token !== state.roots.token || !state.roots.open) return data;
        if (!data || !Array.isArray(data.roots)) {
            show(state.roots.body, [buildElement('p', { className: 'validation-error',
                text: (data && data.error) || 'Could not read the roots.' })]);
            return data;
        }
        renderRoots(data);
        return data;
    }).catch(err => {
        console.error('Could not read the roots:', err);
        show(state.roots.body, [buildElement('p', { className: 'validation-error',
            text: 'Could not read the roots.' })]);
        return null;
    });
}

function buildRootsDialog() {
    const close = buildElement('button', { className: 'close-btn', text: TIMES, title: 'Close',
        attrs: { type: 'button', 'aria-label': 'Close' } });
    const done = buildElement('button', { className: 'btn btn-secondary', text: 'Close', attrs: { type: 'button' } });
    state.roots.body = buildElement('div', { className: 'modal-body roots-body' });
    state.roots.status = buildElement('span', { className: 'roots-status', attrs: { role: 'status' } });
    const modal = buildElement('div', {
        id: 'roots-modal',
        className: 'modal roots-modal hidden',
        attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'roots-title' },
    }, [
        buildElement('div', { className: 'modal-content roots-content' }, [
            buildElement('div', { className: 'modal-header' }, [
                buildElement('h3', { id: 'roots-title', text: 'Roots' }), close]),
            buildElement('p', { className: 'roots-about', text: 'The library remembers each photo as a root and '
                + 'the path under it; this computer says where each root is. Moving a root changes only that: '
                + 'no photo file and no row.' }),
            state.roots.body,
            buildElement('div', { className: 'modal-footer roots-footer' }, [state.roots.status, done]),
        ]),
    ]);
    document.body.append(modal);
    close.addEventListener('click', closeRoots);
    done.addEventListener('click', closeRoots);
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape' || modal.classList.contains('hidden')) return;
        e.preventDefault();
        e.stopPropagation();
        closeRoots();
    });
    state.roots.modal = modal;
}

/** Open the dialog, the roots read now. */
export function openRoots() {
    if (!state.roots.modal) buildRootsDialog();
    state.roots.opener = document.activeElement;
    state.roots.open = true;
    state.roots.token += 1;
    state.roots.modal.classList.remove('hidden');
    show(state.roots.body, [buildElement('p', { className: 'roots-loading', text: 'Reading the roots...' })]);
    say('');
    return loadRoots();
}

export function closeRoots() {
    if (!state.roots.modal) return;
    state.roots.modal.classList.add('hidden');
    state.roots.open = false;
    state.roots.token += 1;
    const opener = state.roots.opener;
    if (opener && typeof opener.focus === 'function') opener.focus();
}
