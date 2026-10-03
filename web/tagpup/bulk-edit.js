// TagPup's page: the edits of a library view's selection -- Add tag, Add person, the pills' Remove and Apply, Shift Date Taken -- each
// asked of the owner by name and count, and started as a bulk JOB (bulk-job.js) with the selection BY ID (selected.js). The folder
// view's bulk writes are selection.js's and are not touched: a path, 5,000 of them, one request.
//
// What is asked is what is sent: the selection is read BEFORE the question and the request carries that, whatever is clicked while a
// question or a placement dialog is open. A limit of the request (more than 20,000 ids, more than 200,000 photos) is said in a sentence
// before anything is asked of the server. Smart Rename stays off in a view: a name is per folder.
import { ruleProblem } from './common/validate.js';
import { tagProblem } from './common/vocabulary.js';
import { state } from './state.js';
import { bulkAddPeopleInput, bulkAddTagsInput, timeshiftDirection, timeshiftMinutesInput } from './elements.js';
import { flagField, setStatus } from './status.js';
import { resolveTagOrPerson, updatePeopleDatalist, updateTagsDatalist } from './tags.js';
import { selectionCount, selectionRequest } from './selected.js';
import { BUSY_SENTENCE, bulkBusy, startBulk } from './bulk-job.js';
import {
    ASK_TWICE_ABOVE, MOST_MINUTES, confirmSentence, describeShift, describeTags, secondQuestion
} from './bulk-words.js';

/** The selection, as a request, and how many photos it is; or null, the owner told why not (nothing is asked of the server). */
function readSelection() {
    if (!state.library) return null;
    if (bulkBusy()) {
        alert(BUSY_SENTENCE);
        return null;
    }
    const asked = selectionRequest();
    if (!asked.ok) {
        if (asked.sentence === 'Nothing is selected.') setStatus('error', 'Select some photos first — nothing is selected', { transient: false });
        else alert(asked.sentence);
        return null;
    }
    return { selection: asked.body, count: selectionCount() };
}

/** The question before a write: the first names it and the count; a large one is asked about a second time. */
function confirmed(desc, count) {
    if (!confirm(confirmSentence(desc, count))) return false;
    return count <= ASK_TWICE_ABOVE || confirm(secondQuestion(count));
}

/**
 * Add the tags (or people) typed in the panel to the selection. The names are checked first, all or nothing, the text stays to be
 * corrected; then the question; and only then is a name resolved -- resolving may make a node of the tag tree, and a question
 * refused makes none. Resolves true when a job began.
 */
export async function addTypedToSelection(isPeople) {
    const input = isPeople ? bulkAddPeopleInput : bulkAddTagsInput;
    const typed = input.value.trim();
    if (!typed || !state.library) return false;
    const names = typed.split(',').map(each => each.trim()).filter(Boolean);
    if (!names.length) return false;
    const refused = names.map(tagProblem).find(Boolean);
    if (refused) {
        alert(refused);
        return false;
    }
    const op = isPeople ? 'people' : 'tags';
    // A second click while the first one's question or placement dialog is open asks nothing more.
    if (state.bulk.asking) return false;
    const picked = readSelection();
    if (!picked) return false;
    state.bulk.asking = true;
    let started;
    try {
        if (!confirmed(describeTags({ op, add: names }), picked.count)) return false;
        const resolved = [];
        for (const name of names) {
            const found = await resolveTagOrPerson(name, isPeople);
            if (found) resolved.push(found);
        }
        if (!resolved.length) return false;
        started = await startBulk({ op, selection: picked.selection, params: { add: resolved, remove: [] },
            desc: describeTags({ op, add: resolved }) });
    } finally {
        state.bulk.asking = false;
    }
    // Only the text that was written: what was typed since stays.
    if (started.ok && input.value.trim() === typed) input.value = '';
    if (started.ok) (isPeople ? updatePeopleDatalist : updateTagsDatalist)();
    return started.ok;
}

/**
 * A pill of the selection's tally: take the tag (or person) off every photo of the selection, or -- the arrow -- put it on every one.
 * `kind` is 'tag' or 'person'; `name` is the tag as the tally gives it, or the person's name.
 */
export async function editByPill({ kind, name, remove }) {
    const op = kind === 'person' ? 'people' : 'tags';
    if (state.bulk.asking) return false;
    const picked = readSelection();
    if (!picked) return false;
    const desc = describeTags({ op, add: remove ? [] : [name], remove: remove ? [name] : [] });
    state.bulk.asking = true;
    try {
        if (!confirmed(desc, picked.count)) return false;
        const started = await startBulk({ op, selection: picked.selection,
            params: remove ? { add: [], remove: [name] } : { add: [name], remove: [] }, desc });
        return started.ok;
    } finally {
        state.bulk.asking = false;
    }
}

/** The minutes typed, as a signed whole number (earlier is negative), or null with the reason said beside the field. */
function shiftTyped() {
    const typed = Number(timeshiftMinutesInput.value.trim());
    const rule = ruleProblem('time shift', typed);
    if (rule || !Number.isFinite(typed)) {
        flagField(timeshiftMinutesInput, rule || 'Enter a shift in minutes (not zero)');
        return null;
    }
    if (typed === 0) {
        flagField(timeshiftMinutesInput, 'Enter a shift in minutes (not zero)');
        return null;
    }
    if (typed < 0) {
        flagField(timeshiftMinutesInput, 'Enter the minutes as a positive number and choose Earlier or Later.');
        return null;
    }
    if (typed > MOST_MINUTES) {
        flagField(timeshiftMinutesInput, `A shift of more than 10 years (${MOST_MINUTES.toLocaleString()} minutes) is more likely a typing mistake than a clock that far out.`);
        return null;
    }
    return timeshiftDirection.value === 'earlier' ? -typed : typed;
}

/** Shift Date Taken of the selection of a library view: the minutes and a direction, no camera. Resolves true when a job began. */
export async function shiftSelectionInView() {
    if (!state.library) return false;
    const minutes = shiftTyped();
    if (minutes === null) return false;
    const picked = readSelection();
    if (!picked) return false;
    const desc = describeShift(minutes);
    if (!confirmed(desc, picked.count)) return false;
    const started = await startBulk({ op: 'time_shift', selection: picked.selection, params: { minutes }, desc });
    if (started.ok) timeshiftMinutesInput.value = 0;
    return started.ok;
}
