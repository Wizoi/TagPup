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

/**
 * "Logs for this run": a button for each log the server says the run `run` logged in
 * (`logs`, its own first), showing its lines there. The page never spells a log's name.
 */
export function runLinks(run, logs) {
    if (!run || !logs || !logs.length) return [];
    return logs.map((name, index) => {
        const button = buildElement('button', {
            className: 'link run-logs', text: index ? `...in ${name}` : 'Logs for this run', title: `${run} in ${name}`,
            attrs: { type: 'button' }, data: { run, log: name },
        });
        button.addEventListener('click', () => showRunLogs(run, name));
        return button;
    });
}
