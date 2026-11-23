/**
 * The phone's Edit sheet over the Photo screen (docs/plans/phone-viewer.md, Screens 5): the photo's people and keywords
 * as chips, an add box listing the library's tags that match what is typed, its title and its Date Taken, one Save.
 *
 * It edits the photo as /api/library/photo reads it when the sheet opens (the file's stamp and what it holds), not the
 * Grid's card. A chip's x waits five seconds behind an Undo before anything is sent; leaving the photo (a swipe, Back,
 * another screen) or the page being hidden (another app, the tab going: Android may discard a hidden tab, its timers
 * frozen) puts it in its turn then, the sheet left open: after a Save still out, never beside it (the Save would be
 * refused as changed on the PC); each write is keepalive, but a removal behind a Save still out when the page really
 * unloads is not sent (accepted, #3172). A tag is added or taken off through the one entry point of photo writes
 * (POST /api/photos/write, op `tags`), the photo named by its id, against what the file holds, so no stamp; refused because
 * the photo is no longer at the path named (renamed on the PC: 409), it is read again by its id and sent once more, named
 * where it is now (photo-write.js readMoved, the PC's rule too). The title and Date Taken through the same entry point, op
 * `set` (web/common/photo-write.js postPhotoMetadata), with the stamp, which the server refuses when the file changed since it was read: nothing is written, the sheet says
 * so and reads the photo again, the text typed kept. Each write of the page waits for the one before it (state.writing).
 * A title or date typed and left by Back or another screen is kept (state.drafts) and put back when the photo's sheet
 * opens again; only Close lets it go, and asks first. The page going while one is kept asks first too. The sheet is a
 * step of the history (mobile-screens.js), its one record of being open: Back closes it, the text kept and a removal
 * still behind its Undo, and a second Back leaves the photo; Close is a Back that lets the text go.
 *
 * A chip taken off and a tag change waiting are the photo's, by its id, not the record read: the sheet read again (Edit
 * opened again, a Save refused and read again) still leaves the chip off, and the write lands on the record it shows.
 */
import { api } from './common/api.js';
import { buildElement, replaceContent } from './common/dom.js';
import { inTurn, postPhotoMetadata, readMoved, takeWritten } from './common/photo-write.js';
import { baseName, pathKey } from './common/paths.js';
import { leafOf, photoAlreadyHas, sortedTags } from './common/vocabulary.js';
import { TAKEN_DIGITS } from './format.js';
import { state } from './mobile-state.js';
import { chipSaid, isPerson, photoShown, photoWritten, readVocabulary, recordLanded } from './mobile-photo.js';
import { closeSheetStep, openSheetStep, whenSheet } from './mobile-screens.js';

const byId = (id) => document.getElementById(id);
const UNDO_MS = 5000;
const MATCHES = 8;
const TYPED = ['edit-title', 'edit-date', 'edit-time'];

function say(text) {
    byId('edit-problem').textContent = text;
    byId('edit-problem').classList.toggle('hidden', !text);
}

/** A failure as the owner reads it: the server's sentence, or a connection that dropped. */
function why(err) {
    return err instanceof TypeError ? 'no connection. Try again.' : (err && err.message) || 'try again.';
}

/** The record the sheet shows of photo `id`, read; null when the sheet is shut, on another photo, or still reading. */
const shownOf = (id) => (state.edit && state.edit.id === id && state.edit.record) || null;

/** A line at the foot of the page that outlives the sheet: `undo` gives it an Undo; it goes after `ms`, or by `close()`. */
function toast(text, undo = null, ms = UNDO_MS) {
    const line = buildElement('div', { className: 'edit-toast' }, [buildElement('span', { text })]);
    const close = () => line.remove();
    if (undo) {
        line.append(buildElement('button', { className: 'edit-undo', text: 'Undo', attrs: { type: 'button' } }));
        line.lastChild.addEventListener('click', () => { close(); undo(); });
    }
    byId('edit-toasts').append(line);
    window.setTimeout(close, ms);
    return close;
}

/**
 * Add `add` or take off `remove` in the file of `job` (a photo's id and path); the sheet's record of it becomes the file's.
 * Refused because the photo is not at `job.path` (409 changed_on_disk: renamed on the PC since it was read), it is read
 * again by its id and sent once more where it is now (readMoved), every write waiting for it, `job` too, naming it there;
 * refused a second time, or gone, it fails.
 */
