// TagPup's page: what a bulk edit of a library view says -- the question before it, the line while it runs, the sentence when it
// ends -- as plain functions of numbers and names, so each can be read and tested without a page (docs/ARCHITECTURE.md, phase 9d-2).

/** Photos a second the page assumes of a bulk edit until the job reports its own pace: about what 9d-1 measured, 15 to 23. */
export const ASSUMED_PER_SECOND = 15;

/** A bulk edit over more photos than this is asked about twice. */
export const ASK_TWICE_ABOVE = 5000;

/** `1 photo`, `3,412 photos`. */
export function photosOf(count) {
    return `${count.toLocaleString()} ${count === 1 ? 'photo' : 'photos'}`;
}

/** How long `seconds` is, roughly, as a person says it after "takes": `about 3 minutes`, `less than a minute`, `about 1 hour 5 minutes`. */
export function howLong(seconds) {
    const minutes = Math.round(seconds / 60);
    if (minutes < 1) return 'less than a minute';
    if (minutes < 60) return `about ${minutes} ${minutes === 1 ? 'minute' : 'minutes'}`;
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    const hourText = `${hours} ${hours === 1 ? 'hour' : 'hours'}`;
    return rest ? `about ${hourText} ${rest} ${rest === 1 ? 'minute' : 'minutes'}` : `about ${hourText}`;
}

/** What the edit is, in words: `{ op, verb, past, what, prep, reverse }` for Add or Remove of tags or people. */
export function describeTags({ op, add = [], remove = [] }) {
    const people = op === 'people';
    const names = (add.length ? add : remove).join(', ');
    const noun = (add.length ? add : remove).length === 1 ? (people ? 'person' : 'tag') : (people ? 'people' : 'tags');
    if (add.length && remove.length) {
        return { op, verb: 'Change', past: 'Changed', what: `${add.join(', ')} and ${remove.join(', ')}`, prep: 'on',
            reverse: 'take them back off or put them back' };
    }
    if (add.length) {
        return { op, verb: 'Add', past: 'Added', what: names, prep: 'to',
            reverse: people ? 'remove the person to reverse it' : `remove the ${noun} to reverse it` };
    }
    return { op, verb: 'Remove', past: 'Removed', what: names, prep: 'from', reverse: `add the ${noun} again to reverse it` };
}

/** "Delete 3,412 photos", "Added Trips/Coast to 1 photo": the words of `desc` for `count` photos, `form` 'verb' or 'past'. */
function phrase(desc, form, count) {
    return [desc[form], desc.what, desc.prep, photosOf(count)].filter(Boolean).join(' ') + (desc.after || '');
}

/**
 * What a Delete of the selection is, in words (#674): `where` is the server's answer of where the files would go
 * (/api/library/selection/delete-check: `permanent`, the photos with no Recycle Bin), `count` the photos selected.
 */
export function describeDelete(where, count) {
    const forGood = Math.min((where && where.permanent) || 0, count);
    const after = !forGood ? ' to the Recycle Bin' : forGood >= count ? ' permanently' : ', those with no Recycle Bin permanently';
    return { op: 'delete', verb: 'Delete', past: 'Deleted', what: '', prep: '', after, permanent: forGood };
}

/** The question before a Delete: how many, where the files go -- the Recycle Bin, or for good and why -- and that the rows go too. */
export function deleteQuestion(where, count) {
    const forGood = Math.min((where && where.permanent) || 0, count);
    const head = `Delete ${photosOf(count)}?`;
    const rows = count === 1 ? ' Its row leaves the library, with its faces.' : ' Their rows leave the library, with their faces.';
    const job = ' It runs as a job you can watch and cancel; it cannot be undone in TagPup.';
    if (!forGood) {
        const bin = count === 1 ? 'The file goes to the Recycle Bin, where it can be restored.' : 'The files go to the Recycle Bin, where they can be restored.';
        return `${head} ${bin}${rows}${job}`;
    }
    const why = ((where && where.reasons) || []).map(each => `${Number(each.photos).toLocaleString()} ${each.reason}`).join(', ');
    if (forGood >= count) {
        const they = count === 1 ? 'It is' : 'They are';
        return `${head} ${they} deleted PERMANENTLY, not moved to the Recycle Bin: there is none where ${count === 1 ? 'it is' : 'they are'} `
            + `(${why}), so ${count === 1 ? 'the file' : 'the files'} cannot be restored.${rows}${job}`;
    }
    const rest = count - forGood;
    return `${head} ${forGood.toLocaleString()} of them ${forGood === 1 ? 'is' : 'are'} deleted PERMANENTLY, not moved to the Recycle `
        + `Bin: there is none where ${forGood === 1 ? 'it is' : 'they are'} (${why}). The other ${rest.toLocaleString()} `
        + `${rest === 1 ? 'goes' : 'go'} to the Recycle Bin.${rows}${job}`;
}

