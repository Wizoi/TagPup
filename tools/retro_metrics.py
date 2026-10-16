"""Counted numbers for a retrospective of a window of days: the git log, the findings ledger, the tests, Claude Code's
token use and the workers' branches. Read-only: it runs git's read commands and reads the session transcripts' usage.

    .venv/Scripts/python.exe tools/retro_metrics.py --since 2026-10-02 --until 2026-10-16 --out retro.md --json retro.json
    .venv/Scripts/python.exe tools/retro_metrics.py --branch worktree-agent-<id>     (before a merge: the budget guard)

`--branch REF` prints the lines REF adds and removes against its merge base with --rev, and exits 1 when it adds over
the budget (1,200 lines, `--budget N`) unless `--allow-over` is given: a deletion is never over budget.

The default window is the last 14 days, the default revision HEAD. Every number is counted by this run and printed with
how it was counted; nothing is recalled (docs/RETRO.md, phase 2: the data file). Days are the machine's local
dates, of the commit date (git) and of the record's timestamp (transcripts).

The transcripts hold real names. Of each record only the usage numbers, the model, the timestamp, the session id and
the sidechain flag are taken, of a compaction its trigger and token count, and of a subagent's meta file its agent type,
its worktree branch (a `worktree-agent-<hex>` name, any other is masked) and whether the owner stopped it
(`transcript_facts`); no message text, tool input or output, brief or description is kept, printed or written.
"""
import argparse
import datetime
import json
import math
import os
import re
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import add_findings  # noqa: E402
from tagpup.core import processes  # noqa: E402

TRANSCRIPTS = os.path.join(os.path.expanduser("~"), ".claude", "projects", "c--src-kidzi-GitHub-TagPup")

#: USD per million tokens, (low, high), for a model whose name starts with the key. Edit to the list prices; these are
#: not billing. Date and source: owner to verify (2026-10-16, the per-million list prices published for the Claude 4
#: families: Opus 4.5 $5/$25 low, Opus 4/4.1 $15/$75 high, Sonnet $3/$15, Haiku 4.5 $1/$5). A model matching no key
#: is reported unpriced: its tokens are counted, its cost is not guessed.
PRICES = {
    "claude-opus": {"input": (5.0, 15.0), "output": (25.0, 75.0)},
    "claude-sonnet": {"input": (3.0, 3.0), "output": (15.0, 15.0)},
    "claude-haiku": {"input": (1.0, 1.0), "output": (5.0, 5.0)},
}
#: A cache write costs this many times an input token (5-minute and 1-hour), a cache read this many.
CACHE_WRITE_5M, CACHE_WRITE_1H, CACHE_READ = 1.25, 2.0, 0.1

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
WORKTREE_MERGE = re.compile(r"^Merge (?:branch ')?worktree-agent-")
TRAILER = re.compile(r"^co-authored-by:\s*([^<\n]+?)\s*(?:<|$)", re.I | re.M)
REVIEW_WORDS = re.compile(r"\b(?:review|reviewer|owner)s?\b", re.I)
SEVERITY = re.compile(r"^\s*(data|wrong|low):", re.I)
#: A call whose context (input, cache write and cache read) is above this is counted as large.
LARGE_CONTEXT = 150000
#: A branch adds about this many lines or fewer (CLAUDE.md, "Keeping the project small"; DECISIONS 2026-10-16: added
#: lines, a deletion is never over budget).
BRANCH_BUDGET = 1200
#: The agent types of the meta files (`agentType`), as the tables name them; any other is "other".
AGENT_TYPES = {"tagpup-worker": "worker", "tagpup-worker-small": "small worker", "tagpup-reviewer": "reviewer",
               "tagpup-quick-reviewer": "quick reviewer", "tagpup-owner-data": "owner: data",
               "tagpup-owner-people": "owner: people", "tagpup-owner-files": "owner: files",
               "tagpup-owner-views-ai": "owner: views-ai", "tagpup-owner-integration": "owner: integration",
               "general-purpose": "general-purpose"}
MAIN = "main session"
AGENT_ORDER = (MAIN, *AGENT_TYPES.values(), "other")
REVIEW_TYPES = ("reviewer", "quick reviewer") + tuple(v for v in AGENT_TYPES.values() if v.startswith("owner: "))
WORK_TYPES = ("worker", "small worker")
#: The only branch name a table prints; any other (a name could hold a person's) is counted as OTHER_BRANCH.
WORKTREE_BRANCH = re.compile(r"^worktree-agent-[0-9a-f]+$")
OTHER_BRANCH = "(not a worktree-agent branch)"
MERGED_BRANCH = re.compile(r"^Merge (?:branch ')?(worktree-agent-[0-9a-f]+)")
TOKENS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def area(path):
    """code / tests / docs / other, by the path's first folder (a repository path, not a tag)."""
    top = re.match(r"[^/]*", path).group(0)
    if top in ("tagpup", "web", "tools", "scripts"):
        return "code"
    return {"tests": "tests", "docs": "docs", ".claude": "docs"}.get(top, "other")


