// TagPup's page: the add boxes of the open photo's people and keywords.
//
// Adding a person or a keyword to the photo is the less common act: the pills show what the photo holds, and the box that
// takes a new one (with its button) is shown by the pencil beside the pills (owner, 2026-11-02). It stays open once opened,
// from photo to photo, so a run of tagging is not a click each; the pencil closes it again, except while a typed name
// waits to be added (that text is a change not saved: edits.js counts it).
import { addPeopleGroup, addTagsGroup, btnEditPeople, btnEditTags, inputAddPerson, inputAddTag } from './elements.js';

function wire(button, group, input) {
    if (!button || !group) return;
    button.addEventListener('click', () => {
        const closing = !group.classList.contains('hidden');
        if (closing && input.value.trim()) {
            input.focus();
            return;
        }
        group.classList.toggle('hidden', closing);
        button.setAttribute('aria-expanded', String(!closing));
        if (!closing) input.focus();
    });
}

export function wireEditToggles() {
    wire(btnEditPeople, addPeopleGroup, inputAddPerson);
    wire(btnEditTags, addTagsGroup, inputAddTag);
}
