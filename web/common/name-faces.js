/**
 * "Name faces from tags": the button of both pages that names the library's faces from the people its photos' tags name
 * (docs/ARCHITECTURE.md, "Name faces from tags"; docs/findings.md, #789).
 *
 * One job on the server (tagpup/jobs/naming_faces.py), shown here as one dialog, whichever page opened it:
 *
 *   1. The plan is read, with a progress bar and Cancel. Nothing is written.
 *   2. The question: "Name N faces in M photos (X by their tag, Y by looking like the person's confirmed faces)? Z are left
 *      for you." Yes writes them, as one change of the library's History. No changes nothing. When it was done before, the
 *      question says so, and Yes is the CLI's --again.
 *   3. Optionally, a second box (off): also group the rest of the faces by who they look like and name those groups from the
 *      tags. It re-derives every automatic name in the library, so its text says so. Not offered for a folder (below).
 *   4. The result, in counts, and for the folder the page has open what changed in it.
 *
 * When the page has a folder open, a first step asks how much to look at (docs/findings.md, #987): "Only this folder and its
 * subfolders" (the default) or the whole library, each with the count of its unnamed faces. The plan, the question and the
 * write are then that scope's, and the question says which. The grouping works on every face of the library and cannot be
 * limited, so a folder is not offered it, and the dialog says why. With no folder open it works on the whole library, as before,
 * and says so at the top. The dialog is a `.modal`, so the pages'
 * shortcuts leave it alone while it is open (web/common/dialog.js). Closing it does not stop the job: the button, pressed
 * again, shows the one that runs. The page is told (`changed`) when names were written, so it shows what its faces hold now.
 */
import { api } from './api.js';
import { buildElement, replaceContent } from './dom.js';
import { baseName } from './paths.js';

/** How often a running job is asked how it is getting on, in milliseconds. */
export const POLL_MS = 1000;

/** The states in which the job has ended. */
const NF_ENDED = ['done', 'cancelled', 'failed', 'expired', 'abandoned'];

const nfDialog = {
    modal: null,
    title: null,
    body: null,
    status: null,
    yes: null,
    no: null,
    cancel: null,
    close: null,
    group: null,
    go: null,
    scope: null,
    /** The first step's choice: the folder it is about (path, and its leaf as offered and as chosen for the question), and the two radios. */
    goFolder: null,
    leaf: null,
    chosen: null,
    onlyFolder: null,
    wholeLibrary: null,
    opener: null,
    /** The page's own: the open folder (or null) and what to do once names were written. */
    folder: null,
    changed: null,
    /** The job being shown, its last status, and the timer that asks again. */
    job: null,
    last: null,
    timer: null,
    /** Changes with each job shown, so an answer that comes late is not drawn over a newer one. */
    token: 0,
    busy: false,
};

function nfNumber(count) {
    return Number(count || 0).toLocaleString('en-US');
}

function nfPlural(count, one, many) {
    return nfNumber(count) + ' ' + (count === 1 ? one : many);
}

/** What the dialog says it works on: the folder chosen (by its leaf, when this page chose it), or the whole library. */
export function scopeLabel(onlyFolder, leaf) {
    if (!onlyFolder) return 'the whole library';
    return (leaf ? 'the folder "' + leaf + '"' : 'the open folder') + ' and its subfolders';
}

/**
 * The question the plan is shown as: "Name 10,477 faces in 9,624 photos (5,093 by their tag, 5,384 by ...)? ..." With `scope`
 * (scopeLabel) it begins "In the whole library:" or "In the folder "..." and its subfolders:", so the question says which.
 */
export function questionText(plan, scope) {
    const left = nfPlural(plan.left, 'photo is', 'photos are');
    const where = scope ? 'In ' + scope + ': ' : '';
    if (!plan.faces) return where + (scope ? 'no' : 'No') + ' face can be named from its tag. ' + left + ' left for you.';
    return where + (scope ? 'name ' : 'Name ') + nfPlural(plan.faces, 'face', 'faces') + ' in ' + nfPlural(plan.photos, 'photo', 'photos') + ' ('
        + nfNumber(plan.by_tag) + ' by their tag, ' + nfNumber(plan.by_comparison)
        + " by looking like the person's confirmed faces)? " + left + ' left for you.';
}