async function writeTags(job) {
    const res = await api.fetch('/api/photos/write', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, keepalive: true,
        body: JSON.stringify({ op: 'tags', changes: { [job.path]: { id: job.id, ...job.change } } }),
    });
    const data = await res.json().catch(() => null);
    const found = await readMoved(data, [job]);
    if (found) {
        if (found[0] === null) throw new Error('this photo is no longer in the library.');
        photoMoved(job.id, found[0].path);
        job.path = found[0].path;
        return writeTags(job);
    }
    // A write that ran is answered 200 even when the photo failed: its sentence is among the `errors`.
    if (!res.ok || !data || !data.success) {
        throw new Error((data && (data.error || (data.errors && data.errors.length && data.errors[0].why))) || 'The tags were not written.');
    }
    const written = Object.entries(data.written || {}).find(([each]) => pathKey(each) === pathKey(job.path));
    if (!written) throw new Error('this file is not where the library last saw it.');
    photoWritten(job.id, { tags: written[1].tags.slice() });
    const record = shownOf(job.id);    // read again since the change was asked, or not shown: the sheet's as it is now
    if (!record) return;
    record.tags = written[1].tags.slice();
    takeWritten(record, written[1], record.tags);
    drawChips();
}

/**
 * Photo `id`'s file is at `path` now (a Save renamed it, or a tag change found it renamed on the PC): its card, the photo
 * shown, the sheet's record and every tag change waiting for it name it there.
 */
function photoMoved(id, path) {
    const card = state.photos && state.photos.cards.get(id);
    for (const held of [card, state.photo && state.photo.id === id ? state.photo : null]) {
        if (!held) continue;
        held.path = path;
        held.name = held.filename = baseName(path);
    }
    const record = shownOf(id);
    if (record) record.path = path;
    for (const each of [...state.removals, ...state.queued]) if (each.id === id) each.path = path;
}

/** A tag change of photo `id` at `path` in its turn, after every write sent before it (a Save renaming the file moves `path`). */
function tagWrite(id, path, change) {
    const job = { id, path, change };
    state.queued.push(job);
    return inTurn(state, 'writing', () => {
        state.queued = state.queued.filter((each) => each !== job);
        return writeTags(job);
    });
}

/** Send a removal waiting behind its Undo, in its turn. */
function sendRemoval(removal) {
    window.clearTimeout(removal.timer);
    removal.close();
    state.removals = state.removals.filter((each) => each !== removal);
    tagWrite(removal.id, removal.path, { remove: [removal.tag] }).catch((err) => {
        if (shownOf(removal.id)) drawChips();    // the chip is back
        toast(`Not taken off ${leafOf(removal.tag)}: ${why(err)}`, null, UNDO_MS * 2);
    });
}

/** Take `tag` off the sheet's photo in five seconds, unless Undo is tapped first. */
function removeLater(tag) {
    const removal = { id: state.edit.id, path: state.edit.record.path, tag };
    removal.close = toast(`Took off ${leafOf(tag)}.`, () => {
        window.clearTimeout(removal.timer);
        state.removals = state.removals.filter((each) => each !== removal);
        if (shownOf(removal.id)) drawChips();
    });
    removal.timer = window.setTimeout(() => sendRemoval(removal), UNDO_MS);
    state.removals.push(removal);
    drawChips();
}

function drawChips() {
    const { id, record } = state.edit;
    const going = new Set(state.removals.filter((each) => each.id === id).map((each) => each.tag));
    for (const job of state.queued) if (job.id === id) (job.change.remove || []).forEach((tag) => going.add(tag));
    const tags = sortedTags((record.tags || []).filter((tag) => !going.has(tag)));
    const chip = (tag) => buildElement('span', { className: 'edit-chip', title: tag }, [
        chipSaid(tags, tag),
        buildElement('button', { className: 'edit-chip-x', text: '×', data: { tag }, attrs: { type: 'button', 'aria-label': `Take off ${tag}` } }),
    ]);
    replaceContent(byId('edit-people'), ...tags.filter(isPerson).map(chip));
    replaceContent(byId('edit-keywords'), ...tags.filter((tag) => !isPerson(tag)).map(chip));
}

/** The library's tags that match the text typed and the photo does not hold, as buttons. */
function drawMatches() {
    const text = byId('edit-add').value.trim().toLowerCase();
    const record = state.edit && state.edit.record;
    const found = !text || !record || !state.tags ? [] : state.tags.filter((tag) => tag.toLowerCase().includes(text)
        && !photoAlreadyHas(record, tag, isPerson, state.people)).slice(0, MATCHES);
    replaceContent(byId('edit-matches'), ...found.map((tag) => buildElement('button', {
        className: 'edit-match', data: { tag }, attrs: { type: 'button' } }, [leafOf(tag),
        tag === leafOf(tag) ? null : buildElement('span', { className: 'edit-match-path', text: ` ${tag}` })])));
}

