// TagPup's page: what the navigator shows, as data (docs/ARCHITECTURE.md, phase 9c). No page, no request: the
// counts GET /api/library/navigator answers, made into the rows a tree shows -- which are the library's folders,
// the tag tree, the people and the years -- and the rows' ids, so that the source a view was opened from
// can be found in them again. navigator.js asks, paints and listens; this is what can be tested and measured alone.
//
// A row is { id, level, label, count, hint, title, expandable, expanded, spec } where `spec` is what
// openLibraryView takes ({ kind, value, recursive }) or null for a row that only groups (the junk years, a branch of people).
// The lists are the whole library's -- 2,746 folders, 895 keyword nodes, 413 people, 61 years on photo_index -- and
// a tree shows only what is expanded, at most NAV_MAX_ROWS rows (a flat list, NAV_MAX_LIST_ROWS): the page never draws them
// all at once, and when it draws fewer than there are it says how many it left out, counted over the whole tree.
//
// SEVERAL ROWS ARE SELECTED AT ONCE (the owner's review, #672): the view shows the union of what they hold. A row holds
// photos of its own (a folder's own photos, a keyword's node alone, a person, a month, the "Other" of a year) and, with
// the rows under it, its whole (a folder and its subfolders, a keyword and everything under it, a year); selecting a row
// selects every row under it, and taking one of those off leaves the row's own photos in. `rowTree` is a section's rows
// as that tree, `compress` turns the rows selected into the fewest sources that hold the same photos (a row whose every
// row below is selected is its whole), and `selectedRows` turns a view's sources back into the rows -- so the address
// holds the selection and Back, Forward and a bookmark restore it.
import { baseName, pathKey } from './common/paths.js';
import { compareTagNames, leafOf } from './common/vocabulary.js';

/** The most rows one section draws; the rest are said in a line and reached by the filter. */
export const NAV_MAX_ROWS = 1500;

/**
 * The most rows a flat list draws: the people, the years and months, what a filter finds. The cap exists for trees, whose
 * open branches can run to thousands of rows; a list of 413 people is not that, and people past a cap of 400 were
 * reachable only by typing their names (findings #567). A list longer than this is cut at its end and says how many.
 */
export const NAV_MAX_LIST_ROWS = 5000;


const NAV_MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September',
    'October', 'November', 'December'];

function navPlural(count, one, many) {
    return `${count.toLocaleString()} ${count === 1 ? one : many}`;
}

// ---- Folders ---------------------------------------------------------------------------------

/**
 * The folder tree of the route's flat list, [{ path, name, parent, direct, recursive }]: nodes by the key of
 * their path (pathKey: the same folder in any spelling), each holding its children in the list's order. A folder
 * whose parent is not in the list is a top of the tree.
 */
export function indexFolders(list) {
    const byKey = new Map();
    for (const each of Array.isArray(list) ? list : []) {
        if (!each || typeof each.path !== 'string') continue;
        const key = pathKey(each.path);
        byKey.set(key, {
            id: `f:${key}`, key, path: each.path, name: each.name || baseName(each.path) || each.path,
            parentKey: each.parent ? pathKey(each.parent) : null,
            direct: Number(each.direct) || 0, recursive: Number(each.recursive) || 0, children: [],
        });
    }
    const tops = [];
    for (const node of byKey.values()) {
        const parent = node.parentKey !== null ? byKey.get(node.parentKey) : null;
        if (parent && parent !== node) parent.children.push(node);
        else tops.push(node);
    }
    return { byKey, tops, size: byKey.size };
}

function navFolderRow(node, level, expanded) {
    const open = expanded.has(node.id);
    return {
        id: node.id, level, label: node.name, count: node.recursive,
        title: `${node.path}\n${navPlural(node.direct, 'photo', 'photos')} in it, ${navPlural(node.recursive, 'photo', 'photos')} with its subfolders`,
        aria: `${node.name}, ${navPlural(node.recursive, 'photo', 'photos')} with its subfolders`,
        hint: '', expandable: node.children.length > 0, expanded: open && node.children.length > 0,
        spec: { kind: 'folder', value: node.path, recursive: true },
    };
}

// ---- Keywords --------------------------------------------------------------------------------

/**
 * The tag tree of the route's list, [{ tag, name, parent, count }] (`parent` the tag of the node above, or null):
 * nodes by tag and by the tag without case, each holding its children alphabetically (compareTagNames).
 */
