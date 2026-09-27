// What the sections share: a section's body, a badge, an empty line, and the link to a
// run's lines in the logs. Elements are built from text (web/common/dom.js).
import { buildElement, replaceContent } from './common/dom.js';
import { showRunLogs } from './logs.js';

/** Show `children` in `body`, a section's body (none when it is not there). */
export function fill(body, ...children) {
    if (body) replaceContent(body, ...children);
    return body;
}

/** A small label saying how something stands: kind is ok, bad, busy or quiet. */
export function badge(text, kind = 'quiet') {
    return buildElement('span', { className: `badge badge-${kind}`, text });
}

/** A line saying there is nothing to show. */
export function none(text) {
    return buildElement('p', { className: 'none', text });
}

/** How an outcome looks: done is ok, failed and abandoned are bad, running is busy. */
export function outcomeKind(outcome) {
    if (outcome === 'done' || outcome === 'completed' || outcome === 'in step' || outcome === 'applied'
        || outcome === 'moved') return 'ok';
    if (outcome === 'failed' || outcome === 'abandoned' || outcome === 'not in step') return 'bad';
    if (outcome === 'running' || outcome === 'queued' || outcome === 'preparing') return 'busy';
    return 'quiet';
}

/** "Logs for this run": a button showing the lines the run `run` logged, in the log `name`. */
export function runLink(run, name) {
    if (!run) return null;
    const button = buildElement('button', {
        className: 'link run-logs', text: 'Logs for this run', title: run, attrs: { type: 'button' },
        data: { run },
    });
    button.addEventListener('click', () => showRunLogs(run, name));
    return button;
}

/** The log a run's lines are in: the indexer's own for a run of the indexer, else the server's. */
export function logOf(run, library) {
    if (run && run.startsWith('index:') && library) return `indexer-${library}.log`;
    return 'tagpup_web.log';
}
