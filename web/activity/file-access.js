// File access: which other programs can interfere with TagPup's files -- Windows Defender's
// real-time scanning, the Search indexer, a cloud-sync client, another antivirus -- as findings
// with what to do and the commands to do it (/api/file-access/check; tagpup.services.file_access).
// The owner runs the commands; the page and the server never do. Asked once when the page opens
// and again on "Check again", never on a timer. A warning the owner has dealt with is dismissed
// ("I've dealt with this"), kept in this browser's localStorage by the finding's id, and shows
// greyed as ok.
import { api } from './common/api.js';
import { buildElement } from './common/dom.js';
import { state } from './state.js';
import { fill, none } from './view.js';

/** The browser's key for the findings dealt with: { finding id: true }. */
export const DISMISSED_KEY = 'tagpup.fileAccess.dismissed';

const ICONS = { ok: 'ok', info: 'i', warn: '!' };
const LEVEL_NAMES = { ok: 'Fine', info: 'Note', warn: 'Warning' };

/** The ids dealt with, from this browser; {} when it keeps nothing. */
export function readDismissed() {
    try {
        const found = JSON.parse(window.localStorage.getItem(DISMISSED_KEY) || '{}');
        return found && typeof found === 'object' && !Array.isArray(found) ? found : {};
    } catch (err) {
        return {};
    }
}

function writeDismissed(found) {
    try {
        window.localStorage.setItem(DISMISSED_KEY, JSON.stringify(found));
    } catch (err) {
        // no storage: the dismissal lasts until the page is closed
    }
}

/** Mark finding `id` dealt with (or not, when `dealt` is false), and show the list again. */
export function dismiss(id, dealt = true) {
    const found = readDismissed();
    if (dealt) found[id] = true;
    else delete found[id];
    state.fileAccessDismissed = found;
    writeDismissed(found);
    renderFileAccess();
}

/**
 * Ask the server what can interfere (`refresh` reads again, past the ten minutes it remembers) and
 * show it. A check takes seconds (PowerShell is asked), so the section says it is checking.
 */
export function loadFileAccess(refresh = false) {
    if (state.fileAccessChecking) return Promise.resolve(null);
    state.fileAccessChecking = true;
    state.fileAccessDismissed = readDismissed();
    renderFileAccess();
    return api.site.json(refresh ? '/api/file-access/check?refresh=1' : '/api/file-access/check').then(data => {
        if (!data || data.success === false || !Array.isArray(data.findings)) {
            state.fileAccessError = (data && data.error) || 'The check gave no answer.';
            return data;
        }
        state.fileAccess = data;
        state.fileAccessError = null;
        return data;
    }).catch(err => {
        state.fileAccessError = `The check failed: ${err.message}`;
        return null;
    }).finally(() => {
        state.fileAccessChecking = false;
        renderFileAccess();
    });
}

/** Put `text` on the clipboard; true when it did. Falls back to selecting it, for the person to copy. */
function copyText(text, box) {
    try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text);
            return true;
        }
    } catch (err) {
        // fall through to selecting
    }
    try {
        const range = document.createRange();
        range.selectNodeContents(box);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        return !!document.execCommand && document.execCommand('copy');
    } catch (err) {
        return false;
    }
}

function commandsBox(commands) {
    const box = buildElement('pre', { className: 'commands', text: commands.join('\n') });
    const copy = buildElement('button', { className: 'btn copy', text: 'Copy', attrs: { type: 'button' } });
    copy.addEventListener('click', () => {
        copy.textContent = copyText(commands.join('\n'), box) ? 'Copied' : 'Select and copy';
    });
    return buildElement('div', { className: 'commands-box' }, [box, copy]);
}

function findingRow(found, dismissed) {
    const dealt = found.level === 'warn' && !!dismissed[found.id];
    const level = dealt ? 'ok' : found.level;
    const head = [
        buildElement('span', { className: `level-icon level-${level}`, text: ICONS[level] || 'i',
                               attrs: { role: 'img', 'aria-label': LEVEL_NAMES[level] || level } }),
        buildElement('strong', { className: 'finding-title', text: found.title }),
    ];
    if (dealt) head.push(buildElement('small', { className: 'detail', text: 'dealt with' }));
    const row = buildElement('div', {
        className: `finding finding-${level}${dealt ? ' dismissed' : ''}`, data: { id: found.id, level: found.level },
    }, [buildElement('div', { className: 'finding-head' }, head)]);
    if (found.why) row.append(buildElement('p', { className: 'why', text: found.why }));
    if (found.what_to_do && !dealt) {
        row.append(buildElement('p', { className: 'what' }, [buildElement('strong', { text: 'What to do: ' }), found.what_to_do]));
    }
    if ((found.commands || []).length && !dealt) row.append(commandsBox(found.commands));
    if (found.level === 'warn') {
        const button = buildElement('button', {
            className: 'link dismiss', text: dealt ? 'Show again' : "I've dealt with this", attrs: { type: 'button' },
        });
        button.addEventListener('click', () => dismiss(found.id, !dealt));
        row.append(button);
    }
    return row;
}

/** Show what state.fileAccess holds. */
export function renderFileAccess() {
    const data = state.fileAccess;
    const dismissed = state.fileAccessDismissed || {};
    const again = buildElement('button', {
        id: 'file-access-again', className: 'btn', text: state.fileAccessChecking ? 'Checking...' : 'Check again',
        attrs: { type: 'button', disabled: state.fileAccessChecking },
    });
    again.addEventListener('click', () => loadFileAccess(true));
    const stamp = buildElement('span', {
        className: 'status', id: 'file-access-checked',
        text: data ? `Last checked ${data.checked_at}${data.cached ? ' (remembered for ten minutes)' : ''}` : '',
    });
    const list = (data && data.findings || []).map(found => findingRow(found, dismissed));
    fill(document.getElementById('file-access-body'),
        buildElement('p', { className: 'about',
            text: 'A program that scans, indexes, syncs or backs up a file opens it, and a file open in another program '
                + 'refuses TagPup\'s save, rename or rewrite. These are the ones on this PC that can. Nothing here changes '
                + 'a setting: the commands are for you to run, if you choose to.' }),
        state.fileAccessError ? buildElement('p', { className: 'error', text: state.fileAccessError }) : null,
        ...(list.length ? list : [none(state.fileAccessChecking ? 'Checking: this takes a few seconds.' : 'Nothing checked yet.')]),
        buildElement('div', { className: 'file-access-controls' }, [again, stamp]));
}