export function indexKeywords(list) {
    const byTag = new Map();
    const byLower = new Map();
    for (const each of Array.isArray(list) ? list : []) {
        if (!each || typeof each.tag !== 'string') continue;
        const node = {
            id: `k:${each.tag}`, tag: each.tag, name: each.name || each.tag, parentTag: each.parent || null,
            count: Number(each.count) || 0, children: [],
        };
        byTag.set(node.tag, node);
        if (!byLower.has(node.tag.toLowerCase())) byLower.set(node.tag.toLowerCase(), node);
    }
    const tops = [];
    for (const node of byTag.values()) {
        const parent = node.parentTag !== null ? byTag.get(node.parentTag) : null;
        if (parent && parent !== node) parent.children.push(node);
        else tops.push(node);
    }
    const alphabetical = (a, b) => compareTagNames(a.name, b.name) || compareTagNames(a.tag, b.tag);
    tops.sort(alphabetical);
    for (const node of byTag.values()) node.children.sort(alphabetical);
    return { byTag, byLower, tops, size: byTag.size };
}

function navKeywordRow(node, level, expanded) {
    const open = expanded.has(node.id);
    return {
        id: node.id, level, label: node.name, count: node.count,
        title: `${node.tag}\n${navPlural(node.count, 'photo', 'photos')} with it or a keyword under it`,
        aria: `${node.name}, ${navPlural(node.count, 'photo', 'photos')}`,
        hint: '', expandable: node.children.length > 0, expanded: open && node.children.length > 0,
        spec: { kind: 'keyword', value: node.tag, recursive: false },
    };
}

// ---- People ----------------------------------------------------------------------------------

/** The row that gathers the people no branch of the tag tree files (no node, a branch of that name, or two nodes). */
export const UNFILED_ID = 'g:';

/**
 * The people of the route's answer -- `list` [{ name, count, group }], `groups` [{ tag, name, parent, count }], `unfiled` the
 * photos naming someone not filed -- as a tree (#673): each person under the branch of the tag tree they are filed in
 * (Family/Immediate apart from Friends), the branches nested as the tree nests them, alphabetically, the branches before
 * the people in each; those not filed under one row at the end. A library whose people are filed nowhere is the flat
 * list it was. Names differing in case are one: the route merges them.
 */
export function indexPeople(list, groups = [], unfiled = 0) {
    const people = [];
    const byLower = new Map();
    const byTag = new Map();
    for (const each of Array.isArray(groups) ? groups : []) {
        if (!each || typeof each.tag !== 'string' || byTag.has(each.tag)) continue;
        byTag.set(each.tag, {
            id: `g:${each.tag}`, tag: each.tag, name: each.name || leafOf(each.tag), parentTag: each.parent || null,
            count: Number(each.count) || 0, group: true, subgroups: [], people: [], children: [],
        });
    }
    for (const each of Array.isArray(list) ? list : []) {
        if (!each || typeof each.name !== 'string') continue;
        // A person is their id when the server has one (two people called alike are two rows: `p:${name}` collided); a name
        // no node is stays the name. `tag` is the person's path.
        const identity = each.person_id !== undefined && each.person_id !== null ? each.person_id : each.name;
        const record = each.person && typeof each.person === 'object' ? each.person : {};
        people.push({
            id: `p:${identity}`, name: each.name, count: Number(each.count) || 0, groupTag: each.group || null, children: [],
            personId: each.person_id === undefined ? null : each.person_id, tag: typeof record.tag === 'string' ? record.tag : null,
        });
    }
    // Two rows of one name are two people: each is asked for by its tag, not as the name (which is everyone called it).
    const called = new Map();
    for (const person of people) called.set(person.name.toLowerCase(), (called.get(person.name.toLowerCase()) || 0) + 1);
    for (const person of people) person.shared = called.get(person.name.toLowerCase()) > 1;
    people.sort((a, b) => compareTagNames(a.name, b.name) || compareTagNames(a.tag || '', b.tag || ''));
    const byPersonTag = new Map();
    for (const person of people) {
        if (!byLower.has(person.name.toLowerCase())) byLower.set(person.name.toLowerCase(), person);
        if (person.tag) byPersonTag.set(person.tag.toLowerCase(), person);
    }
    const alphabetical = (a, b) => compareTagNames(a.name, b.name) || compareTagNames(a.tag, b.tag);
    const tops = [];
    for (const group of [...byTag.values()].sort(alphabetical)) {
        const parent = group.parentTag !== null ? byTag.get(group.parentTag) : null;
        if (parent && parent !== group) parent.subgroups.push(group);
        else tops.push(group);
    }
    const loose = [];
    for (const person of people) {
        const group = person.groupTag !== null ? byTag.get(person.groupTag) : null;
        if (group) group.people.push(person);
        else loose.push(person);
    }
    for (const group of byTag.values()) group.children = [...group.subgroups, ...group.people];
    const grouped = byTag.size > 0;
    if (grouped && loose.length) {
        tops.push({
            id: UNFILED_ID, tag: '', name: 'Not filed in the tag tree', parentTag: null, count: Number(unfiled) || 0, group: true,
            unfiled: true, subgroups: [], people: loose, children: loose,
        });
    }
    return { people, byLower, byPersonTag, groups: byTag, tops: grouped ? tops : loose, grouped, size: people.length };
}

