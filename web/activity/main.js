// The Activity page: start-up and wiring. Each section is a module beside this one, its
// state in state.js; every request goes through api.js, in its form for what covers every
// library (api.site: web/common/api.js). Now is asked every few seconds while the page is
// in view, the rest every half minute, and nothing while the page is hidden (poll.js).
import { state } from './state.js';
import { pauseWhenHidden, poller } from './poll.js';
import { loadAttention } from './attention.js';
import { loadNow } from './now.js';
import { loadJobs } from './jobs.js';
import { loadSync } from './sync.js';
import { loadSnapshots } from './snapshots.js';
import { loadServer } from './server.js';
import { loadTimeline, wireTimeline } from './timeline.js';
import { followLog, loadLogFiles, readLog, wireLogs } from './logs.js';

document.addEventListener('DOMContentLoaded', () => {
    // `?every=<ms>`: how often Now is asked, for a test or a measurement; the rest follow it.
    const every = Number(new URLSearchParams(window.location.search).get('every'));
    if (every > 0) {
        state.every = every;
        state.slowEvery = every * 10;
    }

    // Each section's listeners.
    wireTimeline();
    wireLogs();
    pauseWhenHidden();

    // Now, while the page is in view; the logs followed, when asked; the rest now and then.
    poller(loadNow, state.every).start();
    poller(followLog, state.every).start();
    poller(() => Promise.all([loadAttention(), loadJobs(), loadSync(), loadServer(), loadTimeline()]),
           state.slowEvery).start();
    poller(loadSnapshots, state.slowEvery * 4).start();
    loadLogFiles().then(() => readLog());
});
