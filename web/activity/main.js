// The Activity page: start-up and wiring. Each section is a module beside this one (the folded ones fold.js's), its
// state in state.js; every request goes through api.js, in its form for what covers every
// library (api.site: web/common/api.js). Now is asked every few seconds while the page is
// in view, the jobs and the rest every half minute, and nothing while the page is hidden; a section
// a module has just changed is asked at once (touched, poll.js).
import { state } from './state.js';
import { wireRootsBanner } from './common/roots-banner.js';
import { wireFolds } from './fold.js';
import { pauseWhenHidden, poller } from './poll.js';
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

    // Each section's listeners, and the banner the other pages have (an update while the page is open).
    wireRootsBanner();
    wireFolds();
    wireTimeline();
    wireLogs();
    pauseWhenHidden();

    // A poll a section, named for it, where it says it could not refresh it (poll.js): Now, while the page is in view;
    // the logs followed, when asked; the rest now and then.
    poller('now', loadNow, state.every).start();
    poller('logs', followLog, state.every).start();
    poller('jobs', loadJobs, state.slowEvery).start();
    poller('sync', loadSync, state.slowEvery).start();
    poller('server', loadServer, state.slowEvery).start();
    poller('timeline', loadTimeline, state.slowEvery).start();
    poller('snapshots', loadSnapshots, state.slowEvery * 4).start();
    loadLogFiles().then(() => readLog());
});
