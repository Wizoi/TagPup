"""Which other programs can interfere with TagPup's files, as findings for the owner
(docs/ARCHITECTURE.md, "File access check"): READ-ONLY advice, and nothing else.

A program that scans, indexes, syncs or backs up a file opens it, and a file open in another
program refuses TagPup's rename, its ExifTool rewrite, its state file's replace -- "access is denied" with
nobody named (tagpup.files.lock_owners names the holder after a failure). Windows Defender's
real-time scanning is the usual one, then the Windows Search indexer, then a cloud-sync client
(OneDrive, Dropbox, Google Drive, iCloud) or a backup, then a second antivirus. What the owner can do is
exclude TagPup's folders from them; what this does is tell them which of those apply on this PC, and
the exact commands, which THEY run. It never changes a setting: no Add-MpPreference, no registry write, no
service change, no attribute set -- the one process it starts is a PowerShell that only Gets, through
tagpup.core.processes, with a hidden console and a deadline.

`check(data_folder, places)` -> {"checked_at", "findings": [{"id", "level": "ok"|"info"|"warn", "title", "why",
"what_to_do", "commands": [text], "places": [folder]}], "facts": {...}}. `data_folder` is where the
libraries are (tagpup.config.data_dir; this layer does not import config) and `places` the folders
this machine keeps the library's roots at (tagpup.services.roots). Every finding is decided
from facts a small function reads (`Probes`), so a test feeds the shapes this PC gave, and one
finding that fails is "could not be checked" while the others stand. The whole check is bounded (TOTAL_SECONDS),
runs on a thread of its own, and is remembered for CACHE_SECONDS per process; `refresh` asks again.
"""
import logging
import os
import sys
import threading
import time

from tagpup.core import paths, processes
from tagpup.files import lock_owners, shares

logger = logging.getLogger(__name__)

#: How long the whole check may take, and how long PowerShell may.
TOTAL_SECONDS = 30.0
POWERSHELL_SECONDS = 20.0
#: How long an answer is kept, per process.
CACHE_SECONDS = 600.0

OK, INFO, WARN = "ok", "info", "warn"

#: What PowerShell is asked, once: it only Gets. Output is one line of JSON.
POWERSHELL_SCRIPT = (
    "$ErrorActionPreference = 'SilentlyContinue'; "
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    "$status = Get-MpComputerStatus | Select-Object RealTimeProtectionEnabled, OnAccessProtectionEnabled, "
    "BehaviorMonitorEnabled, IsTamperProtected, AntivirusEnabled; "
    "$preference = Get-MpPreference | Select-Object DisableScanningNetworkFiles, ExclusionPath, ScanAvgCPULoadFactor; "
    "$products = @(Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct "
    "| Select-Object displayName, productState); "
    "[pscustomobject]@{ status = $status; preference = $preference; products = $products } "
    "| ConvertTo-Json -Depth 4 -Compress")
POWERSHELL = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", POWERSHELL_SCRIPT]

#: Where Windows keeps what the exclusions and the Search scope are (read with winreg, KEY_READ only).
DEFENDER_EXCLUSIONS_KEY = r"SOFTWARE\Microsoft\Windows Defender\Exclusions\Paths"
SEARCH_RULES_KEY = r"SOFTWARE\Microsoft\Windows Search\CrawlScopeManager\Windows\SystemIndex\WorkingSetRules"

#: GetFileAttributes: "do not index the content of this file or folder".
NOT_CONTENT_INDEXED = 0x2000

#: What Defender says in place of the exclusions when the process is not elevated.
NOT_ALLOWED = "N/A: Must be an administrator to view exclusions"