/**
 * What a view or a chip of this person asks for: their name, or -- when another person has it -- their tag path, which names
 * exactly them (the library's views: a name is everyone called it, a path is one person).
 */
export function personSource(person) {
    return person.shared && person.tag ? person.tag : person.name;
}

function navPersonRow(person, level = 1) {
    const shown = person.name;
    return {
        id: person.id, level, label: shown, count: person.count,
        title: `${person.shared && person.tag ? person.tag : person.name}${person.groupTag ? ` (${person.groupTag})` : ''}\n${navPlural(person.count, 'photo', 'photos')}`,
        aria: `${shown}, ${navPlural(person.count, 'photo', 'photos')}`,
        hint: '', expandable: false, expanded: false,
        spec: { kind: 'person', value: personSource(person), recursive: false },
    };
}

function navPeopleRow(node, level, expanded) {
    if (!node.group) return navPersonRow(node, level);
    const open = expanded.has(node.id);
    const people = node.unfiled ? node.people.length : navPeopleUnder(node);
    return {
        id: node.id, level, label: node.name, count: node.count,
        title: node.unfiled
            ? `${navPlural(people, 'person', 'people')} no branch of the tag tree files\n${navPlural(node.count, 'photo', 'photos')} naming one of them`
            : `${node.tag}\n${navPlural(people, 'person', 'people')}, ${navPlural(node.count, 'photo', 'photos')} naming one of them`,
        aria: `${node.name}, ${navPlural(people, 'person', 'people')}, ${navPlural(node.count, 'photo', 'photos')}`,
        hint: '', expandable: node.children.length > 0, expanded: open && node.children.length > 0, spec: null,
    };
}

function navPeopleUnder(group) {
    let found = group.people.length;
    for (const sub of group.subgroups) found += navPeopleUnder(sub);
    return found;
}

/** The ids of every branch row of the people, to be open when the section is first drawn: the people show under their headers. */
export function peopleGroupIds(index) {
    return index && index.grouped ? [...[...index.groups.values()].map(group => group.id), UNFILED_ID] : [];
}

// ---- Dates -----------------------------------------------------------------------------------

/**
 * The years of the route's { years: [{ year, count, months: [{ month, count }], other }], undated }: newest
 * first, and those the route calls `implausible` (before 1900 or after next year: a date a camera's clock got wrong, or a
 * number in a file name) apart, as 'Other years': still reachable, collapsed, at the end. The rule is the server's
 * (tagpup.store.library_view.plausible_year); the page shows what it is told (#510).
 */
export function indexDates(data) {
    const years = [];
    for (const each of data && Array.isArray(data.years) ? data.years : []) {
        if (!each || !Number.isFinite(each.year)) continue;
        const months = (Array.isArray(each.months) ? each.months : [])
            .filter(m => m && typeof m.month === 'string' && /^\d{4}-\d{2}$/.test(m.month))
            .map(m => ({ month: m.month, number: Number(m.month.slice(5)), count: Number(m.count) || 0 }))
            .sort((a, b) => a.number - b.number);
        years.push({ year: each.year, count: Number(each.count) || 0, other: Number(each.other) || 0, months,
            implausible: each.implausible === true });
    }
    years.sort((a, b) => b.year - a.year);
    const usual = years.filter(each => !each.implausible);
    const odd = years.filter(each => each.implausible);
    const byYear = new Map(years.map(each => [each.year, each]));
    return {
        usual, odd, byYear, size: years.length,
        oddPhotos: odd.reduce((sum, each) => sum + each.count, 0),
        undated: data && Number.isFinite(data.undated) ? data.undated : 0,
    };
}

