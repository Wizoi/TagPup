// Scheduled jobs: each recurring job, why it is scheduled, and for each library its last
// run -- when, how long, how it ended, what it changed, why it failed -- and when it is due;
// Run now, asked first; its last runs unfolded; a failed last run flagged until a later one
// is done (/api/activity/jobs, POST /api/activity/jobs/run).
import { api } from './common/api.js';
import { buildElement } from './common/dom.js';
import { state } from './state.js';
import { ago, counts, duration } from './format.js';
import { badge, fill, none, outcomeKind, runLink } from './view.js';

/** Read each job's runs, and show them. */
export function loadJobs() {
    return api.site.json('/api/activity/jobs').then(data => {
        if (!data || data.success === false) return data;
        state.jobs = data;
        renderJobs();
        return data;
    });
}

function jobKey(job, library) {
    return `${job}|${library}`;
}

/**
 * Run now, once confirmed: the server answers once the run has started or been refused.
 * Once per click: while its POST is on its way, another for the same job and library sends
 * nothing, and the first answer is what is shown.
 */
export function runNow(job, library) {
    const key = jobKey(job, library);
    if (state.asking[key]) return Promise.resolve(null);
    state.asking[key] = true;
    state.confirming = null;
    state.ranNow = { job, library, text: 'Starting...', ok: true };
    renderJobs();
    const done = () => { delete state.asking[key]; };
    return api.site.json('/api/activity/jobs/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job, library }),
    }).then(answer => {
        let text;
        if (answer && answer.started) text = `Started (run ${answer.run_id}).`;
        else if (answer && answer.started === null) text = `Asked: ${answer.why}.`;
        else text = (answer && (answer.error || answer.why)) || 'Could not start it.';
        done();
        state.ranNow = { job, library, text, ok: !!(answer && answer.success) };
        renderJobs();
        return loadJobs().then(() => answer);
    }).catch(err => {
        done();
        state.ranNow = { job, library, text: `Could not start it: ${err.message || err}`, ok: false };
        renderJobs();
        return null;
    });
}

function runNowControls(job, library) {
    const confirming = state.confirming && state.confirming.job === job.name && state.confirming.library === library;
    if (confirming) {
        const yes = buildElement('button', { className: 'btn btn-danger confirm-run', text: 'Run',
                                             attrs: { type: 'button' } });
        const no = buildElement('button', { className: 'btn cancel-run', text: 'Cancel', attrs: { type: 'button' } });
        yes.addEventListener('click', () => runNow(job.name, library));
        no.addEventListener('click', () => {
            state.confirming = null;
            renderJobs();
        });
        return buildElement('span', { className: 'confirm' }, [
            buildElement('span', { text: `Run ${job.name} for ${library} now?` }), yes, no]);
    }
    const button = buildElement('button', {
        className: 'btn run-now', text: 'Run now',
        attrs: { type: 'button', disabled: !state.jobs.runs_jobs || job.running || !!state.asking[jobKey(job.name, library)] },
        title: state.jobs.runs_jobs ? `Run ${job.name} for ${library} now, whether or not it is due`
            : 'This server runs no recurring jobs; run it with the CLI (jobs run)',
        data: { job: job.name, library },
    });
    button.addEventListener('click', () => {
        // A click that reaches a disabled button (one dispatched to it) asks nothing either.
        if (button.disabled || state.asking[jobKey(job.name, library)]) return;
        state.confirming = { job: job.name, library };
        renderJobs();
    });
    return button;
}

function runRow(run, library) {
    return buildElement('tr', { className: `run outcome-${run.outcome}` }, [
        buildElement('td', { text: run.started, title: ago(run.started) }),
        buildElement('td', { text: duration(run.seconds) }),
        buildElement('td', {}, [badge(run.outcome, outcomeKind(run.outcome))]),
        buildElement('td', { text: counts(run.changed) }),
        buildElement('td', { className: 'error', text: run.error || '' }),
        buildElement('td', {}, [runLink(run.run, 'tagpup_web.log')]),
    ]);
}

function jobRow(job, library) {
    const last = job.runs && job.runs[0];
    const key = jobKey(job.name, library);
    const unfolded = !!state.unfolded[key];
    const fold = buildElement('button', {
        className: 'link fold', text: unfolded ? 'Hide runs' : `Runs (${(job.runs || []).length})`,
        attrs: { type: 'button', 'aria-expanded': unfolded ? 'true' : 'false', disabled: !(job.runs || []).length },
    });
    fold.addEventListener('click', () => {
        state.unfolded[key] = !unfolded;
        renderJobs();
    });
    const ran = state.ranNow && state.ranNow.job === job.name && state.ranNow.library === library ? state.ranNow : null;
    const rows = [buildElement('tr', { className: job.failing ? 'job failing' : 'job', data: { job: job.name, library } }, [
        buildElement('td', { className: 'library', text: library }),
        buildElement('td', {}, [
            last ? badge(last.outcome, outcomeKind(last.outcome)) : badge('never run', 'quiet'),
            job.failing ? badge('failed last time', 'bad') : null,
        ]),
        buildElement('td', { text: last ? ago(last.started) : '', title: last ? last.started : '' }),
        buildElement('td', { text: last ? duration(last.seconds) : '' }),
        buildElement('td', { text: last ? counts(last.changed) : '' }),
        buildElement('td', { className: 'error', text: last && last.error ? last.error : '' }),
        buildElement('td', { text: job.running ? 'running now' : ago(job.next_due), title: job.next_due || '' }),
        buildElement('td', { className: 'actions' }, [
            runNowControls(job, library),
            ran ? buildElement('span', { className: ran.ok ? 'said' : 'said bad', text: ran.text }) : null,
            fold,
            last ? runLink(last.run, 'tagpup_web.log') : null,
        ]),
    ])];
    if (unfolded) {
        rows.push(buildElement('tr', { className: 'runs' }, [buildElement('td', { attrs: { colspan: 8 } }, [
            buildElement('table', { className: 'runs-table' }, [buildElement('tbody', {},
                (job.runs || []).map(run => runRow(run, library)))]),
        ])]));
    }
    return rows;
}

/** Show what state.jobs holds: a table for each job, a row for each library. */
export function renderJobs() {
    const data = state.jobs;
    if (!data) return;
    const byJob = new Map();
    for (const library of data.libraries || []) {
        for (const job of library.jobs || []) {
            if (!byJob.has(job.name)) byJob.set(job.name, { job, rows: [] });
            byJob.get(job.name).rows.push(...jobRow(job, library.name));
        }
    }
    const blocks = [];
    if (!data.runs_jobs) {
        blocks.push(none('This server runs no recurring jobs: the always-on process does. What is shown is what ran.'));
    }
    for (const [name, { job, rows }] of byJob) {
        blocks.push(buildElement('div', { className: 'card job-card', data: { job: name } }, [
            buildElement('h3', {}, [
                buildElement('span', { text: name }),
                badge(job.reason, 'quiet'),
                buildElement('small', { text: job.period }),
            ]),
            buildElement('p', { className: 'about', text: job.about || '' }),
            buildElement('table', { className: 'jobs-table' }, [
                buildElement('thead', {}, [buildElement('tr', {}, ['Library', 'Last run', 'When', 'Took', 'Changed',
                                                                    'Error', 'Next', ''].map(text => buildElement('th', { text })))]),
                buildElement('tbody', {}, rows),
            ]),
        ]));
    }
    fill(document.getElementById('jobs-body'), ...(blocks.length ? blocks : [none('No recurring jobs.')]));
}