/** The sentence at the top of the dialog, for a scope. */
const SCOPE_LINES = {
    library: "This works on the whole library, not only the open folder. It reads the photos' tags and the faces already named.",
    folder: "This works on the open folder and its subfolders only. It reads their photos' tags and the faces already named, "
        + 'and compares a face with the confirmed faces of the person wherever in the library they are.',
    choose: 'Choose how much to look at. Nothing is read or written until you press Read the plan.',
};

function nfSetScope(which) {
    if (nfDialog.scope) nfDialog.scope.textContent = SCOPE_LINES[which];
}

/** What changed in the folder the page has open, from the job's counts, or '' when it was not asked. */
export function folderText(inFolder) {
    if (!inFolder || !inFolder.before || !inFolder.after) return '';
    const more = inFolder.after.named - inFolder.before.named;
    const change = more > 0 ? nfNumber(more) + ' more named' : more < 0 ? nfNumber(-more) + ' fewer named' : 'no face named or unnamed';
    return 'In the open folder: ' + change + '; ' + nfNumber(inFolder.after.named) + ' named and '
        + nfNumber(inFolder.after.unnamed) + ' still unnamed.';
}

/** How long a job has run, "2 min 5 s". */
function nfElapsed(seconds) {
    const total = Math.max(0, Math.round(seconds || 0));
    return total >= 60 ? Math.floor(total / 60) + ' min ' + (total % 60) + ' s' : total + ' s';
}

function nfSay(text) {
    if (nfDialog.status) nfDialog.status.textContent = text || '';
}

function nfButtons({ yes = false, no = false, cancel = false, go = false }) {
    nfDialog.go.classList.toggle('hidden', !go);
    nfDialog.yes.classList.toggle('hidden', !yes);
    nfDialog.no.classList.toggle('hidden', !no);
    nfDialog.cancel.classList.toggle('hidden', !cancel);
}

function nfRenderWorking(status) {
    const bar = buildElement('div', { className: 'name-faces-bar', attrs: { role: 'progressbar', 'aria-valuemin': '0',
        'aria-valuemax': '100', 'aria-valuenow': status.percent === null ? null : String(status.percent) } }, [
        buildElement('div', { className: 'name-faces-bar-fill', style: status.percent === null ? '' : `width: ${status.percent}%` }),
    ]);
    if (status.percent === null) bar.classList.add('name-faces-indeterminate');
    const children = [
        buildElement('p', { className: 'name-faces-step', text: status.label || 'Working...' }),
        bar,
        buildElement('p', { className: 'name-faces-elapsed', text: nfElapsed(status.elapsed) + ' so far' }),
    ];
    if (status.phase === 'applying' || status.phase === 'grouping') {
        children.push(buildElement('p', { className: 'name-faces-note',
            text: 'Until it finishes, changes to faces, tags and folders in this library are refused: saving a photo, bulk tags, '
                + 'adding or indexing folders, a sync, deleting photos, and naming faces in TagTuner. Looking is not refused.' }));
    }
    if (status.cancelling) {
        children.push(buildElement('p', { className: 'name-faces-note', text: status.phase === 'applying'
            ? 'Cancel was asked. The write cannot be stopped once begun: it finishes whole, and nothing more is run.'
            : 'Stopping at the next place where nothing is half written...' }));
    } else if (status.phase === 'applying') {
        children.push(buildElement('p', { className: 'name-faces-note',
            text: 'The names are written in one step. Cancel now lets that step finish, and runs nothing after it.' }));
    } else if (status.phase === 'grouping') {
        children.push(buildElement('p', { className: 'name-faces-note',
            text: 'Cancel before the names are written changes nothing; the write itself, one step at the end, finishes whole.' }));
    }
    replaceContent(nfDialog.body, ...children);
    nfButtons({ cancel: status.can_cancel && !status.cancelling });
    nfSay(status.cancelling ? 'Cancelling...' : '');
}

