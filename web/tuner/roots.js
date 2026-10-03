// Roots, from the gear: where this computer keeps each root of the library, Verify, Change
// location and Change back (docs/ARCHITECTURE.md, "Roots and machines"; /api/roots).
//
// The library remembers a photo as a root and the path under it; this computer's map says
// where the root is. Moving a root edits that map and nothing else: no photo file and no row
// changes. A move is checked first -- a sample of what the new place holds -- and only then
// confirmed; a poor result needs the person to say they mean it. The page's state is
// state.js's; requests go through api.js; paths are the server's, shown as they come.
import { api } from './common/api.js';
import { buildElement, replaceContent } from './common/dom.js';
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

/** What the last Verify found, from its counts, as a sentence. */
export function lastVerifyText(last) {
    if (!last) return 'Not checked yet.';
    const what = last.mode === 'all' ? 'every row' : `a sample of ${count(last.checked)} rows`;
    if (last.outcome === 'failed') return `Last checked ${last.when}: it could not finish (${what}).`;
    const found = [`${count(last.matches)} match`, `${count(last.differs)} changed since they were indexed`,
        `${count(last.missing)} missing`];
    return `Last checked ${last.when}, ${what}: ${found.join(', ')}.`;
}

/** What a Verify answered, as lines for a person: its summary, and what is wrong with the place. */
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

function rootElement(name) {
    for (const element of state.roots.body.querySelectorAll('.roots-root')) {
        if (element.dataset.root === name) return element;
    }
    return null;
}

// ---- Verify ------------------------------------------------------------------------------

function showProgress(element, status) {
    const box = element.querySelector('.roots-progress');
    box.classList.remove('hidden');
    box.querySelector('.roots-progress-text').textContent =
        `Looking at every row: ${count(status.checked)} of ${count(status.rows)} (${count(status.folders)} folders)` +
        (status.cancelling ? ', stopping...' : '...');
}

function hideProgress(element) {
    element.querySelector('.roots-progress').classList.add('hidden');
}

function showFinished(element, status) {
    hideProgress(element);
    const result = element.querySelector('.roots-result');
    if (status.result) {
        lines(result, verifyLines(status.result), status.result.poor ? 'validation-error' : '');
    } else {
        lines(result, [status.error || 'The check stopped.'], 'validation-error');
    }
}

function schedulePoll() {
    if (state.roots.timer !== null || !state.roots.open) return;
    state.roots.timer = window.setTimeout(pollRoots, state.roots.pollMs);
}

/** Ask how the full checks are getting on; ask again while one runs. */
export function pollRoots() {
    state.roots.timer = null;
    if (!state.roots.open) return Promise.resolve(null);
    const token = state.roots.token;
    return api.json('/api/roots').then(data => {
        if (token !== state.roots.token || !state.roots.open || !data || !Array.isArray(data.roots)) return data;
        if (data.poll_ms) state.roots.pollMs = data.poll_ms;
        let running = false;
        for (const entry of data.roots) {
            const element = rootElement(entry.name);
            const status = entry.verifying;
            if (!element) continue;
            if (!status) {
                // The run the dialog was following is gone: the server restarted, and a run is in its memory.
                if (state.roots.watching.has(entry.name)) {
                    state.roots.watching.delete(entry.name);
                    hideProgress(element);
                    lines(element.querySelector('.roots-result'),
                        ['The check stopped (the server restarted); run it again.'], 'validation-error');
                }
                continue;
            }
            if (status.state === 'running') {
                running = true;
                state.roots.watching.add(entry.name);
                showProgress(element, status);
            } else if (state.roots.watching.has(entry.name)) {
                state.roots.watching.delete(entry.name);
                showFinished(element, status);
            }
        }
        if (running) schedulePoll();
        return data;
    }).catch(err => {
        console.error('Could not ask how the check is going:', err);
        return null;
    });
}