export const OTHER_YEARS_ID = 'y:other';

function navYearRow(each, level, expanded) {
    const expandable = each.months.length > 0;
    return {
        id: `y:${each.year}`, level, label: String(each.year), count: each.count,
        title: `${navPlural(each.count, 'photo', 'photos')} taken in ${each.year}`,
        aria: `${each.year}, ${navPlural(each.count, 'photo', 'photos')}`,
        hint: '', expandable, expanded: expandable && expanded.has(`y:${each.year}`),
        spec: { kind: 'year', value: String(each.year), recursive: false },
    };
}

function navMonthRows(each, level) {
    const rows = each.months.map(m => ({
        id: `m:${m.month}`, level, label: `${NAV_MONTH_NAMES[m.number - 1] || m.month}`, count: m.count,
        title: `${navPlural(m.count, 'photo', 'photos')} taken in ${NAV_MONTH_NAMES[m.number - 1] || m.month} ${each.year}`,
        aria: `${NAV_MONTH_NAMES[m.number - 1] || m.month} ${each.year}, ${navPlural(m.count, 'photo', 'photos')}`,
        hint: '', expandable: false, expanded: false,
        spec: { kind: 'month', value: m.month, recursive: false },
    }));
    if (each.other > 0) {
        // Photos of the year whose date names no month of it: counted, and reached by the year.
        rows.push({
            id: `o:${each.year}`, level, label: 'Other', count: each.other,
            title: `${navPlural(each.other, 'photo', 'photos')} of ${each.year} whose date names no month of it`,
            aria: `Other, ${navPlural(each.other, 'photo', 'photos')} of ${each.year} with no month`,
            hint: '', expandable: false, expanded: false, spec: { kind: 'year_other', value: String(each.year), recursive: false },
        });
    }
    return rows;
}

// ---- Rows ------------------------------------------------------------------------------------

function navCapped(rows, cap, total) {
    if (rows.length <= cap) return { rows, hidden: 0 };
    return { rows: rows.slice(0, cap), hidden: Math.max(total, rows.length) - cap };
}

function navMatches(text, needle) {
    return String(text).toLowerCase().includes(needle);
}

/** Every row of the open branches, however many: the count of what is left out is the real one. */
function navWalk(nodes, level, expanded, make, out) {
    for (const node of nodes) {
        const row = make(node, level, expanded);
        out.push(row);
        if (row.expanded) navWalk(node.children, level + 1, expanded, make, out);
    }
}

/**
 * The rows of a section for what is expanded and what is typed in its filter: { rows, hidden } -- `hidden` rows
 * beyond the cap (NAV_MAX_ROWS for the open branches of a tree, NAV_MAX_LIST_ROWS for a flat list) are not drawn and are
 * all counted. With a filter the tree is a flat list of what navMatches (a folder by its name or
 * its path, a keyword by its tag, a person or a date by its words), each with the place it is filed in.
 */
export function sectionRows(section, index, expanded, filter, capped = null) {
    if (!index) return { rows: [], hidden: 0 };
    const treeCap = capped ?? NAV_MAX_ROWS;
    const cap = capped ?? NAV_MAX_LIST_ROWS;
    const needle = String(filter || '').trim().toLowerCase();
    const out = [];
    if (section === 'people') {
        if (!needle) {
            // In a tree the branch above a person says which; in a flat list the row does.
            navWalk(index.tops, 1, expanded, (node, depth, open) => navPeopleRow(node, depth, open), out);
            return navCapped(out, index.grouped ? treeCap : cap, out.length);
        }
        for (const group of index.groups.values()) {
            if (!navMatches(group.name, needle) && !navMatches(group.tag, needle)) continue;
            const row = navPeopleRow(group, 1, new Set());
            row.expandable = false;
            row.hint = group.tag;
            out.push(row);
        }
        for (const person of index.people) {
            if (!navMatches(person.name, needle)) continue;
            const row = navPersonRow(person, 1);
            row.hint = person.groupTag || '';
            out.push(row);
        }
        return navCapped(out, cap, out.length);
    }
    if (section === 'dates') return navDateRows(index, expanded, needle, cap);
    const make = section === 'folders' ? navFolderRow : navKeywordRow;
    if (!needle) {
        navWalk(index.tops, 1, expanded, make, out);
        return navCapped(out, treeCap, out.length);
    }
    let found = 0;
    const nodes = section === 'folders' ? index.byKey.values() : index.byTag.values();
    const all = [];
    for (const node of nodes) {
        const where = section === 'folders' ? node.path : node.tag;
        if (!navMatches(node.name, needle) && !navMatches(where, needle)) continue;
        found += 1;
        all.push(node);
    }
    if (section === 'keywords') all.sort((a, b) => compareTagNames(a.tag, b.tag));
    for (const node of all) {
        const row = make(node, 1, new Set());
        row.expandable = false;
        row.expanded = false;
        row.hint = section === 'folders' ? node.path : node.tag;
        out.push(row);
    }
    return navCapped(out, cap, found);
}