function nfRenderQuestion(status) {
    const plan = status.plan;
    nfSetScope(status.only_folder ? 'folder' : 'library');
    const scope = scopeLabel(status.only_folder, status.only_folder ? nfDialog.chosen : null);
    const children = [buildElement('p', { className: 'name-faces-question', text: questionText(plan, scope) })];
    if (plan.earlier_apply) {
        children.push(buildElement('p', { className: 'name-faces-again', text: 'Applied before. ' + plan.again }));
    }
    nfDialog.undoNote = buildElement('p', { className: 'name-faces-again' });
    if (plan.faces) children.push(nfDialog.undoNote);
    nfDialog.group = null;
    if (status.only_folder) {
        // The grouping re-derives every automatic name of the library: it cannot be limited, so a folder is not offered it.
        children.push(buildElement('p', { className: 'name-faces-no-group',
            text: 'Grouping the rest of the faces is not offered for one folder: it works on every face in the library and '
                + 'cannot be limited to a folder. Choose the whole library for it.' }));
    } else {
        nfDialog.group = buildElement('input', { id: 'name-faces-group', attrs: { type: 'checkbox' } });
        nfDialog.group.addEventListener('change', nfSyncYes);
        children.push(buildElement('label', { className: 'name-faces-group', attrs: { for: 'name-faces-group' } }, [
            nfDialog.group,
            buildElement('span', { text: 'Also group the rest of the faces by who they look like, and name those groups from the tags. '
                + 'This works on every face in the library and re-derives the automatic names it has been given. A name '
                + "its photo's own tags confirm is kept, and so is a name you gave by hand. It can take about an hour on a large library, "
                + 'needs no graphics card, and History cannot undo it. Once it has run, History can no longer be counted on to undo '
                + 'the change that writes those names either.' }),
        ]));
    }
    replaceContent(nfDialog.body, ...children);
    nfButtons({ yes: true, no: true });
    nfSyncYes();
    nfSay('Nothing has been written. This plan is kept for ' + Math.round(status.ask_seconds / 60) + ' minutes.');
}

/** Yes needs something to do: names to write, or the grouping ticked. */
function nfSyncYes() {
    const plan = nfDialog.last && nfDialog.last.plan;
    if (nfDialog.undoNote) {
        nfDialog.undoNote.textContent = nfDialog.group && nfDialog.group.checked
            ? 'With the grouping ticked, History can no longer be counted on to undo this change once grouping has run: '
                + 'grouping rewrites names of faces the change wrote.'
            : 'Yes writes them as one change that History can undo.';
    }
    nfDialog.yes.disabled = nfDialog.busy || !plan || (!plan.faces && !(nfDialog.group && nfDialog.group.checked));
}

function nfRenderEnded(status) {
    const children = [buildElement('p', { className: `name-faces-result name-faces-${status.state}`, text: status.message || '' })];
    const folder = folderText(status.in_folder);
    if (folder) children.push(buildElement('p', { className: 'name-faces-folder', text: folder }));
    replaceContent(nfDialog.body, ...children);
    nfButtons({});
    nfSay('');
}

function nfShow(status) {
    nfDialog.last = status;
    if (status.state !== 'asking') nfSetScope(status.only_folder ? 'folder' : 'library');
    if (status.state === 'asking') nfRenderQuestion(status);
    else if (NF_ENDED.includes(status.state)) nfRenderEnded(status);
    else nfRenderWorking(status);
}

function nfStopPolling() {
    clearTimeout(nfDialog.timer);
    nfDialog.timer = null;
}

function nfWrote(status) {
    return Boolean((status.applied && status.applied.changed) || status.grouped);
}

/** Show `status`, and ask again while the job works; tell the page when it ended with names written. */
function nfFollow(status) {
    nfStopPolling();
    nfDialog.job = status.job;
    nfShow(status);
    const token = ++nfDialog.token;
    if (NF_ENDED.includes(status.state)) {
        if (nfWrote(status) && typeof nfDialog.changed === 'function') nfDialog.changed(status);
        return;
    }
    if (status.state === 'asking') return;
    nfDialog.timer = setTimeout(() => nfPoll(token), POLL_MS);
}

