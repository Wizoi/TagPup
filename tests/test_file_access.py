"""The file access check (tagpup.services.file_access; docs/ARCHITECTURE.md, "File access check"): what it
decides from what Windows says -- fed the shapes this PC gave (Get-MpComputerStatus readable, the
exclusions "N/A: Must be an administrator", SecurityCenter2's products, the Search scope rules) -- and what it
never does: it changes no setting, spawns nothing but through tagpup.core.processes, and a finding that
fails does not hide the others.
"""
import os
import re
import sys
import time
import unittest
from unittest import mock

from tagpup.services import file_access as fa

DATA = r"D:\TagPup\data"
PHOTOS = r"D:\Training\Pictures"

# The shapes measured on the owner's PC, 2026-10-03 (read-only).
DEFENDER_ON = {"RealTimeProtectionEnabled": True, "OnAccessProtectionEnabled": True, "BehaviorMonitorEnabled": True,
               "IsTamperProtected": True, "AntivirusEnabled": True}
NOT_ADMIN = {"DisableScanningNetworkFiles": False, "ExclusionPath": "N/A: Must be an administrator to view exclusions",
             "ScanAvgCPULoadFactor": 50}
DEFENDER_PRODUCT = {"displayName": "Windows Defender", "productState": 397568}


def shell(status=DEFENDER_ON, preference=NOT_ADMIN, products=(DEFENDER_PRODUCT,)):
    return {"status": status, "preference": preference, "products": list(products)}


def probes(**replaced):
    base = dict(powershell=lambda: shell(), registry_exclusions=lambda: None, search_rules=lambda: [],
                not_indexed=lambda folder: False, unmarked_files=lambda folder: (5, 0), synced_folders=lambda: [], running=lambda: [],
                on_a_share=lambda place: False)
    base.update(replaced)
    return fa.Probes(**base)


def run(places=(PHOTOS,), **replaced):
    result = fa.check(DATA, places, probes=probes(**replaced))
    return {each["id"].split(":")[0]: each for each in result["findings"]}, result


