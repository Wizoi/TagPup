"""Which domain owner each test file belongs to, which scenario a scenario file runs, and each file's size. Read by
tools/run_tests.py (--owner, --scenarios, --size, the per-owner line), tests/test_the_test_map.py; written like tests/tiers.py, by hand, in the commit that adds a test file.

    OWNERS     the six domain owners (docs/OWNERS.md): their charters are .claude/agents/tagpup-owner-<data, people,
               files, media, views-ai, integration>.md; `apps` is Integration and apps.
    SCENARIOS  {id: (owner, one sentence)}: a flow a person lives through. The letter of an id is its owner's (D data,
               P people, F files, C media (clips), V views, I apps). A charter lists its ids; a scenario file names the ones it runs,
               one or more (its owner is the owner of its first; test_roots_scenarios is D7 and I6).
    FILES      {test file: (owner, kind, scenario ids)} for every tests/test_*.py (the module name) and every
               tests/frontend/*.test.mjs (the file name).
    SIZES      {test file: size} for every tests/test_*.py: the most its tests reach for, in SIZE_ORDER: pure
               (Python only), sqlite (a database), subprocess (a process, or a Flask app: its routes start
               ExifTool), browser (a real server under a real page). Derived from the names the file uses and
               kept by tests/test_every_test_file_has_its_size.py, which prints the _SIZES to paste; a page
               test (node) counts as browser.
    kind       scenario (runs a flow, names its ids), unit (one module's behaviour), migration (a schema step), guard (a
               rule about the source text), unmapped (legacy: no import or name placed it; apps owns it until it is
               placed; the count may only go down, tests/test_the_test_map.py).

A file got its owner from the module of tagpup/ (or the page module of web/) it imports most, through the Scope in the
owner charter that lists it; a file naming no scope module was placed by its name or by hand. A new test file is added
to _FILES below under its owner; the guard names a file that is not.
"""
OWNERS = ("data", "people", "files", "media", "views", "apps")
KINDS = ("scenario", "unit", "migration", "guard", "unmapped")
#: Finding numbers a test file once cited and may stop citing: only a number listed here may leave tests/findings_cited.txt's set.
RETIRED = frozenset({
    745,   # the supervisor's hung-path finding: the supervisor is deleted (DECISIONS 2026-10-11); its test went with it
    80,    # migration below the baseline deleted (migration 4's face counter); its test went with it (owner, 2026-10-15)
    81,    # migration below the baseline deleted (migration 4's photo id counter); its test went with it
    82,    # migration below the baseline deleted (migration 4's stored form of a never-indexed photo); its test went with it
    83,    # migration below the baseline deleted (migration 4 with foreign keys on); its test went with it
    96,    # migration below the baseline deleted (migration 7's saved suggestions under two folders); its test went with it
    753,   # migration below the baseline deleted (migration 24's word index being made, answered 503); its test went with it
    250,   # refresh_rows' planner reading a row never read: the planner is deleted (DECISIONS 2026-10-28); its test went with it
})

