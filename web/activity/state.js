// The Activity page's mutable state, in one object: a module that imports a binding cannot
// reassign it.
export const state = {
    //: How often Now is asked again while the page is in view, and the rest of the page
    //: (jobs, syncs, snapshots, the always-on process, the timeline), in milliseconds.
    //: `?every=<ms>` sets the first, for a test or a measurement; the rest follow it.
    every: 3000,
    slowEvery: 30000,
    //: What each section last read, as the server answered it.
    now: null,
    jobs: null,
    sync: null,
    snapshots: null,
    server: null,
    timeline: { limit: 50, entries: [], more: false },
    //: The Run now asked and waiting for its confirmation: { job, library } or null.
    confirming: null,
    //: The last Run now's answer, shown beside the job: { job, library, text, ok }.
    ranNow: null,
    //: The Run now whose POST is on its way: "job|library" -> true. Its button is
    //: disabled, and a second click -- a double click on Run -- sends nothing.
    asking: {},
    //: Which jobs have their last runs unfolded: "job|library" -> true.
    unfolded: {},
    logs: {
        files: [],
        name: null,
        level: 'WARNING',
        text: '',
        //: A run's tag: only its lines ("Logs for this run").
        run: null,
        follow: false,
        records: [],
        //: Where the oldest record read begins, and where the file ended when read.
        start: null,
        end: null,
        complete: false,
        //: The request under way, so a newer one is not answered by an older.
        asked: 0,
    },
    //: The pollers (poll.js), paused while the page is hidden.
    pollers: [],
};