class Defender(unittest.TestCase):
    def test_exclusions_that_cannot_be_read_are_a_warning_with_the_commands_to_run(self):
        found, _ = run()
        one = found["defender-exclusions"]
        self.assertEqual("warn", one["level"])
        self.assertEqual("info", found["defender"]["level"])
        self.assertIn("Add-MpPreference -ExclusionPath 'D:\\TagPup\\data'", one["commands"])
        self.assertIn("Get-MpPreference | Select-Object -ExpandProperty ExclusionPath", one["commands"])
        self.assertTrue(any("trade-off" in line for line in one["commands"]), "the photo folder is marked a trade-off")
        self.assertIn("Add-MpPreference -ExclusionPath 'D:\\Training\\Pictures'", one["commands"])
        self.assertIn("administrator", one["what_to_do"])

    def test_a_path_with_a_quote_is_quoted_for_powershell(self):
        found = fa.check(r"D:\Kid's Photos\data", [], probes=probes())
        commands = [f for f in found["findings"] if f["id"].startswith("defender-exclusions")][0]["commands"]
        self.assertIn("Add-MpPreference -ExclusionPath 'D:\\Kid''s Photos\\data'", commands)

    def test_readable_exclusions_that_cover_everything_are_ok(self):
        found, _ = run(registry_exclusions=lambda: ["D:\\TagPup", "d:\\training"])
        self.assertEqual("ok", found["defender-exclusions"]["level"])

    def test_readable_exclusions_from_get_mppreference_when_elevated(self):
        elevated = dict(NOT_ADMIN, ExclusionPath=["D:\\TagPup\\data", "D:\\Training\\Pictures"])
        found, _ = run(powershell=lambda: shell(preference=elevated))
        self.assertEqual("ok", found["defender-exclusions"]["level"])

    def test_elevated_and_nothing_excluded_is_readable_and_a_warning(self):
        found, _ = run(powershell=lambda: shell(preference=dict(NOT_ADMIN, ExclusionPath=None)))
        self.assertEqual("warn", found["defender-exclusions"]["level"])
        self.assertIn("Not excluded", found["defender-exclusions"]["why"])

    def test_readable_and_not_covering_names_the_place(self):
        found, _ = run(registry_exclusions=lambda: ["D:\\TagPup\\data"])
        one = found["defender-exclusions"]
        self.assertEqual("warn", one["level"])
        self.assertEqual([PHOTOS], one["places"])
        self.assertIn(PHOTOS, one["why"])
        self.assertNotIn("'D:\\TagPup\\data'", " ".join(one["commands"]), "the data folder is covered: not asked again")

    def test_a_sibling_with_the_same_start_is_not_covered(self):
        found, _ = run(registry_exclusions=lambda: ["D:\\TagPup\\dat", "D:\\Training\\Pic"])
        self.assertEqual("warn", found["defender-exclusions"]["level"])
        self.assertEqual([DATA, PHOTOS], found["defender-exclusions"]["places"])

    def test_real_time_scanning_off_is_ok_and_asks_nothing_more(self):
        found, _ = run(powershell=lambda: shell(status=dict(DEFENDER_ON, RealTimeProtectionEnabled=False,
                                                            OnAccessProtectionEnabled=False)))
        self.assertEqual("ok", found["defender"]["level"])
        self.assertNotIn("defender-exclusions", found)

    def test_powershell_missing_is_could_not_check_and_not_an_error(self):
        found, result = run(powershell=lambda: None)
        self.assertEqual("info", found["defender"]["level"])
        self.assertIn("could not be checked", found["defender"]["title"])
        self.assertFalse(result["facts"]["powershell"])
        self.assertIn("windows-search", found, "the rest is still checked")

    def test_network_scanning_matters_only_for_a_share(self):
        found, _ = run()
        self.assertNotIn("defender-network", found)
        found, _ = run(places=[r"\\nas\photos"], on_a_share=lambda place: True)
        self.assertEqual("warn", found["defender-network"]["level"])
        found, _ = run(places=[r"\\nas\photos"], on_a_share=lambda place: True,
                       powershell=lambda: shell(preference=dict(NOT_ADMIN, DisableScanningNetworkFiles=True)))
        self.assertEqual("ok", found["defender-network"]["level"])

    def test_another_antivirus_is_named(self):
        other = {"displayName": "Fictional Shield 9", "productState": 266240}
        found, _ = run(powershell=lambda: shell(products=[DEFENDER_PRODUCT, other]))
        self.assertEqual("warn", found["other-antivirus"]["level"])
        self.assertIn("Fictional Shield 9", found["other-antivirus"]["title"])
        self.assertIn("exclusion", found["other-antivirus"]["what_to_do"])
        found, _ = run()
        self.assertEqual("ok", found["other-antivirus"]["level"])


GUID = "d8254c53-2b61-4c77-9a0e-5f3b1c6a7e90"
#: Rules as this PC's registry holds them (2026-10-03): a [volume guid] after the drive, wildcards in a component, a
#: drive that is a wildcard. WorkingSetRules is the WHOLE working set: a copy of every default (Default=1) and the
#: user's own (Default=0) -- among them C:\Users\ Include=0, where DefaultRules says Include=1.
WORKING_SET = [
    ("file:///C:\\[%s]\\Users\\*\\AppData\\Local\\Temp\\" % GUID, 0),
    ("file:///C:\\[%s]\\ProgramData\\" % GUID, 0),
    ("file:///C:\\[%s]\\Users\\" % GUID, 0),
    ("file:///C:\\[%s]\\Users\\pat\\.*\\" % GUID, 0),
    ("file:///C:\\[%s]\\Users\\pat\\AppData\\" % GUID, 0),
    ("file:///C:\\[%s]\\Users\\pat\\Pictures\\Keep\\" % GUID, 1),
    ("file:///C:\\[%s]\\ProgramData\\Microsoft\\Windows\\Start Menu\\" % GUID, 1),
    ("file:///*\\$RECYCLE.BIN\\", 0),
    ("iehistory://{S-1-5-21-1}/", 1),
]
DEFAULT = [("file:///C:\\[%s]\\Users\\" % GUID, 1), ("file:///C:\\[%s]\\ProgramData\\" % GUID, 0),
           ("file:///C:\\[%s]\\Users\\pat\\Pictures\\Keep\\" % GUID, 0)]


