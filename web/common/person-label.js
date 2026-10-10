/**
 * A person's label as an element, for the places a window can be too narrow for it (docs/ARCHITECTURE.md, "People by id, stage 2",
 * "Showing the group"): `Sam · Thackeray`, the name first because lists are sorted by it and it is what is scanned for, the group
 * dimmed after it. When there is no room the GROUP is cut at its END (`Sam · Thackeray/Cou…`: the server sends the shortest tail that
 * tells the sharing people apart, so its start is the discriminator) and the name never is -- the stylesheet's job (person-choice.css: `.person-label`), which
 * needs the group in a span of its own. Its text is exactly personLabel's; its title is the full tag. Names go in as text.
 */
import { buildElement } from './dom.js';
import { GROUP_SEPARATOR, personTitle } from './vocabulary.js';

/** The label of a person record ({id, name, tag, group, shared}) as a span; a name that is not shared is the name alone. */
export function personLabelNode(person) {
    const name = String(person && person.name !== undefined && person.name !== null ? person.name : '');
    const title = personTitle(person);
    if (!person || !person.shared || !person.group) return buildElement('span', { className: 'person-label', text: name, title });
    return buildElement('span', { className: 'person-label is-shared', title }, [
        buildElement('span', { className: 'person-label-name', text: name + GROUP_SEPARATOR }),
        buildElement('span', { className: 'person-label-group' }, [buildElement('bdi', { text: String(person.group) })]),
    ]);
}

/** The label of the person a row names (`row.person`, else the name it holds under `field`) as a span. */
export function personLabelNodeOf(row, field = 'name') {
    if (row && row.person) return personLabelNode(row.person);
    return buildElement('span', { className: 'person-label', text: String((row && row[field]) ?? '') });
}