def git(repo, *args, ok=(0,)):
    done = processes.run(["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if done.returncode not in ok:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), done.stderr.strip()))
    return done.stdout


def rev_before(repo, rev, moment):
    """The last trunk commit (first parent) of `rev` committed before `moment`, or the empty tree."""
    return git(repo, "rev-list", "-1", "--first-parent", "--before=" + moment, rev).strip() or EMPTY_TREE


def window_days(since, until):
    day, days = since, []
    while day <= until:
        days.append(day.isoformat())
        day += datetime.timedelta(days=1)
    return days


def commits(repo, start, end):
    """The commits reaching `end` that `start` lacks: [{sha, parents, day, subject, body, files: [(path, +, -)]}]."""
    span = end if start == EMPTY_TREE else "%s..%s" % (start, end)
    out = git(repo, "log", span, "--no-renames", "--numstat", "--date=format-local:%Y-%m-%d",
              "--format=%x1e%H%x1f%P%x1f%cd%x1f%s%x1f%b%x1f")
    found = []
    for chunk in out.split("\x1e")[1:]:
        sha, parents, day, subject, body, stat = chunk.split("\x1f", 5)
        files = []
        for line in stat.strip().splitlines():
            added, removed, path = line.split("\t", 2)
            files.append((path, int(added) if added.isdigit() else 0, int(removed) if removed.isdigit() else 0))
        found.append({"sha": sha, "parents": parents.split(), "day": day, "subject": subject, "body": body,
                      "files": files})
    return found


def lines_at(repo, rev):
    """Lines in the tracked text files of `rev`, by area."""
    totals = {"code": 0, "tests": 0, "docs": 0, "other": 0}
    if rev != EMPTY_TREE:
        for line in git(repo, "diff", "--numstat", "--no-renames", EMPTY_TREE, rev).splitlines():
            added, _removed, path = line.split("\t", 2)
            totals[area(path)] += int(added) if added.isdigit() else 0
    return totals


def only_findings(paths):
    return bool(paths) and all(p.startswith("docs/findings") or p.startswith("docs/history/findings_closed_")
                               for p in paths)


def git_section(repo, start, end, found):
    plain = [c for c in found if len(c["parents"]) < 2]
    per_day, trailers, outside_docs = {}, {}, set()
    for c in found:
        day = per_day.setdefault(c["day"], {"commits": 0, "merges": 0, "code": [0, 0], "tests": [0, 0],
                                            "docs": [0, 0], "other": [0, 0]})
        day["commits"] += 1
        day["merges"] += len(c["parents"]) > 1
        for path, added, removed in c["files"]:
            day[area(path)][0] += added
            day[area(path)][1] += removed
            if area(path) != "docs":
                outside_docs.add(path)
        names = TRAILER.findall(c["body"]) or ["(no trailer)"]
        for name in set(names):
            trailers[name] = trailers.get(name, 0) + 1
    totals = {k: [sum(d[k][0] for d in per_day.values()), sum(d[k][1] for d in per_day.values())]
              for k in ("code", "tests", "docs", "other")}
    return {
        "start": start, "end": end, "commits": len(found), "non_merge_commits": len(plain),
        "merges": len(found) - len(plain), "per_day": dict(sorted(per_day.items())),
        "files_changed_outside_docs": len(outside_docs), "lines": totals,
        "lines_in_tree": {"start": lines_at(repo, start), "end": lines_at(repo, end)},
        "worktree_merges": sum(1 for c in found if WORKTREE_MERGE.match(c["subject"]) and len(c["parents"]) > 1),
        "worktree_merge_subjects_on_one_parent": sum(1 for c in found if WORKTREE_MERGE.match(c["subject"])
                                                     and len(c["parents"]) < 2),
        "reverts": sum(1 for c in found if c["subject"].startswith("Revert")),
        "findings_only_commits": sum(1 for c in plain if only_findings([f[0] for f in c["files"]])),
        "trailers": dict(sorted(trailers.items(), key=lambda kv: -kv[1])),
    }


def show(repo, rev, path):
    return "" if rev == EMPTY_TREE else git(repo, "show", "%s:%s" % (rev, path), ok=(0, 128))


def ledger(repo, rev):
    """{number: (found, finding, status)} of docs/findings.md and the archives at `rev`."""
    names = ["docs/findings.md"]
    if rev != EMPTY_TREE:
        listed = git(repo, "ls-tree", "--name-only", rev, "docs/history/").split()
        names += [n for n in listed if add_findings.ARCHIVE_NAME.match(os.path.basename(n))]
    rows = {}
    for name in names:
        for line in show(repo, rev, name).splitlines():
            if re.match(r"\| \d+ \|", line):
                cells = line.rstrip().rstrip("|").split(" | ")
                rows[int(cells[0].strip("| "))] = (cells[1].strip(), " | ".join(cells[2:-1]), cells[-1].strip())
    return rows


def status_kind(status):
    match = add_findings.CLOSED.match(status)
    if not match:
        return "open"
    word = match.group(0).lower()
    return {"decision": "decided"}.get(word, word if word in ("fixed", "decided", "obsolete", "closed",
                                                              "not reproduced") else "other closed (" + word + ")")


def findings_section(repo, start, end, days, day_revs):
    before, after = ledger(repo, start), ledger(repo, end)
    opened = {n: r for n, r in after.items() if r[0][:10] in days}
    closed_before = {n for n, r in before.items() if status_kind(r[2]) != "open"}
    closed = {n: r for n, r in after.items() if status_kind(r[2]) != "open" and n not in closed_before}
    by_day, kinds, severities = {}, {}, {}
    for _n, (found, finding, _status) in opened.items():
        by_day[found[:10]] = by_day.get(found[:10], 0) + 1
        sev = SEVERITY.match(finding)
        key = sev.group(1).lower() if sev else "none"
        severities[key] = severities.get(key, 0) + 1
    for _n, (_found, _finding, status) in closed.items():
        kinds[status_kind(status)] = kinds.get(status_kind(status), 0) + 1
    file_lines = {}
    for day, rev in day_revs.items():
        text = show(repo, rev, "docs/findings.md")
        file_lines[day] = {"lines": len(text.splitlines()),
                           "open_rows": sum(1 for line in text.splitlines()
                                            if re.match(r"\| \d+ \|", line) and not add_findings.is_closed(line))}
    return {
        "opened": len(opened), "opened_per_day": dict(sorted(by_day.items())), "opened_per_severity": severities,
        "closed": len(closed), "closed_per_kind": dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
        "open_now": sum(1 for r in after.values() if status_kind(r[2]) == "open"),
        "highest": max(after) if after else 0,
        "opened_naming_review_or_owner": sum(1 for r in opened.values() if REVIEW_WORDS.search(r[1])),
        "closed_obsolete_or_stale": sum(1 for r in closed.values()
                                        if status_kind(r[2]) == "obsolete" or "stale" in r[2].lower()),
        "findings_md_per_day": file_lines,
    }


def tests_at(repo, rev):
    if rev == EMPTY_TREE:
        return {"files": 0, "test_files": 0, "def_test": 0}
    paths = git(repo, "ls-tree", "-r", "--name-only", rev, "--", "tests/").splitlines()
    tests = [p for p in paths if re.match(r"tests/test_[^/]*\.py$", p) or re.match(r"tests/frontend/[^/]*\.test\.mjs$", p)]
    return {"files": len(paths), "test_files": len(tests), "def_test": count_matches(repo, rev, "def test_")}


def count_matches(repo, rev, text):
    out = git(repo, "grep", "-c", "-F", text, rev, "--", "tests/", ok=(0, 1))
    return sum(int(line.rsplit(":", 1)[1]) for line in out.splitlines() if line)


def tests_section(repo, start, end):
    def changed(kind):
        if start == EMPTY_TREE:
            out = git(repo, "ls-tree", "-r", "--name-only", end, "--", "tests/") if kind == "A" else ""
        else:
            out = git(repo, "diff", "--name-only", "--no-renames", "--diff-filter=" + kind, start, end, "--", "tests/")
        return len(out.split())
    return {"start": tests_at(repo, start), "end": tests_at(repo, end), "files_added": changed("A"),
            "files_deleted": changed("D"), "lifted_lines": count_matches(repo, end, "Lift" + "ed:")}


def transcript_files(folder):
    """[(path, is a subagent's file)]: a session's .jsonl at the top, its agents' in a folder below it."""
    found = []
    for here, _folders, names in os.walk(folder):
        for name in names:
            if name.endswith(".jsonl"):
                found.append((os.path.join(here, name), os.path.normpath(here) != os.path.normpath(folder)))
    return sorted(found)


def agent_meta(path):
    """Of the meta file beside a subagent's transcript (every one has one: 438 of 438 on 2026-10-16) only its facts:
    {agent_type, branch, stopped}. Its text fields (the description, the request) are never taken."""
    with open(path[:-len(".jsonl")] + ".meta.json", encoding="utf-8") as handle:
        meta = json.load(handle)
    branch = meta.get("worktreeBranch")
    return {"agent_type": AGENT_TYPES.get(meta.get("agentType"), "other"),
            "branch": (branch if WORKTREE_BRANCH.match(str(branch)) else OTHER_BRANCH) if branch else None,
            "stopped": meta.get("stoppedByUser") is True}


def moment(timestamp):
    return datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone()


def local_day(timestamp):
    return moment(timestamp).date().isoformat()


def transcript_facts(folder):
    """(messages, compactions). One message per assistant message (its repeated content-block records folded, the
    largest output kept): {model, day, at (epoch seconds), session, agent_file, sidechain, tokens..., cache_1h,
    agent_type, branch, stopped}. One compaction per compact boundary: {session, day, main, trigger, pre_tokens}. Nothing else is kept."""
    messages, compactions = {}, []
    for path, below in transcript_files(folder):
        meta = agent_meta(path) if below else {"agent_type": MAIN, "branch": None, "stopped": False}
        with open(path, encoding="utf-8", errors="replace") as handle:
            for number, line in enumerate(handle):
                if '"assistant"' not in line and '"compact_boundary"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not record.get("timestamp"):
                    continue
                if record.get("type") == "system" and record.get("subtype") == "compact_boundary":
                    facts = record.get("compactMetadata") if isinstance(record.get("compactMetadata"), dict) else {}
                    compactions.append({"session": str(record.get("sessionId")), "day": local_day(record["timestamp"]),
                                        "main": not below, "pre_tokens": int(facts.get("preTokens") or 0),
                                        "trigger": facts.get("trigger") if facts.get("trigger") in ("auto", "manual")
                                        else "other"})
                    continue
                message = record.get("message") if isinstance(record.get("message"), dict) else {}
                usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
                if record.get("type") != "assistant" or not usage:
                    continue
                cache = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
                kept = dict(meta, model=str(message.get("model")), day=local_day(record["timestamp"]),
                            at=int(moment(record["timestamp"]).timestamp()),
                            session=str(record.get("sessionId")), agent_file=os.path.basename(path) if below else None,
                            sidechain=bool(record.get("isSidechain")) or below,
                            cache_1h=int(cache.get("ephemeral_1h_input_tokens") or 0))
                kept.update({k: int(usage.get(k) or 0) for k in TOKENS})
                key = message.get("id") or record.get("requestId") or (path, number)
                if key not in messages or kept["output_tokens"] >= messages[key]["output_tokens"]:
                    messages[key] = kept
    return list(messages.values()), compactions


def price_of(model):
    keys = [k for k in PRICES if model.startswith(k)]
    return PRICES[max(keys, key=len)] if keys else None


def cost(record, which):
    """The API-equivalent dollars of one record at the low (0) or high (1) list price, or None if unpriced."""
    price = price_of(record["model"])
    if price is None:
        return None
    per_in, per_out = price["input"][which] / 1e6, price["output"][which] / 1e6
    write_1h = min(record["cache_1h"], record["cache_creation_input_tokens"])
    return (record["input_tokens"] * per_in + record["output_tokens"] * per_out
            + (record["cache_creation_input_tokens"] - write_1h) * per_in * CACHE_WRITE_5M
            + write_1h * per_in * CACHE_WRITE_1H + record["cache_read_input_tokens"] * per_in * CACHE_READ)


def tally(records):
    """{tokens, calls, runs, cost_low, cost_high, unpriced_calls} of some message records (a run is an agent's
    transcript, or a main session)."""
    row = {"tokens": 0, "calls": 0, "cost_low": 0.0, "cost_high": 0.0, "unpriced_calls": 0}
    for r in records:
        row["calls"] += 1
        row["tokens"] += sum(r[k] for k in TOKENS)
        if price_of(r["model"]) is None:
            row["unpriced_calls"] += 1
        else:
            row["cost_low"] += cost(r, 0)
            row["cost_high"] += cost(r, 1)
    row["runs"] = len({r["agent_file"] or r["session"] for r in records})
    return row


def branch_states(repo, rev, started):
    """{branch: state} at `rev` of each branch of `started` ({branch: when its run began, epoch seconds}). merged: a
    merge on `rev` names it (`Merge worktree-agent-<id>`), or its ref is in `git branch --merged` off the first-parent
    line. fast-forward: its tip is on the first-parent line and was committed after its run began. nothing to merge:
    its tip is on the first-parent line, from before its run (it committed nothing on its own branch; a brief may
    have named another worktree). open: its ref is kept, unmerged.
    no merge found: no ref and no merge names it (never merged, or merged under another name: an upper bound)."""
    named = {m.group(1) for m in map(MERGED_BRANCH.match, git(repo, "log", "--merges", "--format=%s", rev).splitlines())
             if m}
    tips = {name: (sha, int(when)) for name, sha, when in (line.split() for line in git(
        repo, "for-each-ref", "--format=%(refname:short) %(objectname) %(committerdate:unix)",
        "refs/heads/worktree-agent-*").splitlines())}
    merged = set(git(repo, "branch", "--merged", rev, "--list", "worktree-agent-*",
                     "--format=%(refname:short)").split())
    line = set(git(repo, "rev-list", "--first-parent", rev).split())

    def state(name):
        if name in named:
            return "merged"
        if name not in tips:
            return "no merge found"
        if name not in merged:
            return "open"
        sha, when = tips[name]
        if sha not in line:
            return "merged"
        return "fast-forward" if when > started[name] else "nothing to merge"
    return {name: state(name) for name in started}


def agents_section(records, compactions, repo, rev):
    """Tokens by agent type, by branch and of the runs the owner stopped; the main sessions and their compactions."""
    by_type = {t: tally([r for r in records if r["agent_type"] == t]) for t in AGENT_ORDER}
    started = {}
    for r in records:
        if r["branch"] and r["branch"] != OTHER_BRANCH:
            started[r["branch"]] = min(r["at"], started.get(r["branch"], r["at"]))
    states = dict(branch_states(repo, rev, started), **{OTHER_BRANCH: "not a worktree branch"})
    by_branch = {b: dict(tally([r for r in records if r["branch"] == b]), state=states[b])
                 for b in {r["branch"] for r in records if r["branch"]}}
    per_state = {s: dict(tally([r for r in records if r["branch"] and states[r["branch"]] == s]),
                         branches=sum(1 for b in by_branch.values() if b["state"] == s))
                 for s in sorted({b["state"] for b in by_branch.values()})}
    main = [r for r in records if r["agent_type"] == MAIN]
    sessions = {}
    for s in sorted({r["session"] for r in main}):
        context = [sum(r[k] for k in TOKENS) - r["output_tokens"] for r in main if r["session"] == s]
        mine = [c for c in compactions if c["main"] and c["session"] == s]
        sessions[s[:8]] = {"calls": len(context), "average_context": round(sum(context) / len(context)),
                           "max_context": max(context), "compactions": len(mine),
                           "compactions_auto": sum(1 for c in mine if c["trigger"] == "auto"),
                           "tokens_before_compaction": [c["pre_tokens"] for c in mine]}
    contexts = [sum(r[k] for k in TOKENS) - r["output_tokens"] for r in main]
    return {
        "by_agent_type": by_type,
        "review": tally([r for r in records if r["agent_type"] in REVIEW_TYPES]),
        "work": tally([r for r in records if r["agent_type"] in WORK_TYPES]),
        "by_branch": dict(sorted(by_branch.items(), key=lambda kv: -kv[1]["tokens"])), "branches_per_state": per_state,
        "stopped_by_the_owner": tally([r for r in records if r["stopped"]]),
        "main_sessions": sessions,
        "main_average_context": round(sum(contexts) / len(contexts)) if contexts else 0,
        "subagent_compactions": sum(1 for c in compactions if not c["main"]),
    }


def usage_section(folder, days, repo, rev):
    messages, compactions = transcript_facts(folder)
    records = [r for r in messages if r["day"] in days]
    per_model, per_day, sessions, agents, split = {}, {}, {}, {}, {"main": 0, "subagent": 0}
    for r in records:
        total = sum(r[k] for k in TOKENS)
        for table, key in ((per_model, r["model"]), (per_day, (r["day"], r["model"]))):
            row = table.setdefault(key, dict({k: 0 for k in TOKENS}, turns=0, large_context=0, cost_low=0.0,
                                             cost_high=0.0, priced=price_of(r["model"]) is not None))
            row["turns"] += 1
            row["large_context"] += total - r["output_tokens"] > LARGE_CONTEXT
            for k in TOKENS:
                row[k] += r[k]
            if row["priced"]:
                row["cost_low"] += cost(r, 0)
                row["cost_high"] += cost(r, 1)
        sessions.setdefault(r["day"], set()).add(r["session"])
        if r["agent_file"]:
            agents.setdefault(r["day"], set()).add(r["agent_file"])
        split["subagent" if r["sidechain"] else "main"] += total
    return {
        "folder_files": len(transcript_files(folder)), "assistant_messages": len(records),
        "per_model": dict(sorted(per_model.items())),
        "per_day_model": {"%s %s" % k: v for k, v in sorted(per_day.items())},
        "sessions_per_day": {d: len(s) for d, s in sorted(sessions.items())},
        "subagent_files_per_day": {d: len(s) for d, s in sorted(agents.items())},
        "tokens_main_vs_subagent": split,
        "cost_low": sum(v["cost_low"] for v in per_model.values()),
        "cost_high": sum(v["cost_high"] for v in per_model.values()),
        "unpriced_models": sorted(m for m, v in per_model.items() if not v["priced"]),
        "agents": agents_section(records, [c for c in compactions if c["day"] in days], repo, rev),
    }


def percentile(values, share):
    """Nearest rank."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * share) - 1)] if ordered else 0


def lines_between(repo, base, tip):
    """(added, removed) text lines of `tip` against `base` (`git diff --numstat`; a binary file counts 0)."""
    out = git(repo, "diff", "--numstat", "--no-renames", base, tip)
    pairs = [(int(a), int(r)) for a, r, _p in (line.split("\t", 2) for line in out.splitlines())
             if a.isdigit() and r.isdigit()]
    return sum(a for a, _r in pairs), sum(r for _a, r in pairs)


def branch_sizes(repo, shas):
    """[(added, removed)] of each merge's second parent against the merge base."""
    sizes = []
    for sha in shas:
        first, second = git(repo, "rev-parse", sha + "^1", sha + "^2").split()
        sizes.append(lines_between(repo, git(repo, "merge-base", first, second).strip(), second))
    return sizes


def distribution(sizes):
    """Over added lines (the budget's measure), and the old measure (added plus removed) beside it."""
    added, both = [a for a, _r in sizes], [a + r for a, r in sizes]
    return {"count": len(sizes), "median": statistics.median(added) if added else 0, "p90": percentile(added, 0.9),
            "max": max(added) if added else 0, "over_budget": sum(1 for s in added if s > BRANCH_BUDGET),
            "removed": sum(r for _a, r in sizes), "old_median": statistics.median(both) if both else 0,
            "old_over_budget": sum(1 for s in both if s > BRANCH_BUDGET)}


def workers_section(repo, start, end, found):
    worktree = [c["sha"] for c in found if WORKTREE_MERGE.match(c["subject"]) and len(c["parents"]) > 1]
    span = end if start == EMPTY_TREE else "%s..%s" % (start, end)
    trunk = git(repo, "rev-list", "--first-parent", "--merges", span).split()
    trunk_sizes = branch_sizes(repo, trunk)
    by_sha = {c["sha"]: c for c in found}
    return {"worktree_branches": distribution(branch_sizes(repo, worktree)),
            "merges_into_the_trunk": distribution(trunk_sizes),
            "trunk_merges_of_other_branches": sum(1 for sha in trunk if not MERGED_BRANCH.match(by_sha[sha]["subject"])),
            "trunk_merges": [{"sha": sha[:10], "day": by_sha[sha]["day"], "added": a, "removed": r,
                              "subject": by_sha[sha]["subject"][:90] if a > BRANCH_BUDGET else ""}
                             for sha, (a, r) in zip(trunk, trunk_sizes)]}


def branch_budget(repo, rev, ref, budget):
    """{base, added, removed, over} of `ref` against its merge base with `rev`."""
    base = git(repo, "merge-base", rev, ref).strip()
    added, removed = lines_between(repo, base, ref)
    return {"ref": ref, "base": base[:10], "added": added, "removed": removed, "budget": budget,
            "over": added > budget}


def report(repo, rev, transcripts, since, until):
    days = window_days(since, until)
    start = rev_before(repo, rev, "%s 00:00:00" % days[0])
    end = rev_before(repo, rev, "%s 23:59:59" % days[-1])
    day_revs = {d: rev_before(repo, end, "%s 23:59:59" % d) for d in days}
    found = commits(repo, start, end)
    return {"window": {"since": days[0], "until": days[-1], "rev": rev, "start": start, "end": end},
            "git": git_section(repo, start, end, found), "findings": findings_section(repo, start, end, days, day_revs),
            "tests": tests_section(repo, start, end),
            "usage": usage_section(transcripts, days, repo, rev) if os.path.isdir(transcripts) else None,
            "workers": workers_section(repo, start, end, found)}


def table(head, rows):
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
                     + ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]) + "\n"


