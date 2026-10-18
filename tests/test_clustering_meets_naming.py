"""A hand-run cluster-faces reads the faces at its start and writes their names at its end (53 minutes on the
68,000-photo library). A person who names a face in TagTuner in between keeps that name: the clustering's guess does not
overwrite it, the photo's keyword and the face stay in step, and the run says how many faces it left (docs/findings.md, #832).
A photo whose keywords name two people nobody's face is known for keeps its two faces unnamed, whether the two would be paired
by elimination (#1374) or one face given the first of a tie of votes (#1454), and the run says how many earlier guesses it took
back and how many faces it left so (#1455). A cluster holding as many anchors of two people names neither: its faces are named
by likeness or elimination (#1468). A face named on likeness alone needs a margin over the next person: a face between two
cousins is neither named nor offered, a name it carries stays (AFaceBetweenTwoCousins; docs/AI_PIPELINE.md, phase 2b).
"""
import json
import math
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import face_rows  # noqa: E402
from test_face_clustering_rules import FaceClusteringTestBase, identity_vector, near  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.services import identify  # noqa: E402
from tagpup.services.search import PhotoIndex  # noqa: E402
from tagpup.store import db, face_tags  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402
from tagpup.store import taxonomy  # noqa: E402


class ClusteringMeetsNaming(FaceClusteringTestBase):
    def run_while(self, meanwhile):
        """cluster-faces, with `meanwhile(conn)` written by another connection just before it writes its names;
        returns the faces it said it left."""
        self.index.close()
        self.index = PhotoIndex(db_path=self.db_path)
        self.index.load()
        heard = []

        def on_step(stage, done=0, total=1):
            if stage == "saving":
                db.write_with_connection(self.db_path, meanwhile, label="a person names a face")
            if stage == "left":
                heard.append(done)
            if stage == "unpaired":
                self.unpaired.append((done, total))
        self.unpaired = []
        from tagpup.services import identities
        self.stats = identities.resolve(self.index, on_step=on_step)
        return heard

    def row(self, face_id):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return conn.execute("SELECT name, name_source FROM faces WHERE id = ?", (face_id,)).fetchone()
        finally:
            conn.close()

    def two_faces_of_jane(self):
        jane = identity_vector(1)
        first = self.add_face(self.add_photo("one.jpg", people=["Jane Doe"]), jane)
        second = self.add_face(self.add_photo("two.jpg", people=["Jane Doe"]), near(jane, 2))
        return first, second

    def test_a_face_named_by_hand_while_clustering_runs_keeps_the_owners_name(self):
        first, second = self.two_faces_of_jane()

        heard = self.run_while(lambda conn: store_faces.name(conn, [second], face_rows.person_of(conn, "Bob Roe")))

        self.assertEqual(("Bob Roe", "manual"), self.row(second), "the owner's naming was overwritten by the guess")
        self.assertEqual(("Jane Doe", None), self.row(first), "the face nobody touched is named as before")
        self.assertEqual([1], heard, "the run says how many faces it left")

    def test_a_face_another_run_named_meanwhile_is_not_written_over(self):
        first, second = self.two_faces_of_jane()

        heard = self.run_while(lambda conn: store_faces.name_if_unnamed(conn, second, face_rows.person_of(conn, "Bob Roe")))

        self.assertEqual("Bob Roe", self.row(second)[0], "a face that carries another person than the one read is left")
        self.assertEqual([1], heard)

    def test_a_face_nobody_touched_is_all_written_and_nothing_is_said_of_leaving(self):
        first, second = self.two_faces_of_jane()

        heard = self.run_while(lambda conn: None)

        self.assertEqual([self.row(first)[0], self.row(second)[0]], ["Jane Doe", "Jane Doe"])
        self.assertEqual([], heard)


    def id_of(self, face_id):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return conn.execute("SELECT tag_id FROM faces WHERE id = ?", (face_id,)).fetchone()[0]
        finally:
            conn.close()

    def test_a_person_renamed_while_clustering_runs_keeps_the_faces_guessed_for_them(self):
        """#1271: the guess is for the person (their id), not for the spelling the run read."""
        first, second = self.two_faces_of_jane()

        def rename(conn):
            tag = conn.execute("SELECT tag FROM tag_taxonomy WHERE name = 'Jane Doe'").fetchone()[0]
            taxonomy.move_branch(conn, tag, tag[:-len("Jane Doe")] + "Jane Roe")

        self.run_while(rename)

        self.assertEqual(("Jane Roe", None), self.row(first), "the guess was lost to the rename")
        self.assertEqual(("Jane Roe", None), self.row(second))
        self.assertEqual(1, len({self.id_of(first), self.id_of(second)}))
        self.assertIsNotNone(self.id_of(first))

    def test_a_guess_moved_from_one_face_of_a_photo_to_another_is_written_and_counted(self):
        """A re-run that moves a person from face 2 of a photo to face 1: face 1 is not refused for the person face 2 carried a
        moment before, and the photo does not end with nobody."""
        photo = self.add_photo("pair.jpg", people=["Jane Doe"])
        low = self.add_face(photo, identity_vector(1), box=(0, 0, 50, 50))
        high = self.add_face(photo, identity_vector(5), box=(60, 0, 110, 50))
        from tagpup.store import person_ids

        def guessed(conn):
            conn.execute("UPDATE faces SET name = 'Jane Doe', tag_id = ? WHERE id = ?", (face_rows.person_of(conn, "Jane Doe"), high))

        db.write_with_connection(self.db_path, guessed, label="an earlier run guessed")
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            jane = person_ids.read(conn).person("Jane Doe").id
        finally:
            conn.close()

        written, unnamed, left = db.write_with_connection(
            self.db_path, lambda conn: store_faces.set_names(conn, {low: jane, high: None}, {low: None, high: jane}),
            label="a run moves the guess")

        self.assertEqual([low, high], sorted(written))
        self.assertEqual(([], []), (unnamed, left))
        self.assertEqual(jane, self.id_of(low))
        self.assertIsNone(self.id_of(high))

    def test_the_summary_counts_the_faces_written_not_the_faces_meant(self):
        first, second = self.two_faces_of_jane()

        self.run_while(lambda conn: store_faces.name(conn, [second], face_rows.person_of(conn, "Bob Roe")))

        self.assertEqual({"Jane Doe": 1}, self.stats, "a face the owner named meanwhile was counted as linked")

    def test_a_person_named_by_hand_on_one_face_is_not_guessed_on_another_of_the_photo(self):
        """#1272: the owner names face B of a photo while the run guesses the same person for face A of it."""
        jane = identity_vector(1)
        self.add_face(self.add_photo("one.jpg", people=["Jane Doe"]), jane)
        photo = self.add_photo("two.jpg", people=["Jane Doe"])
        guessed = self.add_face(photo, near(jane, 2), box=(0, 0, 50, 50))
        chosen = self.add_face(photo, identity_vector(5), box=(60, 0, 110, 50))

        heard = self.run_while(lambda conn: store_faces.name(conn, [chosen], "Jane Doe"))

        self.assertEqual(("Jane Doe", "manual"), self.row(chosen))
        self.assertEqual((None, None), self.row(guessed), "the photo ended with two faces of one person")
        self.assertEqual([1], heard, "the run says it left the one face that would double the person")

    def test_a_person_merged_away_while_clustering_runs_is_nobody_to_give_and_the_run_finishes(self):
        first, second = self.two_faces_of_jane()
        self.add_face(self.add_photo("three.jpg", people=["Bob Roe"]), identity_vector(7))

        def merge(conn):
            jane = conn.execute("SELECT tag FROM tag_taxonomy WHERE name = 'Jane Doe'").fetchone()[0]
            bob = conn.execute("SELECT tag FROM tag_taxonomy WHERE name = 'Bob Roe'").fetchone()[0]
            taxonomy.move_branch(conn, jane, bob)

        heard = self.run_while(merge)

        self.assertEqual((None, None), self.row(first), "a person who is gone was given to a face")
        self.assertEqual((None, None), self.row(second))
        self.assertEqual([2], heard)

    def test_the_cli_says_how_many_faces_it_left_and_how_many_of_them_lost_their_guess(self):
        """The owner runs cluster-faces and, while it runs, names Jane by hand on a face of a photo an earlier run had guessed as
        Bob's. The run takes the wrong guess off and is then refused Jane for that face (she is on the photo now): the face is
        unnamed, and the CLI's last word says so, beside the face the owner named under it."""
        import io
        import types
        from unittest import mock
        from rich.console import Console
        import tagpup_cli
        from tagpup.services import identities
        jane = identity_vector(1)
        self.add_face(self.add_photo("one.jpg", people=["Jane Doe"]), jane)
        photo = self.add_photo("two.jpg", people=["Jane Doe"])
        wrong = self.add_face(photo, near(jane, 2), box=(0, 0, 50, 50))
        chosen = self.add_face(photo, identity_vector(5), box=(60, 0, 110, 50))
        db.write_with_connection(self.db_path, lambda conn: conn.execute(
            "UPDATE faces SET name = 'Bob Roe', tag_id = ? WHERE id = ?", (face_rows.person_of(conn, "Bob Roe"), wrong)),
            label="an earlier run guessed")
        self.index.close()
        real = identities.resolve

        def resolve(photo_index, on_step=None):
            def heard(stage, done=0, total=1):
                if stage == "saving":
                    db.write_with_connection(self.db_path, lambda conn: store_faces.name(conn, [chosen], "Jane Doe"))
                on_step(stage, done, total)
            return real(photo_index, on_step=heard)
        said = io.StringIO()
        with mock.patch.object(identities, "resolve", resolve), \
                mock.patch.object(tagpup_cli, "console", Console(file=said, width=400, color_system=None)):
            tagpup_cli._resolve_identities(types.SimpleNamespace(model_key=lambda library: None), self.db_path)

        self.assertEqual([(None, None), ("Jane Doe", "manual")], [self.row(wrong), self.row(chosen)])
        self.assertIn("1 face(s) were not given what this run found: the library changed under them while it ran; 1 of them"
                      " lost the guess they carried and are unnamed.", said.getvalue())

    def photo_tagged(self, name, people):
        """A photo whose keywords are People/<person> for each of `people`, as the index records a file it read: the tree's nodes,
        the keywords, the photo's people."""
        tags = ["People/" + person for person in people]
        db.write_with_connection(self.db_path, lambda conn: [taxonomy.add_path(conn, "People", root_has_face=1)]
                                 + [taxonomy.add_path(conn, tag) for tag in tags])
        photo = self.add_photo(name, people=people)
        db.write_with_connection(self.db_path, lambda conn: conn.execute(
            "UPDATE photos SET tags = ?, raw_metadata = ? WHERE path = ?",
            (json.dumps(tags), json.dumps({"XMP:HierarchicalSubject": tags}), photo)))
        return photo

    def test_a_photo_retagged_as_the_run_reads_the_library_is_read_whole(self):
        """#1372: the owner retags a photo in another window just as cluster-faces reads the library. The run reads each photo's
        people and its keywords in one statement, so its lone face is not named after the person just taken off. Mutation: the
        people read in a statement of their own before the rows (store.people.listed_ids) named the face Mara Quill."""
        from unittest import mock
        from tagpup.store import people as store_people
        from tagpup.store import photos as store_photos
        photo = self.photo_tagged("solo.jpg", ["Mara Quill"])
        face = self.add_face(photo, identity_vector(21))
        db.write_with_connection(self.db_path, lambda conn: taxonomy.add_path(conn, "People/Tobin Ash"))
        real = store_photos.records

        def retag(conn):
            """The other window's keyword write: the photo's keywords, and its people rebuilt in the same transaction."""
            photo_id = conn.execute("SELECT id FROM photos WHERE path = ?", (photo,)).fetchone()[0]
            conn.execute("UPDATE photos SET tags = ?, raw_metadata = ? WHERE id = ?",
                         (json.dumps(["People/Tobin Ash"]), json.dumps({"XMP:HierarchicalSubject": ["People/Tobin Ash"]}), photo_id))
            store_people.rebuild(conn, [photo_id])

        def retagged_first(*args, **kwargs):
            db.write_with_connection(self.db_path, retag)
            return real(*args, **kwargs)
        with mock.patch.object(store_photos, "records", retagged_first):
            self.run_while(lambda conn: None)

        self.assertEqual("Tobin Ash", self.row(face)[0], "the face was named after the person taken off its photo")

    def test_two_people_no_face_is_known_for_leave_the_two_faces_of_their_photo_unnamed(self):
        """#1374: the owner tags a photo with two people and the indexer finds two faces in it; neither person has a face known
        anywhere. Nothing tells which face is whose, so both stay unnamed -- they were paired by the order of the people's ids, a
        coin toss written as a name -- and the run says how many faces it left for that reason."""
        photo = self.photo_tagged("pair.jpg", ["Mara Quill", "Tobin Ash"])
        left_face = self.add_face(photo, identity_vector(21), box=(0, 0, 200, 200))
        right_face = self.add_face(photo, identity_vector(22), box=(300, 0, 500, 200))

        heard = self.run_while(lambda conn: None)

        self.assertEqual([(None, None), (None, None)], [self.row(left_face), self.row(right_face)],
                         "a face was given one of two people with nothing to tell them apart")
        self.assertEqual({}, self.stats, "the summary counted a face as named")
        self.assertEqual([(0, 2)], self.unpaired, "the run did not say it left the two faces unnamed, and took back no guess")
        self.assertEqual([], heard)

    def test_a_pairing_an_earlier_run_guessed_for_two_unknown_people_is_taken_back(self):
        """The owner's library after an earlier run paired them: the two guesses are unnamed, and counted as taken back (#1455);
        the run after it changes nothing and says so."""
        photo = self.photo_tagged("pair.jpg", ["Mara Quill", "Tobin Ash"])
        conn = db.connect(self.db_path)
        try:
            guessed = [face_rows.add_face(conn, photo, box=(0, 0, 200, 200), embedding=identity_vector(21).tobytes(),
                                          name="Mara Quill", prob=0.99),
                       face_rows.add_face(conn, photo, box=(300, 0, 500, 200), embedding=identity_vector(22).tobytes(),
                                          name="Tobin Ash", prob=0.99)]
            conn.commit()
        finally:
            conn.close()

        self.run_while(lambda conn: None)

        self.assertEqual([(None, None), (None, None)], [self.row(face) for face in guessed],
                         "a coin-toss pairing of an earlier run was kept")
        self.assertEqual([(2, 2)], self.unpaired)

        self.run_while(lambda conn: None)

        self.assertEqual([(0, 2)], self.unpaired, "a rerun counted the same guesses as taken back again")

    def test_one_face_left_and_one_person_left_on_a_photo_is_still_named(self):
        """The case #1374 keeps: every other face of the photo is somebody known, so the one face left is the one person left."""
        jane = identity_vector(1)
        self.add_face(self.photo_tagged("one.jpg", ["Jane Doe"]), jane)
        photo = self.photo_tagged("pair.jpg", ["Jane Doe", "Mara Quill"])
        known = self.add_face(photo, near(jane, 2), box=(0, 0, 200, 200))
        other = self.add_face(photo, identity_vector(21), box=(300, 0, 500, 200))

        self.run_while(lambda conn: None)

        self.assertEqual([("Jane Doe", None), ("Mara Quill", None)], [self.row(known), self.row(other)])
        self.assertEqual([], self.unpaired)

    def test_a_tie_of_votes_between_two_people_no_face_is_known_for_names_neither_face(self):
        """#1454: a solo photo of Wren is her anchor; the owner tags a pair photo with Mara and Tobin, whose faces nobody knows. Its
        first face is like no other; its second looks like Wren, who is not on the photo. The first face's votes are one each for
        Mara and Tobin: a tie, no evidence. It was given the lower id's person, and elimination then gave the second face the other:
        two coin tosses written as names. Both stay unnamed."""
        wren = identity_vector(3)
        solo = self.add_face(self.photo_tagged("solo.jpg", ["Wren Hollis"]), wren)
        photo = self.photo_tagged("pair.jpg", ["Mara Quill", "Tobin Ash"])
        unique = self.add_face(photo, identity_vector(21), box=(0, 0, 200, 200))
        like_wren = self.add_face(photo, near(wren, 2), box=(300, 0, 500, 200))

        self.run_while(lambda conn: None)

        self.assertEqual([(None, None), (None, None)], [self.row(unique), self.row(like_wren)],
                         "a face was named by a tie of votes, or by elimination after one")
        self.assertEqual(("Wren Hollis", None), self.row(solo))
        self.assertEqual({"People/Wren Hollis": 1}, self.stats)
        self.assertEqual([(0, 2)], self.unpaired)

    def test_a_person_with_more_votes_than_the_others_is_still_named(self):
        """A tie lower down is no tie: Mara is on both photos the face is in, Tobin and Pell on one each. The face is hers, and
        each photo's other face is the one person left on it."""
        mara = identity_vector(31)
        first = self.photo_tagged("first.jpg", ["Mara Quill", "Tobin Ash"])
        second = self.photo_tagged("second.jpg", ["Mara Quill", "Pell Varga"])
        hers = [self.add_face(first, mara, box=(0, 0, 200, 200)), self.add_face(second, near(mara, 2), box=(0, 0, 200, 200))]
        tobin = self.add_face(first, identity_vector(32), box=(300, 0, 500, 200))
        pell = self.add_face(second, identity_vector(33), box=(300, 0, 500, 200))

        self.run_while(lambda conn: None)

        self.assertEqual([("Mara Quill", None)] * 2, [self.row(face) for face in hers])
        self.assertEqual([("Tobin Ash", None), ("Pell Varga", None)], [self.row(tobin), self.row(pell)])
        self.assertEqual([], self.unpaired)

    def two_look_alike_siblings_and_their_photo_together(self, by_hand):
        """Ivo and Ines Marsh look alike: a solo photo of each, tagged with them alone, and a photo tagged with both whose first
        face is closer to Ines -- by more than the margin of naming unasked (about 0.05; AFaceBetweenTwoCousins for one
        closer by less) -- and whose second is like neither. The two solo faces and the first face of the pair group into
        one cluster. `by_hand`: the owner also names each solo face in TagTuner."""
        ivo = identity_vector(41)
        ines = near(ivo, 2, distance=0.35)
        solos = [self.add_face(self.photo_tagged("ivo.jpg", ["Ivo Marsh"]), ivo),
                 self.add_face(self.photo_tagged("ines.jpg", ["Ines Marsh"]), ines)]
        if by_hand:
            db.write_with_connection(self.db_path, lambda conn: (store_faces.name(conn, [solos[0]], "Ivo Marsh"),
                                                                  store_faces.name(conn, [solos[1]], "Ines Marsh")))
        shared = self.photo_tagged("both.jpg", ["Ivo Marsh", "Ines Marsh"])
        look_alike = self.add_face(shared, near(ines, 3, distance=0.1), box=(0, 0, 200, 200))
        other = self.add_face(shared, identity_vector(42), box=(300, 0, 500, 200))
        self.run_while(lambda conn: None)
        return solos, look_alike, other

    def test_two_look_alike_siblings_named_once_each_do_not_toss_a_coin_for_their_photo_together(self):
        """#1468: the cluster holding both siblings' solo faces has one anchor of each, a tie: no evidence of whose cluster it
        is. It was named after the first anchor read, Ivo, so the pair photo's look-alike face was given Ivo whatever it looked
        like. Now the cluster names nobody; the face is named by the likeness step (the known people's Hungarian match), which
        gives it Ines, whose face it is closer to, and the other face, like neither sibling, stays unnamed."""
        solos, look_alike, other = self.two_look_alike_siblings_and_their_photo_together(by_hand=True)

        self.assertEqual(("Ines Marsh", None), self.row(look_alike), "the face was named by the first of a tie of anchors")
        self.assertEqual((None, None), self.row(other), "the face like neither sibling was given one")
        self.assertEqual([("Ivo Marsh", "manual"), ("Ines Marsh", "manual")], [self.row(face) for face in solos])
        self.assertEqual([], self.unpaired)

    def test_two_look_alike_siblings_only_tagged_keep_their_solo_faces_after_a_tie(self):
        """The same with nothing named by hand: each solo face is its photo's one person by elimination, and the pair photo's
        look-alike face is again Ines by likeness."""
        solos, look_alike, other = self.two_look_alike_siblings_and_their_photo_together(by_hand=False)

        self.assertEqual(("Ines Marsh", None), self.row(look_alike), "the face was named by the first of a tie of anchors")
        self.assertEqual((None, None), self.row(other))
        self.assertEqual([("Ivo Marsh", None), ("Ines Marsh", None)], [self.row(face) for face in solos])
        self.assertEqual([], self.unpaired)


