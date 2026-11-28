"""The bake the index writes on its own (docs/PLAN_KEEP_IN_STEP.md, cut 2): after an index has recorded its batches and
let the graphics card go, every photo of each folder it recorded a photo of that tagpup.services.baking.plan_photo marks
ToBake is written, the folder as one change of file_changes.write_fields, the one writer of photo files.

A bake is the index's values stamped on the file, never an operation the owner sees or undoes (owner, decision 2): its
change is internal (file_journal.INTERNAL), the record a crash needs while the write is under way -- the next start
settles a half-written file by what it holds -- and erased once the folder is finished, but for its files that ended
a conflict the doctor counts (file_journal.NOTES), kept with it (file_journal.finish; owner, 2026-11-03). Where the index's rows and the
file's bake differ the rows win (decision 3): a face deleted in TagPup is gone from the file at the folder's next index.

A folder is planned twice, on a read-only connection of its own: first every photo without its decode (the bake fields
of the folder's files read in one ExifTool read, the first time a photo asks), then only the photos to bake with their
fingerprint, the index's own decode's when it handed one over (`handed`) and the file is still at the stamp the index
recorded it under, so a photo it decoded is not decoded again; a file changed since is decoded. Its files are looked at
through one patient walk (baking.walk): a share slow to wake is waited for once, and only one that stays away has its
photos counted not answered -- nothing is called unreachable, and the next index bakes them.
Each file is read again just before its write (write_fields with no `held`): one changed outside since is a conflict,
never overwritten. A file that may be an incomplete copy (#407: it ends in images.ZERO_TAIL zero bytes or more, as an
interrupted copy leaves one) is written nothing, neither its bake nor its identity, and counted (_incomplete): the
index tells the owner it was left as it is, and so it is.

**A photo's identity** (its XMP-xmpMM:DocumentID, tagpup.files.identity) is a field of the bake (owner, DECISIONS
2026-11-26, decision 1): minted once, after a photo's second plan, for a photo whose picture decoded in full and whose
file holds no DocumentID. A photo the bake writes anyway (planned ToBake) gets it beside the bake's fields, whether or
not the index recorded it this run (DECISIONS 2026-11-27: no write and no decode more; a photo a stopped bake left is
minted by the next index's bake of it). One whose file holds its bake already (Current) is planned with the id alone,
its bake never rewritten (DECISIONS 2026-10-27), only when the index recorded it this run (`handed`) and its file is at
the stamp the index recorded it under (one changed since is read again by the next index, and minted then), and is
given its fingerprint row only once that write is done or found held, at the stamp it recorded (_adopt: the follower
puts a row only from a Fingerprint written); a write that failed or never ran leaves it owed. It is the folder's one internal
change: in no History, never undone, recorded in the photo's row (store.photos.follow_fields: DocumentID ->
document_id), and a done rename is carried over it (file_changes._follow_rename). A file that came to hold an id since
it was read (another library's bake of a folder both hold) keeps its own (_mint_kept). Not minted here: a Current photo
that holds no id and was not recorded this run (a write for the id alone: the one-off's, D1), one the bake cannot plan
(minted when it can be), a clip (never baked), and nothing under --skip-faces, which bakes nothing. A read or a write ExifTool does not answer in its deadline (a share gone away) ends the run at that
folder; the folders after it are baked by the next index. One snapshot of the library is taken before the first write
(snapshots.before_a_long_write: none when today's stands); if it fails nothing is written.

The one rule of a photo's fingerprint row here: a photo the run is handed -- one the index recorded this run (`handed`),
or one with no row (photo_fingerprints.NO_FINGERPRINT: what sync owes the index for a current vector whose picture was
never fingerprinted) -- holds a row after it only when its file was written (the row follows the write, in the
transaction that marks it done: baking.follower), was planned holding the library's current bake already (and, planned
with its identity alone, that write is done), may be an incomplete copy (_incomplete), or failed its write on a file marked read-only (file_changes' `read_only`, the one
failure no retry changes); the last three are given the decode's (_adopt, photo_fingerprints.adopt), so sync does not
queue their folder again for a write that cannot change. To hold it, the rows the index recorded this run are taken away before the first folder is planned (owe_first):
a row comes back only by one of those three. Every other photo has none and the next sync owes its folder to the index
again, however the run went: a write that failed otherwise (a file another program holds open; a packet ExifTool refuses
even with -m, which has no live instance since DECISIONS 2026-10-27 and is retried at each sync), a conflict, a photo not
answered (a share that stopped answering while its folder was read or written), a folder not reached (asked to stop, the
roots changed, an exception, a Ctrl+C), and a process killed or a power cut once the bake has begun. An index that ends
before the bake begins (an error, a Ctrl+C: tagpup_cli._owe_the_recorded) does the same; a process killed before it
begins leaves the index's rows, by design (0 instances). Left, the index's row would keep a new photo's unbaked file
from being owed until something else in its folder changed.
"""
import collections
import dataclasses
import logging
import os