def markdown(r):
    w, g, f, t, u, k = r["window"], r["git"], r["findings"], r["tests"], r["usage"], r["workers"]
    out = ["# Retro metrics %s .. %s\n\n" % (w["since"], w["until"]),
           "Counted by `tools/retro_metrics.py` from `%s`: the window is %s..%s (the last first-parent commits before its "
           "start and end); days are local commit dates.\n" % (w["rev"], w["start"][:10], w["end"][:10]),
           "\n## 1. Git\n\n", "Commits that reached the trunk in the window (`git log start..end --numstat --no-renames`): "
           "%d, of them %d merges. Lines are summed over the non-merge commits by path (code = tagpup/ web/ tools/ "
           "scripts/, tests = tests/, docs = docs/ .claude/, other = the rest).\n\n" % (g["commits"], g["merges"])]
    rows = [[d, v["commits"], v["merges"]] + ["+%d/-%d" % tuple(v[a]) for a in ("code", "tests", "docs", "other")]
            + [sum(v[a][0] - v[a][1] for a in ("code", "tests", "docs", "other"))] for d, v in g["per_day"].items()]
    rows.append(["total", g["commits"], g["merges"]] + ["+%d/-%d" % tuple(g["lines"][a]) for a in
                ("code", "tests", "docs", "other")] + [sum(a - b for a, b in g["lines"].values())])
    out.append(table(["day", "commits", "merges", "code", "tests", "docs", "other", "net"], rows))
    s, e = g["lines_in_tree"]["start"], g["lines_in_tree"]["end"]
    out.append("\nLines in the tree (`git diff --numstat <empty tree> <rev>`, text files): "
               + ", ".join("%s %d -> %d (%+d)" % (a, s[a], e[a], e[a] - s[a]) for a in s) + ".\n")
    out.append("\n- Files changed outside docs/ and .claude/ (distinct paths in the non-merge numstat): %d\n"
               "- Merges of worktree branches (two parents, subject `Merge worktree-agent-` or `Merge branch "
               "'worktree-agent-`): %d; one-parent commits with such a subject (squashed or re-made): %d\n"
               "- Reverts (subject starts `Revert`): %d\n"
               "- Commits touching only docs/findings* or docs/history/findings_closed_*: %d\n"
               % (g["files_changed_outside_docs"], g["worktree_merges"], g["worktree_merge_subjects_on_one_parent"],
                  g["reverts"], g["findings_only_commits"]))
    out.append("\nCommits per `Co-Authored-By` trailer name (a commit counts once per distinct name):\n\n"
               + table(["trailer", "commits"], g["trailers"].items()))
    out.append("\n## 2. Findings ledger\n\nRows of docs/findings.md and docs/history/findings_closed_*.md "
               "(`git show <rev>:<path>`). Opened = Found date in the window; closed = closed at the end and not at the "
               "start, by the status word now (tools/add_findings.py's CLOSED).\n\n"
               "- Opened: %d; closed: %d; open at the end: %d; highest number: %d\n"
               "- Opened rows whose Finding says review/reviewer/owner (a proxy for review-found): %d\n"
               "- Closed as obsolete or with 'stale' in the status (a proxy for ledger drift): %d\n\n"
               % (f["opened"], f["closed"], f["open_now"], f["highest"], f["opened_naming_review_or_owner"],
                  f["closed_obsolete_or_stale"]))
    out.append(table(["closed as", "rows"], f["closed_per_kind"].items()) + "\n")
    out.append(table(["opened with severity", "rows"], sorted(f["opened_per_severity"].items())) + "\n")
    out.append(table(["day", "opened", "findings.md lines", "open rows"],
                     [[d, f["opened_per_day"].get(d, 0), v["lines"], v["open_rows"]]
                      for d, v in f["findings_md_per_day"].items()]))
    out.append("\n## 3. Tests\n\n`git ls-tree` and `git grep -c` at the start and end revisions (nothing run).\n\n"
               + table(["", "start", "end"], [[key, t["start"][key], t["end"][key]] for key in t["start"]])
               + "\nFiles under tests/ added %d, deleted %d (`git diff --diff-filter=A/D start end -- tests/`); "
               "`Lifted:` lines at the end: %d.\n" % (t["files_added"], t["files_deleted"], t["lifted_lines"]))
    out.append("\n## 4. Claude Code sessions (usage only)\n\n")
    if u is None:
        out.append("No transcript folder.\n")
    else:
        out.append("%d transcript files; %d assistant messages in the window (records of one message id folded). "
                   "Cost is the API-equivalent at PRICES (low..high; edit to the list prices, these are not billing; "
                   "unpriced: %s).\n\n" % (u["folder_files"], u["assistant_messages"], ", ".join(u["unpriced_models"])
                                           or "none"))
        head = ["model", "turns", "input", "output", "cache write", "cache read", "context > %dk" %
                (LARGE_CONTEXT // 1000), "cost USD"]

        def cells(v):
            money = "%.0f..%.0f" % (v["cost_low"], v["cost_high"]) if v["priced"] else "unpriced"
            return [v["turns"]] + [v[k] for k in TOKENS] + [v["large_context"], money]
        out.append(table(head, [[m] + cells(v) for m, v in u["per_model"].items()]))
        split = u["tokens_main_vs_subagent"]
        share = 100.0 * split["subagent"] / max(1, split["main"] + split["subagent"])
        out.append("\nTotal cost %.0f..%.0f USD (priced models only). Tokens (all four kinds) in main-session records "
                   "%d, in subagent records (a file under a session's folder, or isSidechain) %d: %.0f%% subagent.\n\n"
                   % (u["cost_low"], u["cost_high"], split["main"], split["subagent"], share))
        out.append(table(["day model"] + head[1:], [[d] + cells(v) for d, v in u["per_day_model"].items()]))
        out.append("\n" + table(["day", "sessions", "subagent files"],
                                [[d, n, u["subagent_files_per_day"].get(d, 0)] for d, n in u["sessions_per_day"].items()]))
        out.append(agents_markdown(u["agents"], u["tokens_main_vs_subagent"], k["trunk_merges_of_other_branches"]))
    out.append("\n## 6. Workers\n\nLines added and removed by each merge's second parent against the merge base "
               "(`git diff --numstat <base> ^2`). The budget counts lines ADDED (a deletion is never over budget); the "
               "old measure, added plus removed, is beside it so two retros compare.\n\n")
    out.append(table(["merges", "count", "median added", "p90 added", "max added", "over %d added" % BRANCH_BUDGET,
                      "removed", "old: median +/-", "old: over %d +/-" % BRANCH_BUDGET],
                     [[name] + [v[x] for x in ("count", "median", "p90", "max", "over_budget", "removed", "old_median",
                                               "old_over_budget")]
                      for name, v in k.items() if name in ("worktree_branches", "merges_into_the_trunk")]))
    out.append("\nEach merge into the trunk (the subject only of one adding over %d):\n\n" % BRANCH_BUDGET
               + table(["merge", "day", "added", "removed", "over %d added" % BRANCH_BUDGET, "subject"],
                       [[m["sha"], m["day"], m["added"], m["removed"], "over" if m["added"] > BRANCH_BUDGET else "",
                         m["subject"].replace("|", "/")] for m in k["trunk_merges"]]))
    if u is not None and k["merges_into_the_trunk"]["count"]:
        out.append("\nCost per merge into the trunk: %.0f..%.0f USD (the priced total over %d merges).\n"
                   % (u["cost_low"] / k["merges_into_the_trunk"]["count"],
                      u["cost_high"] / k["merges_into_the_trunk"]["count"], k["merges_into_the_trunk"]["count"]))
    out.append("\nNot counted: how many branches were reviewed before their merge and review rounds per branch (a "
               "reviewer's or owner's meta file names no branch: only workers run in a worktree), escapes to the owner, "
               "the time a person spent, and the cost billed.\n")
    return "".join(out)


def money(v):
    return "%.0f..%.0f" % (v["cost_low"], v["cost_high"]) + (" (+%d unpriced calls)" % v["unpriced_calls"]
                                                             if v["unpriced_calls"] else "")


def agents_markdown(a, split, other_merges):
    total = max(1, split["main"] + split["subagent"])
    def row(name, v):
        return [name, v["runs"], v["calls"], v["tokens"], "%.1f%%" % (100.0 * v["tokens"] / total), money(v)]
    head = ["", "runs", "calls", "tokens", "share", "cost USD"]
    out = ["\n## 5. Agents and branches (subagent meta files)\n\nEach subagent transcript's `.meta.json` gives its "
           "agent type (`agentType`), its worktree branch (`worktreeBranch`) and whether the owner stopped it "
           "(`stoppedByUser`); no other field is read. Tokens are all four kinds; share is of all tokens in the window; "
           "cost as in the model table (low..high).\n\n### TOKENS BY AGENT TYPE\n\n",
           table(head, [row(t, v) for t, v in a["by_agent_type"].items() if v["calls"]])]
    review, work = a["review"], a["work"]
    out.append("\nReview work (reviewer, quick reviewer, the five owners): %d calls, %d tokens, %s USD; workers (worker "
               "and small worker): %d calls, %d tokens, %s USD; review is %.0f%% of the two.\n"
               % (review["calls"], review["tokens"], money(review), work["calls"], work["tokens"], money(work),
                  100.0 * review["tokens"] / max(1, review["tokens"] + work["tokens"])))
    out.append("\n### TOKENS BY BRANCH\n\nA worker's run names its worktree branch; its state at --rev: merged (a merge "
               "names it, or `git branch --merged` off the first-parent line), fast-forward (its tip is on the "
               "first-parent line, committed after the run began), nothing to merge (its tip is on the first-parent "
               "line, from before the run: nothing committed on its own branch, its work may be on a worktree its brief "
               "named), open (its ref is kept, unmerged), no merge found (no ref and no merge names "
               "it: never merged, or merged under another branch name, so an upper bound; %d trunk merges in the window "
               "name another branch).\n\n" % other_merges)
    out.append(table(["branch", "state"] + head[1:],
                     [[b, v["state"]] + row(b, v)[1:] for b, v in list(a["by_branch"].items())[:15]]))
    out.append("\n" + table(["state", "branches"] + head[1:],
                             [[s, v["branches"]] + row(s, v)[1:] for s, v in a["branches_per_state"].items()]))
    stopped = a["stopped_by_the_owner"]
    out.append("\n### RUNS STOPPED BY THE OWNER\n\n%d runs (`stoppedByUser`), %d calls, %d tokens, %s USD.\n"
               % (stopped["runs"], stopped["calls"], stopped["tokens"], money(stopped)))
    out.append("\n### MAIN SESSIONS\n\n%d main sessions in the window (distinct session ids of the top-level "
               "transcripts); average context per call %d tokens (input, cache write and cache read). Compactions are the "
               "`compact_boundary` records (`compactMetadata.trigger`, `preTokens`); subagent compactions: %d.\n\n"
               % (len(a["main_sessions"]), a["main_average_context"], a["subagent_compactions"]))
    out.append(table(["session", "calls", "average context", "max context", "compactions", "of them auto",
                      "tokens before each"],
                     [[s, v["calls"], v["average_context"], v["max_context"], v["compactions"], v["compactions_auto"],
                       ", ".join(str(n) for n in v["tokens_before_compaction"])] for s, v in a["main_sessions"].items()]))
    return "".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    today = datetime.date.today()
    parser.add_argument("--since", type=datetime.date.fromisoformat, default=today - datetime.timedelta(days=13))
    parser.add_argument("--until", type=datetime.date.fromisoformat, default=today)
    parser.add_argument("--rev", default="HEAD", help="the trunk to count (default HEAD)")
    parser.add_argument("--repo", default=ROOT)
    parser.add_argument("--transcripts", default=TRANSCRIPTS)
    parser.add_argument("--out", help="write the markdown here instead of printing it")
    parser.add_argument("--json", help="write the numbers as JSON here")
    parser.add_argument("--branch", metavar="REF", help="only the budget guard: REF's lines against its merge base")
    parser.add_argument("--budget", type=int, default=BRANCH_BUDGET, help="added lines a branch may hold (--branch)")
    parser.add_argument("--allow-over", action="store_true", help="report a branch over budget but exit 0")
    args = parser.parse_args(argv)
    if args.branch:
        b = branch_budget(args.repo, args.rev, args.branch, args.budget)
        print("%s: +%d -%d lines against %s (its merge base with %s); budget %d added: %s"
              % (b["ref"], b["added"], b["removed"], b["base"], args.rev, b["budget"],
                 ("OVER" + (", allowed" if args.allow_over else "")) if b["over"] else "within"))
        return 1 if b["over"] and not args.allow_over else 0
    counted = report(args.repo, args.rev, args.transcripts, args.since, args.until)
    text = markdown(counted)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text)
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(text)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(counted, handle, indent=1, default=list)
    return 0


if __name__ == "__main__":
    sys.exit(main())
