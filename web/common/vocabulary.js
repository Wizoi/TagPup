/**
 * How the pages talk about a tag: the same helpers in both, written once.
 *
 * A person has two shapes and they are not interchangeable. Their identity is a leaf
 * -- "Hazel Brookmire" -- which is what the faces table, the suggester and
 * photo.people speak in. Their tag is a path -- "People/Hazel Brookmire" -- which is
 * what the keywords must hold and what the server matches on, exactly. Every bug in
 * this area was a site converting between the two by hand, so the conversions live
 * here: tests/frontend/tag-vocabulary.test.mjs fails on a raw `.split('/')` in the
 * pages outside them. The server's reading of a tag is tagpup/core/vocabulary.py's.
 *
 * And what may be set as a tag, a name or a caption, which is the server's to say
 * (tagpup/core/validation.py): asked of web/common/validate.js, which applies the
 * rules the server publishes.
 */
import { ruleProblem } from './validate.js';

/** The last segment of a tag path: the name a person is known by. */
export function leafOf(tag) {
    if (!tag) return '';
    const text = String(tag);
    return text.includes('/') ? text.split('/').pop().trim() : text.trim();
}

/** The first segment of a tag path: the category it is filed under. */
export function rootOf(tag) {
    if (!tag) return '';
    return String(tag).split('/')[0].trim();
}

/** The tag of a node named `name` under the tag `parentTag` (none: a root): "People/Rowan Thackeray". */
export function joinTag(parentTag, name) {
    return parentTag ? `${parentTag}/${name}` : String(name);
}

// ---- The order tags are shown in ---------------------------------------------------
//
// Every list of tags or people that is shown as a set -- a photo's tags, a picker, an
// autocomplete, a placement question -- is alphabetical, one way: case and accents
// ignored, digits read as numbers ("Trip 3" before "Trip 10"), then the exact spelling,
// so the order is total and a reload does not reshuffle it. A tag path is compared one
// level at a time, the way the tag editor's tree orders it, so "Family" is followed by
// "Family/Amy" and then "Family Tree", not by the tag a space would put first. A list
// that is ranked on purpose (by count, score or similarity) keeps its rank and breaks
// its ties with this order. It is the display's order only: what is written to a file,
// stored or sent keeps the order it has. The server's is tagpup.core.vocabulary.tag_sort_key;
// tests/fixtures/tag_order.json is the table both are held to. The collator is fixed to
// English, not the machine's language, so the order is the same on every computer.
const NAME_ORDER = new Intl.Collator('en', { sensitivity: 'base', numeric: true });

function exactOrder(left, right) {
    return left < right ? -1 : left > right ? 1 : 0;
}

function orderOf(a, b) {
    const shared = Math.min(a.parts.length, b.parts.length);
    for (let i = 0; i < shared; i++) {
        const found = NAME_ORDER.compare(a.parts[i], b.parts[i]);
        if (found !== 0) return found;
    }
    return (a.parts.length - b.parts.length) || exactOrder(a.text, b.text);
}

function ordering(value) {
    const text = String(value ?? '');
    return { text, parts: text.split('/') };
}

/** The order tags are listed in: alphabetical, then the exact spelling, so it is total. */
export function compareTagNames(a, b) {
    return orderOf(ordering(a), ordering(b));
}

/**
 * A copy of `list` in the order tags are shown in. `key` says which text of an item to
 * order by (the item itself when it is a tag). With `rank`, a number each item is
 * ranked by, the biggest first, the alphabet only breaks ties. Each text is taken apart
 * once, not once per comparison.
 */
export function sortedTags(list, key = (item) => item, { rank = null } = {}) {
    const entries = Array.from(list, (item) => ({
        item, ...ordering(key(item)), rank: rank ? Number(rank(item)) || 0 : 0,
    }));
    entries.sort((a, b) => (b.rank - a.rank) || orderOf(a, b));
    return entries.map(entry => entry.item);
}

/** Do these two tags name the same person, however each is spelled? */
export function samePerson(a, b) {
    const left = leafOf(a).toLowerCase();
    return Boolean(left) && left === leafOf(b).toLowerCase();
}