class TheRegistrysRules(unittest.TestCase):
    def rules(self):
        return [(fa.parse_scope_url(url), bool(include)) for url, include in WORKING_SET if fa.parse_scope_url(url)]

    def test_the_volume_guid_is_not_part_of_the_folder(self):
        self.assertEqual("C:\\Users\\*\\AppData\\Local\\Temp", fa.parse_scope_url(WORKING_SET[0][0]))
        self.assertEqual("C:\\Users", fa.parse_scope_url(DEFAULT[0][0]))
        self.assertEqual("*\\$RECYCLE.BIN", fa.parse_scope_url(WORKING_SET[7][0]))

    def test_the_working_set_alone_decides(self):
        include = lambda path: fa.search_scope_includes(path, self.rules())   # noqa: E731
        self.assertFalse(include("C:\\Users\\pat\\Pictures\\TagPup"), "the user's C:\\Users\\ Include=0 beats the default")
        self.assertFalse(include("C:\\Users\\bob\\Documents"))
        self.assertFalse(include("C:\\Users\\pat\\AppData\\Roaming\\TagPup"), "a named folder rule excludes")
        self.assertFalse(include("C:\\Users\\pat\\.config\\x"), "Users\\pat\\.* is a wildcard component")
        self.assertFalse(include("C:\\Users\\bob\\AppData\\Local\\Temp\\x"), "Users\\*\\AppData\\Local\\Temp")
        self.assertFalse(include("D:\\Training\\Pictures"), "no rule covers D:")
        self.assertFalse(include("E:\\$RECYCLE.BIN\\x"), "a rule whose drive is a wildcard")
        self.assertFalse(include("C:\\ProgramData\\x"))
        self.assertTrue(include("C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\x"), "the more specific include wins")

    def test_a_user_rule_that_includes_what_the_defaults_exclude_wins(self):
        with mock.patch.object(fa, "_registry_rules", lambda key: WORKING_SET if "WorkingSet" in key else DEFAULT), \
                mock.patch.object(fa.sys, "platform", "win32"):
            found = fa.read_search_rules()
        self.assertTrue(fa.search_scope_includes("C:\\Users\\pat\\Pictures\\Keep\\2019", found),
                        "Include=1 Default=0 against the default's Include=0 for the same URL")

    def test_only_the_working_set_is_read_unless_it_cannot_be(self):
        asked = []

        def registry_rules(key):
            asked.append(key.rsplit("\\", 1)[-1])
            return WORKING_SET if "WorkingSet" in key else DEFAULT

        with mock.patch.object(fa, "_registry_rules", registry_rules), mock.patch.object(fa.sys, "platform", "win32"):
            found = fa.read_search_rules()
        self.assertEqual(["WorkingSetRules"], asked)
        self.assertIn(("C:\\Users", False), found)
        self.assertNotIn(("C:\\Users", True), found)

        asked.clear()

        def only_defaults(key):
            asked.append(key.rsplit("\\", 1)[-1])
            return None if "WorkingSet" in key else DEFAULT

        with mock.patch.object(fa, "_registry_rules", only_defaults), mock.patch.object(fa.sys, "platform", "win32"):
            found = fa.read_search_rules()
        self.assertEqual(["WorkingSetRules", "DefaultRules"], asked)
        self.assertIn(("C:\\Users", True), found)

    def test_neither_set_readable_is_unknown(self):
        with mock.patch.object(fa, "_registry_rules", lambda key: None), mock.patch.object(fa.sys, "platform", "win32"):
            self.assertIsNone(fa.read_search_rules())