function nfPoll(token) {
    api.json(`/api/name-faces/status?job=${encodeURIComponent(nfDialog.job)}`).then(answer => {
        if (token !== nfDialog.token || !nfDialog.modal || nfDialog.modal.classList.contains('hidden')) return;
        if (!answer || !answer.success) {
            nfSay((answer && answer.error) || 'The server did not answer.');
            nfDialog.timer = setTimeout(() => nfPoll(token), POLL_MS * 3);
            return;
        }
        nfFollow(answer.status);
    }).catch(err => {
        console.error('Could not ask how naming faces is going:', err);
        if (token === nfDialog.token) nfDialog.timer = setTimeout(() => nfPoll(token), POLL_MS * 3);
    });
}

function nfPost(path, body) {
    return api.json(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}

function nfRefused(text) {
    nfStopPolling();
    replaceContent(nfDialog.body, buildElement('p', { className: 'name-faces-result name-faces-failed', text }));
    nfButtons({});
    nfSay('');
}

/**
 * A refusal of a Yes or a No while the question is shown: said under it, and the job read again -- the plan may be
 * gone (let go after its minutes, or by a restart), and a question that cannot be answered is not left on screen.
 */
function nfRecheck(error) {
    nfSay(error);
    return api.json(`/api/name-faces/status?job=${encodeURIComponent(nfDialog.job)}`).then(answer => {
        if (answer && answer.success && answer.status.state !== 'asking') return nfFollow(answer.status);
        if (!answer || !answer.success) return nfRefused(error);
        return null;
    });
}

/** The server's answer to start, confirm or cancel: the job shown, or the refusal said. */
function nfTook(answer) {
    if (answer && answer.success) return nfFollow(answer.status);
    if (answer && answer.job) {
        // Already running (another tab, another page, or this one pressed twice): show the one that runs.
        nfFollow(answer.job);
        nfSay(answer.error);
        return null;
    }
    const error = (answer && answer.error) || 'The server did not answer.';
    if (nfDialog.last && nfDialog.last.state === 'asking') return nfRecheck(error);
    return nfRefused(error);
}

function nfAct(path, body) {
    if (nfDialog.busy) return Promise.resolve(null);
    nfDialog.busy = true;
    nfSyncYes();
    return nfPost(path, body).then(nfTook).catch(err => {
        console.error('Naming faces from tags:', err);
        nfSay('The server did not answer.');
    }).finally(() => {
        nfDialog.busy = false;
        if (nfDialog.last && nfDialog.last.state === 'asking') nfSyncYes();
    });
}

function nfYes() {
    return nfAct('/api/name-faces/confirm', { job: nfDialog.job, group: Boolean(nfDialog.group && nfDialog.group.checked) });
}

function nfNo() {
    return nfAct('/api/name-faces/cancel', { job: nfDialog.job });
}

/** "1 photo", "3 faces", for the counts the choice shows. */
function nfCount(count, one, many) {
    return nfPlural(count, one, many);
}

/**
 * The first step, when the page has a folder open (#987): how much to look at. The folder is the default; the whole library is
 * the explicit other choice, with its count. A folder the library holds no photo under cannot be chosen, and the answer says why.
 */
function nfRenderChoice(folderPath, scope) {
    nfSetScope('choose');
    const leaf = baseName(folderPath) || folderPath;
    const inFolder = scope.folder;
    nfDialog.onlyFolder = buildElement('input', { id: 'name-faces-only-folder', attrs: { type: 'radio', name: 'name-faces-scope' } });
    nfDialog.wholeLibrary = buildElement('input', { id: 'name-faces-whole-library', attrs: { type: 'radio', name: 'name-faces-scope' } });
    nfDialog.onlyFolder.checked = Boolean(inFolder);
    nfDialog.onlyFolder.disabled = !inFolder;
    nfDialog.wholeLibrary.checked = !inFolder;
    const library = scope.library;
    const children = [
        buildElement('label', { className: 'name-faces-choice', attrs: { for: 'name-faces-only-folder' } }, [
            nfDialog.onlyFolder,
            buildElement('span', { text: 'Only this folder and its subfolders: ' + leaf + (inFolder
                ? ' (' + nfCount(inFolder.photos, 'photo', 'photos') + ', ' + nfCount(inFolder.unnamed, 'face still unnamed', 'faces still unnamed') + ')'
                : '') }),
        ]),
        buildElement('label', { className: 'name-faces-choice', attrs: { for: 'name-faces-whole-library' } }, [
            nfDialog.wholeLibrary,
            buildElement('span', { text: 'The whole library (' + nfCount(library.unnamed, 'face still unnamed', 'faces still unnamed')
                + ', ' + nfNumber(library.named) + ' named). Its plan takes longer to read.' }),
        ]),
    ];
    if (!inFolder) {
        children.push(buildElement('p', { className: 'name-faces-note', text: scope.why || 'This folder cannot be chosen.' }));
    }
    children.push(buildElement('p', { className: 'name-faces-note',
        text: 'For a folder only its photos are read and written. A face is still compared with the confirmed faces of the person '
            + 'wherever in the library they are. Grouping the rest of the faces works on the whole library and is only offered for it.' }));
    nfDialog.leaf = leaf;
    replaceContent(nfDialog.body, ...children);
    nfButtons({ go: true });
    nfSay('Nothing has been read or written.');
}

function nfGo(folderPath) {
    const only = Boolean(nfDialog.onlyFolder && nfDialog.onlyFolder.checked);
    nfDialog.chosen = only ? nfDialog.leaf : null;
    return nfAct('/api/name-faces/start', { folder: folderPath, only_folder: only });
}

/** Ask what the choice shows; a job already there is shown as it is instead, since the choice would not change it. */
function nfChoose(folderPath) {
    nfDialog.chosen = null;
    replaceContent(nfDialog.body, buildElement('p', { className: 'name-faces-step', text: 'Starting...' }));
    nfButtons({});
    nfSay('');
    return api.json('/api/name-faces/scope?folder=' + encodeURIComponent(folderPath)).then(answer => {
        if (!answer || !answer.success) return nfRefused((answer && answer.error) || 'The server did not answer.');
        if (answer.job) return nfFollow(answer.job);
        nfDialog.goFolder = folderPath;
        nfRenderChoice(folderPath, answer);
        return null;
    }).catch(err => {
        console.error('Could not ask what naming faces would look at:', err);
        return nfRefused('The server did not answer.');
    });
}

function nfBuild() {
    const close = buildElement('button', { className: 'close-btn', text: String.fromCharCode(0xd7), title: 'Close',
        attrs: { type: 'button', 'aria-label': 'Close' } });
    nfDialog.close = buildElement('button', { className: 'btn btn-secondary', text: 'Close', attrs: { type: 'button' },
        title: 'Close this window. A job that is running goes on.' });
    nfDialog.go = buildElement('button', { className: 'btn btn-primary hidden', text: 'Read the plan', attrs: { type: 'button' },
        title: 'Read what could be named: nothing is written until you answer Yes' });
    nfDialog.yes = buildElement('button', { className: 'btn btn-primary hidden', text: 'Yes', attrs: { type: 'button' },
        title: 'Write these names as one change of the library, which History can undo' });
    nfDialog.no = buildElement('button', { className: 'btn btn-secondary hidden', text: 'No', attrs: { type: 'button' },
        title: 'Change nothing' });
    nfDialog.cancel = buildElement('button', { className: 'btn btn-secondary hidden', text: 'Cancel', attrs: { type: 'button' },
        title: 'Stop, at the next place where nothing is half written' });
    nfDialog.title = buildElement('h3', { id: 'name-faces-title', text: 'Name faces from tags' });
    nfDialog.body = buildElement('div', { className: 'modal-body name-faces-body' });
    nfDialog.status = buildElement('span', { className: 'name-faces-status-line', attrs: { role: 'status' } });
    nfDialog.scope = buildElement('p', { className: 'name-faces-scope', text: SCOPE_LINES.library });
    const modal = buildElement('div', {
        id: 'name-faces-modal',
        className: 'modal name-faces-modal hidden',
        attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'name-faces-title' },
    }, [
        buildElement('div', { className: 'modal-content name-faces-content' }, [
            buildElement('div', { className: 'modal-header' }, [nfDialog.title, close]),
            nfDialog.scope,
            nfDialog.body,
            buildElement('div', { className: 'modal-footer name-faces-footer' }, [
                nfDialog.status, nfDialog.go, nfDialog.yes, nfDialog.no, nfDialog.cancel, nfDialog.close]),
        ]),
    ]);
    document.body.append(modal);
    nfDialog.modal = modal;
    close.addEventListener('click', closeNameFaces);
    nfDialog.close.addEventListener('click', closeNameFaces);
    nfDialog.go.addEventListener('click', () => nfGo(nfDialog.goFolder));
    nfDialog.yes.addEventListener('click', nfYes);
    nfDialog.no.addEventListener('click', nfNo);
    nfDialog.cancel.addEventListener('click', () => nfAct('/api/name-faces/cancel', { job: nfDialog.job }));
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape' || modal.classList.contains('hidden')) return;
        e.preventDefault();
        e.stopPropagation();
        closeNameFaces();
    });
}

