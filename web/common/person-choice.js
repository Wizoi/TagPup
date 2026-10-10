/**
 * "Which one?": the question a page asks when a name is two people's.
 *
 * Typing "Sam" where a Sam under Friends and a Sam under Pets both exist cannot be answered by the page, and must not be
 * answered by the first in the list. The people are offered with their labels (`Sam · Friends`) and the full tag in each one's
 * title, and the answer is the person chosen -- an object with the id -- or null when the question is declined (Cancel, Escape, a
 * click on the backdrop). One question at a time: asking again while one is open gets the same answer as the first.
 *
 * Built from the pages' shared modal classes (`.modal-overlay.active`), so a page's shortcuts leave their keys alone while it
 * is open (web/common/dialog.js).
 */
import { buildElement } from './dom.js';
import { personLabelNode } from './person-label.js';
import { personTitle } from './vocabulary.js';

// The question being asked, with the ids of the people it asks about: the same question asked again gets the same answer; a question
// about OTHER people replaces it (the first is answered "none"), never answered by the first's choice.
let personQuestion = null;

/**
 * Ask which of `people` is meant. Resolves to the person chosen, or null. `title` and `about` are the question's words.
 */
export function choosePerson(people, { title = 'Which person?', about = '' } = {}) {
    const list = Array.isArray(people) ? people : [];
    const key = list.map(person => person.id).join(',');
    if (personQuestion && personQuestion.key === key) return personQuestion.promise;
    if (personQuestion) personQuestion.cancel();
    const mine = { key, promise: null, cancel: null };
    const opener = document.activeElement;
    mine.promise = new Promise((resolve) => {
        let chosen = list.length ? list[0] : null;
        const choices = list.map((person, index) => buildElement('label', {
            className: 'person-choice-option', title: personTitle(person), data: { personId: person.id },
        }, [
            buildElement('input', { attrs: { type: 'radio', name: 'person-choice', value: String(index), checked: index === 0 } }),
            buildElement('span', { className: 'person-choice-label' }, [personLabelNode(person)]),
            buildElement('span', { className: 'person-choice-tag', text: person.tag ? ` ${personTitle(person)}` : '' }),
        ]));
        const cancel = buildElement('button', { className: 'btn btn-secondary btn-cancel', text: 'Cancel', attrs: { type: 'button' } });
        const confirm = buildElement('button', { className: 'btn btn-primary btn-confirm', text: 'Choose', attrs: { type: 'button' } });
        const overlay = buildElement('div', {
            className: 'modal-overlay active person-choice',
            attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
        }, [
            buildElement('div', { className: 'modal-container', style: 'max-width: 450px;' }, [
                buildElement('div', { className: 'modal-header' }, [buildElement('h2', { text: title })]),
                buildElement('div', { className: 'modal-body' }, [
                    about ? buildElement('p', { className: 'person-choice-about', text: about }) : null,
                    buildElement('div', { className: 'person-choice-options' }, choices),
                ]),
                buildElement('div', { className: 'modal-footer' }, [cancel, confirm]),
            ]),
        ]);
        const finish = (value) => {
            document.removeEventListener('keydown', onKey, true);
            overlay.remove();
            if (personQuestion === mine) personQuestion = null;
            if (opener && typeof opener.focus === 'function' && opener.isConnected) opener.focus();
            resolve(value);
        };
        mine.cancel = () => finish(null);
        const onKey = (event) => {
            if (event.key !== 'Escape') return;
            event.preventDefault();
            event.stopPropagation();
            finish(null);
        };
        overlay.addEventListener('change', (event) => {
            const index = Number(event.target && event.target.value);
            if (Number.isInteger(index) && list[index]) chosen = list[index];
        });
        overlay.addEventListener('click', (event) => {
            if (event.target === overlay) finish(null);
        });
        cancel.addEventListener('click', () => finish(null));
        confirm.addEventListener('click', () => finish(chosen));
        document.addEventListener('keydown', onKey, true);
        document.body.append(overlay);
        const first = overlay.querySelector('input');
        if (first) first.focus();
    });
    personQuestion = mine;
    return mine.promise;
}