function addTag(tag) {
    const { id, record } = state.edit;
    const typed = byId('edit-add').value;
    byId('edit-add').value = '';
    drawMatches();
    say(`Adding ${leafOf(tag)}...`);
    tagWrite(id, record.path, { add: [tag] }).then(() => {
        if (shownOf(id)) say('');
    }, (err) => {
        if (!shownOf(id)) return;
        say(`Not added: ${why(err)}`);
        if (!byId('edit-add').value) byId('edit-add').value = typed;    // the text typed, to try again
    });
}

/** The Date Taken as the file holds it, "YYYY:MM:DD HH:MM:SS", as the date and time inputs answer it. */
function whenOf(taken) {
    const found = TAKEN_DIGITS.exec(String(taken || '').trim());
    return found ? { date: `${found[1]}-${found[2]}-${found[3]}`, time: `${found[4]}:${found[5]}:${found[6]}` } : { date: '', time: '' };
}

const fullTime = (time) => (time.length === 5 ? `${time}:00` : time);    // a time input may leave out ":00"
const typedWhen = () => `${byId('edit-date').value}T${fullTime(byId('edit-time').value)}`;
const keptWhen = (record) => { const when = whenOf(record.taken); return `${when.date}T${when.time}`; };

/** Do the title or the date say anything the photo does not hold? */
export function editDirty() {
    const record = state.edit && state.edit.record;
    return Boolean(record) && (byId('edit-title').value !== (record.title || '') || typedWhen() !== keptWhen(record));
}

/** The fields as `record` holds them; with `keepTyped`, the title and date typed are left as they are. */
function drawRecord(record, keepTyped) {
    drawChips();
    drawMatches();
    // Read again with the text kept, the sentence saying why (changed on the PC) stays over the read that went well.
    if (record.missing) say('This file is not where the library last saw it: moved or deleted on the PC.');
    else if (!keepTyped) say('');
    if (keepTyped) return;
    byId('edit-title').value = record.title || '';
    const when = whenOf(record.taken);
    byId('edit-date').value = when.date;
    byId('edit-time').value = when.time;
}

/** Read the sheet's photo as its file is now: the record the facts under the sheet hold too (recordLanded). */
function readRecord(keepTyped = false) {
    const edit = state.edit;
    api.fetch(`/api/library/photo?id=${edit.id}`)
        .then((res) => api.answered(res, 'Could not read this photo.'))
        .then((body) => {
            if (state.edit !== edit || !body || !body.photo) return;
            edit.record = recordLanded(edit.id, body.photo);
            drawRecord(edit.record, keepTyped);
            if (!keepTyped) putDraftBack(edit);
        })
        .catch((err) => {
            if (state.edit === edit && !keepTyped) say(`Could not read this photo: ${why(err)}`);
        });
}

/** The title and date typed on this photo before and left unsaved, back in their fields, and said so. */
function putDraftBack(edit) {
    const draft = state.drafts.get(edit.id);
    if (!draft) return;
    for (const id of TYPED) byId(id).value = draft[id];
    if (editDirty()) say('Not saved yet: the title and date typed before are back. Save them, or Close to let them go.');
    else state.drafts.delete(edit.id);    // the file holds them now
}

/** Edit tapped: the sheet, a step of the history, drawn by it (sheetStep). */
function openEdit() {
    if (photoShown() !== null) openSheetStep();
}

/** The sheet over the photo shown, read as its file is now; nothing when no photo is shown. */
function showEdit() {
    const id = photoShown();
    if (id === null) return;
    // `letGo`: Close tapped, its Back on the way: the sheet shuts as it lands, without keeping what was typed.
    state.edit = { id, record: null, saving: false, letGo: false };
    byId('edit-sheet').classList.remove('hidden');
    for (const id of ['edit-people', 'edit-keywords', 'edit-matches']) replaceContent(byId(id));
    for (const id of ['edit-add', 'edit-title', 'edit-date', 'edit-time']) byId(id).value = '';
    say('Reading this photo...');
    readVocabulary().then(() => {
        if (state.edit && state.edit.record) drawRecord(state.edit.record, true);
    }, () => {});    // asked again at the next open
    readRecord();
}