SCENARIOS = {
    "D1": ("data", "A library one migration behind, or newer than the code, is served or refused at start without losing a row."),
    "D2": ("data", "A change is one journaled step and Undo puts every row of it back."),
    "D3": ("data", "A crash between writing and done, or a restart part-way through a write, is finished at the next start."),
    "D4": ("data", "A snapshot restore puts the library back as it was, a monthly from before the library had roots included, under a running index; one below the baseline is refused."),
    "D5": ("data", "Every writer rebuilds the derived rows in its own transaction."),
    "D6": ("data", "A library's settings are its own: stamped, changed, undone, and read by the next run."),
    "D7": ("data", "The folders a library holds (roots) are stored, verified and followed through the same flows with and without them."),
    "D9": ("data", "A run nests under the run of its library that queued it and says how far it got, at most every 10 s, skipped while the library is written, never a path; one gone or another process's is left alone."),
    "D10": ("data", "A folder removed from either app takes its rows and faces and leaves its files; it is refused while it, a folder above it or a folder below it waits or is indexed in that library, never in another library holding it."),
    "D8": ("data", "A library kept at the baseline meets an additive and a destructive step: interrupted, two openers, a failing check, a backup, an older app, undoable; one below the baseline is refused untouched; the owner's library at 32 meets the real 33, at 33 the real 34, at 34 the real 35, at 35 the real 36, at 36 the real 37, and at 37 the real 38."),
    "P1": ("people", "A person is the id of a tag-tree node: two processes naming, renaming or merging at once leave one answer."),
    "P2": ("people", "Names, tags and faces stay in step across photo files, jobs and the CLI's journaled apply."),
    "P3": ("people", "Identify faces: a cluster is named, ignored or split, and the page shows what the server says."),
    "P4": ("people", "Name faces from tags runs as a resumable job: a restart in the middle settles, a second click does not repeat."),
    "P5": ("people", "A tree edit (new, rename, merge, move) keeps every photo, face and filing in step."),
    "P6": ("people", "Two cousins called alike under two groups are named by id; a typed bare name that fits both is refused, naming both paths."),
    "P7": ("people", "Identify Faces: ignore, assign, restore and undo, against the real server."),
    "P10": ("people", "A library on its face model: a new photo is indexed and Suggested by it, the face under the floor not made, and Name faces from tags reads its vectors by its own cuts."),
    "P11": ("people", "The same face gets the same nearest person in Suggest, Identify, the strip, the boxes and Name faces from tags, a person who changed over the years by the faces of the face's own years, an undated face by every year."),
    "P12": ("people", "A face in the background of its picture (under 2% of the shorter side the photo's row stores) is hidden by the strip and offered or named by none of Organize, Suggest, Name faces from tags and cluster-faces; on a photo of no stored size it is in the background nowhere."),
    "P13": ("people", "A clip's people are the people its frames show: seen in one frame or four, offered once by the one matrix, Apply All writes the tag and the clip's photo_people row follows, nothing of a face is stored, frames unreadable is tags only, a cancel keeps nothing."),
    "V19": ("people", "Somebody tags the open photo while its faces are shown: the strip keeps its cards, never 'Loading', until the new answer replaces them in one step, and is read again only when the save's reply lists a face of it (named by the one-face rule, unnamed by a person taken off, named again), never for a reply that lists none; a face named by its card, its person then taken off by the pill; two quick saves, another photo chosen meanwhile, back to the grid and the same photo again, an undo of another photo, a failed read, the focus on a card, a save during a naming or during the first read."),
    "F1": ("files", "A photo write is journaled, one at a time, read back; a click three times writes once."),
    "F2": ("files", "The indexer writes nothing into a photo that does not decode; nothing is written into a folder the library does not hold."),
    "F3": ("files", "A bulk edit over many photos: dry run first, a crash or a stalled ExifTool part-way, then resume."),
    "F4": ("files", "A folder renamed or a photo moved is followed by sync and the watcher; sync never writes a file, and removes only a row it looked for and did not find, its root confirmed there."),
    "F5": ("files", "Two libraries holding one folder stay each their own."),
    "F6": ("files", "A row never claims more than was read: an index or a partial write stamps only what described the file."),
    "F7": ("files", "A share asleep is not missing."),
    "F8": ("files", "Just look edits a file only, and delete, rename and the recycle bin keep their rules."),
    "F9": ("files", "A run holds one map of the roots; a root moved meanwhile stops a write with a sentence."),
    "F10": ("files", "One photo whatever its path's spelling."),
    "F11": ("files", "Add a folder: it is queued, indexed by the CLI with progress, its photos' bakes read from their files under the library's keys, followed from every entry point, when it recorded a photo, by a matching of the faces it made that writes nothing, and a second run reads nothing."),
    "C1": ("media", "A clip in a folder is indexed as a row of kind video, dated by its XMP dates first, then its QuickTime ones, its vector made of its frames after the photos; the owed scan never queues it again, the folder view lists it beside its photos, and the photos beside it are indexed as before; sync follows a clip moved or renamed and reads again one whose copy went on; a clip cut short is a row never decoded, tried again by every index; an index stopped part-way makes the rest next time; two libraries holding one folder of clips keep their own rows."),
    "C2": ("media", "A clip's tags and title are written in place into its XMP, never by ExifTool's rewrite: a selection of photos and clips tagged writes both, a clip the patch cannot place is refused before anything is written, a cleared title frees its QuickTime title and its undo gives it back, a tag save undone from History puts its XMP back; a clip's Windows Tags moved into its XMP free its Windows Xtra in the same patch, a second move and a clip with no Windows Tags write nothing, and its undo gives the Xtra back; the one-time move over a library (a dry run reads and writes nothing, nor settles a change left half done; --apply refused while the app runs, --limit, Ctrl+C and a re-run continuing, a clip open in a player refused while the others go, a share away left for the next run, a tag not in the tree left as it is, the tags a clip holds kept); a write cut short is settled by its bytes at the next start (written again, cut back, recorded, or a conflict), two writes of one clip run one after the other, and the player streams it at its new size; a turn naming a clip, or a time shift over its folder, leaves its file as it was with a sentence, the photos beside it written."),
    "C3": ("media", "A clip of a folder the library holds is Smart Renamed with the photos (a day's selection, its button, a Save after its title), the name it had kept first in its XMP, undone from History; a clip the patch cannot place keeps its name with a sentence while the photos beside it are renamed, a rename stopped part-way and run again renames with the names kept, a second library finds it by the name it kept, and a clip of a folder the library does not hold keeps its name."),
    "C4": ("media", "A clip of up to 2 GiB is deleted as a photo is (from a share through this PC, its copy whole, its vector, frames and poster with it, other writers let in after each clip of a bulk delete, its stream refused while it is deleted); a larger one, or a file its own drive's Recycle Bin would not keep, or one from a share whose copy this PC's Recycle Bin would not keep (full: 'The Recycle Bin is full: delete permanently instead?'; the Bin still keeps what it has room for), is deleted for good only once the owner confirmed the warning naming it (no copy made or counted; unconfirmed, or a question that did not name it, nothing touched), the reply saying for good; one held open, or whose size cannot be read, is left with its row and a sentence."),
    "C5": ("media", "A clip's Date Taken is written into its XMP dates (xmp:CreateDate only where it holds one), every QuickTime header's creation time (movie, each track, each media; at its own width, local time written as UTC) set with it in the one patch, each box given back by the undo, and no header written without its XMP date; a date no header holds (before 1904, after early 2040, not to the second) refused with a sentence, nothing written; its row and the library's order following; undone from History, the old xmp:CreateDate given back; refused outside the library while the held clip is dated; a date save cut short settled by its bytes at the next start; a clip whose file holds no date dated by the date in its file name (never its folder's, never a photo's), a real date or a date saved winning, and a clip with no date at all keeping no year (the owner dates it by hand); the one-off that wrote the name's date into the clips' XMP is deleted (change 8261, undone from History as a save is); the one-off writing each clip's Date Taken into its headers (a zero header, or one the owner's list names, the 8 to 10 hours it keeps left out; a dry run, --limit, Ctrl+C and a re-run continuing, refused while an app runs, a clip dated since its listing or holding the date already not counted written, a gone file and a share away said, a date no header holds refused and counted, one change undone from History)."),
    "C6": ("media", "A tag-tree rename, merge or retire of a tag a clip holds rewrites the clip's XMP with the photos, its row following, and the tree moves once every file is rewritten."),
    "C7": ("media", "A clip of an old format (.wmv, .mpg, .avi; an .mp4 of MPEG-4 Part 2 under its own name) is converted to H.264 and AAC in an MP4 as one change of History that is never undone: its row followed to the MP4, its title, tags and the name it had carried into the copy (its Windows Tags the tag tree holds among its tags, the copy holding no Xtra and read back before a renamed original goes; a Windows tag it cannot carry refuses it, named), its Date Taken kept in the copy's XMP and every QuickTime header, ffmpeg carrying none (a camera's .mov; an iPhone's dated with its zone, the zone in the XMP alone; an .mp4 or a .wmv dated in its XMP, its headers zero after ffmpeg; a .wmv's creation time never made one), the original deleted for good last (never binned); a conversion cut short at any step is settled at the next start, its copy on its disk before it was recorded (a power cut finds it whole); ffmpeg failing, or a copy its disk will not take, leaves the original and no temp; an original that cannot be deleted is kept, the copy in place, for the next settle; a second conversion of one clip is refused before ffmpeg runs; a clip another program writes while Convert reads it is refused CHANGED, nothing planned; the panel's Convert and Make smaller (MP4) by id answer at once with a ticket the Activity strip lists and Cancel stops, a second click and a stale stamp are refused, a shrink no smaller is withdrawn, a button that is not the clip's plan is refused."),
    "C8": ("media", "A video played in the browser: a range answered with its bytes and capped, one that cannot be answered refused with the size, sought back and forth quickly, closed half way without holding the file, renamed while it plays ending every stream short, a share that stops answering ending the stream within its wait and the next ask answered 503 at once, an id a video in one library and a photo in another; the cards and the record say which are videos; a clip the browser cannot play (an older format, a picture it does not decode) says what it needs to play, ffprobe bounded on a share gone silent."),
    "C9": ("media", "A clip in the grid shows its poster frame, its middle frame, kept as a photo's thumbnail is: made once (two cards at once one ffmpeg run, a folder scrolled fast at most three at once), found again by its stamp, made again once renamed or changed, its last poster shown once its file is gone; a clip cut short or a machine with no ffmpeg answered the placeholder and nothing kept, a share asleep one bounded look and no ffmpeg; `thumbs warm` making the posters ahead."),
    "V1": ("views", "A view lists each photo once, in the owner's order, with its count; a clip by its XMP date first, then its QuickTime one, a date of zeros no date; under the Photos / Videos filter its photos or its clips alone, the source's whole count beside."),
    "V2": ("views", "A typed search ANDs its words and ranks nothing."),
    "V3": ("views", "Suggest, and the analyse-only run, from start to end: a folder's photos and its clips, a clip offered tags alone (its frames read when it has no vector, why it could not be said on its entry and tried again), Apply All patching a held clip's XMP in place and Undo taking it off, a clip only looked at kept nowhere and left unwritten beside the photos written."),
    "V4": ("views", "One process at a time has a model on the graphics card; the waiter is told who holds it."),
    "V5": ("views", "The navigator's counts are the photos under a node, each once."),
    "V6": ("views", "The server goes idle: models, vectors and caches are let go, and the next ask loads them again."),
    "V7": ("views", "Select all minus the excluded: the tally, the folders and a bulk edit read the same photos under the caps, and under the Photos / Videos filter the same clips."),
    "V8": ("views", "A write elsewhere makes the open view and the cached scan read again."),
    "V9": ("views", "Vectors and suggestions follow their photo through rename, settle, rotate, delete and undo."),
    "V11": ("views", "A library made on the new face model is refused the old one, nothing written, nor in a dry run."),
    "V12": ("views", "A library switches to SigLIP 2 SO400M once the new model's vectors are made for its photos, refused with the counts before, and its undo held to the same share; each library then searches by its own model's vectors, a run on the model it began with."),
    "V13": ("views", "The new photo model's vectors are made for a library beside its own (embed-photos): a dry run that writes nothing, a run stopped and run again that makes only the rest, a batch one write, a photo removed meanwhile given no row back, a photo edited meanwhile stamped as it was opened, a file that does not decode tried again, a drive not connected stopping the run with a sentence, two libraries holding one folder each by its own run, the turn on the card taken before the list is read, a running server giving its kept turn to the run and its next Suggest on the switched model; then the switch from the command line (set-photo-model), refused before, passes as one change of the journal, and its undo puts the old model back."),
    "V14": ("views", "After the switch, the old photo model's vectors and Suggest's words under it are retired (retire-photo-vectors): a dry run that writes nothing, the own key's vectors and words kept byte for byte, one change a key whose undo is refused and the undo of the switch refused with the switch's sentence, a run stopped (or an app started) and run again as the same change, a switch back beside the run ending it, a photo removed meanwhile, and a library whose own model does not hold its photos refused."),
    "V17": ("views", "The owner measures Suggest's neighbour cut for clips on a copy of a library, nothing written to it and no model loaded: a clip matched on its best of five frames against its frame 0 alone, the share of the neighbour slots clips take and the tags offered at each cut, held out by photo and by folder, ids and counts only."),
    "V20": ("views", "Somebody takes tags off the open photo by its pills: each goes at the click, its write waiting with the page's tag changes, first in line, behind the photo's write out; pills clicked while one is out are written together and the file holds none of them, a removal and a save around it keep both and a title's save already out draws no pill again, a failed write draws its own tag again, says so and offers no Retry while the next removal still goes, the panel's own removals never make it read-only, another photo chosen meanwhile shows its own tags, and a pill taken off by the keyboard leaves the focus on the next pill or the add box."),
    "V27": ("views", "Somebody moves on from a photo while its save is still out: the move waits, and a wait longer than a moment says so over the photo, never flashing for a save answered at once and never taking the focus; presses made meanwhile move once, the last asked; the note clears itself when the save is written and the move goes once; a save that failed with its text still in the panel asks as before and does not move; Escape or Stay here lets the waiting move go."),
    "V18": ("views", "Somebody works the main page by the keyboard alone: Tab reaches every control the mouse does, in reading order, nothing unseen (a closed dialog), the file list one stop, an opened dialog holds the focus and gives it back."),
    "V21": ("views", "Somebody presses Refresh on an open folder: it is synced first and its list read again once, the status line saying what was found and whether the full check that removes a gone file started, ran already, or could not; three presses during a slow answer send one, another folder opened meanwhile drops the answer, a sync that fails or is refused still reads the list and says so, and unsaved edits are asked about before anything is sent."),
    "V22": ("views", "Somebody tags many photos at once, a folder of 50 or a select-all, while clicking on: each file's tag change waits in the page and goes 25 files a write under one group token, a tag added meanwhile rides the write of the files not yet sent and reaches the written ones in a later write, clicks on one tag leave the last one, a write that fails keeps its files' changes for Retry and never loops by itself, a single save clicked meanwhile goes between two writes, the open photo is read-only while a change it does not draw waits for its file, saying how many, and editable again once that file is written or the change cancelled, viewing and the arrows free, Cancel says how many photos it left unwritten, and choosing another library asks while changes are not sent; staying keeps them."),
    "V23": ("views", "On the phone page somebody walks Folders, Keywords, People or Dates down one level at a time and Back up one at a time, to the list of libraries at the top and never off the page; Show these photos and a search of words open the grid on that view (a folder with its subfolders); a section that could not be read says so with Try again, a slow reply never draws over a newer one, two quick taps open one level, an empty search does nothing, and an address naming a view opens its photos."),
    "V24": ("views", "On the phone page somebody opens a view of tens of thousands of photos and scrolls through its thumbnails: only the window in view has its cards asked, the next as it is scrolled to; the order or Photos/Clips chosen reads the view again from the top and a reply for the order left is dropped; a clip shows its play mark and a stale photo its badge; a tap opens the photo, two quick taps one step, and Back is the grid where it was left with nothing asked again; ids or cards that could not be read say so with a Try again that works, and an empty view says why."),
    "V25": ("views", "On the phone page somebody taps a thumbnail and looks at the photo large, swiping left and right through the view: rapid swipes go as many photos on in one step of history, the first and last go no further, a photo whose card is still being read waits for it and a reply for one swiped past is dropped, a tap zooms the file whole, and Back after the swipes is the grid with the card swiped to in view; a missing file, a picture or card that could not be read and a clip that will not play each say so and the swipe goes on; under the picture its facts (Date Taken, a title clamped to three lines with more, the people and keywords as chips, Edit) and, pulled up, the rest: a photo with no date or no keywords, 40 keywords, a clip and a record that could not be read each say what they hold, a swipe is refused while the sheet is up, rapid swipes read the facts one at a time, and a phone on its side keeps the picture's height."),
    "V26": ("views", "On the phone page somebody opens a photo's Edit sheet and adds and takes off people and keywords, "
            "each one path's change with no stamp; rapid x taps each have their own Undo and an Undo in time sends "
            "nothing; a swipe, Back or the page going sends a removal still waiting, even one "
            "behind a Save still out; the title and Date Taken save with the stamp read, a Save refused while the "
            "folder indexes is read again and sent once more (not one with a Date Taken), the photo left meanwhile or "
            "not, a file changed on the PC is "
            "never written over and the text typed is kept, a dropped connection says so, a title or date left by "
            "Back is put back when the sheet opens again, and Close with text typed asks."),
    "V28": ("views", "Somebody marks rows of the navigator's tabs in Filter mode, any of, must or not: the marks are one "
            "search the server reads as said (two people ANDed, one NOT another, a tab's any of ANDed with a year, a folder "
            "with its subfolders, nothing found), the address read back (Back, a bookmark, one naming a tag deleted since) "
            "gives the same marks, and a search made elsewhere (words, an Any-of list, Within's union of two tabs) marks no row."),
    "V29": ("views", "Somebody turns Filter mode on and clicks rows: off it is Browse, on is remembered; rapid clicks on one "
            "row cycle its mark in one place of the history and the last leaves it once; Ctrl-click sets any of; Back and a "
            "bookmark give the marks and chips, a tag deleted since struck through; a filter finding nothing keeps its chips; "
            "Clear all, the search box's Clear or the toggle clicked twice before Back arrives goes back once; a list past "
            "the server's cap is refused with a sentence; unsaved edits in the open photo ask before any mark."),
    "V33": ("views", "Somebody tags the photos of a folder just added while the index writes its own fields into their files: a save refused as changed on disk is read again and sent once more naming what the page read, the server writing it when the file's tags and caption are as that, the status line saying so; tags changed by someone else meanwhile, or the bake writing again before the second send, are refused again: the refusal as before, nothing merged and no third send; a photo the page no longer lists, or read again gone or unreadable, is not sent again; the next photo or folder opened meanwhile waits for it; from the refusal until the second send is answered it waits in the page, the write line saying so and closing the tab asking, refused again it waits for Retry until written or dismissed; a caption that renames the file follows the rename; two refusals in a row each go once more; a library view's photo, a grid card's title, Carry forward, a suggestion taken and the Edit Date Taken dialog's (its base naming the date the page read; another program's date refused, #3425) the same."),
    "V34": ("views", "Somebody tags a folder's photos and moves on while they are written: the write line says which "
            "writes are sent (the server finishes them) and which wait in the page (lost if it goes), and the browser's "
            "Leave prompt asks only for those; another folder asks Keep writing (first), Discard them or Stay, Keep "
            "writing opening it and writing them in the background, not asked again on the next folder; rapid switches "
            "give one question, the first folder's; a write refused as changed on disk is read again and sent once more, "
            "one failed waits for Retry; one refused because a photo named by id was renamed elsewhere waits in the "
            "page while that photo is read again; a single save that failed waits for its Retry, closing the tab asking, "
            "the next photo opened, until it is written or dismissed; another library, a new page, offers Stay first or Discard them."),
    "V30": ("views", "On the phone page somebody moves between its screens by the bar of tabs at the foot: a page on a "
            "library opens on Library, one on none shows the list with no bar; each tab is one step of history however "
            "fast it is pressed and none for the tab shown, Back walks back through the tabs to the page's first step, "
            "Library from a level of Browse opens its top, Photos the view the Grid showed last or the whole library, the "
            "Photo screen has no bar, Filter opens its sheet over the Grid (Back closes it, a second Back leaves the Grid), "
            "and a phone turned keeps the bar and the screen."),
    "V31": ("views", "On the phone page somebody filters the library from the Filter sheet over the Grid: two people and a "
            "keyword marked any of and a keyword tapped round to not are one search, its chips above the grid; rapid taps "
            "on one row cycle it in one place of history and a reply for a mark left is dropped; Filter over a group's, a "
            "keyword's or a person's photos opens over the same view, its rows marked any of, nothing read again, the first "
            "mark making it a search in the same place; Back closes the sheet "
            "keeping the filter, a second Back leaves the Grid and Forward gives the marks back; a chip taken off after "
            "the sheet closed stays off through Forward and Back; a reload on the sheet's step and Back keeps its marks; a "
            "row tapped as Show closes the sheet keeps its mark; a filter finding nothing "
            "says so with Remove and Clear all, 0 photos and Show not pressable, and Videos over marks that find only photos "
            "says the kind finds none and offers Both; an address naming a deleted keyword gives "
            "a struck chip; another library is another page with no chip of the first; a section that could not be read "
            "says so with Try again; a filter at the server's cap refuses one more with its sentence and reads nothing; no "
            "cover is asked under the sheet; six chips at 320 px are one row scrolled sideways."),
    "V32": ("views", "Somebody adds a folder from Organize and works on it at once: the page follows the library's queue "
            "whoever queued it, saying Indexing N of M folders, a Suggest run meanwhile carrying the index's progress; it "
            "follows on in a view of the library and back in the folder, where the list kept from before the index is not "
            "shown; when the index ends the Library pane's folders, the open folder and the open view are read again and "
            "the status line keeps the index's closing sentence; rapid folder switches send one ask at a time and read "
            "again only a folder the index holds, a scan out at the end reads again as it lands, a reply for the library "
            "left is dropped, the open folder second in line is read again at its own end only, a cancelled index reads "
            "nothing again, a queue that does not answer says nothing wrong and is asked no more, and nothing is asked "
            "once nothing is queued. The Folders tab names the folder at once, under the folder above it, marked queued "
            "with 0, then indexing with its count filling while the tab is on screen, then its count alone on the same "
            "row; a cancel takes it off, another library's queue never shows, and the filter and a click find it as any "
            "folder."),
    "I1": ("apps", "Switching library in the middle of a folder shows no card of the first."),
    "I2": ("apps", "A write through a server update is waited out and not repeated."),
    "I3": ("apps", "An update by a launch and by an install hands one server over to the next without killing its work."),
    "I4": ("apps", "A library one migration behind is served at start, and a request for one being migrated gets 503 at once."),
    "I5": ("apps", "A page loaded against the real server, not a fake one."),
    "I6": ("apps", "The same flows with and without roots, end to end."),
    "I7": ("apps", "The CLI, the MCP server and the server act on one library at once."),
    "I8": ("apps", "A page save from a stale record is refused, not overwritten."),
    "I9": ("apps", "A page's saved scan of an older shape is read again, not trusted; a write's reply reaches every scan "
                   "this browser keeps of its photos (a subfolder's, a parent's), and one that cannot be written back is forgotten."),
    "I10": ("apps", "A background run that failed overnight says, from its row, what happened and what to do next; TagPup's own work in the way never asks."),
    "I11": ("apps", "On a machine without ffmpeg (or with one that does not run) the server and the CLI's index runs start nothing and say what to install; an install says so and leaves the old server running."),
    "I12": ("apps", "Refresh on a folder syncs it through the library's walk, and a file it finds gone starts the whole sync once, which removes it or follows its move; pressed again while that walks, a share that does not answer, another process's run and a server with no runner each sync or start nothing more and say why in Run now's words."),
    "I13": ("apps", "In Organize somebody selects photos and clips of a folder and clicks Delete: the library view's delete, by path, asked once by the server's count; the folder is read again, the deleted leave the list and the selection and the open photo among them closes onto the next; one a share or an error left stays, a clip stopped for it plays again only if it stayed, another folder or view opened meanwhile is untouched, one delete at a time on the page, and in Just look the button is inert with its sentence."),
    "I14": ("apps", "On a phone the owner opens TagPup's phone page: an address naming no library opens the one this browser opened last while the server lists it, else the list; Back goes up a screen; another library is the phone's page on it; a server updated meanwhile says so in the banner; a list that could not be read says so with Try again; a slow first reply never draws over a newer one."),
    "I15": ("apps", "Somebody tags photos, saves one photo's title and tags, or shifts photos' Date Taken by some minutes, "
            "and the page sends it to the one entry point of a write of photo files: one envelope whatever the op, a "
            "shift journaled as a time shift; an update stopping the batches begins nothing; two "
            "writes at once both land, one after the other; a photo on a share that does not answer is its error at "
            "once and the others are written (one that stops answering part-way ends the change there, what landed listed); a stale record, or an id now naming another file, is refused 409 and nothing is written; a photo "
            "of a folder the library does not hold is named by its path alone."),
    "I16": ("apps", "The test runner answers an ExifTool command it has seen for the same bytes, arguments and ExifTool from "
            "what it kept, starting no process, with the same answer and bytes as ExifTool's; a changed ExifTool, config "
            "or scratch in the way is a miss; two processes at once, an entry cut off by a kill, the cache deleted "
            "mid-run each end with one whole entry; a file's dates are never kept; the kept answers stay within a cap; "
            "TagPup itself never replays."),
}