class IdsCarryTheirSpecifics(unittest.TestCase):
    """Dismissing one warning (the page keeps the id) must not hide a different later one."""

    def ids(self, **replaced):
        return {each["id"] for each in fa.check(DATA, replaced.pop("places", [PHOTOS]), probes=probes(**replaced))["findings"]
                if each["level"] == "warn"}

    def test_exclusions_not_known_is_not_exclusions_missing(self):
        unknown = self.ids()
        missing = self.ids(registry_exclusions=lambda: ["D:\\TagPup"])
        other = self.ids(registry_exclusions=lambda: ["D:\\TagPup"], places=["E:\\Elsewhere"])
        self.assertEqual(1, len(unknown & {i for i in unknown if i.startswith("defender-exclusions")}))
        self.assertFalse(unknown & missing & {i for i in unknown if i.startswith("defender-exclusions")})
        self.assertNotEqual(missing, other, "a different folder is a different warning")

    def test_search_cloud_and_antivirus_name_their_folder_or_product(self):
        rules = [("D:\\Training", True)]
        a = self.ids(search_rules=lambda: rules)
        b = self.ids(search_rules=lambda: rules, places=["D:\\Training\\Other"])
        self.assertTrue({i for i in a if i.startswith("windows-search")})
        self.assertNotEqual({i for i in a if i.startswith("windows-search")}, {i for i in b if i.startswith("windows-search")})
        one = self.ids(synced_folders=lambda: [("OneDrive", "D:\\Training")])
        two = self.ids(synced_folders=lambda: [("OneDrive", "D:\\TagPup")])
        self.assertNotEqual({i for i in one if i.startswith("cloud-sync")}, {i for i in two if i.startswith("cloud-sync")})
        shield = lambda name: self.ids(powershell=lambda: shell(products=[DEFENDER_PRODUCT, {   # noqa: E731
            "displayName": name, "productState": 266240}]))
        self.assertNotEqual({i for i in shield("Fictional Shield 9") if i.startswith("other-antivirus")},
                            {i for i in shield("Other Guard 2") if i.startswith("other-antivirus")})

    def test_the_same_situation_keeps_its_id(self):
        self.assertEqual(self.ids(), self.ids())


class WindowsSearch(unittest.TestCase):
    RULES = [("C:\\Users", True), ("C:\\Users\\Fictional\\AppData", False), ("D:\\Training", True),
             ("D:\\Training\\Pictures\\Raw", False)]

    def test_the_most_specific_rule_decides(self):
        include = fa.search_scope_includes
        self.assertTrue(include(r"C:\Users\Fictional\Pictures", self.RULES))
        self.assertFalse(include(r"C:\Users\Fictional\AppData\Local", self.RULES))
        self.assertTrue(include(r"D:\Training\Pictures", self.RULES))
        self.assertFalse(include(r"D:\Training\Pictures\Raw\2019", self.RULES))
        self.assertFalse(include(r"E:\Elsewhere", self.RULES))
        self.assertTrue(include(r"d:\TRAINING\pictures", self.RULES), "case does not matter")
        self.assertFalse(include(r"D:\TrainingExtra", self.RULES), "a longer name is not inside")
        self.assertFalse(include(r"D:\x", [(r"D:\x", True), (r"D:\x", False)]), "at one depth an exclusion wins")

    def test_rule_urls(self):
        self.assertEqual("D:\\Training", fa.parse_scope_url("file:///D:\\Training\\"))
        self.assertEqual("C:\\", fa.parse_scope_url("file:///C:\\"))
        self.assertIsNone(fa.parse_scope_url("iehistory://{S-1-5-21}/"))
        self.assertIsNone(fa.parse_scope_url(None))

    def test_a_folder_in_the_scope_is_a_warning_with_the_attribute_command(self):
        found, _ = run(search_rules=lambda: self.RULES)
        one = found["windows-search"]
        self.assertEqual("warn", one["level"])
        self.assertEqual([PHOTOS], one["places"])
        self.assertEqual('attrib +I "D:\\Training\\Pictures" /S /D', one["commands"][-1])

    def test_a_folder_marked_not_indexed_with_every_sampled_file_marked_is_ok(self):
        found, _ = run(search_rules=lambda: self.RULES, not_indexed=lambda folder: True, unmarked_files=lambda f: (20, 0))
        self.assertEqual("ok", found["windows-search"]["level"])

    def test_the_folders_mark_alone_is_not_ok_because_search_honours_it_per_file(self):
        found, _ = run(search_rules=lambda: self.RULES, not_indexed=lambda folder: True, unmarked_files=lambda f: (20, 7))
        one = found["windows-search"]
        self.assertEqual("warn", one["level"])
        self.assertIn("7 of 20", one["why"])
        self.assertIn("per file", one["why"])

    def test_a_marked_folder_whose_files_could_not_be_sampled_is_not_ok(self):
        found, _ = run(search_rules=lambda: self.RULES, not_indexed=lambda folder: True, unmarked_files=lambda f: None)
        self.assertNotEqual("ok", found["windows-search"]["level"])

    def test_the_advice_names_what_the_attribute_command_costs(self):
        found, _ = run(search_rules=lambda: self.RULES)
        one = found["windows-search"]
        said = one["what_to_do"] + " ".join(one["commands"])
        self.assertIn("trade-off", said)
        self.assertIn("change time", said)
        self.assertIn("every file", said)

    def test_a_scope_that_cannot_be_read_is_info(self):
        found, _ = run(search_rules=lambda: None)
        self.assertEqual("info", found["windows-search"]["level"])