function closeEdit() {
    state.edit = null;
    byId('edit-sheet').classList.add('hidden');
}

/** The title and date typed and not saved, kept by the photo's id for its sheet's next opening. */
function keepDraft() {
    if (!state.edit || !state.edit.record) return;    // still reading: nothing typed or read, a draft kept stays
    if (editDirty()) state.drafts.set(state.edit.id, Object.fromEntries(TYPED.map((id) => [id, byId(id).value])));
    else state.drafts.delete(state.edit.id);
}

/** Every removal waiting behind its Undo, sent now in its turn. */
function sendWaiting() {
    state.removals.slice().forEach(sendRemoval);
}

/** Another photo or screen is shown: every removal waiting is sent in its turn, the sheet closes. */
export function leaveEdit() {
    keepDraft();
    sendWaiting();
    closeEdit();
}

/** The step shown has the sheet (Edit, Back or Forward onto it) or not (Back or Close off it, the text kept unless Close let it go). */
function sheetStep(open) {
    if (open && !state.edit) showEdit();
    else if (!open && state.edit) {
        if (!state.edit.letGo) keepDraft();
        closeEdit();
    }
}

/** The one Save: the title and the Date Taken, named by the stamp of the file as it was read. */
function save() {
    const edit = state.edit;
    if (!edit || !edit.record || edit.saving || !editDirty()) return;
    const record = edit.record;
    const fields = { title: byId('edit-title').value };
    if (typedWhen() !== keptWhen(record)) fields.date_taken = typedWhen();
    if (fields.date_taken && !(byId('edit-date').value && byId('edit-time').value)) {
        say('Give the date and the time it was taken.');
        return;
    }
    edit.saving = true;
    say('Saving...');
    inTurn(state, 'writing', () => postPhotoMetadata(record, fields, undefined, { keepalive: true })).then((data) => {
        record.title = fields.title;
        if (data.taken) record.taken = data.taken;
        const card = state.photos && state.photos.cards.get(edit.id);
        for (const held of [card, state.photo && state.photo.id === edit.id ? state.photo : null]) {
            if (held) Object.assign(held, { mtime: record.mtime, taken: record.taken });   // stamped as its file is now
        }
        if (data.new_path) {    // a title can rename it: a tag change of the photo still waiting names the file by its new name
            record.path = data.new_path;
            photoMoved(edit.id, data.new_path);
        }
        state.drafts.delete(edit.id);
        photoWritten(edit.id, { title: record.title, taken: record.taken, path: record.path });
        if (state.edit === edit) {
            drawRecord(record, false);
            say('Saved.');
        }
    }, (err) => {
        if (state.edit !== edit) return;
        say(err.changedOnDisk ? 'Not saved: this photo changed on the PC since it was read. It was read again; your text is'
            + ' kept. Look, then Save again.' : `Not saved: ${why(err)}`);
        if (err.changedOnDisk) readRecord(true);
    }).finally(() => {
        edit.saving = false;
    });
}

export function wireEdit() {
    byId('photo-edit').addEventListener('click', openEdit);
    byId('edit-save').addEventListener('click', save);
    byId('edit-close').addEventListener('click', () => {
        const edit = state.edit;
        if (!edit || edit.letGo) return;    // a second tap before the first one's Back lands
        if (editDirty() && !window.confirm('Close without saving the title and date?')) return;
        edit.letGo = true;
        state.drafts.delete(edit.id);
        closeSheetStep();
    });
    byId('edit-add').addEventListener('input', drawMatches);
    byId('edit-matches').addEventListener('click', (event) => {
        const match = event.target.closest('.edit-match');
        if (match && state.edit && state.edit.record) addTag(match.dataset.tag);
    });
    for (const id of ['edit-people', 'edit-keywords']) {
        byId(id).addEventListener('click', (event) => {
            const x = event.target.closest('.edit-chip-x');
            if (x && state.edit && state.edit.record) removeLater(x.dataset.tag);
        });
    }
    whenSheet(sheetStep);
    // The page hidden: Chrome on Android says so when another app is shown, and may then discard the tab with no pagehide.
    // The sheet stays: kept in the back-forward cache, the page comes back on the sheet's step, the sheet open.
    window.addEventListener('pagehide', sendWaiting);
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'hidden') sendWaiting();
    });
    // Another library or a reload with a title or date typed and not saved: the browser asks first, as Close does.
    window.addEventListener('beforeunload', (event) => {
        if (!editDirty() && !state.drafts.size) return;
        event.preventDefault();
        event.returnValue = '';
    });
}