class ARunRewritesTheGuessesOfAPhoto(FaceClusteringTestBase):
    """#1582: a re-run of cluster-faces rewrites the guesses it wrote before. Its write (store_faces.set_names) takes off every
    guess it changes before it gives any, so a face is refused a person only for a face outside the run's plan -- one the owner
    named meanwhile -- never for a sibling the same plan rewrites. On photo_index's run of 2026-10-16, 62 faces of 31 photos whose
    guesses the run exchanged were refused for ever, and 91 more by the order of the writes."""

    def guessed(self, name, *people):
        """A photo tagged with `people` whose faces an earlier run guessed as `people`, in order (None: a face it left unnamed);
        returns their ids."""
        photo = self.add_photo(name, people=[person for person in people if person])
        conn = db.connect(self.db_path)
        try:
            faces = [face_rows.add_face(conn, photo, box=(index * 300, 0, index * 300 + 200, 200),
                                        embedding=identity_vector(60 + index).tobytes(), prob=0.99,
                                        **({"name": person} if person else {}))
                     for index, person in enumerate(people)]
            conn.commit()
        finally:
            conn.close()
        return faces

    def person(self, name):
        def made(conn):
            return face_rows.person_of(conn, name)
        return db.write_with_connection(self.db_path, made, label="the tree has the person")

    def carried(self):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return dict(conn.execute("SELECT id, tag_id FROM faces").fetchall())
        finally:
            conn.close()

    def write(self, plan, meanwhile=None):
        """The run's write of `plan` ({face id: person id or None}), against the faces as they were before `meanwhile`."""
        as_read = self.carried()
        if meanwhile:
            db.write_with_connection(self.db_path, meanwhile, label="the owner, meanwhile")
        return db.write_with_connection(self.db_path, lambda conn: store_faces.set_names(conn, plan, {face: as_read[face]
                                                                                                    for face in plan}),
                                        label="the run writes its guesses")

    def test_two_faces_whose_guesses_the_run_exchanges_are_both_written(self):
        first, second = self.guessed("pair.jpg", "Mara Quill", "Tobin Ash")
        mara, tobin = self.person("Mara Quill"), self.person("Tobin Ash")

        outcome = self.write({first: tobin, second: mara})

        self.assertEqual((tobin, mara), (self.carried()[first], self.carried()[second]), "a swap was refused for ever")
        written, unnamed, left = outcome
        self.assertEqual(([first, second], [], []), (sorted(written), unnamed, left))

    def test_a_person_moved_to_an_unnamed_face_from_a_face_given_another_is_written_whatever_the_order(self):
        """A none -> Mara, B Mara -> Tobin, B's id after A's: A was refused while B still carried Mara."""
        first, second = self.guessed("pair.jpg", None, "Mara Quill")
        mara, tobin = self.person("Mara Quill"), self.person("Tobin Ash")

        outcome = self.write({first: mara, second: tobin})

        self.assertEqual((mara, tobin), (self.carried()[first], self.carried()[second]), "a face was refused by the order of writes")
        written, unnamed, left = outcome
        self.assertEqual(([first, second], [], []), (sorted(written), unnamed, left))

    def test_a_face_another_run_changed_meanwhile_is_left_as_it_found_it(self):
        """The face carries another person than the run read (another run's guess): it is not taken off nor overwritten."""
        first, second = self.guessed("pair.jpg", "Mara Quill", "Tobin Ash")
        mara, tobin, pell = self.person("Mara Quill"), self.person("Tobin Ash"), self.person("Pell Varga")

        written, unnamed, left = self.write({first: tobin, second: mara},
                                            meanwhile=lambda conn: store_faces.set_names(conn, {first: pell}, {first: mara}))

        self.assertEqual(([second], [], [first]), (written, unnamed, left))
        self.assertEqual((pell, mara), (self.carried()[first], self.carried()[second]), "a face changed meanwhile was overwritten")

    def test_a_person_the_owner_named_on_another_face_meanwhile_is_not_given_twice_and_the_guess_is_taken_off(self):
        """The sibling test means what it says: a face outside the plan carries the person. The face the run moved away from its
        guess is left unnamed, not with the guess the run rejected (as the propagation does with a guess it no longer bears)."""
        first, second, third = self.guessed("three.jpg", "Mara Quill", "Tobin Ash", None)
        mara, tobin = self.person("Mara Quill"), self.person("Tobin Ash")

        written, unnamed, left = self.write({first: tobin, second: mara},
                                            meanwhile=lambda conn: store_faces.name(conn, [third], mara))

        self.assertEqual(([first], [second], []), (written, unnamed, left))
        self.assertEqual((tobin, None, mara), tuple(self.carried()[face] for face in (first, second, third)))

    def test_a_face_named_by_hand_meanwhile_is_untouched(self):
        first, second = self.guessed("pair.jpg", "Mara Quill", "Tobin Ash")
        mara, tobin = self.person("Mara Quill"), self.person("Tobin Ash")

        written, unnamed, left = self.write({first: tobin, second: mara},
                                            meanwhile=lambda conn: store_faces.name(conn, [first], mara))

        self.assertEqual(([], [second], [first]), (written, unnamed, left))
        self.assertEqual((mara, None), (self.carried()[first], self.carried()[second]), "a face named by hand was rewritten")

    def test_a_person_merged_away_meanwhile_is_given_to_nobody_and_their_face_keeps_its_guess(self):
        first, second = self.guessed("pair.jpg", "Mara Quill", "Tobin Ash")
        mara, tobin, pell = self.person("Mara Quill"), self.person("Tobin Ash"), self.person("Pell Varga")

        def merge(conn):
            tobin_tag, pell_tag = (conn.execute("SELECT tag FROM tag_taxonomy WHERE id = ?", (node,)).fetchone()[0]
                                   for node in (tobin, pell))
            taxonomy.move_branch(conn, tobin_tag, pell_tag)

        written, unnamed, left = self.write({first: tobin, second: mara}, meanwhile=merge)

        self.assertEqual(([], [], [first, second]), (written, unnamed, sorted(left)), "a person merged away was given")
        self.assertEqual((mara, pell), (self.carried()[first], self.carried()[second]))

    def test_a_merge_the_owner_makes_as_the_write_reads_the_tree_waits_for_it_and_no_guess_is_lost(self):
        """The owner merges Tobin into Pell in TagTuner just as the run's write reads the People tree. The write reads the tree
        inside its own transaction, so the merge cannot land between that read and the first UPDATE: it waits (here: is
        refused at once and made after), and the faces end as a merge after the run leaves them. Before, the merge landed in
        the gap: the write still took Tobin for a person, took Mara off the first face and was then refused Tobin, so the face
        lost its guess."""
        from unittest import mock
        import sqlite3
        from tagpup.store import person_ids
        first, second = self.guessed("pair.jpg", "Mara Quill", "Tobin Ash")
        mara, tobin, pell = self.person("Mara Quill"), self.person("Tobin Ash"), self.person("Pell Varga")
        as_read = self.carried()
        real = person_ids.read
        waited, asked = [], []

        def merge(conn):
            tobin_tag, pell_tag = (conn.execute("SELECT tag FROM tag_taxonomy WHERE id = ?", (node,)).fetchone()[0]
                                   for node in (tobin, pell))
            taxonomy.move_branch(conn, tobin_tag, pell_tag)

        def read_then_the_owner_merges(conn, *args, **kwargs):
            known = real(conn, *args, **kwargs)
            if not asked:   # once: the merge reads the tree too
                asked.append(True)
                owner = db.connect(self.db_path)
                try:
                    owner.execute("PRAGMA busy_timeout = 0")
                    try:
                        merge(owner)
                        owner.commit()
                        waited.append(False)
                    except sqlite3.OperationalError:
                        owner.rollback()
                        waited.append(True)
                finally:
                    owner.close()
            return known

        with mock.patch.object(person_ids, "read", read_then_the_owner_merges):
            written, unnamed, left = db.write_with_connection(
                self.db_path, lambda conn: store_faces.set_names(conn, {first: tobin, second: mara},
                                                                 {first: as_read[first], second: as_read[second]}),
                label="the run writes its guesses")
        if waited == [True]:
            db.write_with_connection(self.db_path, merge, label="the owner's merge, after the write")

        self.assertEqual([], unnamed, "a face lost its guess to a merge in the gap")
        self.assertEqual((pell, mara), (self.carried()[first], self.carried()[second]))
        self.assertEqual([True], waited, "the merge landed between the write's read of the tree and its first UPDATE")


