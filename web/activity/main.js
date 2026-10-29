// The Activity page: start-up and wiring. Each section is a module beside this one (the folded ones fold.js's), its
// state in state.js; every request goes through api.js, in its form for what covers every
// library (api.site: web/common/api.js). Now is asked every few seconds while the page is
// in view, the jobs and the rest every half minute, and nothing while the page is hidden (poll.js).
import { state } from './state.js';
import { wireRootsBanner } from './common/roots-banner.js';
import { wireFolds } from './fold.js';
import { pauseWhenHidden, poller } from './poll.js';
import { loadNow, renderNow } from './now.js';
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

    // Now, while the page is in view; the logs followed, when asked; the rest now and then, Now drawn again with the jobs:
    // it says a library whose jobs could not be read, and what finished in it last (both /api/activity/jobs).
    poller(loadNow, state.every, 'now').start();
    poller(followLog, state.every, 'follow').start();
    // The jobs on their own, so Run now's read after its answer is this poll's (soon('jobs'), jobs.js).
    poller(() => loadJobs().then(data => {
        renderNow();
        return data;
    }), state.slowEvery, 'jobs').start();
    poller(() => Promise.all([loadSync(), loadServer(), loadTimeline()]), state.slowEvery, 'rest').start();
    poller(loadSnapshots, state.slowEvery * 4, 'snapshots').start();
    loadLogFiles().then(() => readLog());
});
