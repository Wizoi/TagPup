/**
 * The library's history, from the gear: its last changes, and Undo beside each that can
 * be undone (docs/ARCHITECTURE.md, phase 7.5; docs/findings.md, #266).
 *
 * Every bulk edit, file write, save and settings change is a change of the library's
 * journal, and until now only the CLI and the MCP server could undo one. This lists what
 * /api/history answers -- each change's operation, when, and how many rows and photo
 * files it touched; never its values, which can name people -- newest first. Undo asks
 * the server to rehearse first (/api/history/<id>/undo, apply false): nothing is
 * written, and the row says what would be put back and what refused. The button becomes
 * Confirm undo, and a second press makes it. A photo file changed since the change is
 * refused, named by photo id, and never overwritten.
 *
 * The writes of one action sent in chunks are ONE line, "Tags on 50 photos (3 writes)"
 * (/api/history's `members`, tagpup.services.journal.lines); its Undo posts each of them,
 * newest first, through the same per-change route, rehearsed and then confirmed alike.
 *
 * After an undo that changed something the page is told (`undone`), so it can show what
 * its photos hold now. The dialog is a `.modal`, so the pages' shortcuts leave it alone
 * while it is open (web/common/dialog.js); its frame is its own (history-dialog.css), as
 * TagPup's page styles no `.modal`.
 */
import { api } from './api.js';
import { holdFocus, releaseFocus } from './dialog.js';
import { buildElement, replaceContent } from './dom.js';

const historyDialog = {
    modal: null,
    title: null,
    body: null,
    status: null,
    /** What to do once an undo changed something: the page's own refresh. */
    undone: null,
    /** The change whose undo was rehearsed and waits for Confirm undo, or null. */
    confirming: null,
    busy: false,
};

function say(text) {
    if (historyDialog.status) historyDialog.status.textContent = text || '';
}

function total(counts) {
    let sum = 0;
    for (const value of Object.values(counts || {})) {
        sum += typeof value === 'number' ? value : total(value);
    }
    return sum;
}

/** What a change touched, by count: "3 photo file(s)", "12 row(s)". */
export function describeChange(change) {
    const parts = [];
    const files = total(change.files);
    const rows = total(change.rows);
    if (files) parts.push(`${files} photo file(s)`);
    if (rows) parts.push(`${rows} row(s)`);
    return parts.join(', ');
}

/** A group's line, by what is left of it: "Tags on 50 photos (3 writes)". */
function groupWords(change) {
    const photos = (change.summary && change.summary.photos) || 0;
    return `Tags on ${photos} ${photos === 1 ? 'photo' : 'photos'} (${change.writes} ${change.writes === 1 ? 'write' : 'writes'})`;
}

/** What an undo's answer says, for a person to read. */
function outcome(answer, rehearsed) {
    if (!answer) return 'The server did not answer.';
    if (answer.refused) return `Cannot be undone: ${answer.refused}`;
    const refused = (answer.errors || []).map(e => `${e.what}: ${e.why}`);
    const differences = answer.differences || [];
    if (rehearsed) {
        const would = `Undoing it would put back ${answer.would_put_back || 0} item(s)`;
        const notes = differences.length ? `; not put back: ${differences.join('; ')}` : '';
        return `${would}${notes}. Press Confirm undo to do it.`;
    }
    const done = `Undone: ${answer.changed} put back`;
    return refused.length ? `${done}; not undone: ${refused.join('; ')}` : `${done}.`;
}

/** What the answers to a group's undo ({id, answer} each) say, summed, for a person to read. */
function groupOutcome(answers, rehearsed) {
    const sum = key => answers.reduce((n, { answer }) => n + ((answer && answer[key]) || 0), 0);
    const refused = answers.flatMap(({ id, answer }) => (!answer ? [`#${id}: the server did not answer`]
        : answer.refused ? [`#${id}: ${answer.refused}`]
            : (answer.errors || []).map(e => `${e.what}: ${e.why}`)));
    if (rehearsed) {
        const notes = refused.length ? `; not now: ${refused.join('; ')}` : '';
        return `Undoing its ${answers.length} write(s) would put back ${sum('would_put_back')} item(s)${notes}. Press Confirm undo to do it.`;
    }
    const done = `Undone: ${sum('changed')} put back`;
    return refused.length ? `${done}; not undone: ${refused.join('; ')}` : `${done}.`;
}