function navDateRows(index, expanded, needle, cap) {
    const out = [];
    const everyYear = [...index.usual, ...index.odd];
    if (needle) {
        let found = 0;
        for (const each of everyYear) {
            if (navMatches(each.year, needle)) {
                found += 1;
                if (out.length <= cap) { const row = navYearRow(each, 1, new Set()); row.expandable = false; out.push(row); }
            }
            for (const m of each.months) {
                const words = `${NAV_MONTH_NAMES[m.number - 1] || ''} ${each.year} ${m.month}`;
                if (!navMatches(words, needle)) continue;
                found += 1;
                if (out.length <= cap) {
                    const row = navMonthRows({ ...each, months: [m], other: 0 }, 1)[0];
                    row.hint = String(each.year);
                    out.push(row);
                }
            }
        }
        return navCapped(out, cap, found);
    }
    for (const each of index.usual) {
        const row = navYearRow(each, 1, expanded);
        out.push(row);
        if (row.expanded) out.push(...navMonthRows(each, 2));
    }
    if (index.odd.length) {
        const open = expanded.has(OTHER_YEARS_ID);
        out.push({
            id: OTHER_YEARS_ID, level: 1, label: `Other years (${index.odd.length.toLocaleString()})`, count: index.oddPhotos,
            title: 'Years that are probably not when a photo was taken: a camera clock gone wrong, or a number in a file name',
            aria: `Other years, ${navPlural(index.odd.length, 'year', 'years')}, ${navPlural(index.oddPhotos, 'photo', 'photos')}`,
            hint: '', expandable: true, expanded: open, spec: null,
        });
        if (open) {
            for (const each of index.odd) {
                const row = navYearRow(each, 2, expanded);
                out.push(row);
                if (row.expanded) out.push(...navMonthRows(each, 3));
            }
        }
    }
    return navCapped(out, cap, out.length);
}

// ---- Finding a view's source in the tree -----------------------------------------------------

/**
 * The row id a view's source is, and the ids of the rows to open so that it shows: { id, open: [ids] } or null
 * when the tree has no row for it (a keyword with no node, a person no photo names, a folder the library holds
 * no photo in).
 */
export function locate(section, index, spec) {
    if (!index || !spec) return null;
    if (section === 'folders' && spec.kind === 'folder') {
        let node = index.byKey.get(pathKey(spec.value));
        if (!node) return null;
        const id = node.id;
        const open = [];
        while (node && node.parentKey !== null) {
            node = index.byKey.get(node.parentKey);
            if (node) open.push(node.id);
        }
        return { id, open };
    }
    if (section === 'keywords' && (spec.kind === 'keyword' || spec.kind === 'keyword_only')) {
        let node = index.byTag.get(spec.value) || index.byLower.get(String(spec.value).toLowerCase());
        if (!node) return null;
        const id = node.id;
        const open = [];
        const seen = new Set();
        while (node && node.parentTag !== null && !seen.has(node.tag)) {
            seen.add(node.tag);
            node = index.byTag.get(node.parentTag);
            if (node) open.push(node.id);
        }
        return { id, open };
    }
    if (section === 'people' && spec.kind === 'person') {
        const wanted = String(spec.value).toLowerCase();
        const person = (index.byPersonTag && index.byPersonTag.get(wanted)) || index.byLower.get(wanted);
        if (!person) return null;
        const open = [];
        for (let tag = person.groupTag; tag && index.groups && index.groups.has(tag) && open.length < 64; tag = index.groups.get(tag).parentTag) {
            open.push(index.groups.get(tag).id);
        }
        if (index.grouped && !(person.groupTag && index.groups.has(person.groupTag))) open.push(UNFILED_ID);
        return { id: person.id, open };
    }
    if (section === 'dates' && (spec.kind === 'year' || spec.kind === 'month' || spec.kind === 'year_other')) {
        const year = spec.kind === 'month' ? Number(String(spec.value).slice(0, 4)) : Number(spec.value);
        const each = index.byYear.get(year);
        if (!each) return null;
        const open = [];
        if (index.odd.includes(each)) open.push(OTHER_YEARS_ID);
        if (spec.kind === 'year_other') {
            if (!each.other) return null;
            open.push(`y:${year}`);
            return { id: `o:${year}`, open };
        }
        if (spec.kind === 'month') {
            if (!each.months.some(m => m.month === spec.value)) return null;
            open.push(`y:${year}`);
            return { id: `m:${spec.value}`, open };
        }
        return { id: `y:${year}`, open };
    }
    return null;
}

