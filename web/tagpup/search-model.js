// TagPup's page: what the search box offers and shows, as data (docs/ARCHITECTURE.md, phase 9e-2). No page, no request: the
// names the picker offers, made from the navigator's own indexes of the keywords and the people (navigator-model.js), the ones
// that match what is typed, and what a chip of a search's list says. search.js asks, paints and listens.
//
// ONE SOURCE OF NAMES. The picker offers what the navigator's rows are, because a search's member is exactly what a row's
// source is ({ kind: 'person', value: <name> }, { kind: 'keyword', value: <tag> }; the contract of phase 9e-1): the people
// GET /api/library/navigator?section=people answers -- the names photo_people holds, filed by the one rule of who is a person
// (tagpup.store.person_ids: a leaf node of a face root, never a branch) -- each shown by name, and every node of the tag tree,
// shown as its path. Not /api/tags and /api/people, the lists the edit fields complete from: those are the tags typed into
// photos and the names on faces, which a search would match differently.
//
// A NAME THAT IS A BRANCH IS NEVER A PERSON (#660). A photo tagged People/Family, where Family is a branch with people under
// it, lists "Family" among its people; the navigator shows that name unfiled. The picker does not offer it as a person: the
// branch is offered as the tag it is (People/Family, everything under it). On photo_index 4 names are so, each the name of a
// branch the people answer lists (counted read-only, 2026-10-04). And a filed person's own node is not offered a second time
// as a tag: they are offered by name.
import { compareTagNames, joinTag } from './common/vocabulary.js';
import { memberLabel, viewLabel } from './library-source.js';

/** The most names the picker shows at once; more are counted ("12 more: keep typing"). */
export const PICKER_MAX = 30;

/**
 * Every name the picker may offer, from the navigator's indexes of the keywords and the people (either may be null: not read,
 * or not readable): [{ member, label, hint, count, what, words }] -- `member` the source a chip holds, `label` what is shown
 * (a person's name, a tag's path), `hint` where a person is filed, `what` 'person' or 'tag', `words` what is matched (lower case).
 */
export function pickerNames(keywords, people) {
    const names = [];
    const personNodes = new Set();
    if (people) {
        const branches = new Set([...people.groups.values()].map(group => group.name.toLowerCase()));
        for (const person of people.people) {
            const key = person.name.toLowerCase();
            if (!person.groupTag && branches.has(key)) continue;   // a branch is never a person (#660)
            if (person.groupTag) personNodes.add(joinTag(person.groupTag, person.name).toLowerCase());
            names.push({
                member: { kind: 'person', value: person.name, recursive: false }, label: person.name, hint: person.groupTag || '',
                count: person.count, what: 'person', words: [key],
            });
        }
    }
    if (keywords) {
        for (const node of keywords.byTag.values()) {
            if (personNodes.has(node.tag.toLowerCase())) continue;   // offered as the person, by name
            names.push({
                member: { kind: 'keyword', value: node.tag, recursive: false }, label: node.tag, hint: '', count: node.count,
                what: 'tag', words: [node.name.toLowerCase(), node.tag.toLowerCase()],
            });
        }
    }
    return names;
}

/** How well `text` (lower case) matches `typed`: 0 it starts with it, 1 a word of it does, 2 it holds it, -1 not at all. */
function matchRank(text, typed) {
    const at = text.indexOf(typed);
    if (at < 0) return -1;
    if (at === 0) return 0;
    for (let from = at; from >= 0; from = text.indexOf(typed, from + 1)) {
        if (/[\s/(_.-]/.test(text[from - 1])) return 1;
    }
    return 2;
}

/**
 * The names that match what is typed, best first -- a name that starts with it, then one with a word that does, then one that
 * holds it; people before tags in each; then alphabetically (compareTagNames) -- at most `cap`: { options, more }. Nothing typed
 * offers nothing.
 */
export function matchNames(names, typed, cap = PICKER_MAX) {
    const wanted = String(typed || '').trim().toLowerCase();
    if (!wanted) return { options: [], more: 0 };
    const found = [];
    for (const name of names) {
        let best = -1;
        for (const words of name.words) {
            const here = matchRank(words, wanted);
            if (here >= 0 && (best < 0 || here < best)) best = here;
        }
        if (best >= 0) found.push({ name, best });
    }
    found.sort((a, b) => (a.best - b.best) || ((a.name.what === 'person' ? 0 : 1) - (b.name.what === 'person' ? 0 : 1))
        || compareTagNames(a.name.label, b.name.label));
    return { options: found.slice(0, cap).map(each => each.name), more: Math.max(0, found.length - cap) };
}

/** The one spelling of a source, to tell two apart: a chip already in a list is not added again. */
export function memberKey(member) {
    if (member.kind === 'any_of') return `any_of:${(member.value || []).map(memberKey).join('|')}`;
    const value = member.kind === 'person' || member.kind === 'keyword' || member.kind === 'keyword_only'
        ? String(member.value).toLowerCase() : String(member.value);
    return `${member.kind}:${value}:${member.recursive ? 1 : 0}`;
}

/** What a chip of a search's list says, and its tooltip: { text, title }. A source the picker does not offer is "within" it. */
export function chipLabel(member) {
    if (member.kind === 'person') return { text: String(member.value), title: `${member.value}: photos naming them` };
    if (member.kind === 'keyword') return { text: String(member.value), title: `${member.value}, and every tag under it` };
    if (member.kind === 'keyword_only') return { text: `${member.value} alone`, title: `${member.value}, without the tags under it` };
    return { text: `Within ${memberLabel(member)}`, title: `Within: ${viewLabel(member)}` };
}

/**
 * Does the library still have this chip's tag or person, as the navigator's indexes say? true or false for a tag or a person
 * whose index is read; null when it cannot be said (not read, or another kind of source). A bookmark may name a tag renamed
 * or deleted since: the chip says so, and the search answers what the library holds.
 */
export function chipKnown(member, keywords, people) {
    if (member.kind === 'person') return people ? people.byLower.has(String(member.value).toLowerCase()) : null;
    if (member.kind === 'keyword' || member.kind === 'keyword_only') {
        return keywords ? keywords.byTag.has(member.value) || keywords.byLower.has(String(member.value).toLowerCase()) : null;
    }
    return null;
}