// ---- How a person is shown -----------------------------------------------------------------
//
// A person is the whole tag path and their name is its leaf: shown, never compared. Two people called alike are told apart by
// the tag in the title and in the "which one?" question (person-choice.js), not by a group joined to the name.

/** What is shown for a person: their name. */
export function personLabel(person) {
    return person ? String(person.name ?? '') : '';
}

/** The full tag of a person, for the element's title and the screen reader: where they are filed. */
export function personTitle(person) {
    if (!person) return '';
    return String(person.tag || person.name || '');
}

/** What to show for the person a row names: the name of its `person` (the nested {id, name, tag} the server sends), else the
 *  name it holds under `field`. */
export function personLabelOf(row, field = 'name') {
    if (!row) return '';
    return row.person ? personLabel(row.person) : String(row[field] ?? '');
}

/** The hover and the screen reader's text for the same: the full tag when the row's person has one, else the name. */
export function personTitleOf(row, field = 'name') {
    if (!row) return '';
    return row.person ? personTitle(row.person) : String(row[field] ?? '');
}

/**
 * How a request names a person: the id of their node when the page has one (the only way to name one of two people called alike),
 * else the name they are known by (a name no tag has; an open page the server has not told). `person` is a {id, name} or a bare name.
 */
export function personFields(person) {
    if (person && typeof person === 'object') {
        if (person.id !== undefined && person.id !== null) return { person_id: person.id };
        return { person_name: String(person.name ?? '') };
    }
    return { person_name: String(person ?? '') };
}

// ---- The people of a library, by id --------------------------------------------------------
//
// /api/people?records=1 answers every person with a tag as {id, name, tag}. The pages hold them in a PeopleDirectory and ask it
// who a text is -- a tag path names exactly one person, a bare name names one only when nobody else is called it -- and compare
// people by id, never by leaf. A name two people have is `shared`: a page asks which one, and never picks one for the owner. tests/frontend/person-directory.test.mjs holds it to the owner's real case: a pet
// and a friend of one name.

const textKey = (text) => String(text ?? '').trim().toLowerCase();

export class PeopleDirectory {
    /** `records`: the answer of /api/people?records=1 (anything else in the list -- a name, a failed lookup's object -- is let go). */
    constructor(records = []) {
        this.records = (Array.isArray(records) ? records : [])
            .filter(each => each && typeof each === 'object' && each.id !== undefined && each.id !== null && each.tag);
        this.byId = new Map();
        this.byTag = new Map();
        this.byName = new Map();
        for (const each of this.records) {
            this.byId.set(each.id, each);
            this.byTag.set(textKey(each.tag), each);
            const key = textKey(each.name);
            if (!this.byName.has(key)) this.byName.set(key, []);
            this.byName.get(key).push(each);
        }
    }

    get size() {
        return this.records.length;
    }

    ofId(id) {
        return (id !== undefined && id !== null && this.byId.get(id)) || null;
    }

    /** The person filed at exactly this tag path, or null. */
    ofTag(tag) {
        return this.byTag.get(textKey(tag)) || null;
    }

    /** Everyone called `name` (without case): one, two, or none. */
    called(name) {
        return (this.byName.get(textKey(name)) || []).slice();
    }

    /** The one person called `name`, or null when nobody is or when two or more are (a name alone cannot say which). */
    only(name) {
        const found = this.byName.get(textKey(name));
        return found && found.length === 1 ? found[0] : null;
    }

    /** Do two or more people share this name? */
    shared(name) {
        return (this.byName.get(textKey(name)) || []).length > 1;
    }

    /** The person a text names: a tag path exactly, a bare name when it is one person's. Null for neither. */
    ofText(text) {
        const value = String(text ?? '').trim();
        if (!value) return null;
        return value.includes('/') ? this.ofTag(value) : this.only(value);
    }