#: scenario id -> its scenario files; a file listed under more than one runs more than one scenario (the first is its owner's).
_SCENARIO_FILES = {
    "D1": "test_a_library_from_a_newer_tagpup test_newer_library_guard_in_a_long_lived_process",
    "D2": "test_journal test_maintenance test_undo_after_every_later_kind test_a_chunked_tag_write_is_one_line_of_history",
    # test_a_photo_written_by_its_own_editors runs F1 too (the own writer's saves); its crash settles are D3's.
    "D3": (
        "test_journal test_a_crash_part_way_is_settled_at_the_next_start test_a_photo_written_by_its_own_editors "
        "test_a_patch_that_grows_a_file_is_settled_by_its_bytes test_a_long_change_cut_off_is_settled_in_chunks"
    ),
    "D4": "test_snapshots",
    "D5": "test_derived_follow_writes test_photo_people_follow_their_sources",
    "D6": "test_a_librarys_settings_are_its_own",
    "D8": "test_migrations",
    # test_an_index_is_one_run_under_its_sync is Data's file (D9, the row); its index-queue scenarios (F11) are reviewed by
    # the Files owner with the integration owner.
    "D9": "test_runs_nest_and_say_how_far_they_got test_an_index_is_one_run_under_its_sync",
    "D10": "test_removing_a_folder_from_either_app",
    "D7": (
        "test_library_roots test_roots_at_scale test_roots_ingress test_roots_location test_roots_scenarios "
        "test_roots_store"
    ),
    "F1": (
        "test_a_file_holds_what_exiftool_kept test_a_photo_patched_in_place "
        "test_a_rename_stopped_part_way test_smart_rename_one_photo smart-rename-one-photo "
        "test_file_changes_one_at_a_time test_file_journal test_rotate_crash_settles_with_the_boxes test_rotate_is_journaled test_rotate_keeps_the_photo "
        "test_tag_a_photo_read_it_back_and_undo test_time_shift shifting-a-folders-date-taken test_xmp_packet_edits_what_it_owns "
        "test_iptc_block_edits_what_it_owns test_exif_block_edits_what_it_owns test_segment_edits_choose_what_to_write "
        "test_a_photo_written_by_its_own_editors test_the_two_writers_compared_on_copies "
        "test_a_photos_title_and_caption test_tag_writes_keep_the_index_true test_a_tag_added_touches_its_file_no_more_than_it_must"
    ),
    "F2": "test_no_writes_to_damaged_photos test_rows_only_in_folders_added test_an_index_reads_the_next_photos_ahead",
    "F3": (
        "test_batches test_bulk_edits test_bulk_edits_real_exiftool test_bulk_edits_stalled_exiftool "
        "test_bulk_writes_start_from_the_file test_a_bake_is_planned_from_the_library "
        "test_an_index_reads_a_bake "
        "test_the_index_writes_the_bake test_a_caption_repeating_the_title_is_cleared "
        "test_a_long_change_cut_off_is_settled_in_chunks"
    ),
    "F4": "test_folder_watcher test_one_walk_of_a_library_at_a_time test_sync",
    "F5": "test_scenario_two_libraries_share_a_folder",
    "F6": "test_index_progress_end_to_end test_partial_writes_leave_rows_unread",
    "F7": "test_a_share_asleep test_a_share_that_drops_mid_walk test_a_share_that_wakes",
    "F8": (
        "test_bulk_delete test_delete_a_photo test_just_look_bin_and_long_names test_just_look_delete_and_rename_rules "
        "test_just_look_edits test_a_delete_cut_short test_a_bulk_delete_stopped_part_way"
    ),
    "F9": "test_roots_old_place test_roots_pinned_runs test_a_root_moved_during_a_long_undo_or_settle",
    "F10": "test_one_photo_whatever_its_spelling",
    "F11": (
        "test_an_index_is_one_run_under_its_sync test_index_progress_end_to_end test_new_faces_are_matched_after_an_index test_an_index_reads_the_next_photos_ahead "
        "test_an_index_reads_a_bake test_a_machine_without_a_card"
    ),
    "C1": "test_a_clip_is_indexed_as_a_video",
    "C2": (
        "test_a_clip_patched_in_place test_an_adobe_clip_packet_edited_and_read_back "
        "test_a_clip_tagged_through_the_journal test_a_clip_titled_in_quicktime test_a_clip_tag_undone "
        "test_a_clip_patch_cut_short test_a_clip_is_not_turned"
    ),
    "C3": "test_a_clip_renamed",
    "C4": "test_a_clip_deleted test_a_delete_when_the_recycle_bin_is_full",
    "C5": "test_a_clip_dated_through_the_journal test_a_clip_dated_by_its_name",
    "C6": "test_a_tag_tree_edit_of_a_tag_a_clip_holds",
    "C7": "test_a_clip_converted clip-convert",
    "C8": "test_playing_a_video test_what_a_clip_needs_to_play",
    "C9": "test_a_clip_shows_its_poster",
    "I1": "scenario-switching-library-mid-folder",
    "I2": "scenario-write-through-a-server-update test_server_drain",
    "I3": "test_install_app test_install_hands_over test_launcher_hands_over",
    "I4": "test_server_migrates_at_start",
    "I5": "test_pages_against_the_real_server",
    "I6": "test_roots_scenarios",
    "I7": "test_mcp_server test_one_library_three_programs",
    "I8": "test_stale_record_precondition test_what_the_server_keeps_follows_a_rename",
    "I9": "scenario-a-scan-kept-by-an-older-page scenario-a-write-reaches-every-scan-kept",
    "I10": "test_a_failed_run_says_what_to_do",
    "I11": "test_ffmpeg_is_a_prerequisite",
    "I12": "test_a_folders_refresh_syncs_it",
    "I13": "scenario-deleting-the-selected-photos-of-a-folder",
    "I14": "scenario-opening-the-phone-page",
    "I15": "test_a_write_through_the_one_entry_point",
    "I16": "test_exiftool_answers_kept_for_tests",
    "P1": "test_people_by_id_at_once",
    "P2": (
        "test_a_cropped_picture_gets_new_faces test_bulk_faces_and_tags test_cluster_faces_on_a_crowded_library test_clustering_meets_naming "
        "test_face_people_follow_file_changes "
        "test_faces_and_tags_in_step test_faces_from_tags_in_a_folder test_person_filing_table "
        "test_tags_from_faces_in_a_folder"
    ),
    "P3": "test_reexamine_folder",
    "P4": "test_a_person_count_reads_only_their_photos test_name_faces_routes test_naming_faces_job",
    "P5": "test_tag_tree_edits_are_batches test_taxonomy_lifecycle",
    "P6": "test_two_cousins_called_alike",
    "P7": "test_identify_ignore_assign_restore_undo",
    "P10": "test_a_library_indexes_and_suggests_with_its_face_model",
    "P11": "test_a_face_gets_one_nearest_person_everywhere",
    "P12": "test_a_face_in_the_background_is_one_answer",
    "P13": "test_a_clips_people_are_the_people_its_frames_show",
    "V19": "scenario-tagging-keeps-the-faces-in-place",
    "V1": "test_library_views_scenarios test_a_view_of_photos_or_videos_alone scenario-a-view-of-photos-or-videos-alone "
          "test_photos_with_no_tag",
    "V2": "test_library_search",
    # test_a_clip_is_matched_on_its_best_frame: Suggest's neighbours, a clip among them at its best frame (M2).
    "V3": "test_analyse_only_looks_are_kept test_analyse_only_suggest test_suggest_then_apply_all "
          "test_a_clip_is_matched_on_its_best_frame",
    "V4": "test_one_gpu_at_a_time test_one_gpu_jobs",
    "V5": "test_selection_scenarios test_photos_with_no_tag",
    "V6": "test_models_released_when_idle",
    "V7": "test_selection_scenarios test_bulk_edits test_a_view_of_photos_or_videos_alone scenario-a-view-of-photos-or-videos-alone",
    "V8": "test_library_views_scenarios",
    "V9": "test_suggest_then_apply_all",
    "V11": "test_switching_a_librarys_face_model",
    "V12": "test_switching_a_librarys_photo_model",
    "V13": "test_making_a_new_photo_models_vectors",
    "V14": "test_retiring_the_old_photo_models_vectors",
    "V17": "test_measuring_the_neighbour_cut_for_clips",
    "V18": "scenario-tabbing-through-the-main-page",
    "V20": "scenario-taking-tags-off-the-open-photo",
    "V21": "scenario-refreshing-a-folder",
    "V22": "scenario-tagging-many-photos-at-once",
    "V23": "scenario-browsing-on-the-phone",
    "V24": "scenario-looking-through-photos-on-the-phone",
    "V25": "scenario-looking-at-a-photo-on-the-phone",
    "V26": "scenario-editing-a-photo-on-the-phone",
    "V27": "scenario-moving-on-while-a-photo-saves",
    "V28": "test_filtering_the_library_by_rows scenario-filtering-the-library-by-rows",
    "V29": "scenario-filtering-the-library-in-filter-mode",
    "V30": "scenario-moving-between-tabs-on-the-phone",
    "V31": "scenario-filtering-on-the-phone",
    "V32": "scenario-working-on-a-new-folder test_queued_folders_in_the_folders_tab "
           "scenario-queued-folders-in-the-folders-tab",
    "V33": "scenario-saving-while-the-index-writes",
    "V34": "scenario-tagging-a-folder-and-moving-on-without-losing-anything",
}

