// Needs you: each failure that asks the owner to act, as the server words it (/api/activity/jobs, `needs_you`: groups
// of a kind, {kind, means, items}). A block an item: what happened, what it means (its kind's), what to do and how often;
// Run it now only where the server says the job has it (`run_now`), asked first as Scheduled jobs asks (jobs.js gives
// the control). Drawn again only when what it shows changes: a poll that reads the same leaves the page alone.
import { buildElement } from './common/dom.js';
import { state } from './state.js';
import { badge, fill, none } from './view.js';

/** "once, at <last>" or "3 times since <since>, the last at <last>". */
function howOften(item) {
    return item.count > 1 ? `${item.count} times since ${item.since}, the last at ${item.last}` : `once, at ${item.last}`;
}

function fact(label, ...value) {
    return [buildElement('dt', { text: label }), buildElement('dd', {}, value)];
}

function block(group, item, controls) {
    return buildElement('article', { className: `card need need-${group.kind}`,
                                     data: { kind: group.kind, library: item.library, job: item.job } }, [
        buildElement('h3', {}, [
            buildElement('span', { text: `${item.library}: ${item.job}` }),
            badge(group.kind, 'bad'),
        ]),
        buildElement('dl', { className: 'need-facts' }, [
            ...fact('What happened', buildElement('span', { text: item.sentence })),
            ...fact('What it means', buildElement('span', { text: group.means })),
            ...fact('What to do', buildElement('span', { text: item.next_step }),
                    ...(item.run_now ? controls(item.job, item.library) : [])),
            ...fact('How often', buildElement('span', { text: howOften(item) })),
        ]),
    ]);
}

/** Show the Needs you of `data` (the jobs' answer); `controls(job, library)` gives Run it now and what it answered. */
export function renderNeeds(data, controls) {
    const groups = (data && data.needs_you) || [];
    const drawn = JSON.stringify([groups, state.confirming, state.ranNow, state.asking]);
    if (drawn === state.needsDrawn) return;
    state.needsDrawn = drawn;
    const blocks = groups.flatMap(group => (group.items || []).map(item => block(group, item, controls)));
    fill(document.getElementById('needs-body'), ...(blocks.length ? blocks : [none('Nothing needs you.')]));
}