function changeRow(change) {
    const grouped = Array.isArray(change.members);
    const when = change.undone || change.applied || change.created || '';
    const note = buildElement('div', { className: 'history-note', attrs: { role: 'status' } });
    const children = [
        buildElement('div', { className: 'history-what' }, [
            buildElement('span', { className: 'history-operation', text: grouped ? groupWords(change) : change.operation }),
            buildElement('span', { className: 'history-id', text: `#${change.id}` }),
        ]),
        buildElement('div', { className: 'history-detail' }, [
            buildElement('span', { className: 'history-when', text: when }),
            buildElement('span', { className: 'history-counts', text: describeChange(change) }),
            buildElement('span', { className: `history-status history-status-${change.status}`, text: change.status }),
        ]),
        note,
    ];
    const row = buildElement('li', { className: 'history-change', data: { change: String(change.id) } }, children);
    if (change.undoable) {
        const button = buildElement('button', {
            className: 'btn btn-secondary btn-sm history-undo',
            text: 'Undo',
            title: `Undo change ${change.id}: see what it would put back first`,
            attrs: { type: 'button' },
            data: { change: String(change.id) },
        });
        button.addEventListener('click', () => (grouped ? undoGroup : undoChange)(change, button, note));
        row.querySelector('.history-what').append(button);
    } else if (change.why_not) {
        // Why not, as the server's undo would refuse it: no button to press in vain.
        row.append(buildElement('div', { className: 'history-why', text: change.why_not }));
    }
    return row;
}

function renderHistory(data) {
    historyDialog.confirming = null;
    historyDialog.title.textContent = `History: ${data.library}`;
    const changes = data.changes || [];
    if (!changes.length) {
        replaceContent(historyDialog.body, buildElement('p', { className: 'history-empty', text: 'No changes yet.' }));
    } else {
        replaceContent(historyDialog.body, buildElement('ul', { className: 'history-list' }, changes.map(changeRow)));
    }
}

function loadHistory() {
    return api.json('/api/history').then(data => {
        if (!data || !Array.isArray(data.changes)) {
            replaceContent(historyDialog.body, buildElement('p', { className: 'validation-error',
                text: (data && data.error) || 'Could not read the history.' }));
            return data;
        }
        renderHistory(data);
        return data;
    }).catch(err => {
        console.error('Could not read the history:', err);
        replaceContent(historyDialog.body, buildElement('p', { className: 'validation-error',
            text: 'Could not read the history.' }));
        return null;
    });
}

function askUndo(change, apply) {
    return api.json(`/api/history/${change.id}/undo`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ apply }),
    });
}

/**
 * Undo, the first press: rehearsed, and the button becomes Confirm undo. The second
 * press makes it, then the list is read again and the page told.
 */
export function undoChange(change, button, note) {
    if (historyDialog.busy) return Promise.resolve(null);
    historyDialog.busy = true;
    button.disabled = true;
    const confirming = historyDialog.confirming === change.id;
    note.textContent = confirming ? 'Undoing...' : 'Checking what undoing it would do...';
    return askUndo(change, confirming).then(answer => {
        historyDialog.busy = false;
        button.disabled = false;
        if (!confirming) {
            if (!answer || answer.refused || !answer.success) {
                note.textContent = outcome(answer, false);
                return answer;
            }
            historyDialog.confirming = change.id;
            button.textContent = 'Confirm undo';
            note.textContent = outcome(answer, true);
            return answer;
        }
        historyDialog.confirming = null;
        const text = outcome(answer, false);
        return loadHistory().then(() => {
            say(text);
            if (answer && answer.changed && typeof historyDialog.undone === 'function') historyDialog.undone(answer);
            return answer;
        });
    }).catch(err => {
        historyDialog.busy = false;
        button.disabled = false;
        console.error('Could not undo:', err);
        note.textContent = 'Could not undo it.';
        return null;
    });
}