function verifySample(entry, element, button) {
    const result = element.querySelector('.roots-result');
    button.disabled = true;
    lines(result, ['Looking at a sample of the photos...'], 'roots-wait');
    return post('/api/roots/verify', { root: entry.name }).then(answer => {
        button.disabled = false;
        if (!answer || !answer.success) {
            lines(result, [(answer && answer.error) || 'Could not check it.'], 'validation-error');
            return answer;
        }
        lines(result, verifyLines(answer.verify), answer.verify.poor ? 'validation-error' : '');
        return answer;
    }).catch(err => {
        button.disabled = false;
        console.error('Could not verify:', err);
        lines(result, ['Could not check it.'], 'validation-error');
        return null;
    });
}

function verifyAll(entry, element, button) {
    const result = element.querySelector('.roots-result');
    button.disabled = true;
    return post('/api/roots/verify', { root: entry.name, all: true }).then(answer => {
        button.disabled = false;
        if (!answer || !answer.success) {
            lines(result, [(answer && answer.error) || 'Could not start it.'], 'validation-error');
            return answer;
        }
        show(result, []);
        state.roots.watching.add(entry.name);
        showProgress(element, answer.status);
        schedulePoll();
        return answer;
    }).catch(err => {
        button.disabled = false;
        console.error('Could not verify every row:', err);
        lines(result, ['Could not start it.'], 'validation-error');
        return null;
    });
}

function cancelVerify(entry, element) {
    return post('/api/roots/verify-cancel', { root: entry.name }).then(answer => {
        if (answer && answer.cancelled) {
            element.querySelector('.roots-progress-text').textContent = 'Stopping...';
        }
        return answer;
    }).catch(err => {
        console.error('Could not cancel the check:', err);
        return null;
    });
}

// ---- Change location, Change back ----------------------------------------------------------

/**
 * The panel under a root for moving it. `mode` is 'move' (a folder to name, then Check) or
 * 'back' (the previous place is checked at once). Nothing is changed until Move is pressed,
 * and Move is pressed once: a second press while the first is on its way does nothing.
 */
