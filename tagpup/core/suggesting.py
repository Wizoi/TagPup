"""Which suggested tags are worth a person's time: the value a tag's score must reach.

A tag's score is its share of a photo's nearest neighbours, weighted by how alike they
are, after the folder's consensus (tagpup.services.suggester). The app showed a tag from 0.6;
the CLI's `write`, the writer and the runner wrote from 0.5, so the same suggestions put
tags on files that the app never showed (docs/findings.md, #70). There is one value now,
here, for showing and for writing.

Measured on 2026-09-24 on `photo_index`: 907 photos already tagged, in 60 folders, each
suggested for with its own folder left out of its neighbours -- as a folder Suggest is
run on for the first time, whose photos carry no tags yet. People are left out; their
values are in tagpup.core.clustering.

    score at least   tags offered   right    real tags found
        0.50             774        78.8%        22.9%
        0.60             653        85.0%        20.8%
        0.70             560        88.2%        18.5%
        0.80             487        91.6%        16.7%

The tags between 0.5 and 0.6 were right 45% of the time. Few real tags are found at any
value: a folder's own tags are often its own, and neighbours elsewhere cannot know them.

What CLIP is asked about a photo is here too: the words (zero_shot_words) and the
sentence each is put in (clip_prompt). The server and the CLI each merged the tree's
words into config.ini's, and the suggester wrote the sentences (docs/findings.md, #74).
"""
from tagpup.core import vocabulary

#: A suggested tag is shown in the panel, applied by Apply All, and written by the CLI,
#: the writer and the runner when its score is at least this. Six in seven are right.
#: The owner chose it (2026-09-24).
OFFER_A_TAG = 0.60


def zero_shot_words(configured, tag_paths, face_roots):
    """The words CLIP scores a photo against: `configured` (config.ini's candidates, in
    order), then the leaf of each of `tag_paths` in sorted order, once each whatever the
    case -- but no one filed under one of `face_roots` (lowercased). People are matched
    by their faces, not by asking CLIP whether a photo looks like a name (#66)."""
    words, seen = [], set()
    for word in list(configured) + [
            parts[-1] for parts in (vocabulary.segments(p) for p in sorted(tag_paths))
            if parts and parts[0].lower() not in face_roots]:
        word = word.strip()
        if word and word.lower() not in seen:
            seen.add(word.lower())
            words.append(word)
    return words


def clip_prompt(word, year=None, person=False):
    """The sentence CLIP embeds for `word`, of photos taken in `year` when it is given.
    A person's name keeps its case; a thing is lower-cased. It is also the key a word's
    embedding is kept under in the library, so a change here computes them all again."""
    thing = word if person else "a " + word.lower()
    return "a photo of %s in %s" % (thing, year) if year is not None else "a photo of %s" % thing


def offered_tags(entry, threshold=0.0):
    """What Apply All writes for one photo from its saved suggestions `entry`: the
    `tags` and `people` the panel showed, scoring at least `threshold`.

    Exactly what the panel offered. Apply All read `raw_suggestions` -- everything the
    suggester produced down to its own floor -- while the panel showed only what scored
    OFFER_A_TAG or better. The two lists drifted: a photo came back from Apply All
    carrying two people the panel had never mentioned, while the suggestions it *had*
    listed were still sitting there unapplied. One list, two consumers.
    """
    offered = list(entry.get("tags") or [])
    offered_people = list(entry.get("people") or [])
    chosen = [t["tag"] for t in offered if t.get("score", 0.0) >= threshold]
    chosen += [p["name"] for p in offered_people if p.get("score", 0.0) >= threshold]
    return chosen


def caption_from_tags(tags, face_roots):
    """Derive a clean, readable caption based directly on the hierarchical/flat tags.

    `face_roots` are the library's roots that hold people, lowercased, as its tree flags
    them (taxonomy.people_roots): this listed family and friends, and put everyone under
    People among the others (docs/findings.md, #66)."""
    if not tags:
        return None
        
    # The roots are tagpup.core.vocabulary's, as a new library is given them.
    activity_root = vocabulary.ACTIVITY_ROOT.lower()
    place_roots = [root.lower() for root in vocabulary.PLACE_ROOTS]
    people = []
    activities = []
    places = {root: [] for root in place_roots}
    others = []
    
    # Sort tags to ensure consistent, deterministic ordering (e.g. alphabetical)
    sorted_tags = sorted(list(set(tags)))
    
    for tag in sorted_tags:
        leaf = vocabulary.leaf_of(tag)
        root = vocabulary.root_of(tag).lower()
        
        if root in face_roots:
            people.append(leaf)
        elif root == activity_root:
            activities.append(leaf)
        elif root in places:
            places[root].append(leaf)
        else:
            others.append(leaf)
            
    # Remove duplicates from lists while preserving order
    def unique_list(lst):
        seen = set()
        return [x for x in lst if not (x in seen or seen.add(x))]
        
    people = unique_list(people)
    activities = unique_list(activities)
    loc_list = [leaf for root in place_roots for leaf in unique_list(places[root])]
    others = unique_list(others)

    if not people and not activities and not loc_list and not others:
        return None
        
    # Helper to join list with commas and 'and'
    def format_list(lst):
        if not lst:
            return ""
        if len(lst) == 1:
            return lst[0]
        if len(lst) == 2:
            return f"{lst[0]} and {lst[1]}"
        return ", ".join(lst[:-1]) + f", and {lst[-1]}"
        
    people_str = format_list(people)
    activity_str = format_list(activities)
    loc_str = format_list(loc_list)
    
    if people_str:
        caption = people_str
        if activity_str:
            caption += f" - {activity_str}"
        if loc_str:
            caption += f", {loc_str}"
    else:
        # No people in the tags
        if activity_str:
            caption = activity_str
            if loc_str:
                caption += f", {loc_str}"
        elif loc_str:
            caption = loc_str
        else:
            caption = format_list(others)
            
    return caption