from tagpup.core import fields as core_fields
from tagpup.core import paths
from tagpup.core import progress_line
from tagpup.files import bake, identity, images, shares
from tagpup.services import baking, file_changes, settings
from tagpup.services import snapshots as snapshot_service
from tagpup.store import db, inspection, photo_fingerprints
from tagpup.store import photos as store_photos
from tagpup.store import roots as store_roots

logger = logging.getLogger(__name__)

#: The start of each line the index's bake says (Baked.said, and the CLI's when it stops), the sentence of its `baked`
#: fact (Baked.fact), which the index queue keeps in the job's message (tagpup.services.indexing).
SAID = "Bake: "

#: The start of the sentence the bake says as it begins each folder, and again as each chunk of the folder's files is
#: written (folder_told): which of how many, the run's counts of the folders done, how many of the folder's files are
#: reached when a chunk ends (written, found current, or left as a conflict or a failure: of the files to write), and
#: the folder (owner, DECISIONS 2026-10-28: the live line shows folder paths). The index queue shows it in the job's live
#: line (tagpup.services.indexing), and the run's log holds it: a long bake, and a long folder (759 files over 57
#: minutes, photo_index 2026-10-28), is seen to move.
FOLDER_SAID = "Bake folder "
_FOLDER_COUNTS = "%d of %d, %d written, %d current, %d conflicts"
_FOLDER_FILES = ", %d of %d of its files reached"


def folder_told(number, of, baked, folder, files=None):
    """The `phase` fact (tagpup.core.progress_line) of the `number`th folder of `of` as the bake begins it, with
    `baked`'s counts so far (Baked); with `files`, (done, of the folder's files to write), as a chunk of its write ends.
    Its counts are named as the run's row names them -- `folders_done` (before this one), `folders`, `written`,
    `current`, `conflicts`, `percent` (of the folders done before this one and this one's files reached, of all), and
    with `files`, `files_done` and `files` -- and its sentence names the folder whole."""
    done, total = files if files is not None else (None, None)
    percent = (number - 1) * 100 // of if files is None else ((number - 1) * total + done) * 100 // (of * total)
    sentence = (FOLDER_SAID + _FOLDER_COUNTS % (number, of, baked.written, baked.current, baked.conflicts)
                + (_FOLDER_FILES % files if files is not None else "") + ": " + folder)
    return progress_line.fact("phase", phase="bake", sentence=sentence, folders_done=number - 1, folders=of,
                              written=baked.written, current=baked.current, conflicts=baked.conflicts, percent=percent,
                              files_done=done, files=total)


#: The parts of a line Baked.said makes: the counts every line has, and the conflicts it adds when there are any.
_SAID_COUNTS = "%d file(s) written, %d already held it"
_SAID_CONFLICTS = "%d conflict(s), left as they are"
_SAID_REFUSED = "%d write(s) discarded for changing the picture and made again"
_SAID_INCOMPLETE = "%d photo(s) that may be incomplete copies left as they are"


#: A folder whose write the library's roots, changed by another process, stopped (the run ends there).
ROOTS_CHANGED = "the library's roots were changed by another process"

#: The field a photo's identity is minted into, read beside the bake's (FolderRead) and written with them.
DOCUMENT_ID = identity.DOCUMENT_ID_FIELD
#: The fields a folder's write reads of each file before it (write_fields' `read`): the bake's and the identity.
_READ = list(bake.WRITTEN.values()) + [DOCUMENT_ID]


