// TagPup's page: what a bulk edit of a library view says -- the question before it, the line while it runs, the sentence when it
// ends -- as plain functions of numbers and names, so each can be read and tested without a page (docs/ARCHITECTURE.md, phase 9d-2).

/** Photos a second the page assumes of a bulk edit until the job reports its own pace: about what 9d-1 measured, 15 to 23. */
export const ASSUMED_PER_SECOND = 15;

/** A bulk edit over more photos than this is asked about twice. */
export const ASK_TWICE_ABOVE = 5000;

/** The longest shift the page takes, in minutes: ten years. More is a typing mistake, and the file's date could not hold it. */
export const MOST_MINUTES = 10 * 365 * 24 * 60;

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

/** The same for Shift Date Taken by `minutes` (negative is earlier). */
export function describeShift(minutes) {
    const count = Math.abs(minutes).toLocaleString();
    const how = `${count} ${Math.abs(minutes) === 1 ? 'minute' : 'minutes'} ${minutes < 0 ? 'earlier' : 'later'}`;
    return { op: 'time_shift', verb: 'Shift', past: 'Shifted', what: `Date Taken ${how}`, prep: 'in',
        reverse: `shift them ${minutes < 0 ? 'later' : 'earlier'} by the same minutes to reverse it`, minutes };
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
    return `changed ${(job.changed || 0).toLocaleString()} · unchanged ${(job.unchanged || 0).toLocaleString()} · `
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
    if (desc) parts.push(`${desc.past} ${desc.what} ${desc.prep} ${photosOf(done)}`);
    else parts.push(`Bulk edit: ${photosOf(done)} changed`);
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

/** The name the strip gives a job: what it does, or -- for one found again after a reload -- its kind. */
export function titleOf(job, desc) {
    if (desc) return `${desc.verb} ${desc.what} ${desc.prep} ${photosOf(job.total || 0)}`;
    const kind = { tags: 'tags', people: 'people', time_shift: 'Date Taken' }[job.op];
    return `Bulk edit of ${kind || 'photos'} (${photosOf(job.total || 0)})`;
}