/**
 * Undo of a group's line: each of its changes not undone posted newest first through the one per-change route (a newer
 * write of a file is undone before an older one). The first press rehearses those the server offers now, the second makes
 * every one; a refused one is said and the rest are made. Stopped part-way (a closed tab, a server gone), the list read
 * again shows what is left, and Undo makes the rest.
 */
export function undoGroup(change, button, note) {
    if (historyDialog.busy) return Promise.resolve(null);
    historyDialog.busy = true;
    button.disabled = true;
    const confirming = historyDialog.confirming === change.id;
    const asked = confirming ? change.members : change.members.filter(member => !member.why_not);
    const answers = [];
    const next = index => {
        if (index >= asked.length) return Promise.resolve();
        note.textContent = `${confirming ? 'Undoing' : 'Checking'} write ${index + 1} of ${asked.length}...`;
        return askUndo(asked[index], confirming).then(answer => {
            answers.push({ id: asked[index].id, answer });
            return next(index + 1);
        });
    };
    return next(0).then(() => {
        historyDialog.busy = false;
        button.disabled = false;
        if (!confirming) {
            note.textContent = groupOutcome(answers, true);
            if (answers.some(({ answer }) => answer && answer.success && !answer.refused)) {
                historyDialog.confirming = change.id;
                button.textContent = 'Confirm undo';
            }
            return answers;
        }
        historyDialog.confirming = null;
        const text = groupOutcome(answers, false);
        const changed = answers.find(({ answer }) => answer && answer.changed);
        return loadHistory().then(() => {
            say(text);
            if (changed && typeof historyDialog.undone === 'function') historyDialog.undone(changed.answer);
            return answers;
        });
    }).catch(err => {
        historyDialog.busy = false;
        button.disabled = false;
        historyDialog.confirming = null;
        console.error('Could not undo:', err);
        note.textContent = 'Could not undo it.';
        // Part of it may be undone: the list read again shows what is left, and Undo makes the rest.
        return confirming ? loadHistory().then(() => { say('Could not undo all of it: the list shows what is left.'); return null; }) : null;
    });
}

/** The dialog's frame, built once and put at the end of the page's body. */
function buildHistoryDialog() {
    // The multiplication sign, by its code: no escape in this file can be mangled in transit.
    const close = buildElement('button', { className: 'close-btn', text: String.fromCharCode(0xd7), title: 'Close',
        attrs: { type: 'button', 'aria-label': 'Close' } });
    const done = buildElement('button', { className: 'btn btn-secondary', text: 'Close', attrs: { type: 'button' } });
    historyDialog.title = buildElement('h3', { id: 'history-title', text: 'History' });
    historyDialog.body = buildElement('div', { className: 'modal-body history-body' });
    historyDialog.status = buildElement('span', { className: 'history-status-line', attrs: { role: 'status' } });
    const modal = buildElement('div', {
        id: 'history-modal',
        className: 'modal history-modal hidden',
        attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'history-title' },
    }, [
        buildElement('div', { className: 'modal-content history-content' }, [
            buildElement('div', { className: 'modal-header' }, [historyDialog.title, close]),
            historyDialog.body,
            buildElement('div', { className: 'modal-footer history-footer' }, [historyDialog.status, done]),
        ]),
    ]);
    document.body.append(modal);
    close.addEventListener('click', closeHistory);
    done.addEventListener('click', closeHistory);
    historyDialog.modal = modal;
}

/**
 * Open the dialog on the page's library, its changes read now. `undone` is called with
 * the server's answer after an undo that changed something.
 */
export function openHistory({ undone } = {}) {
    if (!historyDialog.modal) buildHistoryDialog();
    historyDialog.undone = undone || null;
    historyDialog.modal.classList.remove('hidden');
    holdFocus(historyDialog.modal, { cancel: closeHistory });
    replaceContent(historyDialog.body, buildElement('p', { className: 'history-loading', text: 'Reading the history...' }));
    say('');
    return loadHistory();
}

export function closeHistory() {
    if (!historyDialog.modal) return;
    historyDialog.modal.classList.add('hidden');
    historyDialog.confirming = null;
    releaseFocus(historyDialog.modal);
}