/** Which tab (section) a source belongs to; null for the whole library, which no tab names. A union: its first source's. */
export function sectionOf(spec) {
    if (!spec) return null;
    if (spec.kind === 'any_of') return Array.isArray(spec.value) && spec.value.length ? sectionOf(spec.value[0]) : null;
    if (spec.kind === 'search') return null;
    if (spec.kind === 'folder') return 'folders';
    if (spec.kind === 'keyword' || spec.kind === 'keyword_only') return 'keywords';
    if (spec.kind === 'person') return 'people';
    if (spec.kind === 'year' || spec.kind === 'month' || spec.kind === 'year_other') return 'dates';
    return null;
}

// ---- Several rows selected: the union (#672) ---------------------------------------------------

/**
 * The sources a view's source is the union of: a union's list, a source alone, nothing for the whole library -- nor for a search,
 * which no row is (a Ctrl-click in a search starts the selection again).
 */
export function membersOf(spec) {
    if (!spec || spec.kind === 'all' || spec.kind === 'search') return [];
    if (spec.kind === 'any_of') return Array.isArray(spec.value) ? spec.value : [];
    return [{ kind: spec.kind, value: spec.value, recursive: Boolean(spec.recursive) }];
}

/** The source of these sources: null for none, the source itself for one, else their union. */
export function specOfMembers(members) {
    if (!members.length) return null;
    if (members.length === 1) return { kind: members[0].kind, value: members[0].value, recursive: Boolean(members[0].recursive) };
    return { kind: 'any_of', value: members.map(m => ({ kind: m.kind, value: m.value, recursive: Boolean(m.recursive) })), recursive: false };
}

/**
 * A section's rows as the tree selection works on, whatever is drawn: { tops, children, own, whole, parent } -- the rows
 * under each, what it holds of its own and with everything under it (a source, or null for a row that only groups:
 * a branch of people, the junk years, a year, whose photos are all in its months and its "Other"). Made once for an index.
 */
export function rowTree(section, index) {
    if (!index) return null;
    if (index.rowTree) return index.rowTree;
    const children = new Map();
    const own = new Map();
    const whole = new Map();
    const parent = new Map();
    let tops = [];
    const add = (id, kids, mine, all) => {
        children.set(id, kids);
        own.set(id, mine);
        whole.set(id, all);
        for (const kid of kids) if (!parent.has(kid)) parent.set(kid, id);
    };
    if (section === 'folders') {
        for (const node of index.byKey.values()) {
            add(node.id, node.children.map(c => c.id), { kind: 'folder', value: node.path, recursive: false },
                { kind: 'folder', value: node.path, recursive: true });
        }
        tops = index.tops.map(n => n.id);
    } else if (section === 'keywords') {
        for (const node of index.byTag.values()) {
            add(node.id, node.children.map(c => c.id), { kind: 'keyword_only', value: node.tag, recursive: false },
                { kind: 'keyword', value: node.tag, recursive: false });
        }
        tops = index.tops.map(n => n.id);
    } else if (section === 'people') {
        const visit = (node) => {
            if (children.has(node.id)) return;
            if (node.group) {
                add(node.id, node.children.map(c => c.id), null, null);
                node.children.forEach(visit);
            } else {
                const spec = { kind: 'person', value: personSource(node), recursive: false };
                add(node.id, [], spec, spec);
            }
        };
        index.tops.forEach(visit);
        tops = index.tops.map(n => n.id);
    } else if (section === 'dates') {
        for (const each of [...index.usual, ...index.odd]) {
            const months = each.months.map(m => `m:${m.month}`);
            for (const m of each.months) {
                const spec = { kind: 'month', value: m.month, recursive: false };
                add(`m:${m.month}`, [], spec, spec);
            }
            if (each.other > 0) {
                const spec = { kind: 'year_other', value: String(each.year), recursive: false };
                add(`o:${each.year}`, [], spec, spec);
                months.push(`o:${each.year}`);
            }
            add(`y:${each.year}`, months, null, { kind: 'year', value: String(each.year), recursive: false });
        }
        tops = index.usual.map(each => `y:${each.year}`);
        if (index.odd.length) {
            add(OTHER_YEARS_ID, index.odd.map(each => `y:${each.year}`), null, null);
            tops.push(OTHER_YEARS_ID);
        }
    }
    index.rowTree = { tops, children, own, whole, parent };
    return index.rowTree;
}

