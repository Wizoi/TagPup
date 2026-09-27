// Asking the server again and again while the page is in view, and not while it is hidden:
// a tab left open in the background all day asks nothing. A poll waits for the one before
// it to be answered, so a slow answer never piles requests up behind it.
import { state } from './state.js';

function hidden() {
    return document.visibilityState === 'hidden';
}

/**
 * A poller of `task` (returning a promise) every `ms` milliseconds: { start, stop, wake,
 * sleep, busy }. `start()` asks now and then every `ms`; `sleep()` stops asking until
 * `wake()`, which asks at once; `stop()` for good. Each is registered in state.pollers,
 * paused when the page is hidden (pauseWhenHidden).
 */
export function poller(task, ms) {
    const self = { ms, timer: null, asking: false, stopped: true, asked: 0 };

    const schedule = () => {
        if (self.stopped || self.timer || self.asking || hidden()) return;
        self.timer = setTimeout(ask, self.ms);
    };

    function ask() {
        self.timer = null;
        if (self.stopped || hidden() || self.asking) return;
        self.asking = true;
        self.asked += 1;
        Promise.resolve()
            .then(task)
            .catch(err => console.error('Could not ask the server:', err))
            .then(() => {
                self.asking = false;
                schedule();
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
    self.busy = () => self.asking;
    state.pollers.push(self);
    return self;
}

/** Sleep every poller while the page is hidden; wake each, asking at once, when it is back. */
export function pauseWhenHidden() {
    document.addEventListener('visibilitychange', () => {
        state.pollers.forEach(each => (hidden() ? each.sleep() : each.wake()));
    });
}