#: Programs that scan, index, sync or back up, by their executable's name without ".exe", lower case: {name: what}.
BACKGROUND = {
    "msmpeng": "Microsoft Defender (scanning)", "searchindexer": "Windows Search (indexing)",
    "onedrive": "OneDrive (sync)", "dropbox": "Dropbox (sync)", "googledrivefs": "Google Drive (sync)",
    "icloudservices": "iCloud (sync)", "iclouddrive": "iCloud (sync)", "backblaze": "Backblaze (backup)",
    "bzserv": "Backblaze (backup)", "crashplanservice": "CrashPlan (backup)", "acronis": "Acronis (backup)",
    "tbsvc": "Acronis (backup)", "veeam.endpoint.service": "Veeam (backup)", "plex media server": "Plex (scanning)",
    "avastsvc": "Avast (antivirus)", "avgsvc": "AVG (antivirus)", "mbamservice": "Malwarebytes (antivirus)",
    "ekrn": "ESET (antivirus)", "bdagent": "Bitdefender (antivirus)", "vsserv": "Bitdefender (antivirus)",
    "avp": "Kaspersky (antivirus)", "mfemms": "McAfee (antivirus)", "mcshield": "McAfee (antivirus)",
    "nortonsecurity": "Norton (antivirus)", "ccsvchst": "Norton (antivirus)", "savservice": "Sophos (antivirus)",
    "sophosfilescanner": "Sophos (antivirus)", "cyserver": "Cylance (antivirus)", "csfalconservice": "CrowdStrike (antivirus)",
}

#: Cloud-sync folders a PC has by default, under the user's profile: (folder name or prefix, whether a prefix, provider).
SYNC_DEFAULTS = (("Dropbox", False, "Dropbox"), ("Google Drive", False, "Google Drive"),
                 ("iCloudDrive", False, "iCloud Drive"), ("iCloud Drive", False, "iCloud Drive"),
                 ("OneDrive", True, "OneDrive"))
#: The environment variables OneDrive sets to its folders.
SYNC_VARIABLES = (("OneDrive", "OneDrive"), ("OneDriveConsumer", "OneDrive"), ("OneDriveCommercial", "OneDrive"))


def quoted(path):
    """`path` as a PowerShell single-quoted string."""
    return "'%s'" % str(path).replace("'", "''")


def covers(excluded, path):
    """Is `path` the excluded folder, or inside it? An excluded entry with a wildcard or an environment variable that
    does not expand covers nothing here: it is not a folder this can compare."""
    entry = os.path.expandvars(str(excluded or "").strip().strip('"'))
    if not entry or "*" in entry or "?" in entry or "%" in entry:
        return False
    return paths.same(path, entry) or paths.is_under(path, entry)


def parse_scope_url(url):
    """The folder a Windows Search scope rule's URL names -- file:///D:\\Photos\\ -- or None for a rule of another
    kind (iehistory://, csc://, mapi16://)."""
    text = str(url or "")
    if not text.lower().startswith("file:///"):
        return None
    folder = text[len("file:///"):].replace("/", os.sep).rstrip("\\/")
    if len(folder) == 2 and folder[1] == ":":
        folder += os.sep
    return folder or None


def search_scope_includes(path, rules):
    """Does the Windows Search scope include `path`? `rules` is [(folder, include)]: a path is included when the most
    specific rule that covers it -- the longest folder it equals or is inside -- includes it. At one length, an
    exclusion wins. No rule covering it: not included."""
    best, included = -1, False
    for folder, include in rules:
        if not folder:
            continue
        if paths.same(path, folder) or paths.is_under(path, folder):
            length = len(paths.key(folder).rstrip("\\/"))
            if length > best or (length == best and not include):
                best, included = length, bool(include)
    return included


def synced_under(path, folders):
    """The provider of the first synced folder `path` is inside, or None. `folders` is [(provider, folder)]."""
    for provider, folder in folders:
        if paths.same(path, folder) or paths.is_under(path, folder):
            return provider
    return None


# ---- What is read (each a small function a test replaces) ---------------------------------------

def read_powershell():
    """What the one PowerShell command answered, as a dict ({"status", "preference", "products"}), or None when it
    could not be asked: PowerShell missing or blocked, slow, or answering nothing parseable."""
    import json
    try:
        done = processes.run(POWERSHELL, capture_output=True, timeout=POWERSHELL_SECONDS, input=b"")
    except Exception as why:   # missing (FileNotFoundError), blocked, timed out
        logger.info("File access check: PowerShell could not be asked: %s", type(why).__name__)
        return None
    text = (done.stdout or b"").decode("utf-8", "replace").strip().lstrip("\ufeff")
    try:
        found = json.loads(text.splitlines()[-1]) if text else None
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


