// Folders to review: the folders under the library's roots that hold photos and no
// indexed photo (docs/ARCHITECTURE.md, phase 8). Sync never indexes one on its own; this
// dialog offers each with Include -- indexed with its subfolders, on the index queue --
// or Ignore, which adds it to the library's ignored folders (a journaled change of its
// settings). Reached from the gear, and from a notice saying how many there are, which
// the last sync's counts give (/api/sync); the list itself is /api/sync/review, one walk
// of the roots. The page's state is state.js's; requests go through api.js.
import { api } from './common/api.js';
import { buildElement, replaceContent } from './common/dom.js';
import { state } from './state.js';
import { restoreIndexingState } from './indexing.js';

const reviewNotice = document.getElementById('review-notice');

function noticeSays(count) {
    if (!reviewNotice) return;
    reviewNotice.classList.toggle('hidden', !count);
    reviewNotice.textContent = count === 1 ? '1 folder to review' : `${count} folders to review`;
}

/** Show how many folders wait to be reviewed, as the library's last sync counted them. */
export function showReviewNotice() {
    return api.json('/api/sync').then(data => {
        const found = data && data.last_run && data.last_run.found;
        noticeSays(found ? found.review_folders || 0 : 0);
        return data;
    }).catch(err => {
        console.error('Could not read the last sync:', err);
        return null;
    });
}

function say(text) {
    if (state.review.status) state.review.status.textContent = text || '';
}

function post(path, folder) {
    return api.json(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder }),
    });
}

function settled(entry, row, text) {
    state.review.folders = state.review.folders.filter(f => f !== entry);
    row.remove();
    noticeSays(state.review.folders.length);
    if (!state.review.folders.length) renderReview();
    say(text);
}

/** Include: the folder, with its subfolders, is queued for indexing. */
export function includeFolder(entry, row) {
    if (state.review.busy) return Promise.resolve(null);
    state.review.busy = true;
    return post('/api/sync/review/include', entry.path).then(answer => {
        state.review.busy = false;
        if (!answer || !answer.success) {
            say((answer && answer.error) || 'Could not queue it.');
            return answer;
        }
        settled(entry, row, `Queued for indexing: ${entry.path}`);
        restoreIndexingState();
        return answer;
    }).catch(err => {
        state.review.busy = false;
        console.error('Could not include the folder:', err);
        say('Could not queue it.');
        return null;
    });
}

/** Ignore: the folder is added to the library's ignored folders, and never offered again. */
export function ignoreFolder(entry, row) {
    if (state.review.busy) return Promise.resolve(null);
    state.review.busy = true;
    return post('/api/sync/review/ignore', entry.path).then(answer => {
        state.review.busy = false;
        if (!answer || !answer.success) {
            say((answer && answer.error) || 'Could not ignore it.');
            return answer;
        }
        settled(entry, row, `Ignored: ${entry.path}. The library's settings list it; History can undo it.`);
        return answer;
    }).catch(err => {
        state.review.busy = false;
        console.error('Could not ignore the folder:', err);
        say('Could not ignore it.');
        return null;
    });
}

function folderRow(entry) {
    const include = buildElement('button', {
        className: 'btn btn-primary btn-sm review-include', text: 'Include',
        title: 'Index this folder and its subfolders; sync keeps it in step from then on',
        attrs: { type: 'button' },
    });
    const ignore = buildElement('button', {
        className: 'btn btn-secondary btn-sm review-ignore', text: 'Ignore',
        title: "Never offer this folder, or any under it, again (the library's ignored folders)",
        attrs: { type: 'button' },
    });
    const row = buildElement('li', { className: 'review-folder', data: { path: entry.path } }, [
        buildElement('span', { className: 'review-path', text: entry.path }),
        buildElement('span', { className: 'review-count', text: `${entry.photos} photo(s)` }),
        include, ignore,
    ]);
    include.addEventListener('click', () => includeFolder(entry, row));
    ignore.addEventListener('click', () => ignoreFolder(entry, row));
    return row;
}

function renderReview() {
    const folders = state.review.folders;
    if (!folders.length) {
        replaceContent(state.review.body, buildElement('p', { className: 'review-empty',
            text: 'No folders to review: every folder under the library\'s roots is indexed or ignored.' }));
        return;
    }
    replaceContent(state.review.body, buildElement('ul', { className: 'review-list' }, folders.map(folderRow)));
}

function loadReview() {
    return api.json('/api/sync/review').then(data => {
        if (!data || !Array.isArray(data.folders)) {
            replaceContent(state.review.body, buildElement('p', { className: 'validation-error',
                text: (data && data.error) || 'Could not list the folders to review.' }));
            return data;
        }
        state.review.folders = data.folders;
        noticeSays(data.folders.length);
        renderReview();
        say(data.folders.length ? `${data.photos} photo(s) in ${data.folders.length} folder(s).` : '');
        return data;
    }).catch(err => {
        console.error('Could not list the folders to review:', err);
        replaceContent(state.review.body, buildElement('p', { className: 'validation-error',
            text: 'Could not list the folders to review.' }));
        return null;
    });
}

function buildReviewDialog() {
    // The multiplication sign, by its code: no escape in this file can be mangled in transit.
    const close = buildElement('button', { className: 'close-btn', text: String.fromCharCode(0xd7), title: 'Close',
        attrs: { type: 'button', 'aria-label': 'Close' } });
    const done = buildElement('button', { className: 'btn btn-secondary', text: 'Close', attrs: { type: 'button' } });
    state.review.body = buildElement('div', { className: 'modal-body review-body' });
    state.review.status = buildElement('span', { className: 'review-status', attrs: { role: 'status' } });
    const modal = buildElement('div', {
        id: 'review-modal',
        className: 'modal review-modal hidden',
        attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'review-title' },
    }, [
        buildElement('div', { className: 'modal-content review-content' }, [
            buildElement('div', { className: 'modal-header' }, [
                buildElement('h3', { id: 'review-title', text: 'Folders to review' }), close]),
            buildElement('p', { className: 'review-about', text: 'Folders under the library\'s roots holding photos '
                + 'that are not in it. Include indexes one with its subfolders; Ignore never offers it again.' }),
            state.review.body,
            buildElement('div', { className: 'modal-footer review-footer' }, [state.review.status, done]),
        ]),
    ]);
    document.body.append(modal);
    close.addEventListener('click', closeReview);
    done.addEventListener('click', closeReview);
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape' || modal.classList.contains('hidden')) return;
        e.preventDefault();
        e.stopPropagation();
        closeReview();
    });
    state.review.modal = modal;
}

/** Open the dialog, the folders listed now. */
export function openReview() {
    if (!state.review.modal) buildReviewDialog();
    state.review.opener = document.activeElement;
    state.review.modal.classList.remove('hidden');
    replaceContent(state.review.body, buildElement('p', { className: 'review-loading',
        text: 'Looking through the library\'s roots...' }));
    say('');
    return loadReview();
}

export function closeReview() {
    if (!state.review.modal) return;
    state.review.modal.classList.add('hidden');
    const opener = state.review.opener;
    if (opener && typeof opener.focus === 'function') opener.focus();
}

/**
 * The notice opens the dialog; the page asks once how many folders wait. Opened with
 * `?review=1` -- the Activity page's link -- the page opens the dialog as it starts.
 */
export function wireReview() {
    if (reviewNotice) reviewNotice.addEventListener('click', openReview);
    showReviewNotice();
    if (new URLSearchParams(window.location.search).get('review') === '1') openReview();
}
