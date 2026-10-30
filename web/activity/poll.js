// Asking the server again and again while the page is in view, and not while it is hidden:
// a tab left open in the background all day asks nothing. A poll waits for the one before
// it to be answered, so a slow answer never piles requests up behind it. A poll that fails
// (no answer, or one saying success: false) waits twice as long each time, up to a minute
// (or its own interval, if longer). Each poll says its own failure where its sections are:
// Now's in the top line (the page's pulse; now.js writes "Updated" there when it answers),
// each other section's under its heading (`#<section>-status`), and each poll's next answer
// clears its own. A request that failed counts; an error drawing what was answered is the
// page's, logged, and the poll goes on asking on time.
// One poller a section, named for it, and a section is asked by its poller alone: one request
// of it in flight at a time, so an older answer is never drawn over a newer one. A module that
// has just changed what a section shows says so, touched(...sections), and each is asked once
// more: now, or once the answer in flight is in, several touches of one section one more ask.
import { state } from './state.js';

/** The longest a failing poller waits before asking again, unless its own interval is longer. */
const LONGEST_WAIT = 60000;

/** A request the server did not answer (asked()): what a poller counts, unlike an error drawing an answer. */
class NoAnswer extends Error {
    constructor(cause) {
        super(`The server did not answer: ${(cause && cause.message) || cause}`);
        this.cause = cause;
    }
}

/** `request` (a promise of the server's answer), its failure marked as the server's: what a section's load asks through. */
export function asked(request) {
    return Promise.resolve(request).catch(err => {
        throw new NoAnswer(err);
    });
}

function hidden() {
    return document.visibilityState === 'hidden';
}

function clock(ms) {
    return new Date(ms).toTimeString().slice(0, 8);
}

/** What section `section` says while its poll fails ('' once it answered): Now's in the top line, any other's under its heading. */
function say(section, nextAt) {
    if (section === 'now') {
        if (nextAt) document.getElementById('updated').textContent = `The server did not answer; asking again at ${clock(nextAt)}.`;
        return;   // answered: now.js wrote "Updated" with what it drew
    }
    const line = document.getElementById(`${section}-status`);
    const title = document.getElementById(`${section}-title`);
    if (line) line.textContent = nextAt ? `${title ? title.textContent : section} could not be refreshed; asking again at ${clock(nextAt)}.` : '';
}

/** A section's load: whether it failed (not answered, or answered success: false); an error drawing it is logged, not counted. */
function settle(section, load) {
    return Promise.resolve().then(load).then(answer => !!answer && answer.success === false, err => {
        if (err instanceof NoAnswer) {
            console.error(`Could not ask the server (${section}):`, err.cause);
            return true;
        }
        console.error(`Could not draw ${section}:`, err);
        return false;
    });
}

/**
 * The poller of section `name`, asking `load` (returning a promise) every `ms` milliseconds: { start, stop, wake, sleep,
 * soon, busy }. `start()` asks now and then every `ms`; `sleep()` stops asking until `wake()`, which asks at once;
 * `soon()` asks now, or once the request in flight is answered, resolving when that answer is in; `stop()` for good.
 * Each is registered in state.pollers, paused when the page is hidden (pauseWhenHidden).
 */
export function poller(name, load, ms) {
    const self = { name, ms, timer: null, asking: false, stopped: true, asked: 0, failures: 0, nextAt: null,
                   again: false, waiting: [] };

    const wait = () => Math.min(self.ms * 2 ** self.failures, Math.max(self.ms, LONGEST_WAIT));

    const schedule = () => {
        if (self.stopped || self.timer || self.asking || hidden()) return;
        self.nextAt = Date.now() + wait();
        self.timer = setTimeout(ask, wait());
    };

    function ask() {
        self.timer = null;
        if (self.stopped || hidden() || self.asking) return;
        self.asking = true;
        self.again = false;
        self.asked += 1;
        const answered = self.waiting.splice(0);
        settle(name, load).then(failed => {
            self.failures = failed ? self.failures + 1 : 0;
            self.asking = false;
            answered.forEach(resolve => resolve());
            if (self.again) ask();
            else schedule();
            if (!failed) say(name, null);
            else if (self.timer) say(name, self.nextAt);
        });
    }

    self.start = () => {
        self.stopped = false;
        ask();
        return self;
    };
    self.stop = () => {
        self.stopped = true;
        clearTimeout(self.timer);
        self.timer = null;
    };
    self.sleep = () => {
        clearTimeout(self.timer);
        self.timer = null;
    };
    self.wake = () => {
        if (!self.stopped && !self.timer && !self.asking) ask();
    };
    self.soon = () => new Promise(resolve => {
        self.waiting.push(resolve);
        if (self.asking) {
            self.again = true;
            return;
        }
        clearTimeout(self.timer);
        ask();
    });
    self.busy = () => self.asking;
    state.pollers.push(self);
    return self;
}

/** What a module just changed is shown by `sections`: ask each once more, now or once its answer in flight is in; resolving when all are. */
export function touched(...sections) {
    return Promise.all(sections.map(section => state.pollers.find(each => each.name === section).soon()));
}

/** Sleep every poller while the page is hidden; wake each, asking at once, when it is back. */
export function pauseWhenHidden() {
    document.addEventListener('visibilitychange', () => {
        state.pollers.forEach(each => (hidden() ? each.sleep() : each.wake()));
    });
}
