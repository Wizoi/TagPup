// Asking the server again and again while the page is in view, and not while it is hidden:
// a tab left open in the background all day asks nothing. A poll waits for the one before
// it to be answered, so a slow answer never piles requests up behind it. A poll that fails
// (no answer, or one saying success: false) waits twice as long each time, up to a minute
// (or its own interval, if longer), the top line saying so; one answered puts it back.
// soon(name) asks a poller once more now, or once its answer is in: a button's follow-up
// read is the poll's, never a second request beside it.
import { state } from './state.js';

/** The longest a failing poller waits before asking again, unless its own interval is longer. */
const LONGEST_WAIT = 60000;

function hidden() {
    return document.visibilityState === 'hidden';
}

/** Whether `answer` is a failure: a reply saying success: false, or a group of replies one of which does. */
function answerFailed(answer) {
    if (Array.isArray(answer)) return answer.some(answerFailed);
    return !!answer && answer.success === false;
}

function clock(ms) {
    return new Date(ms).toTimeString().slice(0, 8);
}

/** While any poller is failing, the top line says so, and when the page asks again (the soonest). Now's answer puts it back. */
function sayFailing() {
    const waiting = state.pollers.filter(each => each.failures && each.timer);
    if (!waiting.length) return;
    const next = Math.min(...waiting.map(each => each.nextAt));
    document.getElementById('updated').textContent = `The server did not answer; asking again at ${clock(next)}.`;
}

/**
 * A poller of `task` (returning a promise) every `ms` milliseconds, called `name`: { start, stop,
 * wake, sleep, soon, busy }. `start()` asks now and then every `ms`; `sleep()` stops asking until
 * `wake()`, which asks at once; `soon()` asks now, or once the request in flight is answered,
 * resolving when that answer is in; `stop()` for good. Each is registered in state.pollers,
 * paused when the page is hidden (pauseWhenHidden).
 */
export function poller(task, ms, name) {
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
        Promise.resolve()
            .then(task)
            .then(answerFailed, err => {
                console.error('Could not ask the server:', err);
                return true;
            })
            .then(failure => {
                self.failures = failure ? self.failures + 1 : 0;
                self.asking = false;
                answered.forEach(resolve => resolve());
                if (self.again) ask();
                else schedule();
                sayFailing();
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

/** Ask the poller `name` once more: now, or once the request it has in flight is answered. */
export function soon(name) {
    return state.pollers.find(each => each.name === name).soon();
}

/** Sleep every poller while the page is hidden; wake each, asking at once, when it is back. */
export function pauseWhenHidden() {
    document.addEventListener('visibilitychange', () => {
        state.pollers.forEach(each => (hidden() ? each.sleep() : each.wake()));
    });
}