/** `ids` and every row under each of them, as a Set (selecting a row selects the rows under it). */
export function withRowsUnder(tree, ids) {
    const found = new Set();
    const stack = [...ids];
    while (stack.length) {
        const id = stack.pop();
        if (found.has(id) || !tree.children.has(id)) continue;
        found.add(id);
        stack.push(...tree.children.get(id));
    }
    return found;
}

/**
 * The fewest sources that hold what the `selected` rows hold: a row with every row under it selected is its whole (a
 * folder with its subfolders, a keyword and everything under it, a year); a row selected without all of them is its own
 * photos, and the rows under it that are selected are read the same way. A branch of people or the junk years, which
 * hold nothing of their own, are the people or years under them.
 */
export function compress(tree, selected) {
    const complete = new Map();
    const touched = new Map();
    const isComplete = (id) => {
        if (complete.has(id)) return complete.get(id);
        complete.set(id, false);   // a damaged tree that loops ends here
        const found = selected.has(id) && tree.children.get(id).every(isComplete);
        complete.set(id, found);
        return found;
    };
    const isTouched = (id) => {
        if (touched.has(id)) return touched.get(id);
        touched.set(id, false);
        const found = selected.has(id) || tree.children.get(id).some(isTouched);
        touched.set(id, found);
        return found;
    };
    const out = [];
    const seen = new Set();
    const emit = (id) => {
        if (seen.has(id) || !tree.children.has(id) || !isTouched(id)) return;
        seen.add(id);
        if (isComplete(id) && tree.whole.get(id)) {
            out.push(tree.whole.get(id));
            return;
        }
        if (selected.has(id) && tree.own.get(id)) out.push(tree.own.get(id));
        for (const kid of tree.children.get(id)) emit(kid);
    };
    for (const id of tree.tops) emit(id);
    for (const id of selected) if (!seen.has(id) && !tree.parent.has(id)) emit(id);   // a top a damaged tree hides
    return out;
}

/**
 * The rows of a section that a view's source selects, as a Set, and the rows to open so that each shows: { rows, open }.
 * A source that is a row's whole selects the row and every row under it; its own, the row alone. A source no row is (a
 * keyword with no node, a person no photo names, another section's) selects nothing here.
 */
export function selectedRows(section, index, spec) {
    const rows = new Set();
    const open = [];
    const tree = rowTree(section, index);
    if (!tree) return { rows, open };
    for (const member of membersOf(spec)) {
        if (sectionOf(member) !== section) continue;
        const found = locate(section, index, member);
        if (!found || !tree.children.has(found.id)) continue;
        if (open.length < 2000) open.push(...found.open);
        const mine = tree.own.get(found.id);
        const isOwn = mine && mine.kind === member.kind && Boolean(mine.recursive) === Boolean(member.recursive)
            && tree.children.get(found.id).length > 0;
        if (isOwn) rows.add(found.id);
        else for (const id of withRowsUnder(tree, [found.id])) rows.add(id);
    }
    // A row that holds nothing of its own (a branch of people, the junk years) is selected when every row under it is.
    const full = new Map();
    const isFull = (id) => {
        if (full.has(id)) return full.get(id);
        full.set(id, false);   // a damaged tree that loops ends here
        const kids = tree.children.get(id);
        const found = rows.has(id) || (tree.own.get(id) === null && kids.length > 0 && kids.every(isFull));
        full.set(id, found);
        return found;
    };
    if (rows.size) {
        for (const [id, mine] of tree.own) if (mine === null && isFull(id)) rows.add(id);
    }
    return { rows, open };
}