function openPanel(entry, element, mode) {
    const panel = element.querySelector('.roots-panel');
    const check = buildElement('div', { className: 'roots-check', attrs: { role: 'status' } });
    const override = buildElement('input', { attrs: { type: 'checkbox' } });
    const overrideLabel = buildElement('label', { className: 'roots-override hidden' },
        [override, ' I mean it: change it anyway']);
    const confirm = buildElement('button', { className: 'btn btn-primary btn-sm roots-confirm',
        text: mode === 'back' ? 'Change back' : 'Move it here', attrs: { type: 'button', disabled: true } });
    const cancel = buildElement('button', { className: 'btn btn-secondary btn-sm roots-cancel', text: 'Cancel',
        attrs: { type: 'button' } });
    const parts = [];
    let location = null;
    if (mode === 'move') {
        location = buildElement('input', { className: 'roots-location', attrs: { type: 'text', size: 48,
            placeholder: 'The full path of a folder on this computer, or of a share',
            'aria-label': 'The folder to move the root to' } });
        const browse = buildElement('button', { className: 'btn btn-secondary btn-sm roots-browse',
            text: 'Browse...', attrs: { type: 'button' } });
        const looks = buildElement('button', { className: 'btn btn-secondary btn-sm roots-look',
            text: 'Check this place', attrs: { type: 'button' } });
        parts.push(buildElement('p', { text: 'Move it to this folder on this computer:' }),
            buildElement('div', { className: 'roots-row' }, [location, browse, looks]));
        browse.addEventListener('click', () => browseFolder(location, ready));
        looks.addEventListener('click', () => checkPlace());
        location.addEventListener('input', ready);
        location.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                checkPlace();
            }
        });
    }
    parts.unshift(buildElement('p', { className: 'roots-note', text: BEFORE_MOVING }));
    parts.push(check, overrideLabel, buildElement('div', { className: 'roots-row' }, [confirm, cancel]));
    show(panel, parts);
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
        const asked = { root: entry.name, dry_run: dryRun, from: entry.active || undefined };
        if (mode === 'move') asked.location = location.value.trim();
        if (!dryRun && override.checked) asked.override = true;
        return asked;
    }

    function ask(dryRun) {
        return post(mode === 'back' ? '/api/roots/change-back' : '/api/roots/change-location', body(dryRun));
    }

    function checkPlace() {
        if (mode === 'move' && !location.value.trim()) {
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
                lines(check, [answer.error, ...found.slice(0, 1)], 'validation-error');
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
    if (mode === 'back') checkPlace();
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

function rootRow(entry) {
    const verify = buildElement('button', { className: 'btn btn-secondary btn-sm roots-verify', text: 'Verify',
        title: 'Look at a sample of the photos: are they there, and as they were when indexed?',
        attrs: { type: 'button', disabled: !entry.mapped } });
    const verifyEvery = buildElement('button', { className: 'btn btn-secondary btn-sm roots-verify-all',
        text: 'Verify all', title: 'Look at every photo, and count the photos that have no row (it takes a while)',
        attrs: { type: 'button', disabled: !entry.mapped } });
    const move = buildElement('button', { className: 'btn btn-secondary btn-sm roots-move',
        text: 'Change location...', attrs: { type: 'button' } });
    const back = buildElement('button', { className: 'btn btn-secondary btn-sm roots-back', text: 'Change back',
        title: entry.previous ? `Go back to ${entry.previous}` : 'There is no earlier place to go back to',
        attrs: { type: 'button', disabled: !entry.previous } });
    const cancel = buildElement('button', { className: 'btn btn-secondary btn-sm roots-cancel-verify',
        text: 'Cancel', attrs: { type: 'button' } });
    const progress = buildElement('div', { className: 'roots-progress hidden', attrs: { role: 'status' } },
        [buildElement('span', { className: 'roots-progress-text' }), ' ', cancel]);
    const facts = [];
    if (entry.mapped) {
        facts.push(buildElement('p', { className: 'roots-place', text: `Kept at ${entry.active}` }));
        facts.push(buildElement('p', { className: 'roots-writes', text: entry.writes_to }));
        if (entry.previous) {
            facts.push(buildElement('p', { className: 'roots-previous',
                text: `Before that: ${entry.previous}. It is a separate copy: nothing written now goes there. `
                    + 'Moving again forgets it.' }));
        }
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
        buildElement('p', { className: 'roots-last', text: lastVerifyText(entry.last_verify) }),
        buildElement('div', { className: 'roots-row' }, [verify, verifyEvery, move, back]),
        progress,
        buildElement('div', { className: 'roots-result', attrs: { role: 'status' } }),
        buildElement('div', { className: 'roots-panel hidden' }),
    ]);
    verify.addEventListener('click', () => verifySample(entry, row, verify));
    verifyEvery.addEventListener('click', () => verifyAll(entry, row, verifyEvery));
    cancel.addEventListener('click', () => cancelVerify(entry, row));
    move.addEventListener('click', () => openPanel(entry, row, 'move'));
    back.addEventListener('click', () => openPanel(entry, row, 'back'));
    if (entry.verifying && entry.verifying.state === 'running') {
        state.roots.watching.add(entry.name);
        showProgress(row, entry.verifying);
    }
    return row;
}

function renderRoots(data) {
    const body = state.roots.body;
    const problem = data.problem ? [buildElement('p', { className: 'validation-error', text: data.problem })] : [];
    if (!data.roots.length) {
        show(body, [
            ...problem,
            buildElement('p', { className: 'roots-empty',
                text: 'This library has not adopted a root yet; nothing to change here.' }),
            buildElement('p', { text: 'To adopt one, run this (a dry run first; add --apply to convert):' }),
            buildElement('code', { className: 'roots-command', text: data.adopt_hint }),
        ]);
        return;
    }
    show(body, [...problem, buildElement('ul', { className: 'roots-list' }, data.roots.map(rootRow))]);
    if (data.busy && data.busy.length) {
        say(`Moving a root waits while ${data.busy.join(' and ')}.`);
    }
    if (data.roots.some(entry => entry.verifying && entry.verifying.state === 'running')) schedulePoll();
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
        if (data.poll_ms) state.roots.pollMs = data.poll_ms;
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
    if (state.roots.timer !== null) window.clearTimeout(state.roots.timer);
    state.roots.timer = null;
    state.roots.watching.clear();
    const opener = state.roots.opener;
    if (opener && typeof opener.focus === 'function') opener.focus();
}