@dataclasses.dataclass
class Baked:
    """What a run did, counted from what each write reported: photos planned to bake, files written, found holding
    their bake already (planned or at the write's read), conflicts, failed, not answered (a share away), and the photos
    the library cannot plan, by baking's kind of Cannot; `refused`, the folders whose write was refused, by its sentence
    (counts only: too many photos, a batch ahead, the roots changed); `stalled`, a share stopped answering and ended the
    run; `roots_changed`, the library's roots were changed by another process and ended it; `pictures_refused`, the writes
    discarded because they changed the picture's bytes and made again (file_changes, the result's `pictures_refused`);
    `minted`, the photos whose write gave them an identity (counted from the write's report, logged, not said);
    `incomplete`, the photos written nothing for their file may be an incomplete copy (_incomplete)."""
    planned: int = 0
    written: int = 0
    current: int = 0
    adopted: int = 0
    conflicts: int = 0
    failed: int = 0
    not_answered: int = 0
    cannot: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    refused: collections.Counter = dataclasses.field(default_factory=collections.Counter)
    stalled: bool = False
    roots_changed: bool = False
    pictures_refused: int = 0
    minted: int = 0
    incomplete: int = 0

    def said(self):
        """The run in one line, counts only (the paths name people)."""
        line = SAID + _SAID_COUNTS % (self.written, self.current)
        more = [("%d fingerprint(s) recorded with no write" % self.adopted) if self.adopted else "",
                (_SAID_CONFLICTS % self.conflicts) if self.conflicts else "",
                ("%d failed" % self.failed) if self.failed else "",
                ("%d not answered (a share away)" % self.not_answered) if self.not_answered else "",
                (_SAID_REFUSED % self.pictures_refused) if self.pictures_refused else "",
                (_SAID_INCOMPLETE % self.incomplete) if self.incomplete else "",
                ", ".join("%d %s" % (n, kind) for kind, n in sorted(self.cannot.items())),
                ", ".join("%d folder(s) refused: %s" % (n, why.rstrip(".")) for why, n in sorted(self.refused.items()))]
        line += "".join("; " + each for each in more if each)
        if self.stalled:
            line += "; ended at a folder whose share stopped answering, the rest are baked by the next index"
        if self.roots_changed:
            line += "; ended where the library's roots changed, the rest are baked by the next index"
        return line + "."

    def fact(self):
        """The `baked` fact of the run (tagpup.core.progress_line): its line, and its counts written, current and
        conflicts."""
        return progress_line.fact("baked", sentence=self.said(), written=self.written, current=self.current,
                                  conflicts=self.conflicts)


def bake_folders(library, fields, detector, folders, handed, stop=None, each=None):
    """Bake the photos of each of `folders` (native folder paths, the photos directly in each) that plan ToBake, a
    folder at a time: Baked. `detector`, the name the library's face detector records its looks under
    (tagpup.services.faces.detector_named); `handed`, {paths.key(path): ((mtime, size), digest of the upright decode, or None: decoded here)}
    the index took of the photos it recorded, with the stamp it recorded each under; `stop()`, asked before each folder;
    `each(fact)`, the fact of each folder as it begins and as each chunk of its write ends (folder_told), whose sentence
    the run's log holds too: nothing between two is said, so a share that stops answering mid-chunk stops the lines.
    A handed photo keeps its row only when its file is written, holds the current bake or is read-only (owe_first)."""
    baked, snapshot, walk, chunk_lines = Baked(), [], baking.walk(stop), [True]
    owe_first(library, folders, handed)
    # One deadline for the run (Fields.together): a share away ends it at that folder, the rest baked by the next index.
    with fields.together():
        if folders:
            fields.ready_to_write()   # an ExifTool that will not start fails the run once, not as an unreadable line per photo
        for number, folder in enumerate(folders, 1):
            if stop is not None and stop():
                break
            def say(files=None, number=number, folder=folder):
                told = folder_told(number, len(folders), baked, folder, files)
                logger.info("%s", told["sentence"])
                if each is not None:
                    each(told)
            say()

            def say_chunk(done, of, say=say):
                """A chunk's line is best effort: it is said inside the write, and a line that cannot be said (a repo-run
                server restarted on a .py save orphans its indexer, the stdout pipe's reader is gone) must not end the
                write with files still planned. Once one fails none is said for the rest of the bake."""
                if not chunk_lines:
                    return
                try:
                    say((done, of))
                except Exception as error:
                    chunk_lines.clear()
                    logger.warning("The bake's progress lines stop here, its writes go on: %s: %s", type(error).__name__, error)
            to_bake, stored, unfingerprinted, id_alone = _planned(library, fields, detector, folder, handed, baked, walk)
            # A photo holding its bake already, and written nothing, has its row now: its file holds the bake.
            _adopt(library, {key: (photo_id, decode, decode.stamp) for key, (photo_id, decode) in unfingerprinted.items()
                             if key not in to_bake}, handed, baked)
            written = None
            if to_bake and not baked.stalled:
                if not snapshot:
                    snapshot.append(snapshot_service.before_a_long_write(library))
                written = _write(library, fields, stored, to_bake, baked, say_chunk)
            _adopt(library, _given_after(unfingerprinted, id_alone, written), handed, baked)
            if baked.stalled or fields.dead:
                baked.stalled = True
                break
            if baked.roots_changed:
                break
    return baked