def _registry_values(key):
    """[(name, data)] of a registry key's values, read only; None when it cannot be read (not Windows, no key,
    access denied)."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0, winreg.KEY_READ) as opened:
            found, index = [], 0
            while True:
                try:
                    name, data, _kind = winreg.EnumValue(opened, index)
                except OSError:
                    return found
                found.append((name, data))
                index += 1
    except Exception:
        return None


def read_registry_exclusions():
    """The folders Defender excludes, from the registry when this process may read them; None when it may not."""
    found = _registry_values(DEFENDER_EXCLUSIONS_KEY)
    return None if found is None else [name for name, _data in found]


def read_search_rules():
    """[(folder, include)] of Windows Search's scope rules for files, or None when they cannot be read."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        rules = []
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, SEARCH_RULES_KEY, 0, winreg.KEY_READ) as opened:
            index = 0
            while True:
                try:
                    name = winreg.EnumKey(opened, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(opened, name, 0, winreg.KEY_READ) as rule:
                        url = winreg.QueryValueEx(rule, "URL")[0]
                        include = winreg.QueryValueEx(rule, "Include")[0]
                except OSError:
                    continue
                folder = parse_scope_url(url)
                if folder:
                    rules.append((folder, bool(include)))
        return rules
    except Exception:
        return None


def read_not_indexed(folder):
    """Does `folder` carry the 'do not index its content' attribute? None when it cannot be asked."""
    try:
        return bool(os.stat(folder).st_file_attributes & NOT_CONTENT_INDEXED)
    except (OSError, AttributeError):
        return None


def read_synced_folders(environ=None, profile=None, isdir=os.path.isdir, listdir=os.listdir):
    """[(provider, folder)] of the folders a cloud-sync client keeps in sync here: those OneDrive names in the environment,
    and the default folders under the user's profile that exist."""
    environ = os.environ if environ is None else environ
    found = []
    for variable, provider in SYNC_VARIABLES:
        folder = environ.get(variable)
        if folder and (provider, folder) not in found:
            found.append((provider, folder))
    profile = profile or environ.get("USERPROFILE") or os.path.expanduser("~")
    try:
        names = listdir(profile)
    except OSError:
        names = []
    for name in names:
        for wanted, prefix, provider in SYNC_DEFAULTS:
            if (name.lower().startswith(wanted.lower()) if prefix else name.lower() == wanted.lower()):
                folder = os.path.join(profile, name)
                if isdir(folder) and (provider, folder) not in found:
                    found.append((provider, folder))
    return found


def read_running():
    """The executable names of the programs now running (lower case, no ".exe")."""
    return sorted({(name[:-4] if name.lower().endswith(".exe") else name).lower()
                   for name in lock_owners.running().values()})


class Probes:
    """What the check reads. A test replaces any of them with a function that answers what it recorded."""

    def __init__(self, **replaced):
        self.powershell = read_powershell
        self.registry_exclusions = read_registry_exclusions
        self.search_rules = read_search_rules
        self.not_indexed = read_not_indexed
        self.synced_folders = read_synced_folders
        self.running = read_running
        self.on_a_share = shares.on_a_network_drive
        for name, function in replaced.items():
            if not hasattr(self, name):
                raise TypeError("no probe called %s" % name)
            setattr(self, name, function)


# ---- The findings ---------------------------------------------------------------------------------

def finding(id_, level, title, why="", what_to_do="", commands=(), places=()):
    return {"id": id_, "level": level, "title": title, "why": why, "what_to_do": what_to_do,
            "commands": list(commands), "places": list(places)}


def _defender_state(shell):
    """({"realtime": bool, "on_access": bool, "tamper": bool} or None for Defender not answering, whether PowerShell answered)."""
    if shell is None:
        return None, False
    status = shell.get("status")
    if not isinstance(status, dict):
        return None, True
    return {"realtime": bool(status.get("RealTimeProtectionEnabled")),
            "on_access": bool(status.get("OnAccessProtectionEnabled")),
            "tamper": bool(status.get("IsTamperProtected"))}, True


def _exclusions(shell, probes):
    """(the excluded folders, readable): from Get-MpPreference when this process may read them, else from the registry."""
    preference = (shell or {}).get("preference") if shell else None
    if isinstance(preference, dict) and "ExclusionPath" in preference:
        listed = preference["ExclusionPath"]
        if isinstance(listed, str):
            listed = [listed]
        if listed is None:
            listed = []
        if isinstance(listed, list) and not any(str(each).startswith("N/A") or NOT_ALLOWED in str(each)
                                                for each in listed):
            return [str(each) for each in listed], True
    registry = probes.registry_exclusions()
    if registry is not None:
        return registry, True
    return [], False


def exclusion_commands(data_folder, places):
    """The commands the owner may run, elevated, to exclude TagPup's folders -- as text, never run here. `data_folder`
    None when it is excluded already."""
    lines = ["# Windows PowerShell, run as administrator (right-click, Run as administrator)."]
    if data_folder:
        lines += ["# Recommended: TagPup's own data folder (the libraries, thumbnails, bulk-edit state files):",
                  "Add-MpPreference -ExclusionPath %s" % quoted(data_folder)]
    if places:
        lines += ["# Optional, and a trade-off: Defender then no longer scans these photo folders at all:"]
        lines += ["Add-MpPreference -ExclusionPath %s" % quoted(place) for place in places]
    return lines


def defender_findings(shell, probes, data_folder, places):
    state, answered = _defender_state(shell)
    if not answered:
        return [finding("defender", INFO, "Windows Defender could not be checked",
                        "PowerShell did not answer (it is missing, blocked, or was slow), so TagPup cannot tell whether "
                        "Microsoft Defender scans its folders.",
                        "Check Windows Security > Virus & threat protection > Manage settings > Exclusions yourself.")]
    if state is None:
        return [finding("defender", INFO, "Microsoft Defender is not reporting",
                        "Windows did not give Defender's status: another antivirus may be the one scanning (see below).")]
    if not (state["realtime"] or state["on_access"]):
        return [finding("defender", OK, "Microsoft Defender real-time scanning is off",
                        "It does not scan files as they are opened, so it does not hold TagPup's files.")]
    out = [finding("defender", INFO, "Microsoft Defender scans files as they are opened and written",
                   "Real-time protection%s is on. A scan of a photo TagPup has just written holds it open for a moment, and "
                   "a write that meets it can fail with 'access is denied'." % (
                       " and on-access protection" if state["on_access"] else ""),
                   "Exclude TagPup's data folder at least (next finding).")]
    excluded, readable = _exclusions(shell, probes)
    wanted = [("the data folder", data_folder)] + [("a photo folder", place) for place in places if place]
    if not readable:
        out.append(finding(
            "defender-exclusions", WARN, "Whether Microsoft Defender excludes TagPup's folders is not known",
            "Windows lets only an administrator read Defender's exclusions, and TagPup does not run as one, so it cannot "
            "tell whether the data folder%s is excluded." % (" and the photo folders" if places else ""),
            "Open Windows PowerShell as administrator and run the first line to see the excluded folders; add TagPup's "
            "data folder with the next. Excluding the data folder is recommended: SQLite's write-ahead files, the "
            "thumbnail cache and the bulk-edit state files are opened and replaced all the time, and a scan in the "
            "middle of that is what makes a save fail. Excluding the photo folders is your choice (below).",
            ["Get-MpPreference | Select-Object -ExpandProperty ExclusionPath"] + exclusion_commands(data_folder, places),
            [data_folder] + list(places)))
        return out
    missing = [(what, folder) for what, folder in wanted if not any(covers(each, folder) for each in excluded)]
    if not missing:
        out.append(finding("defender-exclusions", OK, "Microsoft Defender excludes TagPup's folders",
                           "The data folder%s lies inside what Defender excludes." % (
                               " and every photo folder" if places else "")))
        return out
    data_missing = any(what == "the data folder" for what, _folder in missing)
    out.append(finding(
        "defender-exclusions", WARN,
        "Microsoft Defender scans %s" % ("TagPup's data folder" if data_missing else "the photo folders"),
        "Not excluded: %s." % "; ".join("%s (%s)" % (folder, what) for what, folder in missing),
        "Exclude the data folder: it is TagPup's own working files. Excluding the photo folders is optional, a trade-off: "
        "those photos are then never scanned by Defender.",
        exclusion_commands(data_folder if data_missing else None,
                           [folder for what, folder in missing if what != "the data folder"]),
        [folder for _what, folder in missing]))
    return out


def network_finding(shell, probes, places):
    shared = [place for place in places if place and probes.on_a_share(place)]
    if not shared:
        return None
    preference = (shell or {}).get("preference") if shell else None
    disabled = preference.get("DisableScanningNetworkFiles") if isinstance(preference, dict) else None
    if disabled is None:
        return finding("defender-network", INFO, "Scanning of files on a network share could not be checked",
                       "These folders are on a share: %s." % "; ".join(shared), places=shared)
    if disabled:
        return finding("defender-network", OK, "Defender does not scan files on network shares",
                       "Its setting for scanning network files is off.", places=shared)
    return finding(
        "defender-network", WARN, "Microsoft Defender scans files on the network share too",
        "Scanning of network files is on, and a photo folder is on a share: every photo opened or written there is "
        "scanned over the network, which is slow and can hold the file.",
        "Exclude the share's folder (optional, a trade-off), or accept the slower writes.",
        ["# Windows PowerShell, run as administrator:"] + ["Add-MpPreference -ExclusionPath %s" % quoted(each)
                                                         for each in shared], shared)


def antivirus_finding(shell):
    products = (shell or {}).get("products") if shell else None
    if products is None:
        return None
    if isinstance(products, dict):
        products = [products]
    others = []
    for each in products:
        if not isinstance(each, dict):
            continue
        name = str(each.get("displayName") or "").strip()
        if not name or "defender" in name.lower():
            continue
        state = each.get("productState")
        on = isinstance(state, int) and (state & 0xF000) == 0x1000   # productState: 0x1000 on, 0x2000 snoozed, 0 off
        others.append((name, on))
    if not others:
        return finding("other-antivirus", OK, "No antivirus other than Microsoft Defender is registered with Windows")
    names = ", ".join(name for name, _on in others)
    return finding(
        "other-antivirus", WARN, "Another antivirus is installed: %s" % names,
        "%s scans files too, and TagPup cannot read its exclusion list. %s" % (
            names, "At least one reports it is on." if any(on for _name, on in others) else
            "It reports it is off, but check."),
        "Open %s and add TagPup's data folder (and, if you accept the trade-off, the photo folders) to its "
        "exclusions for real-time scanning. Its own settings are the only place to do it." % names,
        places=[])


def search_finding(probes, data_folder, places):
    wanted = [data_folder] + [place for place in places if place]
    rules = probes.search_rules()
    included, unknown = [], []
    for folder in wanted:
        if probes.not_indexed(folder):
            continue
        if rules is None:
            unknown.append(folder)
        elif search_scope_includes(folder, rules):
            included.append(folder)
    if included:
        return finding(
            "windows-search", WARN, "Windows Search indexes %s" % ("TagPup's folders" if len(included) > 1 else "a TagPup folder"),
            "The Search indexer opens every file in these folders to index it, and reads photos again when they change: "
            "%s." % "; ".join(included),
            "Remove them from the index (Settings > Search > Searching Windows > Excluded folders), or mark them "
            "not-indexed with the command below, run for each folder.",
            ["attrib +I %s /S /D" % ('"%s"' % folder) for folder in included], included)
    if unknown:
        return finding("windows-search", INFO, "Whether Windows Search indexes TagPup's folders could not be checked",
                       "The Search scope could not be read.", places=unknown)
    return finding("windows-search", OK, "Windows Search does not index TagPup's folders",
                   "They are outside its scope, or marked not to be indexed.")


def sync_finding(probes, data_folder, places):
    folders = probes.synced_folders()
    inside = []
    for what, folder in [("the data folder", data_folder)] + [("a photo folder", place) for place in places if place]:
        provider = synced_under(folder, folders)
        if provider:
            inside.append((what, folder, provider))
    if not inside:
        return finding("cloud-sync", OK, "TagPup's folders are not in a cloud-synced folder")
    providers = sorted({provider for _what, _folder, provider in inside})
    return finding(
        "cloud-sync", WARN, "TagPup's folders are inside %s" % " and ".join(providers),
        "A sync client opens and locks the files it uploads, and a file that is online-only is not on the disk until "
        "something downloads it. %s." % "; ".join("%s: %s (%s)" % (what, folder, provider)
                                                 for what, folder, provider in inside),
        "Keep the data folder outside the synced folder, or pause syncing while TagPup writes. For the photo folders, "
        "mark them 'Always keep on this device'.", places=[folder for _what, folder, _provider in inside])


def running_finding(probes):
    running = probes.running()
    found = sorted({BACKGROUND[name] for name in running if name in BACKGROUND})
    if not found:
        return finding("running-programs", OK, "No scanner, sync or backup program is running that TagPup knows of")
    return finding("running-programs", INFO, "Running now that can open TagPup's files: %s" % ", ".join(found),
                   "These programs read or lock files in the background. The findings above say which can be told to "
                   "leave TagPup's folders alone.")


def _safe(id_, title, build):
    """`build()`'s findings as a list; a finding that raised is one that says it could not be checked."""
    try:
        made = build()
    except Exception as why:
        logger.warning("File access check: %s failed: %s", id_, why)
        return [finding(id_, INFO, "%s could not be checked" % title, "The check failed: %s." % type(why).__name__)]
    if made is None:
        return []
    return list(made) if isinstance(made, list) else [made]


def _gather(data_folder, places, probes, into, facts):
    shell = None
    try:
        shell = probes.powershell()
    except Exception as why:
        logger.warning("File access check: PowerShell failed: %s", why)
    state, answered = _defender_state(shell)
    facts.update(powershell=answered, defender=state, places=list(places), data_folder=data_folder)
    into += _safe("defender", "Microsoft Defender", lambda: defender_findings(shell, probes, data_folder, places))
    into += _safe("defender-network", "Scanning of network files", lambda: network_finding(shell, probes, places))
    into += _safe("other-antivirus", "Other antivirus programs", lambda: antivirus_finding(shell))
    into += _safe("windows-search", "Windows Search", lambda: search_finding(probes, data_folder, places))
    into += _safe("cloud-sync", "Cloud sync", lambda: sync_finding(probes, data_folder, places))
    into += _safe("running-programs", "Running programs", lambda: running_finding(probes))


_cache = {}
_guard = threading.Lock()
_one_at_a_time = threading.Lock()


def forget():
    """Forget what was remembered: what a test starts from."""
    with _guard:
        _cache.clear()


def check(data_folder, places=(), refresh=False, probes=None, total=TOTAL_SECONDS):
    """The findings, read now or remembered (CACHE_SECONDS, per process). A `probes` of a test is never remembered. Never
    raises; a check that did not finish in `total` seconds returns what it had, with a finding saying so."""
    places = [paths.stored(place) for place in places if place]
    data_folder = paths.stored(data_folder)
    key = (paths.key(data_folder), tuple(paths.key(place) for place in places))
    if probes is None:
        with _guard:
            held = _cache.get(key)
        if held and not refresh and time.monotonic() - held[0] < CACHE_SECONDS:
            return dict(held[1], cached=True)
    if not _one_at_a_time.acquire(timeout=total):
        return _result([finding("timeout", INFO, "The file access check is still running",
                                "Another check has not finished; ask again in a moment.")], {})
    try:
        if probes is None and not refresh:
            with _guard:
                held = _cache.get(key)
            if held and time.monotonic() - held[0] < CACHE_SECONDS:
                return dict(held[1], cached=True)
        findings, facts = [], {}
        worker = threading.Thread(target=_gather, args=(data_folder, places, probes or Probes(), findings, facts),
                                  name="file-access-check", daemon=True)
        worker.start()
        worker.join(total)
        done = not worker.is_alive()
        result = _result(list(findings) + ([] if done else [finding(
            "timeout", INFO, "The file access check did not finish in %d seconds" % total,
            "What it had found is shown; a program (or a share that does not answer) held it up.")]), dict(facts))
    finally:
        _one_at_a_time.release()
    if probes is None and done:
        with _guard:
            _cache[key] = (time.monotonic(), result)
    return dict(result, cached=False)


def _result(findings, facts):
    return {"checked_at": time.strftime("%Y-%m-%d %H:%M:%S"), "findings": findings, "facts": facts}