#: owner -> kind -> the unit, migration and guard files, a .mjs without its ".test.mjs".
_FILES = {
    "data": {
        "unit": (
            "tag-rules "
            "test_backups_are_kept_to_five test_bulk_scripts_back_up "
            "test_compact_gives_back_free_space test_derived_batches test_derived_tables test_doctor "
            "test_every_face_has_a_photo_row test_face_save_retries_a_lock "
            "test_faces_detected_record "
            "test_folder_membership test_journal_undo_refusals "
            "test_mcp_photo_against_file test_path_locker "
            "test_roots_sample test_roots_unplaced test_search_words "
            "test_services_refuse_what_the_rules_refuse "
            "test_sync_records test_the_tree_is_the_librarys test_validation test_view_indexes"
        ),
        "migration": "test_a_library_that_cannot_be_migrated_is_still_served test_schema",
        "guard": (
            "test_db_access test_derived_writers test_every_change_kind_has_an_undo_check test_journal_keys_and_cascades "
            "test_photo_rows test_sandbox_roots"
        ),
    },
    "people": {
        "unit": (
            "face-strip grid-build-progress identify-faces identify-faces-cluster-buttons identify-faces-counts "
            "identify-faces-in-place-removal library-sort-and-organize-header name-faces organize-faces person-directory "
            "person-faces person-filing-table person-names person-tag-form review-dialog review-from-activity "
            "selection-suggests-who-the-faces-look-like show-matched-toggle tag-editor-in-place tag-order tag-view tag-vocabulary tagpup-people-by-id "
            "tagpup-people-lists-by-id tags-shown-alphabetical "
            "test_answers_carry_their_person test_automatch_references test_automatch_writes_once test_cluster_candidates "
            "test_clustering_evidence test_every_tag_has_an_id "
            "test_excluded_faces_stay_unnamed test_face_click_reads_named_faces_once test_face_clustering_rules "
            "test_face_exclusion test_face_roots_are_the_trees "
            "test_faces_from_tags test_folder_scope_roots test_group_tag_is_not_a_person "
            "test_clustering_keeps_a_name_the_tags_confirm "
            "test_identify_cache_keeps_others_writes test_identify_cache_sees_every_change test_identify_queries "
            "test_index_resolves_people test_journal_through_the_services "
            "test_new_person_reads_the_pool_once test_organize_faces test_people_by_id_tree "
            "test_people_representative_faces "
            "test_person_face_samples test_person_ids "
            "test_person_rename_reaches_the_files "
            "test_reindex_keeps_face_decisions test_server_lists_are_alphabetical test_service_faces test_service_tags "
            "test_shared_taxonomy_routes test_store_taxonomy_add_path test_tag_order "
            "test_tag_sort_key_edges test_tag_view_api test_tags_are_checked_where_they_are_set test_tags_from_faces "
            "test_taxonomy_edits_report_what_they_wrote "
            "test_trace_file_per_library test_tuner_photo_copy_upright test_tuner_reads "
            "test_tuner_server_api test_vocabulary tuner-assign-label-tells-the-truth "
            "tuner-face-zoom tuner-hidden-people-exist tuner-lists-alphabetical tuner-new-person-group "
            "tuner-people-by-face tuner-people-by-id tuner-reexamine-folder tuner-refresh-keeps-the-grid-loading "
            "tuner-select-all-is-linear tuner-starts-last tuner-suggestions-belong-to-their-grid "
            "tuner-undo-puts-faces-back"
        ),
        "guard": (
            "common-modules test_face_values_have_one_owner test_face_writes_go_through_the_assignment "
            "test_no_person_row_without_an_id test_person_ids_single_owner test_write_routes_are_classified"
        ),
    },
    "files": {
        "unit": (
            "bulk-edit bulk-writes-queue just-look-edits path-helpers photo-write-queue picture-check-says-so rename-failed retried-save "
            "rotate-refreshes-thumbnails smart-rename-count "
            "test_a_bake_is_used_only_whole test_a_face_box_turned_upright_and_back "
            "test_a_photo_is_known_by_its_picture "
            "test_a_row_describes_its_file_one_way "
            "test_batches_go_through_the_model "
            "test_census_of_jpeg_segments "
            "test_dates test_derived_file_changes "
            "test_exiftool_encoding test_exiftool_says_why test_exiftool_session "
            "test_index_adds_the_folder_it_is_given "
            "test_index_startup_reads test_jobs_indexing "
            "test_keyword_fields test_lock_owners "
            "test_metadata_extraction test_minor_errors_in_a_journaled_write test_one_answer_to_the_librarys_folders "
            "test_paths "
            "test_paths_roots test_photo_identity test_photo_meta "
            "test_relink_checks_the_destination test_remove_folder_lists_the_library "
            "test_renaming "
            "test_service_indexing test_service_page_images "
            "test_service_smart_rename "
            "test_settling_carries_vectors "
            "test_thumbnails "
            "test_what_counts_as_a_photo "
            "tuner-index-progress unsaved-edits write-queue-status"
        ),
        "guard": "test_photo_looks_have_one_owner test_the_block_editors_have_one_caller the-write-line-has-one-writer",
    },
    "media": {
        "unit": "test_container_layout test_frames_of_a_video video-viewer",
    },
    "views": {
        "unit": (
            "details-panel folder-view-clips grid-keys grid-window library-header-strip-toolbar library-moves library-photo-back organize-step-keeps-clear-of-header "
            "library-selection library-selection-jumps library-tally library-view looking-suggestions navigator "
            "navigator-multi photo-camera photo-details-layout photo-position photo-suggestions-panel search-page selection "
            "selection-follows-the-photo selection-panel selection-suggestions stale-cards suggest-cancel suggestions-follow-a-saved-photo "
            "tagging-by-keyboard tagging-flow carry-tags-forward-clips tuner-from-the-keyboard "
            "test_a_search_keeps_no_thread_buffers "
            "test_camera_words test_caption_order test_chip_keys_follow_the_server test_view_rules_follow_the_server "
            "test_clip_sees_photos_upright test_embeddings_follow_their_photo "
            "test_every_library_keeps_its_saved_suggestions "
            "test_index_reload_reuses_its_connection test_jobs_suggestions "
            "test_library_covers test_library_union_and_order test_library_view test_library_view_plans "
            "test_models_in_use_survive_an_unload "
            "test_one_value_for_a_suggested_tag "
            "test_person_tag_edges test_photo_index_keeps_only_vectors test_pipeline_measurements "
            "test_selection test_selection_tally "
            "test_suggest_index_stays_current test_suggest_reads_people_once test_suggest_respects_face_decisions "
            "test_suggest_status_poll_is_a_snapshot test_suggest_status_replies test_suggester_scoring "
            "test_suggestions_keep_hierarchy test_tag_usage_from_photo_tags "
            "test_scrfd_adaface_faces "
            "test_the_runtime_keeps_models_by_settings test_the_runtime_reaches_suggest test_thumbs_cli test_vector_index "
            "test_thumbnail_routes test_zero_shot_skips_people vgrid"
        ),
        "guard": "a-written-photos-copies-have-one-writer test_gpu_single_owner test_no_test_loads_a_model the-index-queue-has-one-follower "
                 "write-replies",
    },
    "apps": {
        "unit": (
            "activity-page add-folder-dialog apply-all-failure-undo banner-cost batch-strip cache-version database-routing "
            "delete-key delete-while-folder-changes dialog-frames dialogs-keep-the-keys dom-output video-keys folder-keeps-history-position "
            "folder-manager folder-opening gear history-dialog image-zoom images-after-an-update library-memory a-new-library-opens-manage-folders "
            "library-picker open-photo page-state progress-polling roots-dialog server-replaced server-update "
            "settings-dialog stale-record test_a_request_is_decoded_once "
            "test_activity_page_replies test_activity_routes test_add_findings test_retro_metrics test_stale_findings test_an_empty_folder_is_scanned_again test_background_tasks "
            "test_background_work_is_waited_for test_batch_strip_replies test_browser_opens_once test_config test_page_record_fields "
            "test_dark_selects test_database_context_isolation test_folder_scan_follows_the_library test_gzip_json "
            "test_heavy_imports_stay_lazy test_history_routes test_indexing_names_a_real_control "
            "test_inspect_reads_only_the_photos_asked_about test_inspect_uses_the_stores_mtime_rule "
            "test_library "
            "test_library_registry_race test_local_server_socket test_log_runs test_logs "
            "test_mcp_config_resolves "
            "test_code_tool test_coverage_map test_patch_tool test_photo_open test_recurring_jobs test_result "
            "test_every_route_answers_this_pc_or_says_why test_roots_ingress_keys_cover_the_routes test_roots_map "
            "test_a_photo_made_once_is_copied_for_each_test test_run_tests_keeps_failures test_server_startup test_serving_photos test_suggest_thread_keeps_its_library test_tagpup_routes "
            "test_web_app undo-from-reply validation"
        ),
        "guard": (
            "context-budget dom-contract merge-budget no-heredoc-escapes page-modules page-shape plan-first read-budget "
            "test_config_single_owner test_database_axis_guard test_every_test_file_has_its_size "
            "test_fixtures_make_photo_rows_like_the_indexer test_layers test_lint test_no_test_waits_a_fixed_time "
            "test_page_module_names test_processes_single_owner test_rules_have_one_owner test_sandbox_has_all_the_code "
            "test_spec_accuracy test_sync_entry_points "
            "test_tests_have_homes_of_their_own test_the_test_map test_verify_workflow_is_sandboxed"
        ),
    },
}