def at(degrees):
    """A unit vector `degrees` round a circle of the faces' space: two are as alike as the cosine of the angle between."""
    vector = np.zeros(512, dtype=np.float32)
    vector[0], vector[1] = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return vector


class AFaceBetweenTwoCousins(FaceClusteringTestBase):
    """The margin rule (docs/AI_PIPELINE.md, phase 2b; owner, 2026-10-17). Mara and Tobin Quill are cousins, each with a solo
    photo of their own; a photo of the two of them together holds one face that is `likeness` like Mara and `margin` less like
    Tobin. A name resting on likeness alone needs both the likeness and the margin over the next person (0.04 to name with no
    one looking, 0.02 to offer); a name the face already carries, or one given by hand, is never taken away by it. Each
    screen that names or offers a face is asked of the same face. Mutation: the margin dropped from the gates (the old code)
    names the face at 0.805 and a margin of 0.01 in every place below."""

    photo_tagged = ClusteringMeetsNaming.photo_tagged
    run_while = ClusteringMeetsNaming.run_while
    row = ClusteringMeetsNaming.row

    def cousins(self, likeness, margin, by_hand=False, carried=None):
        """The three photos; the shared photo's face `carried` the person an earlier run gave it, if any. Returns its id."""
        between = math.degrees(math.acos(likeness))
        mara = self.add_face(self.photo_tagged("mara.jpg", ["Mara Quill"]), at(0))
        tobin = self.add_face(self.photo_tagged("tobin.jpg", ["Tobin Quill"]),
                              at(between + math.degrees(math.acos(likeness - margin))))
        if by_hand:
            db.write_with_connection(self.db_path, lambda conn: (store_faces.name(conn, [mara], "Mara Quill"),
                                                                  store_faces.name(conn, [tobin], "Tobin Quill")))
        self.together = self.photo_tagged("together.jpg", ["Mara Quill", "Tobin Quill"])
        face = self.add_face(self.together, at(between), box=(0, 0, 200, 200))
        if carried:   # written as a run writes its guesses (store.faces.set_names)
            written = db.write_with_connection(self.db_path, lambda conn: store_faces.set_names(
                conn, {face: face_rows.person_of(conn, carried)}, {face: None})[0])
            self.assertEqual([face], written)
        return face

    def library(self):
        return Library(self.db_path)

    def named(self):
        return lambda: identify.named_faces(self.library())[1]

    def offered(self, face):
        """What the details panel's strip suggests for the face, and the band the boxes' list gives Mara."""
        from tagpup.services import faces as face_service
        strip = {each["id"]: each["suggestion"] for each in face_service.panel(self.library(), self.together, self.named())["faces"]}
        bands = {match["name"]: match["band"] for match in identify.face_matches(self.library(), face, self.named())}
        return strip[face], bands["Mara Quill"]

    def test_a_face_a_hair_more_like_one_cousin_is_not_named_by_cluster_faces(self):
        face = self.cousins(0.805, 0.01)

        self.run_while(lambda conn: None)

        self.assertEqual((None, None), self.row(face), "a face as like one cousin as the other was named for one")
        self.assertIn("final_matching_tagged_photo_failed", self.traces()[face]["resolution_method"])

    def test_the_same_face_well_ahead_of_the_other_cousin_is_named_as_before(self):
        face = self.cousins(0.85, 0.06)

        self.run_while(lambda conn: None)

        self.assertEqual(("Mara Quill", None), self.row(face))

    def decided(self):
        return lambda: identify.decided_faces(self.library())[1]

    def named_by_its_tags(self):
        """The faces a save of the photos' tags would name (store.face_tags.plan: several faces, several people)."""
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return [choice.face_id for choice in face_tags.plan(conn, references=self.decided()()).named]
        finally:
            conn.close()

    def re_examined(self):
        return face_service_automatch(self.library(), os.path.dirname(self.together), self.decided()).details["faces"]

    def test_it_is_not_offered(self):
        face = self.cousins(0.805, 0.01, by_hand=True)
        self.assertEqual((None, None), self.offered(face), "the strip or the boxes offered one cousin of two")

    def test_re_examine_does_not_name_it(self):
        self.cousins(0.805, 0.01, by_hand=True)
        self.assertEqual(0, self.re_examined(), "Re-examine would name the face for one cousin")

    def test_its_photos_tags_do_not_name_it(self):
        self.cousins(0.805, 0.01, by_hand=True)
        self.assertEqual([], self.named_by_its_tags(), "the photo's tags named the face for one cousin")

    def test_well_ahead_it_is_offered_and_named_by_each(self):
        face = self.cousins(0.85, 0.06, by_hand=True)

        self.assertEqual(("Mara Quill", "likely"), self.offered(face))
        self.assertEqual(1, self.re_examined())
        self.assertEqual([face], self.named_by_its_tags())

    def test_a_name_the_face_carries_stays_through_two_reruns(self):
        """#855: the margin gates names given from now on; the name an earlier run gave, which the photo's keywords bear out,
        stays (about 18,500 of photo_index's would go otherwise, counted 2026-10-17)."""
        face = self.cousins(0.805, 0.01, carried="Mara Quill")

        for _run in range(2):
            self.run_while(lambda conn: None)
            self.assertEqual(("Mara Quill", None), self.row(face), "a name the face carried was taken away by the margin")

    def test_a_name_given_by_hand_with_no_margin_at_all_is_untouched_through_two_reruns(self):
        face = self.cousins(0.805, 0.0)
        db.write_with_connection(self.db_path, lambda conn: store_faces.name(conn, [face], "Tobin Quill"))

        for _run in range(2):
            self.run_while(lambda conn: None)
            self.assertEqual(("Tobin Quill", "manual"), self.row(face))

    def test_a_rerun_names_nothing_new_and_a_run_stopped_at_saving_writes_nothing(self):
        """The face between the cousins stays unnamed run after run (no flip-flop); a run stopped as it saves leaves the
        library as it was."""
        face = self.cousins(0.805, 0.01)
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            before = conn.execute("SELECT id, name, tag_id FROM faces ORDER BY id").fetchall()
        finally:
            conn.close()

        def stopped(conn):
            raise KeyboardInterrupt("the owner closed the window as it saved")
        with self.assertRaises(KeyboardInterrupt):
            self.run_while(stopped)
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            self.assertEqual(before, conn.execute("SELECT id, name, tag_id FROM faces ORDER BY id").fetchall())
        finally:
            conn.close()
        names = []
        for _run in range(3):
            self.run_while(lambda conn: None)
            names.append(self.row(face))
        self.assertEqual([(None, None)] * 3, names, "the face between two people flip-flopped between runs")

    def test_in_a_library_with_one_known_person_the_face_is_named_as_before(self):
        """Two libraries: the cousins' (well apart), and one where only Mara is known: no second person, so nothing to be
        ahead of (an infinite margin, by the arithmetic)."""
        cousins = self.cousins(0.85, 0.06)
        self.run_while(lambda conn: None)
        self.db_path = os.path.join(self.tmpdir, "one_person.db")
        self.index.close()
        self.index = PhotoIndex(db_path=self.db_path)
        self.index.load()   # the second library, made as the first
        self.add_face(self.photo_tagged("mara.jpg", ["Mara Quill"]), at(0))
        pair = self.photo_tagged("pair.jpg", ["Mara Quill"])
        face = self.add_face(pair, at(math.degrees(math.acos(0.805))), box=(0, 0, 200, 200))
        stranger = self.add_face(pair, at(120), box=(300, 0, 500, 200))

        self.run_while(lambda conn: None)

        self.assertEqual([("Mara Quill", None), (None, None)], [self.row(face), self.row(stranger)])
        self.db_path = os.path.join(self.tmpdir, "faces_test.db")
        self.assertEqual(("Mara Quill", None), self.row(cousins))

    def test_new_person_gathers_by_the_margin_a_name_is_offered_at(self):
        """New Person on a face nobody is named for gathers the faces like it, for the owner to look over before they are
        named: an offer, so the margin it asks over everyone named is the offer's, 0.02 (owner, 2026-10-17). Mara, a
        cousin, is named. One face is 0.851 like the chosen face and 0.83 like Mara (0.021 ahead: 0.02 itself is float32's
        coin toss): gathered. One is 0.845 like it and 0.84
        like Mara: it could as well be hers, and is left."""
        mara = self.add_face(self.photo_tagged("mara.jpg", ["Mara Quill"]), at(0))
        db.write_with_connection(self.db_path, lambda conn: store_faces.name(conn, [mara], "Mara Quill"))
        chosen_at = math.radians(60)
        target = self.add_face(self.add_photo("new.jpg"), at(60))

        def like(to_mara, to_chosen):
            """A unit vector `to_mara` like Mara's face and `to_chosen` like the chosen one, out of their plane."""
            across = (to_chosen - to_mara * math.cos(chosen_at)) / math.sin(chosen_at)
            vector = np.zeros(512, dtype=np.float32)
            vector[0], vector[1], vector[2] = to_mara, across, math.sqrt(1 - to_mara ** 2 - across ** 2)
            return vector
        ahead = self.add_face(self.add_photo("ahead.jpg"), like(0.83, 0.851))
        between = self.add_face(self.add_photo("between.jpg"), like(0.84, 0.845))

        found = identify.unnamed_like(self.library(), target, named=self.named())

        gathered = [match["id"] for match in found["matches"]]
        self.assertNotIn(between, gathered, "a face as like Mara as the new person was gathered for them")
        self.assertEqual([ahead], gathered, "a face 0.02 ahead of Mara was not offered for the new person")

def face_service_automatch(library, folder, decided):
    """Re-examine's rehearsal of `folder`: what automatch would name, nothing written."""
    from tagpup.services import faces as face_service
    return face_service.automatch_folder(library, folder, decided, rehearse=True)


if __name__ == "__main__":
    unittest.main()