    /**
     * What typed text means, for a picker that offers people by name:
     *   { kind: 'person', person } -- a tag path, or a name one person has;
     *   { kind: 'choose', people } -- a name two or more people have: the owner is asked which, never given the first;
     *   { kind: 'new', name } -- a name nobody has; { kind: 'none' } -- nothing was typed.
     */
    match(text) {
        const value = String(text ?? '').trim();
        if (!value) return { kind: 'none' };
        const filed = this.ofTag(value);
        if (filed) return { kind: 'person', person: filed };
        const called = this.called(value);
        if (called.length === 1) return { kind: 'person', person: called[0] };
        if (called.length > 1) return { kind: 'choose', people: called };
        return { kind: 'new', name: value };
    }

    /** Every person, by name and then tag (the order the server sends them in), as a copy. */
    all() {
        return this.records.slice();
    }
}

/** Are these two the same person? By id; a person with no id (a name no tag has) by name. Never by label. */
export function sameRecord(a, b) {
    if (!a || !b) return false;
    const left = a.id ?? null, right = b.id ?? null;
    if (left !== null && right !== null) return left === right;
    if (left !== null || right !== null) return false;
    return textKey(a.name) === textKey(b.name);
}

/**
 * Do these two tags -- a path or a bare name each -- name the same person? With the library's people (`directory`): by id, so two
 * people called Sam are two; a bare name two people have names neither of them for certain, so it is nobody's; a text no person has
 * is its leaf. Without a directory (a page that could not read the people): by leaf, as before.
 */
export function sameTagPerson(a, b, directory = null) {
    if (!directory || !directory.size) return samePerson(a, b);
    const left = directory.ofText(a), right = directory.ofText(b);
    if (left && right) return sameRecord(left, right);
    if (left || right) return false;
    return samePerson(a, b) && directory.called(leafOf(a)).length === 0;
}

/**
 * Do two rows that each name someone -- a face and a match, `{name, person?}` -- name the same person? By the id of their nodes
 * when both rows have one (two people called Sam are two), else by name.
 */
export function sameNamed(a, b) {
    if (!a || !b) return false;
    const left = a.person && a.person.id !== undefined ? a.person.id : null;
    const right = b.person && b.person.id !== undefined ? b.person.id : null;
    if (left !== null && right !== null) return left === right;
    return samePerson(a.name, b.name);
}

/**
 * Does the list of names a photo lists (photo.people: leaves, no ids) name this person? Not when two people have the name: a
 * name alone names neither of them for certain, so the list cannot say the photo has THIS one.
 */
export function peopleListHas(names, text, directory = null) {
    const leaf = leafOf(text);
    if (!leaf || (directory && directory.shared(leaf))) return false;
    return (names || []).some(each => samePerson(each, leaf));
}

/**
 * Does this photo already carry this tag, or this same person under another spelling?
 *
 * A plain `tags.includes()` compared "Hazel Brookmire" against
 * "People/Hazel Brookmire", found no match, and wrote the person in a second
 * time. A tag path names exactly one person; a bare name is one only when nobody else is called it,
 * so with the library's people (`directory`) two people called Sam are two (`sameTagPerson`).
 *
 * `isPersonTag` is the page's own answer to whether a tag names a person: which
 * roots hold people is the tag tree's, and only the page has the tree loaded.
 */
export function photoAlreadyHas(photo, tag, isPersonTag, directory = null) {
    const tags = (photo && photo.tags) || [];
    if (tags.includes(tag)) return true;
    if (!isPersonTag(tag)) return false;
    return tags.some(t => isPersonTag(t) && sameTagPerson(t, tag, directory));
}

/**
 * Why this cannot be set as a tag, or null if it can.
 *
 * The server refuses the same tags, with the same words: both ask the rules of
 * tagpup/core/validation.py, the page through validate.js. Asking here first only
 * means nothing is created for a tag that will be refused, and the text stays where
 * it was typed.
 */
export function tagProblem(tag) {
    return ruleProblem('tag', tag);
}

/** The same for a person's name or one level of a tag, which cannot hold a "/" either. */
export function nameProblem(name) {
    return ruleProblem('name', name);
}

/** The same for a photo's caption, which is its title too. */
export function textProblem(text) {
    return ruleProblem('caption', text);
}