def _build():
    files = {}

    def put(names, owner, kind, ids=()):
        for name in names.split():
            name = name if name.startswith("test_") else name + ".test.mjs"
            # A second scenario of one file: the owner stays its first scenario's. The name's own, never carried onto
            # the names after it in the string.
            first, before = (files[name][0], files[name][2]) if name in files and kind == "scenario" else (owner, ())
            files[name] = (first, kind, before + ids)
    for owner, kinds in _FILES.items():
        for kind, names in kinds.items():
            put(names, owner, kind)
    for sid, names in _SCENARIO_FILES.items():
        put(names, SCENARIOS[sid][0], "scenario", (sid,))
    return files


FILES = _build()

#: The sizes, smallest first: `tools/run_tests.py --size sqlite` runs pure and sqlite.
SIZE_ORDER = ("pure", "sqlite", "subprocess", "browser")
#: size -> the test files of it (tests/test_every_test_file_has_its_size.py prints these lines to paste).
_SIZES = {
    "pure": (
        "test_a_face_box_turned_upright_and_back test_a_patch_that_grows_a_file_is_settled_by_its_bytes "
        "test_a_search_keeps_no_thread_buffers test_background_work_is_waited_for "
        "test_browser_opens_once test_clip_sees_photos_upright test_cluster_candidates test_code_tool test_config "
        "test_config_single_owner test_container_layout test_derived_writers "
        "test_every_change_kind_has_an_undo_check test_every_test_file_has_its_size "
        "test_exif_block_edits_what_it_owns test_face_values_have_one_owner "
        "test_face_writes_go_through_the_assignment test_gpu_single_owner test_indexing_names_a_real_control "
        "test_iptc_block_edits_what_it_owns test_keyword_fields test_layers "
        "test_library_registry_race test_local_server_socket test_models_in_use_survive_an_unload "
        "test_no_test_loads_a_model test_no_test_waits_a_fixed_time test_one_value_for_a_suggested_tag "
        "test_page_module_names test_page_record_fields test_patch_tool test_path_locker "
        "test_person_ids_single_owner test_person_tag_edges test_photo_looks_have_one_owner test_photo_meta "
        "test_result test_roots_map test_scrfd_adaface_faces test_segment_edits_choose_what_to_write "
        "test_stale_findings test_suggest_status_poll_is_a_snapshot test_tag_order test_tag_sort_key_edges "
        "test_the_block_editors_have_one_caller test_the_test_map test_trace_file_per_library test_vector_index "
        "test_view_rules_follow_the_server test_vocabulary test_what_counts_as_a_photo "
        "test_xmp_packet_edits_what_it_owns test_zero_shot_skips_people "
    ),
    "sqlite": (
        "test_a_clip_is_matched_on_its_best_frame test_a_person_count_reads_only_their_photos "
        "test_a_share_that_drops_mid_walk test_a_share_that_wakes test_automatch_references "
        "test_automatch_writes_once test_background_tasks test_backups_are_kept_to_five test_bulk_scripts_back_up test_camera_words "
        "test_census_of_jpeg_segments test_chip_keys_follow_the_server test_cluster_faces_on_a_crowded_library "
        "test_clustering_evidence test_clustering_keeps_a_name_the_tags_confirm test_clustering_meets_naming "
        "test_compact_gives_back_free_space test_dates test_db_access test_derived_batches "
        "test_derived_tables test_doctor "
        "test_every_face_has_a_photo_row test_every_tag_has_an_id test_face_clustering_rules "
        "test_face_save_retries_a_lock test_faces_detected_record "
        "test_faces_from_tags test_fixtures_make_photo_rows_like_the_indexer test_folder_scope_roots "
        "test_group_tag_is_not_a_person test_identify_cache_sees_every_change "
        "test_index_adds_the_folder_it_is_given test_index_reload_reuses_its_connection "
        "test_index_resolves_people test_inspect_reads_only_the_photos_asked_about "
        "test_jobs_indexing test_jobs_suggestions test_journal "
        "test_journal_keys_and_cascades test_journal_through_the_services test_journal_undo_refusals test_library "
        "test_library_roots test_library_view test_library_view_plans test_lock_owners test_maintenance "
        "test_making_a_new_photo_models_vectors test_measuring_the_neighbour_cut_for_clips test_naming_faces_job "
        "test_one_walk_of_a_library_at_a_time test_paths test_person_ids "
        "test_photo_index_keeps_only_vectors test_photo_people_follow_their_sources test_photo_rows "
        "test_relink_checks_the_destination test_retiring_the_old_photo_models_vectors test_roots_at_scale "
        "test_roots_sample test_roots_unplaced test_rules_have_one_owner test_runs_nest_and_say_how_far_they_got "
        "test_sandbox_roots test_schema test_search_words test_server_lists_are_alphabetical test_service_faces "
        "test_service_page_images test_snapshots "
        "test_store_taxonomy_add_path test_suggest_index_stays_current test_suggest_reads_people_once "
        "test_suggest_respects_face_decisions test_suggest_status_replies test_suggester_scoring "
        "test_suggestions_keep_hierarchy test_switching_a_librarys_face_model test_sync_records "
        "test_the_runtime_keeps_models_by_settings test_the_tree_is_the_librarys test_thumbs_cli "
        "test_view_indexes "
    ),
    "subprocess": (
        "test_derived_follow_writes test_embeddings_follow_their_photo test_face_roots_are_the_trees "
        "test_inspect_uses_the_stores_mtime_rule test_people_by_id_tree test_service_smart_rename test_service_tags "
        "test_thumbnails "
        "test_a_bake_is_planned_from_the_library test_coverage_map test_a_bake_is_used_only_whole "
        "test_a_bulk_delete_stopped_part_way test_a_caption_repeating_the_title_is_cleared "
        "test_a_chunked_tag_write_is_one_line_of_history test_a_clip_converted test_a_clip_dated_by_its_name "
        "test_a_clip_dated_through_the_journal test_a_clip_deleted test_a_clip_is_indexed_as_a_video "
        "test_a_clip_is_not_turned test_a_clip_patch_cut_short test_a_clip_patched_in_place test_a_clip_renamed "
        "test_a_clip_shows_its_poster test_a_clip_tag_undone test_a_clip_tagged_through_the_journal "
        "test_a_clip_titled_in_quicktime test_a_clips_people_are_the_people_its_frames_show "
        "test_a_crash_part_way_is_settled_at_the_next_start test_a_cropped_picture_gets_new_faces "
        "test_a_delete_cut_short test_a_delete_when_the_recycle_bin_is_full "
        "test_a_face_gets_one_nearest_person_everywhere test_a_face_in_the_background_is_one_answer "
        "test_a_failed_run_says_what_to_do test_a_file_holds_what_exiftool_kept test_a_folders_refresh_syncs_it "
        "test_a_library_from_a_newer_tagpup test_a_library_indexes_and_suggests_with_its_face_model "
        "test_a_library_that_cannot_be_migrated_is_still_served test_a_librarys_settings_are_its_own "
        "test_a_long_change_cut_off_is_settled_in_chunks test_a_machine_without_a_card "
        "test_a_photo_is_known_by_its_picture test_a_photo_made_once_is_copied_for_each_test "
        "test_a_photo_patched_in_place test_a_photo_written_by_its_own_editors test_a_photos_title_and_caption "
        "test_a_rename_stopped_part_way test_a_request_is_decoded_once test_a_row_describes_its_file_one_way "
        "test_a_share_asleep test_a_tag_added_touches_its_file_no_more_than_it_must "
        "test_a_tag_tree_edit_of_a_tag_a_clip_holds test_a_view_of_photos_or_videos_alone "
        "test_a_write_through_the_one_entry_point test_activity_page_replies test_activity_routes "
        "test_add_findings test_an_adobe_clip_packet_edited_and_read_back test_an_empty_folder_is_scanned_again "
        "test_an_index_is_one_run_under_its_sync test_an_index_reads_a_bake "
        "test_an_index_reads_the_next_photos_ahead test_analyse_only_looks_are_kept test_analyse_only_suggest "
        "test_answers_carry_their_person test_batch_strip_replies test_batches test_batches_go_through_the_model "
        "test_bulk_delete test_bulk_edits test_bulk_edits_real_exiftool test_bulk_edits_stalled_exiftool "
        "test_bulk_faces_and_tags test_bulk_writes_start_from_the_file test_caption_order test_dark_selects "
        "test_database_axis_guard test_database_context_isolation test_delete_a_photo test_derived_file_changes "
        "test_every_library_keeps_its_saved_suggestions test_every_route_answers_this_pc_or_says_why "
        "test_excluded_faces_stay_unnamed test_exiftool_answers_kept_for_tests test_exiftool_encoding "
        "test_exiftool_says_why test_exiftool_session test_face_click_reads_named_faces_once test_face_exclusion "
        "test_face_people_follow_file_changes test_faces_and_tags_in_step test_faces_from_tags_in_a_folder "
        "test_ffmpeg_is_a_prerequisite test_file_changes_one_at_a_time test_file_journal "
        "test_filtering_the_library_by_rows test_folder_membership test_folder_scan_follows_the_library "
        "test_folder_watcher test_frames_of_a_video test_gzip_json test_heavy_imports_stay_lazy "
        "test_history_routes test_identify_cache_keeps_others_writes test_identify_ignore_assign_restore_undo "
        "test_identify_queries test_index_progress_end_to_end test_index_startup_reads test_install_app "
        "test_install_hands_over test_just_look_bin_and_long_names test_just_look_delete_and_rename_rules "
        "test_just_look_edits test_launcher_hands_over test_library_covers test_library_search "
        "test_library_union_and_order test_library_views_scenarios test_lint test_log_runs test_logs "
        "test_mcp_config_resolves test_mcp_photo_against_file test_mcp_server test_metadata_extraction "
        "test_migrations test_minor_errors_in_a_journaled_write test_models_released_when_idle "
        "test_name_faces_routes test_new_faces_are_matched_after_an_index test_new_person_reads_the_pool_once "
        "test_newer_library_guard_in_a_long_lived_process test_no_person_row_without_an_id "
        "test_no_writes_to_damaged_photos test_one_answer_to_the_librarys_folders test_one_gpu_at_a_time "
        "test_queued_folders_in_the_folders_tab "
        "test_one_gpu_jobs test_one_library_three_programs test_one_photo_whatever_its_spelling "
        "test_organize_faces test_partial_writes_leave_rows_unread test_paths_roots test_people_by_id_at_once "
        "test_people_representative_faces test_person_face_samples test_person_filing_table "
        "test_person_rename_reaches_the_files test_photo_identity test_photo_open test_photos_with_no_tag "
        "test_pipeline_measurements test_playing_a_video test_processes_single_owner test_recurring_jobs "
        "test_reexamine_folder test_reindex_keeps_face_decisions test_remove_folder_lists_the_library "
        "test_removing_a_folder_from_either_app test_renaming test_retro_metrics test_roots_ingress "
        "test_roots_ingress_keys_cover_the_routes test_roots_location test_roots_old_place test_roots_pinned_runs "
        "test_a_root_moved_during_a_long_undo_or_settle "
        "test_roots_scenarios test_roots_store test_rotate_crash_settles_with_the_boxes test_rotate_is_journaled "
        "test_rotate_keeps_the_photo test_rows_only_in_folders_added test_run_tests_keeps_failures "
        "test_sandbox_has_all_the_code test_scenario_two_libraries_share_a_folder test_selection "
        "test_selection_scenarios test_selection_tally test_server_drain test_server_migrates_at_start "
        "test_server_startup test_service_indexing test_services_refuse_what_the_rules_refuse test_serving_photos "
        "test_settling_carries_vectors test_shared_taxonomy_routes test_smart_rename_one_photo test_spec_accuracy "
        "test_stale_record_precondition test_suggest_then_apply_all test_suggest_thread_keeps_its_library "
        "test_switching_a_librarys_photo_model test_sync test_sync_entry_points "
        "test_tag_a_photo_read_it_back_and_undo test_tag_tree_edits_are_batches test_tag_usage_from_photo_tags "
        "test_tag_view_api test_tag_writes_keep_the_index_true test_tagpup_routes "
        "test_tags_are_checked_where_they_are_set test_tags_from_faces test_tags_from_faces_in_a_folder "
        "test_taxonomy_edits_report_what_they_wrote test_taxonomy_lifecycle test_tests_have_homes_of_their_own "
        "test_the_index_writes_the_bake test_the_runtime_reaches_suggest test_the_two_writers_compared_on_copies "
        "test_thumbnail_routes test_time_shift test_tuner_photo_copy_upright test_tuner_reads "
        "test_tuner_server_api test_two_cousins_called_alike test_undo_after_every_later_kind test_validation "
        "test_verify_workflow_is_sandboxed test_web_app test_what_a_clip_needs_to_play "
        "test_what_the_server_keeps_follows_a_rename test_write_routes_are_classified "
    ),
    "browser": (
        "test_pages_against_the_real_server "
    ),
}
SIZES = {name: size for size, names in _SIZES.items() for name in names.split()}


def files_of(owner, kind=None):
    """The Python test modules (test_x) of an owner, or of one kind of it, sorted."""
    return sorted(n for n, (o, k, _ids) in FILES.items() if o == owner and n.startswith("test_") and kind in (None, k))


def page_files_of(owner):
    """The .test.mjs files of an owner, sorted."""
    return sorted(n for n, (o, _k, _ids) in FILES.items() if o == owner and n.endswith(".test.mjs"))


def size_of(name):
    """A test file's size (a module test_x, or a page test x.test.mjs: browser); a file not in SIZES is browser
    until the guard gives it its line."""
    return SIZES.get(name, "browser")


def within(name, size):
    """Is the file `name` of `size` or smaller?"""
    return SIZE_ORDER.index(size_of(name)) <= SIZE_ORDER.index(size)