/** The question before a bulk edit: it names the write and the count, what it touches, how long, and how to undo it. */
export function confirmSentence(desc, count) {
    const head = `${desc.verb} ${desc.what} ${desc.prep} ${photosOf(count)}?`;
    return `${head} This changes the photo files and takes ${howLong(count / ASSUMED_PER_SECOND)}. `
        + `It cannot be undone as one step; ${desc.reverse}.`;
}

/** The second question over ASK_TWICE_ABOVE photos. */
export function secondQuestion(count) {
    return `This is ${photosOf(count)}. Continue?`;
}

/** The line of counts under the bar: what the job has done to the photos so far. */
export function countsLine(job) {
    return `${job.op === 'delete' ? 'deleted' : 'changed'} ${(job.changed || 0).toLocaleString()} · unchanged ${(job.unchanged || 0).toLocaleString()} · `
        + `missing ${(job.skipped_missing || 0).toLocaleString()} · damaged ${(job.skipped_damaged || 0).toLocaleString()} · `
        + `errors ${(job.error_count || 0).toLocaleString()}`;
}

/** The time left: the job's own, or one at ASSUMED_PER_SECOND until it has made some. '' when nothing is left to say. */
export function etaText(job) {
    if (!job || job.state !== 'running') return '';
    const left = Math.max(0, (job.total || 0) - (job.done || 0));
    const seconds = typeof job.eta_seconds === 'number' ? job.eta_seconds : left / ASSUMED_PER_SECOND;
    return `${howLong(seconds)} left`;
}

/** The sentence a finished job ends in: what was done and what was left out, and the errors, counted. */
export function summarySentence(job, desc) {
    const done = (job.changed || 0);
    const parts = [];
    if (desc) parts.push(phrase(desc, 'past', done));
    else parts.push(job.op === 'delete' ? `Deleted ${photosOf(done)}` : `Bulk edit: ${photosOf(done)} changed`);
    const missing = job.skipped_missing || 0;
    if (missing) parts.push(`${missing.toLocaleString()} missing on disk ${missing === 1 ? 'was' : 'were'} skipped`);
    const damaged = job.skipped_damaged || 0;
    if (damaged) parts.push(`${damaged.toLocaleString()} damaged ${damaged === 1 ? 'was' : 'were'} skipped`);
    const same = job.unchanged || 0;
    if (same) {
        parts.push(job.op === 'time_shift'
            ? `${same.toLocaleString()} with no Date Taken ${same === 1 ? 'was' : 'were'} left as ${same === 1 ? 'it was' : 'they were'}`
            : `${same.toLocaleString()} already ${same === 1 ? 'was' : 'were'} as asked`);
    }
    const errors = job.error_count || 0;
    parts.push(`${errors.toLocaleString()} ${errors === 1 ? 'error' : 'errors'}`);
    return `${parts.join('; ')}.`;
}

/** What the strip says of a job that has ended, in one sentence: the summary for a finished one, the server's own for the rest. */
export function endedSentence(job, desc) {
    if (job.state === 'done') return summarySentence(job, desc);
    const message = job.message || `The bulk edit ${job.state}.`;
    return `${message} ${summarySentence(job, desc)}`;
}

/**
 * Is a 409 sentence the server's "another bulk edit is running" (both of its sentences say "already running")? Any other refusal -- the
 * library not brought up to date, an edit that could not be set up -- is not, and offers no way to a job that is not there. The server
 * sends no code with the sentence, so the page tells the kinds apart by those words.
 */
export function isRunningRefusal(sentence) {
    return /already running/i.test(String(sentence || ''));
}

/** The note that a job took more photos than were picked: the how-many always, the way out only while it can still be used. */
export function widenedSentence({ took, picked }, running) {
    const how = `TagPup took ${took.toLocaleString()} photos but ${picked.toLocaleString()} were selected: ${(took - picked).toLocaleString()} more `
        + 'came into this view since it was read.';
    return running ? `${how} Cancel stops it after the photos being written now; the photos already written keep the edit.` : how;
}

/** The name the strip gives a job: what it does, or -- for one found again after a reload -- its kind. */
export function titleOf(job, desc) {
    if (desc) return phrase(desc, 'verb', job.total || 0);
    if (job.op === 'delete') return `Delete ${photosOf(job.total || 0)}`;
    const kind = { tags: 'tags', people: 'people', time_shift: 'Date Taken' }[job.op];
    return `Bulk edit of ${kind || 'photos'} (${photosOf(job.total || 0)})`;
}