class ASampleOfNoFilesIsNeverOk(unittest.TestCase):
    def test_a_root_of_year_and_month_folders_is_sampled_down_to_its_files(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="years_") as root:
            for year in range(1998, 2024):
                for month in range(1, 13):
                    folder = os.path.join(root, str(year), "%02d" % month)
                    os.makedirs(folder)
                    with open(os.path.join(folder, "IMG_0001.jpg"), "wb") as handle:
                        handle.write(b"x")
            sampled, unmarked = fa.read_unmarked_files(root)
            self.assertGreater(sampled, 0)
            self.assertEqual(sampled, unmarked, "none of them carries the mark")
            found, _ = run(places=[root], search_rules=lambda: [(root, True)], not_indexed=lambda folder: folder == root,
                           unmarked_files=fa.read_unmarked_files)
            self.assertEqual("warn", found["windows-search"]["level"])

    def test_no_file_sampled_is_could_not_be_checked_and_not_ok(self):
        found, _ = run(search_rules=lambda: [(PHOTOS, True)], not_indexed=lambda folder: folder == PHOTOS,
                       unmarked_files=lambda folder: (0, 0))
        self.assertEqual("info", found["windows-search"]["level"])
        self.assertIn("could not be checked", found["windows-search"]["title"])


class TheAttributeIsReadBounded(unittest.TestCase):
    def test_check_again_forgets_an_unanswered_look(self):
        fa.forget()
        self.addCleanup(fa.forget)
        fa._unanswered["\\\\nas\\photos"] = time.monotonic()
        ready = probes()
        with mock.patch.object(fa, "Probes", lambda: ready):
            fa.check(DATA, [], refresh=True)
        self.assertEqual({}, fa._unanswered)

    def setUp(self):
        fa.forget()
        self.addCleanup(fa.forget)

    def test_a_share_that_does_not_answer_is_unknown_quickly_and_asked_once(self):
        import threading
        gate = threading.Event()
        self.addCleanup(gate.set)
        stats = []

        def hang(folder):
            stats.append(folder)
            gate.wait(30)
            return mock.Mock(st_file_attributes=0)

        with mock.patch.object(fa.shares, "on_a_network_drive", lambda path: True), \
                mock.patch.object(fa.os, "stat", hang), mock.patch.object(fa, "SHARE_SECONDS", 0.2):
            started = time.monotonic()
            first = fa.read_not_indexed("\\\\nas\\photos")
            self.assertIsNone(first)
            self.assertLess(time.monotonic() - started, 1.5)
            started = time.monotonic()
            for _ in range(5):
                self.assertIsNone(fa.read_not_indexed("\\\\nas\\photos"))
            self.assertLess(time.monotonic() - started, 0.5, "the failed answer is remembered for a while")
        self.assertEqual(1, len(stats), "no second thread was started at the share that is away")

    def test_a_local_folder_is_read_without_a_thread(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            self.assertIn(fa.read_not_indexed(folder), (True, False))


class CloudSync(unittest.TestCase):
    def test_a_data_folder_inside_onedrive_is_a_warning(self):
        synced = [("OneDrive", "C:\\Users\\Fictional\\OneDrive")]
        result = fa.check("C:\\Users\\Fictional\\OneDrive\\TagPup\\data", [PHOTOS],
                          probes=probes(synced_folders=lambda: synced))
        one = [f for f in result["findings"] if f["id"].startswith("cloud-sync")][0]
        self.assertEqual("warn", one["level"])
        self.assertIn("OneDrive", one["title"])
        self.assertEqual(["C:\\Users\\Fictional\\OneDrive\\TagPup\\data"], one["places"])

    def test_outside_every_synced_folder_is_ok(self):
        found, _ = run(synced_folders=lambda: [("OneDrive", "C:\\Users\\Fictional\\OneDrive")])
        self.assertEqual("ok", found["cloud-sync"]["level"])

    def test_the_synced_folders_come_from_the_environment_and_the_profile(self):
        folders = fa.read_synced_folders(
            environ={"OneDrive": "C:\\Users\\Fictional\\OneDrive", "OneDriveConsumer": "C:\\Users\\Fictional\\OneDrive",
                     "USERPROFILE": "C:\\Users\\Fictional"},
            listdir=lambda profile: ["Dropbox", "Google Drive", "OneDrive - Fictional Ltd", "Documents", "Dropbox2"],
            isdir=lambda folder: True)
        self.assertEqual([("OneDrive", "C:\\Users\\Fictional\\OneDrive"),
                          ("Dropbox", "C:\\Users\\Fictional\\Dropbox"),
                          ("Google Drive", "C:\\Users\\Fictional\\Google Drive"),
                          ("OneDrive", "C:\\Users\\Fictional\\OneDrive - Fictional Ltd")], folders)


class RunningPrograms(unittest.TestCase):
    def test_known_programs_are_listed_as_info(self):
        found, _ = run(running=lambda: ["msmpeng", "searchindexer", "onedrive", "notepad"])
        one = found["running-programs"]
        self.assertEqual("info", one["level"])
        for name in ("Microsoft Defender", "Windows Search", "OneDrive"):
            self.assertIn(name, one["title"])
        self.assertNotIn("notepad", one["title"])


class OneFailureDoesNotHideTheOthers(unittest.TestCase):
    def test_a_probe_that_raises_is_one_finding_that_could_not_be_checked(self):
        def boom():
            raise RuntimeError("the registry fell over")

        found, _ = run(search_rules=boom)
        self.assertEqual("info", found["windows-search"]["level"])
        self.assertIn("could not be checked", found["windows-search"]["title"])
        self.assertIn("cloud-sync", found)
        self.assertIn("defender-exclusions", found)

    def test_powershell_raising_is_the_same_as_missing(self):
        def boom():
            raise OSError("blocked")

        found, _ = run(powershell=boom)
        self.assertIn("could not be checked", found["defender"]["title"])

    def test_a_check_that_does_not_finish_returns_what_it_had(self):
        def slow():
            time.sleep(1.0)
            return shell()

        started = time.monotonic()
        result = fa.check(DATA, [], probes=probes(powershell=slow), total=0.2)
        self.assertLess(time.monotonic() - started, 0.9)
        self.assertEqual("timeout", result["findings"][-1]["id"])

    def test_the_answer_is_remembered_and_refreshed_on_request(self):
        fa.forget()
        self.addCleanup(fa.forget)
        calls = []
        ready = probes(running=lambda: calls.append(1) or [])
        with mock.patch.object(fa, "Probes", lambda: ready):
            first = fa.check(DATA, [])
            again = fa.check(DATA, [])
            fresh = fa.check(DATA, [], refresh=True)
        self.assertEqual([False, True, False], [first["cached"], again["cached"], fresh["cached"]])
        self.assertEqual(2, len(calls))


class TheRealCheck(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows' settings")
    def test_it_runs_on_this_machine_within_its_bound_and_raises_nothing(self):
        started = time.monotonic()
        result = fa.check(os.path.join(os.path.expanduser("~"), "no_such_tagpup_data"), [], refresh=True)
        self.assertLess(time.monotonic() - started, fa.TOTAL_SECONDS + 5)
        self.assertTrue(result["findings"])
        for each in result["findings"]:
            self.assertIn(each["level"], ("ok", "info", "warn"))
            self.assertEqual({"id", "level", "title", "why", "what_to_do", "commands", "places"}, set(each))
        fa.forget()


class WhereThereIsNoWindows(unittest.TestCase):
    """lock_owners and file_access are imported by a server on any platform, and answer nothing there."""

    def test_both_import_and_answer_nothing(self):
        import importlib.util
        import ctypes  # noqa: F401
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        loaded = []
        with mock.patch.object(sys, "platform", "linux"), mock.patch.dict(sys.modules, {"ctypes.wintypes": None}):
            for name in ("tagpup/files/lock_owners.py", "tagpup/services/file_access.py"):
                spec = importlib.util.spec_from_file_location("elsewhere_" + os.path.basename(name)[:-3],
                                                              os.path.join(root, name))
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                loaded.append(module)
            owners, access = loaded
            self.assertIsNone(owners.wintypes, "the guard was exercised: the Windows types were refused")
            self.assertEqual([], owners.holders("/tmp/x.jpg"))
            self.assertEqual({}, owners.running())
            self.assertEqual("", owners.describe([]))
            self.assertIsNone(access.read_search_rules())
            self.assertIsNone(access.read_registry_exclusions())
            result = access.check("/data", [], probes=probes())
            self.assertTrue(result["findings"])


class ItOnlyReads(unittest.TestCase):
    SOURCE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tagpup", "services",
                          "file_access.py")

    def code(self):
        with open(self.SOURCE, encoding="utf-8") as handle:
            return handle.read()

    def test_it_never_calls_subprocess(self):
        self.assertNotRegex(self.code(), r"subprocess|os\.system|os\.popen|Popen")
        self.assertIn("processes.run(", self.code())

    def test_no_registry_write_or_setting_change_function_appears(self):
        text = self.code()
        for writer in ("SetValue", "SetValueEx", "CreateKey", "CreateKeyEx", "DeleteKey", "DeleteValue", "SaveKey",
                       "KEY_WRITE", "KEY_ALL_ACCESS", "KEY_SET_VALUE", "SetFileAttributes", "os.chmod", "os.remove",
                       "os.unlink", "shutil", "write(", "open("):
            self.assertNotIn(writer, text, writer)

    def test_the_command_it_runs_only_gets(self):
        verbs = set(re.findall(r"\b([A-Z][A-Za-z]*)-[A-Z][A-Za-z]+", fa.POWERSHELL_SCRIPT))
        self.assertEqual({"Get", "Select", "ConvertTo"}, verbs)
        self.assertNotRegex(fa.POWERSHELL_SCRIPT, r"Add-|Set-|Remove-|New-|Start-|Stop-|Invoke-")
        self.assertEqual("powershell.exe", fa.POWERSHELL[0])
        self.assertIn("-NoProfile", fa.POWERSHELL)

    def test_the_commands_it_offers_are_text_it_never_runs(self):
        offered = fa.exclusion_commands(DATA, [PHOTOS])
        self.assertTrue(any(line.startswith("Add-MpPreference") for line in offered))
        source = self.code()
        self.assertEqual(1, source.count("processes.run("), "the one place a program is run")


if __name__ == "__main__":
    unittest.main()