/**
 * Open the dialog and start the job: its plan is read, and the question follows (with a folder open, after the choice of how
 * much to look at). `folder()` gives the folder the page has open, or null; `changed(status)` is called once names were written. A job that is already
 * running or waiting for its answer is shown as it is, and not started again.
 */
export function openNameFaces({ folder, changed } = {}) {
    if (!nfDialog.modal) nfBuild();
    nfDialog.folder = typeof folder === 'function' ? folder : null;
    nfDialog.changed = typeof changed === 'function' ? changed : null;
    nfDialog.opener = document.activeElement;
    nfDialog.modal.classList.remove('hidden');
    const start = () => {
        nfDialog.job = null;
        nfDialog.last = null;
        nfDialog.chosen = null;
        const folderPath = nfDialog.folder ? nfDialog.folder() : null;
        // A page with a folder open asks first how much to look at; without one the job is the library's, as it always was.
        if (folderPath) return nfChoose(folderPath);
        nfSetScope('library');
        replaceContent(nfDialog.body, buildElement('p', { className: 'name-faces-step', text: 'Starting...' }));
        nfButtons({});
        nfSay('');
        return nfAct('/api/name-faces/start', { folder: null, only_folder: false });
    };
    if (nfDialog.job && nfDialog.last && !NF_ENDED.includes(nfDialog.last.state)) {
        // Closed while it worked or waited: how it stands now. One that is over is shown, not started again.
        return api.json(`/api/name-faces/status?job=${encodeURIComponent(nfDialog.job)}`).then(
            answer => (answer && answer.success ? nfFollow(answer.status) : start()),
            () => start());
    }
    return start();
}

/**
 * A page opened while the job works (started from another page, tab or before a reload) shows its progress at once: the
 * dialog opens on the job /api/name-faces/current names. A question waiting for its answer is not opened by itself; the
 * button shows it. `options` as openNameFaces.
 */
export function attachNameFaces({ folder, changed } = {}) {
    return api.json('/api/name-faces/current').then(answer => {
        const status = answer && answer.success ? answer.status : null;
        if (!status || !['planning', 'applying', 'grouping'].includes(status.state)) return null;
        if (!nfDialog.modal) nfBuild();
        nfDialog.folder = typeof folder === 'function' ? folder : null;
        nfDialog.changed = typeof changed === 'function' ? changed : null;
        nfDialog.chosen = null;
        nfDialog.opener = document.activeElement;
        nfDialog.modal.classList.remove('hidden');
        return nfFollow(status);
    }).catch(err => {
        console.error('Could not ask whether naming faces is under way:', err);
        return null;
    });
}

export function closeNameFaces() {
    if (!nfDialog.modal) return;
    nfStopPolling();
    nfDialog.token += 1;
    nfDialog.modal.classList.add('hidden');
    if (nfDialog.opener && typeof nfDialog.opener.focus === 'function') nfDialog.opener.focus();
}