def owe_first(library, folders, handed):
    """Take away the fingerprint rows the index recorded this run (`handed`'s photos, directly in `folders`) before the
    bake plans anything: each is then planned as a photo with no row, and has one again only by its file's write
    (baking.follower) or _adopt. Whatever ends the run after this -- a stop, a share away, an exception, a process killed -- the
    photos it did not bake are owed (NO_FINGERPRINT) and the next index bakes them, the index's decode lost (one decode
    each). Deleting a row only makes a photo owed: a writer racing it loses nothing."""
    if not handed:
        return
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        ids = [photo_id for folder in folders for photo_id, path in inspection.under(conn, folder)
               if paths.key(path) in handed and paths.key(os.path.dirname(path)) == paths.key(folder)]
    finally:
        conn.close()
    if ids:
        db.write_with_connection(library.path, lambda conn: photo_fingerprints.forget_photos(conn, ids),
                                 label="the fingerprints the index recorded of the photos its bake is to write")


def _planned(library, fields, detector, folder, handed, baked, walk):
    """({paths.key(path): (path, values)} of the photos of `folder` to write -- to bake, or holding their bake and to be
    given their identity alone --, the folder as the library stores it, {paths.key(path): (photo id, _Decode)} of those
    planned that have no fingerprint row, current or to bake, and the keys of those to be given their identity alone),
    counting into `baked` the photos current, not answered and that cannot be planned. A photo's identity is minted
    here (_minted), once, after its second plan."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        keys = baking.keys(conn, detector)
        stored = store_roots.to_row(conn, folder)
        # A clip's file is never baked: planned, it would be counted a photo that cannot be.
        group = [(photo_id, path) for photo_id, path in inspection.under(conn, folder, store_photos.BAKED_KINDS)
                 if paths.key(os.path.dirname(path)) == paths.key(folder)]
        fingerprinted = photo_fingerprints.fingerprinted(conn, [photo_id for photo_id, _path in group])
        read = baking.FolderRead(fields, [path for _id, path in group], also=(DOCUMENT_ID,))
        to_bake, unfingerprinted, id_alone = {}, {}, set()
        for photo_id, path in group:
            photo, decode = baking.PhotoAt(path, read, walk), _Decode(path, handed)
            plan = photo.plan(conn, keys)
            if isinstance(plan, baking.ToBake) or (isinstance(plan, baking.Current) and photo_id not in fingerprinted):
                plan = photo.plan(conn, keys, decode)
            if photo_id not in fingerprinted and decode.digest is not None and isinstance(
                    plan, (baking.ToBake, baking.Current)):
                unfingerprinted[paths.key(path)] = (photo_id, decode)
            minted = _minted(path, read, handed, decode, plan)
            if (minted is not None or isinstance(plan, baking.ToBake)) and _incomplete(path, baked, unfingerprinted):
                continue
            if minted is not None:
                to_bake[paths.key(path)] = (path, {**(plan.values if isinstance(plan, baking.ToBake) else {}),
                                                   DOCUMENT_ID: [minted]})
                if isinstance(plan, baking.Current):
                    id_alone.add(paths.key(path))
            elif isinstance(plan, baking.ToBake):
                to_bake[paths.key(path)] = (path, plan.values)
            elif isinstance(plan, baking.Current):
                baked.current += 1
            elif photo.not_answered:
                baked.not_answered += 1
            else:
                baked.cannot[plan.kind] += 1
    finally:
        conn.close()
    baked.stalled = baked.stalled or read.stalled
    return to_bake, stored, unfingerprinted, id_alone


def _minted(path, read, handed, decode, plan):
    """A new identity for the photo at `path` (identity.mint_document_id), or None: for one whose picture decoded in full
    (`decode`: the index's, handed over, or the bake's own) and whose file holds no DocumentID (the folder's `read`),
    planned to bake (`plan` ToBake: written anyway, the index's this run or not), or holding its bake (Current: a write
    for the id alone) when the index recorded it this run (`handed`) at the stamp its file is at still. A file that may
    be an incomplete copy is written nothing, its identity with the rest (_incomplete); one changed between the plan and
    the write is a conflict (write_fields' read just before it)."""
    if isinstance(plan, baking.Current):
        recorded = handed.get(paths.key(path))
        if recorded is None or decode.stamp != recorded[0]:
            return None
    elif not isinstance(plan, baking.ToBake):
        return None
    if decode.digest is None:
        return None
    held = read.of(path)
    if isinstance(held, Exception) or core_fields.read_document_id(held):
        return None
    return identity.mint_document_id()


def _incomplete(path, baked, unfingerprinted):
    """Is the file at `path`, planned to be written, to be written nothing: it may be an incomplete copy (#407: it ends in
    images.ZERO_TAIL zero bytes or more, images.tail_check, its end alone read), counted in `baked.incomplete` and left in
    `unfingerprinted` for its row (_adopt: no write would change it; a copy put back has another stamp and is read
    again); or its end could not be read, counted as a photo the bake cannot read (baking.UNREADABLE) and owed, taken
    out of `unfingerprinted`. Asked only of a photo the bake would write."""
    try:
        zeros = images.tail_check(path)
    except OSError as error:
        logger.info("Not written, its end could not be read: %s: %s", path, error)
        baked.cannot[baking.UNREADABLE] += 1
        unfingerprinted.pop(paths.key(path), None)
        return True
    if zeros < images.ZERO_TAIL:
        return False
    logger.info("Possibly an incomplete copy, not written: %s ends in %d zero bytes", path, zeros)
    baked.incomplete += 1
    return True


def _mint_kept(path, held, values):
    """`values` to write into the file at `path` holding `held` just now, but for an identity planned where the file has
    come to hold one since its folder was read (another library's bake of a folder both hold, at once): that one is the
    photo's, never written over, and planned as it is, so the photo's row records it with the write."""
    found = core_fields.read_document_id(held)
    if DOCUMENT_ID not in values or not found:
        return values
    logger.info("Holds an identity given since its folder was read, kept: %s", path)
    return {**values, DOCUMENT_ID: [found]}


class _Decode:
    """The digest of the photo's upright decode, as plan_photo asks it: the index's, handed over, while the file is at
    the stamp the index recorded it under (a file changed since holds another picture, perhaps); or decoded now. Keeps
    the digest and the stamp of the file it was taken of (`digest`, `stamp`; None until asked)."""

    def __init__(self, path, handed):
        self.path, self.handed, self.digest, self.stamp = path, handed, None, None

    def __call__(self):
        stamp, digest = self.handed.get(paths.key(self.path), (None, None))
        now = shares.stamp_of(self.path)
        if digest is None or now != stamp:
            digest = images.fingerprint(images.opened(self.path, upright=True))
        self.stamp, self.digest = now, digest
        return digest


def _given_after(unfingerprinted, id_alone, written):
    """The photos of `unfingerprinted` (_planned's) given their row after the folder's write (`written`, its Result; None:
    nothing written), {key: (photo id, _Decode, stamp)}: each planned with its identity alone (`id_alone`) whose file
    holds it now -- written, at the stamp the write recorded it under (its vector follows the write), or found holding it
    at the write's read, at its decode's -- and each whose write failed on a file marked read-only (file_changes'
    `read_only`: a write no retry changes). A write that failed otherwise, a conflict, a photo not answered or not
    reached leaves the photo owed."""
    if written is None:
        return {}
    done = {paths.key(path) for path in written.details.get("written", {})}
    stamps = written.details.get("stamps", {})
    read_only = {paths.key(path) for path in written.details.get("read_only", [])}
    return {key: (photo_id, decode, stamps.get(key) or decode.stamp) for key, (photo_id, decode) in unfingerprinted.items()
            if key in read_only or (key in id_alone and key in done)}


def _adopt(library, given, handed, baked):
    """Give its decode's fingerprint row (photo_fingerprints.adopt) to each photo of `given` ({key: (photo id, _Decode,
    the stamp of the file it describes)}, of _planned's photos with no row: the index's this run among them, owe_first),
    the caller's choice of them: before the write, those whose file holds the library's current bake and are written
    nothing; after it, _given_after's. Nothing else: a written file's row followed its write, and a photo not written
    for any other reason stays owed. Counted in `baked.adopted`, but for those whose fingerprint `handed` holds: the
    index recorded theirs this run, and given back it is nothing new to say."""
    # A file whose look did not answer when it was decoded has no stamp to hold the vector's against: no row.
    given = {key: found for key, found in given.items() if isinstance(found[2], tuple)}
    if not given:
        return

    def adopt(conn):
        photo_key = settings.photo_key_on(conn)
        return sum(photo_fingerprints.adopt(conn, photo_id, photo_key, stamp, (images.FINGERPRINT_KEY, decode.digest))
                   and handed.get(key, (None, None))[1] is None
                   for key, (photo_id, decode, stamp) in given.items())
    baked.adopted += db.write_with_connection(library.path, adopt, label="the fingerprints of the photos whose files hold their bake")


def _write(library, fields, stored, to_bake, baked, on_chunk):
    """Write the photos of one folder to bake as one internal change, each file read again before its write, and count
    in `baked` what the write reported (its Result: the change is erased once it finishes, its conflicts of NOTES kept); `on_chunk(done, of)` as each
    chunk of its files ends (write_fields)."""
    kept = set()   # the photos that came to hold an identity since their folder was read (_mint_kept)

    def plan(path, held):
        planned = to_bake[paths.key(path)][1]
        values = _mint_kept(path, held, planned)
        if values is not planned:
            kept.add(paths.key(path))
        return file_changes.Plan(after=values)

    result = file_changes.write_fields(
        library, baking.INDEX_OPERATION, fields, [path for path, _values in to_bake.values()], _READ, plan,
        summary={"folder": stored}, on_chunk=on_chunk)
    unanswered = {paths.key(path) for path in result.details.get("not_answered", [])}
    conflicts = len(result.details.get("conflicts", []))
    baked.planned += len(to_bake)
    baked.written += result.changed
    baked.current += len(result.details.get("written", {})) - result.changed
    baked.conflicts += conflicts
    baked.not_answered += len(unanswered)
    baked.pictures_refused += result.details.get("pictures_refused", 0)
    # Minted: written, its id with it (in the stamps of the files written, not taken out by the read back).
    written = {paths.key(path) for path in result.details.get("written", {})} & set(result.details.get("stamps", {}))
    minted = sum(1 for key, (_path, values) in to_bake.items() if DOCUMENT_ID in values and key not in kept and key in written)
    if minted:
        logger.info("%d photo(s) of %s given an identity", minted, stored)
    baked.minted += minted
    baked.failed += max(0, len({paths.key(path) for path, _why in result.errors} - unanswered) - conflicts)
    if (result.details.get("stopped") or {}).get("share"):
        baked.stalled = True
    elif result.details.get("roots_stopped"):
        baked.roots_changed = True
        baked.refused[ROOTS_CHANGED] += 1
    elif result.refused:
        baked.refused[result.refused] += 1     # its sentences give counts, never a path (file_changes.too_many, batches.claim)
    return result
