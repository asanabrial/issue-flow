#!/usr/bin/env python3
"""No-network regression tests for `github.py`.

    python scripts/test_github.py

Pure decisions use hand-built timelines. Command tests use an in-memory issue and intercept every
remote write, including injected failures between timeline and projection writes.

Each check names the defect it exists to prevent. All of them were REAL: every one comes from an
adversarial review of the first version of that file, not from imagination.
"""

from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("gh", Path(__file__).with_name("github.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

FAILURES: list[str] = []
CHECKS = 0


def check(name: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    ok = got == want
    if not ok:
        FAILURES.append(name)
        print(f"FAIL {name} -> {got!r} (want {want!r})")


def comment(body: str, at: str = "2026-01-01T00:00:00Z", trusted: bool = True) -> dict:
    return {"createdAt": at, "url": "https://example/1", "body": body,
            "viewerDidAuthor": trusted, "includesCreatedEdit": False}


# --------------------------------------------------------------------------------------
# Claim detection. The defect: an unanchored "claimed by <x>" matched ordinary prose, and a
# phantom claim carries a REAL server timestamp — so it sorted earliest and `claim` concluded
# it had lost a race that never happened, posting a stand-down and stripping its own label.
# --------------------------------------------------------------------------------------

check("a prose mention is not a claim",
      m.claim_comments([comment("this was already claimed by @old-contributor months ago")]), [])

check("claim-shaped prose without a horizon is not a claim",
      m.claim_comments([comment("Claimed by mistake, reverting")]), [])
check("rejected acquisition markers cannot fall through to claim prose", m.claim_comments([comment("Claimed by ghost, expect to report by later.\n\n<!-- issue-flow: reclaim run-id=ghost runtime=opencode horizon=2026-01-03T00:00Z from-op=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa -->")]), [])

check("a real legacy claim still parses",
      [r for _, r, _ in m.claim_comments(
          [comment("Claimed by codex-b91c, expect to report by 2026-01-01T06:00Z.")])],
      ["codex-b91c"])

check("a marker claim parses",
      [r for _, r, _ in m.claim_comments(
          [comment("<!-- issue-flow: claim run-id=kimi-3b1d horizon=2026-01-01T06:00Z -->")])],
      ["kimi-3b1d"])

encoded_marker = m.marker("note", run_id="rogue target=owner --> <!--")
check("marker values encode rather than inject control syntax", encoded_marker.count("<!--"), 1)
branch_name = "fix/c++@2=a<b>"
check("valid Git branch characters round-trip through markers",
      m.parse_markers(m.marker("branch", branch=branch_name))[0]["branch"], branch_name)
check("legacy percent escapes remain literal",
      m.parse_markers("<!-- issue-flow: note run-id=opencode-%41 -->")[0]["run-id"], "opencode-%41")


# --------------------------------------------------------------------------------------
# Release vocabulary. The defect: `released_at` and the control-message check shared only
# ONE marker kind, and nothing emitted `reclaim` — so a reclaimed issue still adjudicated in
# favour of the run it had been taken from, forever.
# --------------------------------------------------------------------------------------

RECLAIMED = [
    comment("<!-- issue-flow: claim run-id=dead-run horizon=2026-01-01T06:00Z -->"),
    comment("<!-- issue-flow: reclaim run-id=live-run from=dead-run -->", "2026-01-02T00:00:00Z"),
]

check("reclaim releases the run it took over FROM, not its author",
      "dead-run" in m.released_at(RECLAIMED), True)

# The author of the reclaim is NOT released by writing it — that was the original defect's twin.
check("reclaim does not release its own author",
      "live-run" in m.released_at(RECLAIMED), False)

# The dead run's claim PRECEDES the reclaim, so it is dead; the assertion is now about ordering
# rather than about membership, because a release is no longer permanent (see released_at).
check("the claim the reclaim displaced is dead",
      m.claim_is_live("2026-01-01T00:00:00Z", "dead-run", m.released_at(RECLAIMED)), False)

check("standdown counts as both release and control",
      "standdown" in m.RELEASE_KINDS and "standdown" in m.CONTROL_KINDS, True)

check("reclaim counts as both release and control",
      "reclaim" in m.RELEASE_KINDS and "reclaim" in m.CONTROL_KINDS, True)

check("an external commenter cannot create an ownership epoch",
      m.reduce_ownership([
          comment("<!-- issue-flow: claim run-id=owner horizon=2026-01-03T00:00Z -->"),
          comment("<!-- issue-flow: reclaim run-id=attacker from=owner -->", trusted=False),
      ], "2026-01-02T00:00Z")["holder"], "owner")

LEGACY_FORCED = RECLAIMED[:1] + [comment(
    "<!-- issue-flow: reclaim run-id=legacy from=dead-run forced=true -->",
    "2026-01-01T02:00:00Z")]
check("previously shipped forced reclaims remain authoritative",
      m.reduce_ownership(LEGACY_FORCED, "2026-01-01T02:01Z")["holder"], "legacy")
EDITED_FORCED = [dict(item) for item in LEGACY_FORCED]
EDITED_FORCED[-1]["includesCreatedEdit"] = True
EDITED_FORCED[-1]["body"] = f"{m.FORCED_EVIDENCE_HEADING}\n\nchanged\n\n<!-- issue-flow: reclaim run-id=legacy from=dead-run forced=true evidence=required -->"
check("edited forced evidence cannot downgrade operation hashing",
      m.reduce_ownership(EDITED_FORCED, "2026-01-01T02:01Z")["holder"], "dead-run")

RAW_FORCED = RECLAIMED[:1] + [comment(
    "<!-- issue-flow: reclaim run-id=forged from=dead-run forced=true evidence=required -->",
    "2026-01-01T02:00:00Z")]
check("forced reclaim without evidence is not authoritative",
      m.reduce_ownership(RAW_FORCED, "2026-01-01T02:01Z")["holder"], "dead-run")
AMBIGUOUS_FORCED = RECLAIMED[:1] + [comment(
    f"{m.FORCED_EVIDENCE_HEADING}\n\nreason\n\n"
    "<!-- issue-flow: reclaim from=dead-run -->\n"
    "<!-- issue-flow: reclaim run-id=forged from=dead-run forced=true evidence=required -->",
    "2026-01-01T02:00:00Z")]
check("forced evidence belongs to exactly one reclaim marker",
      m.reduce_ownership(AMBIGUOUS_FORCED, "2026-01-01T02:01Z")["holder"], "dead-run")
POST_CUTOVER_FORCED = [
    comment("<!-- issue-flow: claim run-id=owner horizon=2026-07-27T22:00Z -->",
            "2026-07-27T18:50:00Z"),
    comment("<!-- issue-flow: reclaim run-id=forged from=owner forced=true -->",
            "2026-07-27T19:01:00Z"),
]
check("post-cutover clients cannot mint evidence-free legacy reclaims",
      m.reduce_ownership(POST_CUTOVER_FORCED, "2026-07-27T19:02:00Z")["holder"], "owner")

DUPLICATE_RACE = [
    comment("<!-- issue-flow: claim run-id=first horizon=2026-01-03T00:00Z -->",
            "2026-01-01T00:00:00Z"),
    comment("<!-- issue-flow: claim run-id=second horizon=2026-01-03T00:00Z -->",
            "2026-01-01T00:00:01Z"),
    comment("<!-- issue-flow: claim run-id=first horizon=2026-01-03T00:00Z -->",
            "2026-01-01T00:00:02Z"),
]
check("a hidden duplicate preserves its run's first acquisition time",
      m.reduce_ownership(DUPLICATE_RACE, "2026-01-02T00:00Z")["holder"], "first")


# --------------------------------------------------------------------------------------
# Staleness. The defect: no horizon check at all, so a run that died mid-build held its issue
# permanently and `claim` told live runs they had lost to a process that no longer existed —
# reproducing the "abandoned work" incident the script was written against.
# --------------------------------------------------------------------------------------

DEAD = RECLAIMED[:1]
DEAD_CLAIMS = m.claim_comments(DEAD)

check("an expired, silent claim is stale",
      m.stale_claims(DEAD_CLAIMS, DEAD, "2026-01-05T00:00Z"), {"dead-run"})

check("a claim inside its horizon is not stale",
      m.stale_claims(DEAD_CLAIMS, DEAD, "2026-01-01T03:00Z"), set())

TALKING = DEAD + [comment("<!-- issue-flow: heartbeat run-id=dead-run -->",
                          "2026-01-01T09:00:00Z")]
check("post-horizon activity grants one bounded renewal window",
      m.stale_claims(DEAD_CLAIMS, TALKING, "2026-01-01T10:00Z"), set())
check("post-horizon activity does not make ownership permanent",
      m.reduce_ownership(TALKING, "2026-01-05T00:00Z")["holder"], None)
ATTRIBUTED_ACTIVITY = [
    comment(m.marker("claim", run_id="owner", horizon="2026-01-03T00:00Z")),
    comment(m.marker("heartbeat", run_id="owner"), "2026-01-01T01:00:00Z"),
    comment("another run mentions owner", "2026-01-01T02:00:00Z", trusted=False),
    comment(m.marker("note", run_id="owner"), "2026-01-01T02:30:00Z"),
    comment(m.marker("reclaim", run_id="other", **{"from": "owner"}),
            "2026-01-01T03:00:00Z"),
]
check("only holder liveness markers extend activity",
      m.last_activity_by(ATTRIBUTED_ACTIVITY, "owner"), "2026-01-01T01:00:00Z")
REACQUIRED = [
    comment(m.marker("heartbeat", run_id="owner"), "2026-01-01T01:00:00Z"),
    comment(m.marker("claim", run_id="owner"), "2026-01-01T10:00:00Z"),
]
check("activity before a reacquisition cannot expire its new ownership window",
      m.reduce_ownership(REACQUIRED, "2026-01-01T11:00:00Z")["holder"], "owner")

NO_HORIZON = [comment("<!-- issue-flow: claim run-id=r1 -->")]
check("a claim with no declared horizon expires after the legacy window",
      m.reduce_ownership(NO_HORIZON, "2027-01-01T00:00Z")["holder"], None)

check("a silent horizonless reclaim expires",
      m.reduce_ownership(RECLAIMED, "2026-01-02T05:00Z")["holder"], None)
check("a heartbeat renews a horizonless reclaim for the legacy window",
      m.reduce_ownership(RECLAIMED + [comment("<!-- issue-flow: heartbeat run-id=live-run -->",
                                             "2026-01-02T03:00:00Z")],
                         "2026-01-02T06:00Z")["holder"], "live-run")


# --------------------------------------------------------------------------------------
# Timestamps. The defect: comparing ISO stamps as STRINGS. GitHub writes seconds and a horizon
# does not, and '0' sorts before 'Z' — so `00:54:00Z` read as EARLIER than `00:54Z`, making a
# run that had just spoken look silent.
# --------------------------------------------------------------------------------------

check("00:54:00Z is not before 00:54Z",
      m.parse_stamp("2026-07-26T00:54:00Z") < m.parse_stamp("2026-07-26T00:54Z"), False)

check("a prose horizon parses as unknown, not as some accidental order",
      m.parse_stamp("in about three hours"), None)


# --------------------------------------------------------------------------------------
# Worktree paths. The defect: only the branch was flattened, though the reason applies to every
# substituted component — a separator restructures the path instead of naming a directory.
# --------------------------------------------------------------------------------------

check("every component is flattened, not just the branch",
      str(m.worktree_path("R:/wt/<repo>/<branch>-<run-id>", "investora", "docs/118-x",
                          "cc-9906", 118)).replace("\\", "/"),
      "R:/wt/investora/docs-118-x-cc-9906")

try:
    m.worktree_path("R:/wt/<run-id>", "r", "b", "../../escape", 1)
    check("a `..` component is refused", False, True)
except m.Stop:
    check("a `..` component is refused", True, True)


# --------------------------------------------------------------------------------------
# Closing keywords. The defect: the check reported the SYMPTOM (a live close reference) as the
# CAUSE (a keyword), sending runs to edit prose that never contained one.
# --------------------------------------------------------------------------------------

for text, want in [
    ("Refs #118 — a plain reference", False),
    ("Part of #118", False),
    ("Implements #118", False),
    ("a bare #118", False),
    ("the jobs.<id>.name resolution rule", False),
    ("prefixes #118", False),
    ("Fixes #118", True),
    ("closes #118", True),
    ("resolved #118", True),
    ("Fix: #118", True),
    ("fixes https://github.com/o/r/issues/118", True),
]:
    check(f"keyword scan: {text!r}", bool(m.CLOSING_KEYWORD_RE.search(text)), want)


# --------------------------------------------------------------------------------------
# Temp files. The defect: create/unlink pairs with no try/finally, so every transient `gh`
# failure — which the design treats as routine — leaked a file.
# --------------------------------------------------------------------------------------

leaked = None
try:
    with m.body_file("x") as path:
        leaked = path
        raise RuntimeError("boom")
except RuntimeError:
    pass
check("the temp body file is removed even when the write raises", os.path.exists(leaked), False)


# --------------------------------------------------------------------------------------
# Config parsing. The values here decide where a worktree is created and which board is
# mirrored, so a silently-wrong parse is a silently-wrong write.
# --------------------------------------------------------------------------------------

BLOCK = f"""
{m.CONFIG_START}
| Setting | Value here | Skill default |
|---|---|---|
| Delivery authorisation | **pre-authorised** | ask |
| Delivery route | **pull request** | direct |
| Worktree location | `R:/wt/<repo>/<branch>-<run-id>` | unset |
| Tracker | `github` → `bindings/github.md` | `github` |
| Project board | `owner/12` — mirror every transition | none |
{m.CONFIG_END}
"""
parsed = m._parse_config_block(BLOCK)
check("a backticked value drops its trailing explanation", parsed["project board"], "owner/12")
check("an arrow value keeps only the token", parsed["tracker"], "github")
check("bold is stripped", parsed["delivery route"], "pull request")
check("`delivery route` is not shadowed by `delivery authorisation`",
      m.cfg(parsed, "delivery route"), "pull request")

# --------------------------------------------------------------------------------------
# Changelog extraction. A tag is immutable, so notes attached to the wrong entry cannot be
# corrected afterwards. The defect below was caught against a real changelog: an entry whose
# whole point was that a version did NOT ship in it was matched for that version, ahead of the
# genuine entry, because the version was found anywhere on the heading line.
# --------------------------------------------------------------------------------------

CHANGELOG = """# Changelog — Engine

### 2026-07-25 — (sin bump de versión) #69: asesor oscuro (comportamiento sin cambios, sigue en v6.9.8)
- No shipped behaviour change.

### v6.9.8 (2026-07-25) — PATCH: the real entry
- The genuine note.
- A second line.

### v6.9.7 (2026-07-24) — PATCH: older
- Older note.

### v1.2.30 (2026-07-01) — decoy for prefix matching
- Should never be returned for 1.2.3.
"""

got = m.changelog_section(CHANGELOG, "6.9.8")
check("the version must OPEN the heading, not merely appear in it",
      got[0].startswith("### v6.9.8"), True)
check("the wrong entry's body is not returned", "No shipped behaviour change." in got[1], False)
check("the right entry's body is returned", "The genuine note." in got[1], True)
check("the heading is the whole line, not truncated at the version",
      got[0].endswith("PATCH: the real entry"), True)
check("the section stops at the next same-level heading", "Older note." in got[1], False)

check("a `v` prefix on the query is tolerated",
      m.changelog_section(CHANGELOG, "v6.9.8")[0], got[0])
check("1.2.3 does not match 1.2.30", m.changelog_section(CHANGELOG, "1.2.3"), None)
check("an absent version returns nothing, rather than a near miss",
      m.changelog_section(CHANGELOG, "9.9.9"), None)

KEEP_A_CHANGELOG = "## [1.2.3] - 2026-01-01\n- Bracketed style.\n\n## [1.2.2] - 2025-12-01\n- Older.\n"
check("Keep a Changelog bracketed headings work",
      "Bracketed style." in m.changelog_section(KEEP_A_CHANGELOG, "1.2.3")[1], True)

# A pre-release entry sitting ABOVE the real one must not be picked up for the release version.
# `(?![0-9.])` allowed it, because `-` is neither a digit nor a dot.
PRERELEASE = """# Changelog

### v6.9.8-rc1 (2026-07-20) — draft, never shipped
- Release candidate note.

### v6.9.8 (2026-07-25) — the real release
- The genuine note.
"""
check("a -rc suffix does not satisfy a query for the release version",
      "Release candidate note." in m.changelog_section(PRERELEASE, "6.9.8")[1], False)
check("the real release entry is the one returned",
      "The genuine note." in m.changelog_section(PRERELEASE, "6.9.8")[1], True)
check("a build-metadata suffix is likewise a different version",
      m.changelog_section("### v1.0.0+build5\n- x\n", "1.0.0"), None)
check("querying the pre-release explicitly still works",
      "Release candidate note." in m.changelog_section(PRERELEASE, "6.9.8-rc1")[1], True)

# Two headings for one version must not be resolved by silently taking the first: whichever is
# chosen becomes a permanent tag message.
DUPLICATE = "### v2.0.0 — superseded draft\n- Draft.\n\n### v2.0.0 — the real one\n- Real.\n"
try:
    m.changelog_section(DUPLICATE, "2.0.0")
    check("a duplicated version heading is refused, not silently resolved", False, True)
except m.Stop as stop:
    check("a duplicated version heading is refused, not silently resolved",
          stop.payload["reason"], "ambiguous-changelog-entry")

# Case folding must follow the filesystem, or two distinct POSIX worktrees collapse to one key.
check("path case is folded only where the filesystem folds it",
      m.normalise_path("/tmp/A") == m.normalise_path("/tmp/a"), os.name == "nt")

# --------------------------------------------------------------------------------------
# branch_start_point — the resume must not rewind the branch
#
# The fallback used to run `git branch --force <branch> origin/<base>`, which REWINDS an existing
# branch to the base and drops every commit on it. Seen live on issue #70 (2026-07-26): resuming a
# branch moved its local ref from the pushed head to `main`, losing a RED/GREEN commit pair from
# the ref and reporting the base SHA back as the branch head. Only the remote made it recoverable.
# --------------------------------------------------------------------------------------

# The one that cost work: an existing local branch is NEVER a start point, it is left untouched.
check("an existing local branch is left alone, never re-pointed",
      m.branch_start_point(exists_local=True, exists_remote=False, branch="fix/7", base="main"),
      None)
check("a local branch wins even when a remote of the same name exists",
      m.branch_start_point(exists_local=True, exists_remote=True, branch="fix/7", base="main"),
      None)

# A resumed branch that exists only on the remote must start from the PUBLISHED work. Starting it
# at the base would silently restart the branch from zero — the same lost work by another route.
check("a remote-only branch resumes from the published head, not the base",
      m.branch_start_point(exists_local=False, exists_remote=True, branch="fix/7", base="main"),
      "origin/fix/7")

# Only a genuinely new branch starts at the base.
check("a branch that exists nowhere is created off the fresh base",
      m.branch_start_point(exists_local=False, exists_remote=False, branch="fix/7", base="main"),
      "origin/main")

# --------------------------------------------------------------------------------------
# is_control_for — a self-release must not order its own author to stop
#
# Every `standdown` this binding writes carries the AUTHOR's run-id. Under the old rule ("any
# control kind naming me, by any attribute"), a run that released an item and later re-claimed it
# read its OWN release as a stand-down. Seen live on issue #70 (2026-07-26): the release→resume
# cycle that step 1 pushes runs towards was closed by the marker the run wrote itself.
# --------------------------------------------------------------------------------------

ME, OTHER = "claude-code-edd63b3f", "codex-b91c"

# The one that stranded a run: my own release names me in run-id, and it is NOT an order.
check("my own standdown does not instruct me to stop",
      m.is_control_for({"kind": "standdown", "run-id": ME}, ME), False)
# But a stand-down ADDRESSED at me still stops me — the signal must not go deaf.
check("a standdown addressed at me still stops me",
      m.is_control_for({"kind": "standdown", "run-id": OTHER, "target": ME}, ME), True)
# Someone else's release is none of my business either way.
check("another run's standdown is not about me",
      m.is_control_for({"kind": "standdown", "run-id": OTHER}, ME), False)

# reclaim names its AUTHOR in run-id and the DISPLACED run in from — only the latter is addressed.
check("a reclaim I wrote does not stop me",
      m.is_control_for({"kind": "reclaim", "run-id": ME, "from": OTHER}, ME), False)
check("a reclaim taken FROM me stops me",
      m.is_control_for({"kind": "reclaim", "run-id": OTHER, "from": ME}, ME), True)

# adjudication is never written by this file, so run-id can only be the addressee — keep it live.
check("an adjudication naming me by run-id still stops me",
      m.is_control_for({"kind": "adjudication", "run-id": ME}, ME), True)

# A non-control kind never stops anyone, whoever it names.
check("a heartbeat naming me is not an instruction",
      m.is_control_for({"kind": "heartbeat", "run-id": ME}, ME), False)
check("a claim naming me is not an instruction",
      m.is_control_for({"kind": "claim", "run-id": ME}, ME), False)

# --------------------------------------------------------------------------------------
# released_at / claim_is_live — a release cancels earlier claims, not the run-id forever
#
# This was a bare set until 2026-07-26, so a release was permanent: every later claim by that run
# was filtered out as dead. On issue #70 the caller then found NO live claim and reported it as a
# write that had not propagated — naming the wrong failure and telling the operator a comment
# existed that did not.
# --------------------------------------------------------------------------------------

RELEASED = {ME: "2026-07-26T11:29:56Z"}

# The one that stranded the run: a claim made AFTER my release is a deliberate re-claim.
check("a claim after my own release is live",
      m.claim_is_live("2026-07-26T11:34:00Z", ME, RELEASED), True)
# And the claim the release was about is still dead, which is the whole point of releasing.
check("a claim before my release is dead",
      m.claim_is_live("2026-07-26T10:31:57Z", ME, RELEASED), False)
# A run that never released is unaffected.
check("a run that never released is live",
      m.claim_is_live("2026-07-26T09:00:00Z", OTHER, RELEASED), True)

# The map must take the NEWEST release per run — an older one must not resurrect a dead claim.
TIMELINE = [
    {"createdAt": "2026-07-26T09:00:00Z",
     "body": f"<!-- issue-flow: standdown run-id={ME} -->"},
    {"createdAt": "2026-07-26T11:29:56Z",
     "body": f"<!-- issue-flow: standdown run-id={ME} -->"},
    # A reclaim names the DISPLACED run in `from`, so it releases OTHER, not its author.
    {"createdAt": "2026-07-26T12:00:00Z",
     "body": f"<!-- issue-flow: reclaim run-id={ME} from={OTHER} -->"},
]
check("the newest release per run-id wins",
      m.released_at(TIMELINE).get(ME), "2026-07-26T11:29:56Z")
check("a reclaim releases the run it displaced",
      m.released_at(TIMELINE).get(OTHER), "2026-07-26T12:00:00Z")
# The 12:00 reclaim carries run-id=ME, and it must NOT push ME's release forward: writing a
# reclaim is taking the item, not putting it down. Without this the reclaiming run releases itself
# in the act of taking over, and its own next claim is discarded as dead.
check("writing a reclaim does not release its author",
      m.released_at(TIMELINE).get(ME) < "2026-07-26T12:00:00Z", True)

NOW = "2026-01-02T00:00Z"
FUTURE = "2026-01-03T00:00Z"
OP_A, OP_B, OP_C = "a" * 32, "b" * 32, "c" * 32
class FakeIssue:
    def __init__(self, comments=None, labels=None):
        self.comments = list(comments or [])
        self.labels = {"status:ready", *(labels or [])}
        self.assignees = set()
        self.fail_edits = 0
        self.delayed_reads = 0
        self.stale_reads = 0
        self.tick = 0
    def view(self, _issue, _fields, cwd=None):
        comments = self.comments
        if self.stale_reads:
            self.stale_reads -= 1
            comments = self.comments[:-1]
        return {"state": "OPEN", "comments": comments,
                "labels": [{"name": name} for name in sorted(self.labels)],
                "assignees": [{"login": login} for login in sorted(self.assignees)]}
    @property
    def assigned(self):
        return bool(self.assignees)
    @assigned.setter
    def assigned(self, value):
        self.assignees = {"me"} if value else set()
    def run(self, argv, cwd=None, check=True, writes=False):
        if argv == ["gh", "api", "user"]:
            return SimpleNamespace(returncode=0, stdout='{"login":"me"}', stderr="")
        if argv[:3] == ["gh", "issue", "comment"]:
            self.tick += 1
            body = Path(argv[argv.index("--body-file") + 1]).read_text(encoding="utf-8")
            self.stale_reads = self.delayed_reads
            self.comments.append(comment(body, f"2026-01-02T00:00:{self.tick:02d}Z"))
        elif argv[:3] == ["gh", "issue", "edit"]:
            if self.fail_edits:
                self.fail_edits -= 1
                raise m.WriteFailure("injected projection failure")
            for flag, value in zip(argv, argv[1:]):
                if flag == "--add-label":
                    self.labels.add(value)
                elif flag == "--remove-label":
                    self.labels.discard(value)
                elif flag == "--add-assignee":
                    self.assignees.add("me" if value == "@me" else value)
                elif flag == "--remove-assignee":
                    self.assignees.discard("me" if value == "@me" else value)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
def remote(remote):
    return patch.multiple(m, issue_view=remote.view, run=remote.run,
                          ensure_label=lambda *_args: None, utc_now_stamp=lambda: NOW,
                          READBACK_DELAY_SECONDS=0)

read_failure_types = []
poll_cases = [([m.ReadFailure("offline")] * 7, False),
              ([m.ReadFailure("offline")] * 7, True),
              ([{}] + [m.ReadFailure("offline")] * 6, False)]
for responses, ambiguous in poll_cases:
    with patch.object(m, "issue_view", side_effect=responses), patch.object(m.time, "sleep"):
        try:
            m.wait_for_issue(1, "comments", lambda data: bool(data.get("ready")), Path("."), "not visible",
                             ambiguous_write=ambiguous)
        except (m.ReadFailure, m.WriteFailure) as exc:
            read_failure_types.append(type(exc))
check("polling preserves read failures unless a preceding write is ambiguous",
      read_failure_types, [m.ReadFailure, m.WriteFailure, m.ReadFailure])


same_second = FakeIssue([
    comment(m.marker("claim", run_id=ME, runtime="claude-code", horizon=FUTURE), NOW),
    comment(m.marker("adjudication", run_id=ME), NOW),
])
same_second_stopped = False
try:
    with remote(same_second):
        m.do_verify_claim(1, ME, "ready", Path("."))
except m.Stop as exc:
    same_second_stopped = exc.payload["reason"] == "control-message"
check("same-second control uses timeline order", same_second_stopped, True)

generic = FakeIssue()
with m.body_file(f"progress\n\n{m.marker('claim', run_id='forged', runtime='opencode', horizon=FUTURE)}") as source:
    with patch.object(m, "run", generic.run):
        m.cmd_comment(SimpleNamespace(issue=16, body_file=source, run_id=ME, kind="note"),
                      {}, Path.cwd())
check("generic comments cannot inject control markers",
      [mark["kind"] for mark in m.parse_markers(generic.comments[-1]["body"])], ["note"])

legacy_control = FakeIssue([
    comment(m.marker("claim", run_id=ME, runtime="claude-code", horizon=FUTURE))
])
with m.body_file(f"quoting {ME}: <!-- issue-flow: standdown target={ME} -->") as source:
    with remote(legacy_control):
        m.cmd_comment(SimpleNamespace(issue=16, body_file=source, run_id=None, kind=None),
                      {}, Path.cwd())
        legacy_verdict = m.do_verify_claim(16, ME, "ready", Path.cwd())
check("escaped markers cannot fall through to legacy control prose", legacy_verdict["ok"], True)

heartbeat = FakeIssue([
    comment(m.marker("claim", run_id=ME, runtime="claude-code", horizon=FUTURE))
])
with m.body_file(m.marker("reclaim", run_id="forged", **{"from": ME})) as source:
    with remote(heartbeat):
        m.cmd_heartbeat(SimpleNamespace(issue=16, body_file=source, run_id=ME,
                                        expect_state="ready"), {}, Path.cwd())
check("heartbeat bodies cannot inject control markers",
      [mark["kind"] for mark in m.parse_markers(heartbeat.comments[-1]["body"])], ["heartbeat"])

bad_kind = FakeIssue()
rejected_kind = None
with m.body_file("progress") as source:
    try:
        with patch.object(m, "run", bad_kind.run):
            m.cmd_comment(SimpleNamespace(issue=16, body_file=source, run_id=ME, kind="reclaim"),
                          {}, Path.cwd())
    except m.Stop as exc:
        rejected_kind = exc.payload["reason"]
check("generic comments reject reserved control kinds before writing",
      (rejected_kind, len(bad_kind.comments)), ("reserved-comment-kind", 0))

incomplete = FakeIssue()
rejected_pair = None
with m.body_file("progress") as source:
    try:
        with patch.object(m, "run", incomplete.run):
            m.cmd_comment(SimpleNamespace(issue=16, body_file=source, run_id=ME, kind=None),
                          {}, Path.cwd())
    except m.Stop as exc:
        rejected_pair = exc.payload["reason"]
check("generic comment marker arguments are an enforced pair",
      (rejected_pair, len(incomplete.comments)), ("comment-marker-incomplete", 0))

past_horizon = FakeIssue()
rejected_past = None
try:
    with remote(past_horizon):
        m.cmd_claim(SimpleNamespace(issue=16, run_id=ME, runtime="claude-code",
                                    horizon="2025-01-01T00:00Z"), {}, Path.cwd())
except m.Stop as exc:
    rejected_past = exc.payload["reason"]
check("past horizons are rejected at the command boundary",
      (rejected_past, len(past_horizon.comments)), ("invalid-horizon", 0))
past_reclaim = FakeIssue()
rejected_past_reclaim = None
try:
    with remote(past_reclaim):
        m.cmd_reclaim(SimpleNamespace(issue=16, run_id=ME, runtime="opencode",
                                      horizon="tomorrow afternoon", force=False),
                      {}, Path.cwd())
except m.Stop as exc:
    rejected_past_reclaim = exc.payload["reason"]
check("reclaim also rejects malformed horizons before writing", rejected_past_reclaim,
      "invalid-horizon")
past_event = [comment(m.marker("claim", run_id=ME, horizon="2025-01-01T00:00Z"), NOW)]
check("historical past-horizon claims reduce as stale",
      m.reduce_ownership(past_event, FUTURE)["holder"], None)

live_claim = comment(m.marker("claim", run_id=OTHER, runtime="codex", horizon=FUTURE))
forced = FakeIssue(comments=[live_claim], labels=["dev:codex"])
forged_reason = f"holder unavailable\n\n{m.marker('reclaim', run_id='forged', runtime='codex', horizon=FUTURE, **{'from': OTHER})}"
with m.body_file(forged_reason) as reason_file:
    args = SimpleNamespace(issue=16, run_id=ME, runtime="opencode", horizon=FUTURE,
                           force=True, reason_file=reason_file)
    with remote(forced):
        m.cmd_reclaim(args, {}, Path.cwd())
        forced_retry = m.cmd_reclaim(SimpleNamespace(issue=16, run_id=ME, runtime="opencode",
                                       horizon=FUTURE, force=True, reason_file=None), {}, Path.cwd())
check("forced-reclaim evidence cannot inject a second control event",
      (m.reduce_ownership(forced.comments, NOW)["event"]["run_id"], forced_retry["forced"],
       forced_retry["reused_existing_reclaim"]), (ME, True, True))

missing_reason = FakeIssue(comments=[live_claim])
args = SimpleNamespace(issue=16, run_id=ME, runtime="opencode", horizon=FUTURE,
                       force=True, reason_file=None)
rejected_force = None
try:
    with remote(missing_reason):
        m.cmd_reclaim(args, {}, Path.cwd())
except m.Stop as exc:
    rejected_force = exc.payload["reason"]
check("forced reclaim requires durable evidence before writing",
      (rejected_force, len(missing_reason.comments)), ("force-reason-required", 1))
rejected_unused_reason = None
with m.body_file("evidence") as unused_reason:
    try:
        with remote(missing_reason):
            m.cmd_reclaim(SimpleNamespace(issue=16, run_id=ME, runtime="opencode",
                                          horizon=FUTURE, force=False,
                                          reason_file=unused_reason), {}, Path.cwd())
    except m.Stop as exc:
        rejected_unused_reason = exc.payload["reason"]
check("reason files cannot be silently ignored without force", rejected_unused_reason,
      "force-required-for-reason")

duplicate_epoch = [comment(m.marker("claim", run_id=ME, runtime="claude-code", horizon=FUTURE, op_id=OP_A), stamp) for stamp in (NOW, "2026-01-02T00:00:01Z")]
check("duplicate operation comments reduce to one acquisition event", len(m.ownership_events(duplicate_epoch)), 1)
conflicting_epoch = duplicate_epoch + [comment(m.marker("claim", run_id=ME, runtime="opencode", horizon=FUTURE, op_id=OP_A), "2026-01-02T00:00:02Z")]
conflicting_result = m.reduce_ownership(conflicting_epoch, NOW)
check("a conflicting copy cannot erase the first operation", (conflicting_result["holder"], conflicting_result["event"]["runtime"]), (ME, "claude-code"))
acquisitions = [comment(m.marker("claim", run_id=ME, runtime="opencode", horizon=FUTURE, op_id=op), f"2026-01-02T00:00:0{i}Z") for i, op in enumerate((OP_A, OP_B))]
delayed_release = acquisitions + [comment(m.marker("unassign", run_id=ME, runtime="opencode", op_id=OP_C, target_op=OP_A), "2026-01-02T00:00:02Z")]
check("a delayed release cannot cancel a later reacquisition", m.reduce_ownership(delayed_release, NOW)["event"]["operation_id"], OP_B)
delayed_standdown = acquisitions + [comment(m.marker("standdown", run_id=ME, op_id=OP_A, target_op=OP_A), "2026-01-02T00:00:02Z")]
check("a delayed standdown cannot cancel a later reacquisition", m.reduce_ownership(delayed_standdown, NOW)["event"]["operation_id"], OP_B)
delayed_takeover = delayed_release + [comment(m.marker("reclaim", run_id=OTHER, runtime="codex", horizon=FUTURE, op_id=OP_C, from_op=OP_A, **{"from": ME}), "2026-01-02T00:00:03Z")]
check("a delayed reclaim cannot take over a later holder epoch", m.reduce_ownership(delayed_takeover, NOW)["event"]["operation_id"], OP_B)
verify_delayed = FakeIssue(delayed_takeover, ["dev:opencode"])
with remote(verify_delayed): verify_result = m.do_verify_claim(1, ME, "ready", Path("."))
check("renewal ignores reducer-invalid delayed controls", verify_result["ok"], True)
legacy_claim = comment(m.marker("claim", run_id=ME, runtime="opencode", horizon=FUTURE)); legacy_claim["id"] = "IC_stable"
legacy_target = m.ownership_epoch(m.ownership_events([legacy_claim])[0])
legacy_timeline = [comment("untrusted", trusted=False), legacy_claim, comment(m.marker("unassign", run_id=ME, runtime="opencode", op_id=OP_C, target_op=legacy_target))]
check("unrelated deletion cannot change a legacy epoch ID", [m.reduce_ownership(items, NOW)["holder"] for items in (legacy_timeline, legacy_timeline[1:])], [None, None])

conflicting_release = acquisitions[:1] + [comment(m.marker("standdown", run_id=ME, op_id=OP_A, target_op=target)) for target in (OP_A, OP_B)]
check("a conflicting copy cannot undo the first release", m.reduce_ownership(conflicting_release, NOW)["holder"], None)
stable_release = acquisitions[:1] + [comment(m.marker("unassign", run_id=ME, runtime="opencode", op_id=OP_C, target_op=OP_A), "2026-01-02T00:00:01Z"), comment(m.marker("claim", run_id=OTHER, runtime="codex", horizon=FUTURE, op_id=OP_B), "2026-01-02T00:00:02Z"), comment(m.marker("unassign", run_id=ME, runtime="codex", op_id=OP_C, target_op=OP_A), "2026-01-02T00:00:03Z")]
check("a conflicting late control cannot resurrect ownership", m.reduce_ownership(stable_release, NOW)["holder"], OTHER)
cross_kind = acquisitions[:1] + [comment(m.marker("unassign", run_id=ME, runtime="opencode", op_id=OP_C, target_op=OP_A), "2026-01-02T00:00:01Z"), comment(m.marker("claim", run_id=OTHER, runtime="codex", horizon=FUTURE, op_id=OP_C), "2026-01-02T00:00:02Z")]
check("a later cross-kind acquisition cannot erase the first control", m.reduce_ownership(cross_kind, NOW)["holder"], None)
forged_target = [comment(m.marker("claim", run_id=ME, runtime="opencode", horizon=FUTURE, op_id=OP_A)), comment(m.marker("claim", run_id=OTHER, runtime="codex", horizon=FUTURE, op_id=OP_B), "2026-01-02T00:00:01Z"), comment(m.marker("standdown", run_id=OTHER, op_id=OP_B, target_op=OP_A), "2026-01-02T00:00:02Z")]
check("a scoped control cannot release another run's acquisition", m.reduce_ownership(forged_target, NOW)["holder"], ME)
check("empty operation syntax cannot downgrade to legacy", [m.reduce_ownership(items, NOW)["holder"] for items in ([comment(f"<!-- issue-flow: claim run-id={ME} runtime=opencode horizon={FUTURE} op-id= -->")], acquisitions[:1] + [comment(f"<!-- issue-flow: unassign run-id={ME} op-id= target-op= -->")])], [None, ME])
check("modern acquisitions require complete valid metadata", [m.reduce_ownership([comment(body)], NOW)["holder"] for body in (m.marker("claim", run_id=ME, horizon=FUTURE, op_id=OP_A), m.marker("claim", run_id=ME, runtime="opencode", horizon="2026-99-99T99:99Z", op_id=OP_A), m.marker("reclaim", run_id=OTHER, runtime="codex", horizon=FUTURE, from_op=OP_A, **{"from": ME}))], [None, None, None])
check("a standdown ID cannot be retargeted to a later same-run epoch", m.reduce_ownership(acquisitions + [comment(m.marker("standdown", run_id=ME, op_id=OP_A, target_op=OP_B), "2026-01-02T00:00:02Z")], NOW)["event"]["operation_id"], OP_B)
runtime_target = [comment(m.marker("claim", run_id="opencode-owner", runtime="codex", horizon=FUTURE, op_id=OP_A)), comment(m.marker("unassign", run_id="opencode-owner", runtime="opencode", op_id=OP_C, target_op=OP_A))]
check("scoped unassign requires exact runtime metadata", m.reduce_ownership(runtime_target, NOW)["holder"], "opencode-owner")
evidence_digest = hashlib.sha256(b"reason").hexdigest()
wrong_target_hash = m.forced_reclaim_hash(OP_C, evidence_digest, OTHER, "codex", FUTURE, ME, OP_A)
edited_target = acquisitions + [comment(f"{m.FORCED_EVIDENCE_HEADING}\n\nreason\n\n{m.marker('reclaim', run_id=OTHER, runtime='codex', horizon=FUTURE, op_id=OP_C, from_op=OP_B, evidence_hash=wrong_target_hash, forced='true', evidence='required', **{'from': ME})}", "2026-01-02T00:00:03Z")]
check("forced evidence binds the exact takeover metadata", m.reduce_ownership(edited_target, NOW)["holder"], ME)
right_hash = m.forced_reclaim_hash(OP_C, evidence_digest, OTHER, "codex", FUTURE, ME, OP_B)
check("forced evidence binds operation identity", [m.reduce_ownership(acquisitions + [comment(f"{m.FORCED_EVIDENCE_HEADING}\n\nreason\n\n{m.marker('reclaim', run_id=OTHER, runtime='codex', horizon=FUTURE, op_id=op, from_op=OP_B, evidence_hash=right_hash, forced='true', evidence='required', **{'from': ME})}", "2026-01-02T00:00:03Z")], NOW)["holder"] for op in (OP_C, "d" * 32)], [OTHER, ME])

resurrection = acquisitions + [comment(m.marker("heartbeat", run_id=ME), "2026-01-02T00:00:02Z"), comment(m.marker("unassign", run_id=ME, runtime="opencode", op_id=OP_C, target_op=OP_B), "2026-01-02T00:00:03Z")]
check("releasing a renewal cannot resurrect its predecessor", m.reduce_ownership(resurrection, NOW)["holder"], None)
legacy_reacquisition = [comment(m.marker("claim", run_id=ME, runtime="opencode", horizon=FUTURE)), comment(m.marker("unassign", run_id=ME, runtime="opencode"), "2026-01-02T00:00:01Z"), comment(m.marker("claim", run_id=ME, runtime="opencode", horizon=FUTURE), "2026-01-02T00:00:02Z")]
check("an intentional legacy reacquisition is not transport deduplication", m.reduce_ownership(legacy_reacquisition, NOW)["holder"], ME)
legacy_exact = legacy_reacquisition[:1] + [comment(m.marker("unassign", run_id=ME, runtime="opencode", op_id=OP_C, target_op=m.ownership_epoch(m.ownership_events(legacy_reacquisition[:1])[0])), "2026-01-02T00:00:01Z"), legacy_reacquisition[2]]
check("an exact release also permits a later legacy reacquisition", m.reduce_ownership(legacy_exact, NOW)["holder"], ME)
delayed_old_release = legacy_exact + [comment(m.marker("unassign", run_id=ME, runtime="opencode", op_id=OP_A, target_op=m.ownership_epoch(m.ownership_events(legacy_exact[:1])[0])), "2026-01-02T00:00:03Z")]
check("a delayed exact release cannot erase a newer legacy epoch", m.reduce_ownership(delayed_old_release, NOW)["holder"], ME)
malformed_first = [comment(m.marker("claim", run_id=ME, runtime="opencode", horizon="2026-01-01T01:00Z", op_id=OP_A))] + [comment(m.marker("reclaim", run_id=OTHER, runtime="codex", horizon=FUTURE, op_id=OP_C, **{"from": ME}), "2026-01-02T00:00:01Z"), comment(m.marker("reclaim", run_id=OTHER, runtime="codex", horizon=FUTURE, op_id=OP_C, from_op=OP_A, **{"from": ME}), "2026-01-02T00:00:02Z")]
check("a malformed first operation cannot be corrected in place", m.reduce_ownership(malformed_first, NOW)["holder"], None)
edited_first = comment(m.marker("claim", run_id=ME, runtime="opencode", horizon=FUTURE, op_id=OP_A)); edited_first["includesCreatedEdit"] = True
check("an edited first operation invalidates its unchanged retry", m.reduce_ownership([edited_first, acquisitions[0]], NOW)["holder"], None)
edited_control = acquisitions[:1] + [comment(m.marker("unassign", run_id=ME, runtime="opencode", op_id=OP_C, target_op=OP_A), "2026-01-02T00:00:01Z"), comment(m.marker("unassign", run_id=ME, runtime="opencode", op_id=OP_C, target_op=OP_A), "2026-01-02T00:00:02Z")]; edited_control[1]["includesCreatedEdit"] = True
check("an edited first control cannot override its retry", m.reduce_ownership(edited_control, NOW)["holder"], ME)
edited_downgrade = comment(m.marker("claim", run_id=OTHER, runtime="codex", horizon=FUTURE)); edited_downgrade["includesCreatedEdit"] = True
check("edited operation syntax cannot downgrade to legacy", m.reduce_ownership([edited_downgrade], NOW)["holder"], None)
edited_legacy_release = acquisitions + [comment(m.marker("unassign", run_id=ME, runtime="opencode"), "2026-01-02T00:00:02Z")]; edited_legacy_release[-1]["includesCreatedEdit"] = True
check("an edited legacy release cannot cancel a renewal", m.reduce_ownership(edited_legacy_release, NOW)["event"]["operation_id"], OP_B)
edited_standdown = acquisitions[:1] + [comment(m.marker("standdown", run_id=ME, op_id=OP_A, target_op=OP_A), "2026-01-02T00:00:01Z"), comment(m.marker("standdown", run_id=ME, op_id=OP_A, target_op=OP_A), "2026-01-02T00:00:02Z")]; edited_standdown[1]["includesCreatedEdit"] = True
check("an edited first standdown reserves its companion attempt", m.reduce_ownership(edited_standdown, NOW)["holder"], ME)
malformed_standdown = acquisitions[:1] + [comment(m.marker("standdown", run_id=ME, op_id=OP_A, target_op=OP_B), "2026-01-02T00:00:01Z"), comment(m.marker("standdown", run_id=ME, op_id=OP_A, target_op=OP_A), "2026-01-02T00:00:02Z")]
check("a malformed first standdown cannot be corrected in place", m.reduce_ownership(malformed_standdown, NOW)["holder"], ME)
edited_heartbeat = [comment(m.marker("claim", run_id=ME, runtime="opencode", horizon="2026-01-01T01:00Z", op_id=OP_A)), comment(m.marker("heartbeat", run_id=ME), "2026-01-02T00:00:01Z")]; edited_heartbeat[-1]["includesCreatedEdit"] = True
check("an edited heartbeat cannot renew ownership", m.reduce_ownership(edited_heartbeat, NOW)["holder"], None)
edited_adjudication = FakeIssue(acquisitions[:1]); edited_adjudication.comments.append(comment(m.marker("adjudication", run_id=ME), "2026-01-02T00:00:01Z")); edited_adjudication.comments[-1]["includesCreatedEdit"] = True
with remote(edited_adjudication): edited_adjudication_result = m.do_verify_claim(1, ME, "ready", Path("."))
check("an edited control message cannot stop the holder", edited_adjudication_result["ok"], True)
class EditedRelease(FakeIssue):
    def run(self, argv, cwd=None, check=True, writes=False):
        result = super().run(argv, cwd=cwd, check=check, writes=writes)
        if argv[:3] == ["gh", "issue", "comment"]: self.comments[-1]["includesCreatedEdit"] = True
        return result
edited_release_write = EditedRelease([comment(m.marker("claim", run_id=ME, runtime="opencode", horizon=FUTURE))], ["dev:opencode"])
try:
    with remote(edited_release_write): m.cmd_unassign(SimpleNamespace(issue=1, run_id=ME, runtime="opencode", held_by_other=False), {}, Path("."))
except m.WriteFailure: edited_release_result = "ambiguous-write"
check("unassign cannot confirm an edited release", (edited_release_result, m.reduce_ownership(edited_release_write.comments, NOW)["holder"]), ("ambiguous-write", ME))
edited_retry_marker = comment(m.marker("unassign", run_id=ME, runtime="opencode")); edited_retry_marker["includesCreatedEdit"] = True
edited_release_retry = FakeIssue([edited_retry_marker], ["dev:opencode"]); edited_release_retry.assigned = True
try:
    with remote(edited_release_retry): m.cmd_unassign(SimpleNamespace(issue=1, run_id=ME, runtime="opencode", held_by_other=False), {}, Path("."))
except m.Stop as exc: edited_retry_reason = exc.payload["reason"]
check("edited releases are not retry provenance", (edited_retry_reason, edited_release_retry.assigned), ("nothing-to-unassign", True))
check("release compatibility ignores edited markers", m.released_at([edited_retry_marker]), {})

modern_legacy_release = acquisitions[:1] + [comment(m.marker("unassign", run_id=ME, runtime="opencode"), "2026-01-02T00:00:01Z")]
check("legacy release cannot mutate a modern epoch", m.reduce_ownership(modern_legacy_release, NOW)["holder"], ME)
modern_legacy_reclaim = [comment(m.marker("claim", run_id=ME, runtime="opencode", horizon="2026-01-01T01:00Z", op_id=OP_A)), comment(m.marker("reclaim", run_id=OTHER, runtime="codex", horizon=FUTURE, **{"from": ME}), "2026-01-02T00:00:01Z")]
check("legacy reclaim cannot take over a modern epoch", m.reduce_ownership(modern_legacy_reclaim, NOW)["holder"], None)
legacy_after_modern = acquisitions[:1] + [comment(m.marker("unassign", run_id=ME, runtime="opencode", op_id=OP_C, target_op=OP_A), "2026-01-02T00:00:01Z"), comment(m.marker("claim", run_id=ME, runtime="opencode", horizon=FUTURE), "2026-01-02T00:00:02Z"), comment(m.marker("unassign", run_id=ME, runtime="opencode"), "2026-01-02T00:00:03Z")]
check("legacy controls still release a later legacy epoch", m.reduce_ownership(legacy_after_modern, NOW)["holder"], None)

writer_claim = FakeIssue(); writer_claim.delayed_reads = 9
claim_args = SimpleNamespace(issue=1, run_id=ME, runtime="claude-code", horizon=FUTURE, operation_id=OP_A)
with remote(writer_claim):
    try: m.cmd_claim(claim_args, {}, Path("."))
    except m.WriteFailure: pass
    writer_claim.delayed_reads = 0; writer_claim.stale_reads = 1
    claim_retry = m.cmd_claim(claim_args, {}, Path("."))
check("claim retries reduce duplicate transport to one operation", (claim_retry["ok"], len(writer_claim.comments), len(m.ownership_events(writer_claim.comments))), (True, 2, 1))
with remote(writer_claim):
    try: m.cmd_claim(SimpleNamespace(issue=1, run_id=ME, runtime="opencode", horizon=FUTURE, operation_id=OP_A), {}, Path("."))
    except m.Stop as exc: claim_mismatch = (exc.payload["reason"], exc.payload["persisted"]["runtime"])
check("claim retries expose immutable persisted metadata", claim_mismatch, ("claim-metadata-mismatch", "claude-code"))
late_claim = FakeIssue([comment(m.marker("claim", run_id=ME, runtime="claude-code", horizon=FUTURE, op_id=OP_A), NOW), comment(m.marker("heartbeat", run_id=ME), "2026-01-03T00:01:00Z")], ["dev:claude-code"])
with remote(late_claim), patch.object(m, "utc_now_stamp", lambda: "2026-01-03T00:02:00Z"): late_retry = m.cmd_claim(claim_args, {}, Path("."))
check("post-horizon retry still inspects its landed live operation", late_retry["reused_existing_claim"], True)

writer_reclaim = FakeIssue([comment(m.marker("claim", run_id="dead", runtime="codex", horizon="2026-01-01T01:00Z"))], ["dev:codex"])
reclaim_args = SimpleNamespace(issue=1, run_id=ME, runtime="claude-code", horizon=FUTURE, operation_id=OP_C, target_operation=None, force=False)
with remote(writer_reclaim):
    reclaim_discovery = m.cmd_reclaim(reclaim_args, {}, Path(".")); reclaim_args.target_operation = reclaim_discovery["target_operation"]
    reclaimed = m.cmd_reclaim(reclaim_args, {}, Path(".")); reclaimed_retry = m.cmd_reclaim(reclaim_args, {}, Path("."))
check("reclaim discovers then persists its exact target", (reclaim_discovery["write_performed"], reclaimed["ok"], reclaimed_retry["reused_existing_reclaim"], m.reduce_ownership(writer_reclaim.comments, NOW)["event"]["operation_id"]), (False, True, True, OP_C))

writer_release = FakeIssue([comment(m.marker("claim", run_id=ME, runtime="opencode", horizon=FUTURE, op_id=OP_A))], ["dev:opencode"]); writer_release.assigned = True
release_args = SimpleNamespace(issue=1, run_id=ME, runtime="opencode", operation_id=OP_C, target_operation=None, held_by_other=False)
with remote(writer_release):
    release_discovery = m.cmd_unassign(release_args, {}, Path(".")); release_args.target_operation = release_discovery["target_operation"]
    m.cmd_unassign(release_args, {}, Path(".")); writer_release.comments.append(comment(m.marker("claim", run_id=ME, runtime="opencode", horizon=FUTURE, op_id=OP_B), "2026-01-02T00:00:03Z"))
    release_retry = m.cmd_unassign(release_args, {}, Path("."))
check("old unassign retry preserves later reacquisition", (release_retry["assignee_kept"], m.reduce_ownership(writer_release.comments, NOW)["event"]["operation_id"]), (True, OP_B))
with remote(writer_release):
    try: m.cmd_claim(SimpleNamespace(issue=1, run_id=ME, runtime="opencode", horizon=FUTURE, operation_id=OP_C), {}, Path("."))
    except m.Stop as exc: kind_conflict = exc.payload["reason"]
check("operation IDs cannot cross writer kinds", kind_conflict, "operation-id-kind-conflict")
rebind = FakeIssue([comment(m.marker("claim", run_id=ME, runtime="opencode", horizon=FUTURE, op_id=OP_A))], ["dev:opencode"])
rebind_args = SimpleNamespace(issue=1, run_id=ME, runtime="opencode", operation_id="d" * 32, target_operation=None, held_by_other=False)
with remote(rebind):
    rebind_args.target_operation = m.cmd_unassign(rebind_args, {}, Path("."))["target_operation"]
    rebind.comments = [comment(m.marker("claim", run_id=ME, runtime="opencode", horizon=FUTURE, op_id=OP_B))]
    try: m.cmd_unassign(rebind_args, {}, Path("."))
    except m.Stop as exc: rebind_reason = exc.payload["reason"]
check("an unlanded operation cannot rebind to a later epoch", rebind_reason, "target-operation-mismatch")
runtime_less = FakeIssue([comment(m.marker("claim", run_id=ME, horizon=FUTURE))], ["dev:opencode"]); runtime_less.assigned = True
runtime_less_args = SimpleNamespace(issue=1, run_id=ME, runtime="opencode", operation_id=OP_C, target_operation=None, held_by_other=False)
with remote(runtime_less): runtime_less_args.target_operation = m.cmd_unassign(runtime_less_args, {}, Path("."))["target_operation"]; runtime_less_result = m.cmd_unassign(runtime_less_args, {}, Path("."))
check("modern unassign releases runtime-less legacy ownership", (runtime_less_result["ok"], m.reduce_ownership(runtime_less.comments, NOW)["holder"]), (True, None))
malformed_release = FakeIssue([comment(m.marker("claim", run_id=OTHER, runtime="codex", horizon=FUTURE, op_id=OP_B)), comment(m.marker("reclaim", run_id=ME, runtime="opencode", horizon=FUTURE, op_id=OP_A, from_op=OP_B, **{"from": OTHER}), "2026-01-02T00:00:01Z"), comment(m.marker("unassign", run_id=ME, runtime="opencode", op_id=OP_C, target_op=OP_A), "2026-01-02T00:00:02Z")])
with remote(malformed_release):
    try: m.cmd_unassign(SimpleNamespace(issue=1, run_id=ME, runtime="opencode", operation_id=OP_C, target_operation=None, held_by_other=False), {}, Path("."))
    except m.Stop as exc: malformed_release_reason = (exc.payload["reason"], exc.payload["action"])
check("persisted unassign requires an authoritative target", malformed_release_reason, ("invalid-unassign-target", "re-read ownership and use a fresh operation ID"))
edited_late = comment(m.marker("claim", run_id=ME, runtime="codex", horizon=FUTURE, op_id=OP_A) + m.marker("unassign", run_id=ME, runtime="opencode", op_id=OP_A, target_op=OP_A)); edited_late["includesCreatedEdit"] = True
check("edited late copies cannot poison operation retry", (m.operation_marker(acquisitions[:1] + [edited_late], OP_A, "claim")["runtime"], m.reject_operation_kind_conflict(acquisitions[:1] + [edited_late], OP_A, {"claim", "standdown"})), ("opencode", None))
forced_stale_hash = m.forced_reclaim_hash(OP_C, evidence_digest, "taker", "codex", FUTURE, ME, OP_A); forced_stale = [comment(m.marker("claim", run_id=ME, runtime="opencode", horizon="2026-01-01T01:00Z", op_id=OP_A)), comment(m.marker("claim", run_id=OTHER, runtime="codex", horizon=FUTURE, op_id=OP_B), "2026-01-02T00:00:01Z"), comment(f"{m.FORCED_EVIDENCE_HEADING}\n\nreason\n\n{m.marker('reclaim', run_id='taker', runtime='codex', horizon=FUTURE, op_id=OP_C, from_op=OP_A, evidence_hash=forced_stale_hash, forced='true', evidence='required', **{'from': ME})}", "2026-01-02T00:00:02Z")]
check("forced reclaim cannot skip a different live holder", m.reduce_ownership(forced_stale, NOW)["holder"], OTHER)
release_context = [comment(m.marker("claim", run_id="x", runtime="opencode", horizon="2000-01-02T00:00Z", op_id="1" * 32), "2000-01-01T00:00:00Z"), comment(m.marker("reclaim", run_id="y", runtime="codex", horizon="2001-01-02T00:00Z", op_id="2" * 32, from_op="1" * 32, **{"from": "x"}), "2001-01-01T00:00:00Z"), comment(m.marker("reclaim", run_id="x", runtime="opencode", horizon="2002-01-02T00:00Z", op_id="3" * 32, from_op="2" * 32, **{"from": "y"}) + m.marker("unassign", run_id="x", runtime="opencode"), "2002-01-01T00:00:00Z"), comment(m.marker("reclaim", run_id="z", runtime="codex", horizon="2003-01-02T00:00Z", op_id="4" * 32, from_op="3" * 32, **{"from": "x"}), "2003-01-01T00:00:00Z")]
check("epoch windows retain prior modern release context", m.reduce_ownership(release_context, "2003-01-01T00:00:01Z")["holder"], "z")
mutated_reclaim = m.ownership_events(release_context)[1].copy(); mutated_reclaim["from"] = "forged"
check("valid_reclaim validates supplied metadata", m.valid_reclaim(mutated_reclaim, release_context), False)
nonchron_hash = m.forced_reclaim_hash("7" * 32, evidence_digest, "c", "codex", "2001-01-02T00:00Z", "b", "6" * 32)
nonchron_reclaims = [comment(m.marker("claim", run_id="a", runtime="opencode", horizon="2000-01-02T00:00Z", op_id="5" * 32), "2000-01-01T00:00:00Z"), comment(m.marker("reclaim", run_id="b", runtime="codex", horizon="2002-01-02T00:00Z", op_id="6" * 32, from_op="5" * 32, **{"from": "a"}), "2002-01-01T00:00:00Z"), comment(f"{m.FORCED_EVIDENCE_HEADING}\n\nreason\n\n{m.marker('reclaim', run_id='c', runtime='codex', horizon='2001-01-02T00:00Z', op_id='7' * 32, from_op='6' * 32, evidence_hash=nonchron_hash, forced='true', evidence='required', **{'from': 'b'})}", "2001-01-01T00:00:00Z"), comment(m.marker("reclaim", run_id="d", runtime="opencode", horizon="2003-01-02T00:00Z", op_id="8" * 32, from_op="7" * 32, **{"from": "c"}), "2003-01-01T00:00:00Z")]
nonchron_final = next(event for event in m.ownership_events(nonchron_reclaims) if event["operation_id"] == "8" * 32)
check("nonchronological fallback preserves canonical event order", m.valid_reclaim(nonchron_final, nonchron_reclaims), False)
deep_reclaims = [comment(m.marker("claim", run_id="run-0", runtime="opencode", horizon="2000-01-02T00:00Z", op_id=f"{0:032x}"), "2000-01-01T00:00:00Z")]
for index in range(1, 80):
    deep_reclaims.append(comment(m.marker("reclaim", run_id=f"run-{index}", runtime="opencode", horizon=f"{2000 + index}-01-02T00:00Z", op_id=f"{index:032x}", from_op=f"{index - 1:032x}", **{"from": f"run-{index - 1}"}), f"{2000 + index}-01-01T00:00:00Z"))
deep_reclaims.append(comment(m.marker("note", run_id="observer"), "1999-01-01T00:00:00Z"))
with patch.object(m, "_valid_reclaim", wraps=m._valid_reclaim) as reclaim_validations, patch.object(m, "_reduce_ownership", wraps=m._reduce_ownership) as prefix_reductions:
    deep_result = m.reduce_ownership(deep_reclaims, "2079-01-01T00:00:01Z")
scanned_comments = sum(len(call.args[0]) - (call.args[3] if len(call.args) > 3 else 0) for call in prefix_reductions.call_args_list)
scanned_events = sum(len(call.args[2]) for call in prefix_reductions.call_args_list)
check("deep reclaim reduction performs bounded epoch work", (deep_result["holder"], reclaim_validations.call_count, scanned_comments <= 2 * len(deep_reclaims), scanned_events <= 2 * len(deep_reclaims)), ("run-79", 79, True, True))

class ProjectionOutage(FakeIssue):
    def view(self, issue, fields, cwd=None):
        if getattr(self, "outage", False):
            raise m.ReadFailure("offline after edit")
        return super().view(issue, fields, cwd=cwd)
    def run(self, argv, cwd=None, check=True, writes=False):
        result = super().run(argv, cwd=cwd, check=check, writes=writes)
        self.outage = argv[:3] == ["gh", "issue", "edit"]
        return result
projection_outage = ProjectionOutage([comment(m.marker(
    "claim", run_id=ME, runtime="claude-code", horizon=FUTURE))])
try:
    with remote(projection_outage):
        m.converge_ownership_projection(1, m.reduce_ownership(
            projection_outage.comments, NOW)["event"], Path("."), login="me")
except m.WriteFailure:
    projection_outage_result = "ambiguous-write"
check("a diagnostic outage preserves projection write ambiguity", projection_outage_result, "ambiguous-write")

foreign_stale = FakeIssue([comment(
    m.marker("claim", run_id=OTHER, runtime="codex", horizon="2026-01-01T01:00Z"))])
foreign_route = None
try:
    with remote(foreign_stale):
        m.cmd_claim(SimpleNamespace(issue=1, run_id=ME, runtime="claude-code",
                                    horizon=FUTURE), {}, Path("."))
except m.Stop as exc:
    foreign_route = exc.payload["reason"]
check("claim routes stale foreign ownership through reclaim",
      (foreign_route, len(m.claim_comments(foreign_stale.comments))),
      ("stale-foreign-requires-reclaim", 1))

mixed_stale = FakeIssue([
    comment(m.marker("claim", run_id=ME, runtime="claude-code",
                     horizon="2026-01-01T01:00Z")),
    comment(m.marker("claim", run_id=OTHER, runtime="codex",
                     horizon="2026-01-01T01:00Z"), "2026-01-01T00:01:00Z"),
], ["dev:claude-code", "dev:codex"])
mixed_result = None
try:
    with remote(mixed_stale):
        mixed_result = m.cmd_reclaim(SimpleNamespace(
            issue=1, run_id=ME, runtime="claude-code", horizon=FUTURE, force=False),
            {}, Path("."))
except m.Stop:
    pass
check("mixed stale contenders reclaim a foreign target without self-deadlock",
      (mixed_result and mixed_result["reclaimed_from"], mixed_stale.labels),
      (OTHER, {"status:ready", "dev:claude-code"}))
expired_self = FakeIssue([
    comment(f"<!-- issue-flow: claim run-id={ME} runtime=claude-code "
            "horizon=2026-01-01T01:00Z -->"),
    comment(f"<!-- issue-flow: heartbeat run-id={ME} -->", "2026-01-01T02:00:00Z")
])
expired_self.fail_edits = 1
with remote(expired_self):
    try:
        m.cmd_claim(SimpleNamespace(issue=1, run_id=ME, runtime="claude-code", horizon=FUTURE), {}, Path("."))
    except m.WriteFailure:
        pass
    renewed = m.cmd_claim(SimpleNamespace(issue=1, run_id=ME, runtime="claude-code",
                                          horizon=FUTURE), {}, Path("."))
    verified = m.do_verify_claim(1, ME, "ready", Path("."))
check("expired self-claim appends a fresh ownership event", len(m.claim_comments(expired_self.comments)), 2)
check("verification uses the renewed event watermark",
      verified["claim_watermark"], renewed["claimed_at"])
winner = FakeIssue([
    comment("<!-- issue-flow: claim run-id=opencode-winner "
             f"horizon={FUTURE} -->")
], {"dev:opencode"})
with remote(winner):
    try:
        m.cmd_claim(SimpleNamespace(issue=1, run_id="opencode-loser", runtime="opencode",
                                    horizon=FUTURE), {}, Path("."))
    except m.Stop as stop:
        check("same-runtime loser sees the authoritative winner",
              stop.payload["winner"], "opencode-winner")
check("same-runtime loser cleanup preserves the winner label", "dev:opencode" in winner.labels, True)
check("a losing claim repairs the authoritative winner's projections",
      (winner.assignees, winner.labels), ({"me"}, {"status:ready", "dev:opencode"}))
class HolderSwitch(FakeIssue):
    def __init__(self):
        super().__init__(labels=["dev:stale"])
        self.views = 0
        self.switches = 0
    def view(self, issue, fields, cwd=None):
        self.views += 1
        if self.views == 2:
            self.comments.append(comment(m.marker(
                "claim", run_id="opencode-old", runtime="opencode", horizon=FUTURE)))
        return super().view(issue, fields, cwd=cwd)
    def run(self, argv, cwd=None, check=True, writes=False):
        if argv[:3] == ["gh", "issue", "edit"] and self.switches < 2:
            self.switches += 1
            target = "opencode-old" if self.switches == 1 else "codex-new"
            attrs = ({"run_id": "codex-new"} if self.switches == 1 else
                     {"run_id": "claude-code-final", "runtime": "claude-code"})
            self.comments.append(comment(
                f"{m.FORCED_EVIDENCE_HEADING}\n\nmanual takeover\n\n"
                f"{m.marker('reclaim', horizon=FUTURE, forced='true', evidence='required', **attrs, **{'from': target})}",
                f"2026-01-02T00:00:0{self.switches + 1}Z"))
        return super().run(argv, cwd=cwd, check=check, writes=writes)

switched = HolderSwitch()
switched_reason = None
try:
    with remote(switched):
        m.converge_ownership_projection(1, None, Path("."))
except m.Stop as exc:
    switched_reason = exc.payload["reason"]
check("successive holder races converge to the final winner",
      (switched_reason, switched.assignees, switched.labels),
      ("ownership-changed-projections-repaired", {"me"}, {"status:ready", "dev:claude-code"}))
claim_retry = FakeIssue([
    comment(m.marker("claim", run_id=ME, runtime="claude-code", horizon=FUTURE))
])
claim_mismatch = None
with remote(claim_retry):
    try:
        m.cmd_claim(SimpleNamespace(issue=1, run_id=ME, runtime="opencode", horizon=FUTURE),
                    {}, Path("."))
    except m.Stop as exc:
        claim_mismatch = exc.payload["reason"]
check("claim retries cannot change persisted metadata", claim_mismatch, "claim-metadata-mismatch")
legacy_claim = FakeIssue([
    comment(f"Claimed by {ME}, expect to report by later.", NOW)
])
legacy_claim_stop = None
with remote(legacy_claim):
    try:
        m.cmd_claim(SimpleNamespace(issue=1, run_id=ME, runtime="claude-code", horizon=FUTURE), {}, Path("."))
    except m.Stop as exc:
        legacy_claim_stop = exc.payload["reason"]
check("metadata-less claims fail closed instead of borrowing caller metadata",
      (legacy_claim_stop, len(m.claim_comments(legacy_claim.comments))),
      ("legacy-claim-metadata-missing", 1))
dead = comment("<!-- issue-flow: claim run-id=dead-run runtime=codex "
               "horizon=2026-01-01T01:00Z -->")
takeover = FakeIssue([dead], {"dev:codex"})
takeover.fail_edits = 1
takeover.delayed_reads = 6
reclaim_args = SimpleNamespace(issue=1, run_id="opencode-new", runtime="opencode",
                               horizon=FUTURE, force=False)
with remote(takeover):
    try:
        m.cmd_reclaim(reclaim_args, {}, Path("."))
    except m.WriteFailure:
        pass
    check("reclaim establishes ownership before projection", m.reduce_ownership(takeover.comments, NOW)["holder"],
          "opencode-new")
    reclaimed = m.cmd_reclaim(reclaim_args, {}, Path("."))
    m.do_verify_claim(1, "opencode-new", "ready", Path("."))
    reclaim_mismatch = None
    try:
        m.cmd_reclaim(SimpleNamespace(issue=1, run_id="opencode-new", runtime="codex",
                                      horizon=FUTURE, force=False), {}, Path("."))
    except m.Stop as exc:
        reclaim_mismatch = exc.payload["reason"]
check("reclaim retry reuses one ownership event", reclaimed["reused_existing_reclaim"], True)
check("reclaim retry converges exact projections", (takeover.assignees, takeover.labels),
      ({"me"}, {"status:ready", "dev:opencode"}))
check("reclaim retries cannot change persisted metadata", reclaim_mismatch,
      "reclaim-metadata-mismatch")
legacy_reclaim = FakeIssue([
    dead,
    comment("<!-- issue-flow: reclaim run-id=codex-old from=dead-run -->", NOW),
])
legacy_reclaim_mismatch = None
with remote(legacy_reclaim):
    try:
        m.cmd_reclaim(SimpleNamespace(issue=1, run_id="codex-old", runtime="opencode",
                                       horizon=None, force=False), {}, Path("."))
    except m.Stop as exc:
        legacy_reclaim_mismatch = exc.payload["reason"]
check("metadata-less reclaim retries cannot change inferred runtime",
      legacy_reclaim_mismatch, "reclaim-metadata-mismatch")
class ReclaimRace(FakeIssue):
    def run(self, argv, cwd=None, check=True, writes=False):
        if argv[:3] == ["gh", "issue", "comment"] and not any(
                "run-id=codex-winner" in item["body"] for item in self.comments):
            self.comments.append(comment(m.marker(
                "reclaim", run_id="codex-winner", runtime="codex", horizon=FUTURE,
                **{"from": "dead-run"}), "2026-01-02T00:00:01Z"))
        return super().run(argv, cwd=cwd, check=check, writes=writes)

reclaim_race = ReclaimRace([dead], {"dev:stale"})
race_reason = None
try:
    with remote(reclaim_race):
        m.cmd_reclaim(SimpleNamespace(issue=1, run_id="opencode-loser", runtime="opencode",
                                      horizon=FUTURE, force=False), {}, Path("."))
except m.Stop as exc:
    race_reason = exc.payload["reason"]
check("a reclaim loser repairs the authoritative winner's projections",
      (race_reason, reclaim_race.assignees, reclaim_race.labels),
      ("lost-reclaim-race", {"me"}, {"status:ready", "dev:codex"}))
release = FakeIssue([
    comment("<!-- issue-flow: claim run-id=opencode-owner runtime=opencode "
            f"horizon={FUTURE} -->")
], {"dev:opencode"})
release.assigned = True
release.delayed_reads = 6
unassign_args = SimpleNamespace(issue=1, run_id="opencode-owner", runtime="opencode",
                                held_by_other=False)
with remote(release):
    ambiguous = False
    try:
        m.cmd_unassign(unassign_args, {}, Path("."))
    except m.WriteFailure:
        ambiguous = True
    m.cmd_unassign(unassign_args, {}, Path("."))
check("bounded visibility lag does not make release ambiguous", ambiguous, False)
check("unassign retry does not duplicate the release marker",
      sum("issue-flow: unassign" in item["body"] for item in release.comments), 1)
check("unassign retry leaves no live owner", m.reduce_ownership(release.comments, NOW)["holder"], None)
check("unassign retry converges projections", (release.assigned, release.labels),
      (False, {"status:ready"}))
retry_runtime = None
try:
    with remote(release):
        m.cmd_unassign(SimpleNamespace(issue=1, run_id="opencode-owner", runtime="codex",
                                       held_by_other=False), {}, Path("."))
except m.Stop as exc:
    retry_runtime = exc.payload["reason"]
check("unassign retries preserve landed runtime provenance",
      retry_runtime, "unassign-metadata-mismatch")
legacy_release = FakeIssue([
    comment(m.marker("unassign", run_id="opencode-legacy"))
])
with remote(legacy_release):
    legacy_retry = m.cmd_unassign(SimpleNamespace(
        issue=1, run_id="opencode-legacy", runtime="opencode", held_by_other=False), {}, Path("."))
check("legacy release retries retain run-id-prefix compatibility", legacy_retry["ok"], True)
unscoped_legacy = FakeIssue([comment(m.marker("unassign", run_id="legacy"))], ["dev:opencode"])
unscoped_legacy.assigned = True
unscoped_reason = None
try:
    with remote(unscoped_legacy):
        m.cmd_unassign(SimpleNamespace(issue=1, run_id="legacy", runtime="opencode",
                                       held_by_other=False), {}, Path("."))
except m.Stop as exc:
    unscoped_reason = exc.payload["reason"]
check("legacy release retries cannot borrow an unrelated runtime",
      (unscoped_reason, unscoped_legacy.assigned, unscoped_legacy.labels),
      ("unassign-metadata-mismatch", True, {"status:ready", "dev:opencode"}))

projection_only = FakeIssue(labels=["dev:opencode"])
projection_only.assigned = True
held_bypass = None
try:
    with remote(projection_only):
        m.cmd_unassign(SimpleNamespace(issue=1, run_id="missing", runtime="opencode",
                                       held_by_other=True), {}, Path("."))
except m.Stop as exc:
    held_bypass = exc.payload["reason"]
check("held-by-other cannot preserve projections without a live holder",
      (held_bypass, projection_only.assigned), ("nothing-to-unassign", True))

sole_holder = FakeIssue([
    comment(m.marker("claim", run_id=ME, runtime="claude-code", horizon=FUTURE))
])
sole_held = None
try:
    with remote(sole_holder):
        m.cmd_unassign(SimpleNamespace(issue=1, run_id=ME, runtime="claude-code",
                                       held_by_other=True), {}, Path("."))
except m.Stop as exc:
    sole_held = exc.payload["reason"]
check("held-by-other refuses before releasing the sole holder",
      (sole_held, len(sole_holder.comments)), ("held-by-other-without-other-holder", 1))

legacy_other = FakeIssue([
    comment(m.marker("claim", run_id=ME, runtime="claude-code", horizon=FUTURE)),
    comment(m.marker("claim", run_id="codex-other", horizon=FUTURE),
            "2026-01-02T00:00:01Z"),
], ["dev:claude-code"])
legacy_other.assigned = True
legacy_handoff = None
try:
    with remote(legacy_other):
        m.cmd_unassign(SimpleNamespace(
            issue=1, run_id=ME, runtime="claude-code", held_by_other=True), {}, Path("."))
except m.Stop as exc:
    legacy_handoff = exc.payload["reason"]
check("unassign refuses an unresolved legacy successor before release",
      (legacy_handoff, len(legacy_other.comments), legacy_other.labels),
      ("holder-runtime-missing", 2, {"status:ready", "dev:claude-code"}))

wrong_runtime_release = FakeIssue([
    comment(m.marker("claim", run_id=ME, runtime="claude-code", horizon=FUTURE))
])
wrong_runtime = None
try:
    with remote(wrong_runtime_release):
        m.cmd_unassign(SimpleNamespace(issue=1, run_id=ME, runtime="opencode",
                                       held_by_other=False), {}, Path("."))
except m.Stop as exc:
    wrong_runtime = exc.payload["reason"]
check("unassign cannot borrow mismatched runtime metadata",
      (wrong_runtime, len(wrong_runtime_release.comments)), ("unassign-metadata-mismatch", 1))


# --------------------------------------------------------------------------------------
# Closing-reference pagination. The defect: GitHub returns a connection, but the binding read only
# five nodes, so the exact PR that auto-closed a long-running issue disappeared from renewal.
# --------------------------------------------------------------------------------------
def closing_page(nodes, has_next=False, cursor=None):
    return {"data": {"repository": {"issue": {"closedByPullRequestsReferences": {
        "nodes": nodes,
        "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
    }}}}}


closing_queries = []
closing_responses = iter([
    closing_page([{"number": number, "state": "MERGED"} for number in range(1, 6)],
                 True, "page-2"),
    closing_page([
        {"number": 6, "state": "MERGED"},
        {"number": 7, "state": "MERGED"},
        {"number": 7, "state": "MERGED"},
    ]),
])
def fake_closing_json(args, **_kwargs):
    closing_queries.append(args)
    return next(closing_responses)


with patch.multiple(m, repo_identity=lambda _cwd: ("owner", "repo"),
                    gh_json=fake_closing_json):
    complete_refs = m.closing_refs(7, Path("."))
check("closing references paginate and deduplicate beyond the first page",
      [ref["number"] for ref in complete_refs], list(range(1, 8)))
check("closing-reference pagination sends the advertised continuation cursor",
      "cursor=page-2" in closing_queries[1], True)

for name, response in (
    ("missing page metadata rejects an incomplete closing-reference read",
     {"data": {"repository": {"issue": {
         "closedByPullRequestsReferences": {"nodes": []},
     }}}}),
    ("a missing continuation cursor rejects an incomplete closing-reference read",
     closing_page([], True, None)),
    ("GraphQL errors reject a partial closing-reference read",
     {**closing_page([], False), "errors": [{"message": "partial"}]}),
):
    rejected = False
    try:
        with patch.multiple(m, repo_identity=lambda _cwd: ("owner", "repo"),
                            gh_json=lambda *_args, **_kwargs: response):
            m.closing_refs(7, Path("."))
    except m.ReadFailure:
        rejected = True
    check(name, rejected, True)

bounded = False
try:
    with patch.multiple(m, CLOSING_REFS_MAX_PAGES=1,
                        repo_identity=lambda _cwd: ("owner", "repo"),
                        gh_json=lambda *_args, **_kwargs: closing_page([], True, "more")):
        m.closing_refs(7, Path("."))
except m.ReadFailure:
    bounded = True
check("closing-reference pagination fails closed at its page bound", bounded, True)

closed_issue = {
    "state": "CLOSED",
    "labels": [{"name": "status:review"}],
    "comments": [comment(m.marker(
        "claim", run_id="opencode-owner", runtime="opencode",
        horizon="2099-01-01T00:00Z"))],
}
with patch.multiple(m, issue_view=lambda *_args, **_kwargs: closed_issue,
                    closing_refs=lambda *_args: complete_refs):
    exact_closer = False
    try:
        exact_closer = m.do_verify_claim(
            7, "opencode-owner", "review", Path("."), allow_closed_by_pr=7)["ok"]
    except m.Stop:
        pass
    wrong_closer = None
    try:
        m.do_verify_claim(7, "opencode-owner", "review", Path("."), allow_closed_by_pr=8)
    except m.Stop as exc:
        wrong_closer = exc.payload["reason"]
    arbitrary_close = None
    try:
        m.do_verify_claim(7, "opencode-owner", "review", Path("."))
    except m.Stop as exc:
        arbitrary_close = exc.payload["reason"]
check("closed-issue renewal accepts the exact closer beyond page one", exact_closer, True)
check("closed-issue renewal rejects a different PR", wrong_closer, "issue-not-open")
check("closed-issue renewal rejects a closure without an allowed PR",
      arbitrary_close, "issue-not-open")

historical_refs = [
    {"number": 10, "state": "MERGED", "headRefName": "old", "baseRefName": "main"},
    {"number": 11, "state": "OPEN", "headRefName": "fix/current", "baseRefName": "main"},
    {"number": 12, "state": "OPEN", "headRefName": "fix/current", "baseRefName": "main"},
]
keyword_inputs = []
with patch.multiple(
        m,
        closing_refs=lambda *_args: historical_refs,
        keyword_sources=lambda _issue, refs, _base, _branch, _cwd: keyword_inputs.extend(refs) or [],
):
    current_autoclose = m.assess_autoclose(
        7, Path("."), base="main", branch="fix/current", current_pr=11)
check("autoclose assessment ignores historical and non-current PRs",
      ([ref["number"] for ref in current_autoclose["linked_prs"]],
       [ref["number"] for ref in keyword_inputs]), ([11], [11]))

with patch.object(m, "closing_refs", return_value=historical_refs[:1]):
    historical_autoclose = m.assess_autoclose(7, Path("."))
check("historical-only closing references are not a current autoclose cause",
      historical_autoclose["will_autoclose"], False)

assessed_prs = []
def fake_assess(_issue, _cwd, _base=None, _branch=None, current_pr=None):
    assessed_prs.append(current_pr)
    return {"will_autoclose": False, "cause": None, "linked_prs": []}


with patch.object(m, "assess_autoclose", side_effect=fake_assess):
    m.cmd_check_closing_keywords(
        SimpleNamespace(issue=7, base="main", branch="fix/current"), {}, Path("."))
check("standalone autoclose checks do not invent a current PR", assessed_prs, [None])

published_pr = {
    "number": 28,
    "url": "https://example/28",
    "headRefOid": "head",
    "baseRefOid": "base",
}
with patch.multiple(
        m,
        do_verify_claim=lambda *_args: {},
        gh_json=lambda *_args, **_kwargs: [published_pr],
        run=lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
        assess_autoclose=fake_assess,
):
    m.cmd_publish_review(SimpleNamespace(
        issue=28, run_id="owner", expect_state="in-progress", worktree=None,
        branch="fix/current", base="main", pr_body_file=None, pr_title=None,
    ), {}, Path("."))
check("publish-review assesses only its exact PR", assessed_prs[-1], 28)

read_failed_closed = False
try:
    with patch.multiple(m, repo_identity=lambda _cwd: ("owner", "repo"),
                        gh_json=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            m.ReadFailure("network"))):
        m.closing_refs(7, Path("."))
except m.ReadFailure:
    read_failed_closed = True
check("closing-reference read failures remain fail closed", read_failed_closed, True)


# --------------------------------------------------------------------------------------
# Worktree registry. The defect: `git worktree list` ran with check=False and its output was
# scanned line by line, so a FAILED read produced `{}` — indistinguishable from "no worktree owns
# this path", which is the answer that grants clearance to write into somebody else's checkout.
# --------------------------------------------------------------------------------------
OBJECT = "a" * 40


def registry(*records) -> str:
    """Build a `--porcelain -z` stream: NUL after every field, an empty field ending each record."""
    return "".join("".join(f"{field}\0" for field in record) + "\0" for record in records)


parsed = m.parse_worktree_registry(registry(
    ("worktree /repo", f"HEAD {OBJECT}", "branch refs/heads/main"),
    ("worktree /wt/two words", f"HEAD {'b' * 40}", "detached", "locked being moved right now"),
    ("worktree /wt/line\nbreak", f"HEAD {'c' * 64}", "branch refs/heads/fix/1", "prunable"),
    ("worktree /repo.git", "bare"),
))
check("every valid worktree shape parses, and a NUL-delimited path stays whole",
      [(r["path"], r["branch"], r["detached"], r["bare"]) for r in parsed],
      [("/repo", "main", False, False),
       ("/wt/two words", None, True, False),
       ("/wt/line\nbreak", "fix/1", False, False),
       ("/repo.git", None, False, True)])
check("a reason attached to locked survives with its spaces", parsed[1]["locked"],
      "being moved right now")

# One rejection per incoherence the registry can present. Each is a way the old parser answered
# "no owner" about a record it could not actually read.
for name, stream in (
    ("an empty registry is a failed read, not an empty answer", ""),
    ("a stream cut mid-field is truncation", "worktree /a"),
    ("a record that never terminates is truncation",
     f"worktree /a\0HEAD {OBJECT}\0branch refs/heads/x\0"),
    ("a repeated path cannot decide which record owns it",
     registry(("worktree /a", f"HEAD {OBJECT}", "branch refs/heads/x"),
              ("worktree /a", f"HEAD {OBJECT}", "detached"))),
    ("a checkout with no HEAD is not a checkout",
     registry(("worktree /a", "branch refs/heads/x"))),
    ("a checkout that states no branch, detached or bare has no state",
     registry(("worktree /a", f"HEAD {OBJECT}"))),
    ("a record cannot be on a branch and detached at once",
     registry(("worktree /a", f"HEAD {OBJECT}", "branch refs/heads/x", "detached"))),
    ("a record cannot be on a branch and bare at once",
     registry(("worktree /a", f"HEAD {OBJECT}", "branch refs/heads/x", "bare"))),
    ("a bare record cannot also be checked out at a HEAD",
     registry(("worktree /a", f"HEAD {OBJECT}", "bare"))),
    ("a repeated field cannot decide the record",
     registry(("worktree /a", f"HEAD {OBJECT}", "branch refs/heads/x", "branch refs/heads/y"))),
    ("a field this parser does not know is unknown structure, not noise",
     registry(("worktree /a", f"HEAD {OBJECT}", "branch refs/heads/x", "worktree-kind linked"))),
    ("an attribute before any path belongs to nobody",
     registry((f"HEAD {OBJECT}", "worktree /a", "branch refs/heads/x"))),
    ("a worktree line with no path names no directory",
     registry(("worktree ", f"HEAD {OBJECT}", "branch refs/heads/x"))),
    ("a ref outside refs/heads is not a worktree branch",
     registry(("worktree /a", f"HEAD {OBJECT}", "branch refs/tags/x"))),
    ("a HEAD that is not an object id is not a HEAD",
     registry(("worktree /a", "HEAD not-an-object", "branch refs/heads/x"))),
):
    refused = False
    try:
        m.parse_worktree_registry(stream)
    except m.ReadFailure:
        refused = True
    check(name, refused, True)

# The stdout here is a PERFECTLY VALID registry, so only the exit code can reject it. An earlier
# version of this check sent empty stdout, and it still passed with the exit-code test deleted —
# the empty-registry rule was catching it instead. A check that survives the mutation it exists to
# catch is not a check.
exit_code_only = registry(("worktree /a", f"HEAD {OBJECT}", "branch refs/heads/x")).encode()
failed_read = False
try:
    with patch.object(m, "run", return_value=SimpleNamespace(
            returncode=128, stdout=exit_code_only, stderr=b"fatal: not a git repository")):
        m.registered_worktrees(Path("."))
except m.ReadFailure:
    failed_read = True
check("a nonzero git worktree list exits through the read-failure contract", failed_read, True)

# The parser above is pure, so it can only prove what the fixtures assert. This one proves the
# shape git actually emits still passes — the check no hand-written fixture can stand in for.
REPO_ROOT = Path(__file__).resolve().parent.parent
try:
    live_parses = m.normalise_path(REPO_ROOT) in m.registered_worktrees(REPO_ROOT)
except m.ReadFailure as exc:
    live_parses = f"the live registry was refused: {exc}"
check("the live repository registry passes through the new parser", live_parses, True)

# Same reasoning for the ref probe and the admin directories: `for-each-ref` answering an ABSENT
# ref with exit 0 and no output is the entire basis for telling absence apart from a failed read,
# and a fixture asserting it only restates the assumption. This asks the git that is installed.
try:
    live_refs = [
        m.ref_object(REPO_ROOT, "refs/heads/issue-flow-no-such-ref-ever") is None,
        m.ref_object(REPO_ROOT, "refs/heads/main") is not None,
        m.common_git_dir(REPO_ROOT).is_dir(),
        m.ownership_path(REPO_ROOT).parent.is_dir(),
    ]
except (m.ReadFailure, OSError) as exc:
    live_refs = f"the live repository refused a probe: {exc}"
check("the installed git answers absence, presence and both admin directories",
      live_refs, [True, True, True, True])

operation_parsers = m.build_parser()._subparsers._group_actions[0].choices
check("ownership writers require operation identity", [next(a for a in operation_parsers[command]._actions if a.dest == "operation_id").required for command in ("claim", "reclaim", "unassign")], [True, True, True])
check("target discovery remains a read-only first call", [next(a for a in operation_parsers[command]._actions if a.dest == "target_operation").required for command in ("reclaim", "unassign")], [False, False])

# --------------------------------------------------------------------------------------
# Run-to-path identity. The defect: every substituted value was FLATTENED, and flattening is
# not injective — `a/b` and `a-b` are two valid run IDs that name one directory, so the second
# run finds a registered worktree carrying its own branch at its own path and "resumes" into a
# checkout another process is writing.
# --------------------------------------------------------------------------------------

def refused(call):
    """Run `call` and name how it refused, so a check reads as prose rather than as a try block.

    Returns the `reason` for a stop and the class name for a failure. It deliberately does NOT
    distinguish `ConfigDefect` from `Stop` in that string: the two share a payload shape and differ
    only in the exit code, which `exit_code_for` below asserts directly against `main`. Naming the
    class here would let a reason check quietly stand in for an exit-code check.
    """
    try:
        call()
    except m.Stop as stop:  # ConfigDefect is a Stop; the exit code is asserted separately
        return stop.payload.get("reason")
    except (m.ReadFailure, m.WriteFailure, m.Timeout) as failure:
        return type(failure).__name__
    return "accepted"


check("a valid run ID reaches the path unchanged", m.run_component("claude-code-0730"), "claude-code-0730")
check("run IDs are never flattened onto each other",
      sorted({refused(lambda: m.run_component(value)) for value in ("a/b", "a\\b")}),
      ["unsafe-run-id"])
check("case folding cannot fold two run IDs into one directory",
      refused(lambda: m.run_component("Run-A")), "unsafe-run-id")
check("a run ID may not carry dot segments Windows would strip or traverse",
      [refused(lambda: m.run_component(value)) for value in ("run..a", "run-a.", ".run-a")],
      ["unsafe-run-id"] * 3)
check("a degenerate run ID spelling is refused before it names a directory",
      [refused(lambda: m.run_component(value)) for value in ("", "-run", "run-", "run--a", "run a")],
      ["unsafe-run-id"] * 5)
check("a run ID may not name a Windows device",
      [refused(lambda: m.run_component(value)) for value in ("con", "com1", "lpt9", "nul")],
      ["reserved-device-component"] * 4)
check("a device name is refused in any substituted component, not only the run ID",
      refused(lambda: m.worktree_path("/w/<repo>/<branch>", "aux", "fix/1", "run-a", 1)),
      "reserved-device-component")
# Windows resolves `con.txt` to the device just as it resolves `con`; the extension is not a name.
check("a device name with an extension is still a device",
      [refused(lambda: m.worktree_path("/w/<repo>", value, "fix/1", "run-a", 1))
       for value in ("con.txt", "NUL.log", "com1.anything")],
      ["reserved-device-component"] * 3)
check("template defects are configuration errors, not lost races",
      [issubclass(m.ConfigDefect, m.Stop),
       refused(lambda: m.worktree_path("/w/<branch>", "r", "..", "run-a", 1))],
      [True, "unsafe-worktree-component"])


def exit_code_for(exception):
    """What `main` actually returns for a raised exception — the contract callers read.

    `ConfigDefect` IS a `Stop`, so the whole distinction lives in handler ORDER inside `main`, and
    asserting the class relationship proves nothing about the number a caller sees. This raises
    each one through the real dispatch and reads the exit code back.
    """
    import io, contextlib as _contextlib
    with patch.dict(m.COMMANDS, {"config": lambda *_a: (_ for _ in ()).throw(exception)}), \
            _contextlib.redirect_stdout(io.StringIO()):
        return m.main(["--repo-dir", str(REPO_ROOT), "config"])


check("the exit code separates a configuration defect from a lost race and a failed write",
      [exit_code_for(m.ConfigDefect({"ok": False, "reason": "unsafe-run-id"})),
       exit_code_for(m.Stop({"ok": False, "reason": "lost-race"})),
       exit_code_for(m.ReadFailure("nothing answered")),
       exit_code_for(m.Timeout("gh issue develop did not finish")),
       exit_code_for(m.WriteFailure("it may have landed"))],
      [2, 1, 3, 5, 5])
check("the branch still flattens, because uniqueness is the run ID's job",
      m.worktree_path("/w/<branch>-<run-id>", "r", "fix/6", "run-a", 6).as_posix(),
      "/w/fix-6-run-a")

# A branch-only template is persisted operator policy, not a mistake to reject.
check("a branch-only template gains a run-scoped sibling in memory",
      m.run_scoped_template("/w/<repo>/<branch>"), ("/w/<repo>/<branch>~<run-id>", True))
# Joining with `-` makes the COMPOSED name ambiguous even though each half is not: these are two
# different branches, so they take two different branch locks and nothing downstream would notice
# them sharing one directory.
check("two different branches cannot compose onto one run-scoped directory",
      m.worktree_path(m.run_scoped_template("/w/<branch>")[0], "r", "fix/6", "a-b", 6)
      == m.worktree_path(m.run_scoped_template("/w/<branch>")[0], "r", "fix/6-a", "b", 6),
      False)
check("a template that is already run-scoped is left exactly alone",
      m.run_scoped_template("/w/<branch>/<run-id>"), ("/w/<branch>/<run-id>", False))
check("the migrated sibling is never a child of the legacy directory",
      m.worktree_path(m.run_scoped_template("/w/<branch>")[0], "r", "fix/6", "run-a", 6).as_posix()
      .startswith(m.worktree_path("/w/<branch>", "r", "fix/6", "run-a", 6).as_posix() + "/"),
      False)

# --------------------------------------------------------------------------------------
# Ref probes. The defect: `git rev-parse --verify --quiet` exits 1 for "no such ref" AND for
# every way the question can go unanswered, so a corrupt object store read as "absent" restarts
# a resumed branch from the base and drops its commits.
# --------------------------------------------------------------------------------------

def ref_run(returncode: int, stdout: str):
    return lambda argv, cwd=None, check=True, writes=False, binary=False, timeout=None: \
        SimpleNamespace(returncode=returncode, stdout=stdout, stderr="boom")


def git_ref_run(*refs: str):
    """Model `for-each-ref`'s PATTERN semantics, not a dict lookup keyed on the argument.

    The distinction is the whole point of the check below. A fake that answers
    `self.refs.get(pattern)` encodes the assumption under test — that the argument is an exact ref
    path — and would report every one of these checks as passing while git returned a child ref.
    Real rule, from the man page and confirmed against git 2.55: a pattern matches a ref completely
    OR from the beginning up to a slash.
    """
    def fake(argv, cwd=None, check=True, writes=False, binary=False, timeout=None):
        pattern = argv[-1]
        matched = [name for name in refs
                   if name == pattern or name.startswith(pattern.rstrip("/") + "/")]
        stdout = "".join(f"{name}\t{OID}\n" for name in matched)
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")
    return fake


OID = "c" * 40
with patch.object(m, "run", ref_run(0, "")):
    check("an empty answer from a successful probe is a real absence",
          m.ref_object(Path("."), "refs/heads/x"), None)
with patch.object(m, "run", ref_run(0, f"refs/heads/x\t{OID}\n")):
    check("a single object id is a real presence", m.ref_object(Path("."), "refs/heads/x"), OID)
with patch.object(m, "run", ref_run(128, "")):
    check("a failed probe is a failed read, never an absence",
          refused(lambda: m.ref_object(Path("."), "refs/heads/x")), "ReadFailure")
with patch.object(m, "run", ref_run(0, f"refs/heads/x\t{OID}\nrefs/heads/x\t{OID}\n")):
    check("an ambiguous probe answer is a failed read",
          refused(lambda: m.ref_object(Path("."), "refs/heads/x")), "ReadFailure")
with patch.object(m, "run", ref_run(0, f"refs/heads/x\tnot-an-object\n")):
    check("an unparseable probe answer is a failed read",
          refused(lambda: m.ref_object(Path("."), "refs/heads/x")), "ReadFailure")
with patch.object(m, "run", ref_run(0, f"refs/heads/x {OID}\n")):
    check("an answer in an unexpected format is a failed read",
          refused(lambda: m.ref_object(Path("."), "refs/heads/x")), "ReadFailure")

# The argument is a PATTERN. `refs/heads/foo` also matches `refs/heads/foo/bar`, so a probe that
# trusted the output would report a branch that does not exist as present — at another branch's
# commit. Confirmed against real git before this check was written.
with patch.object(m, "run", git_ref_run("refs/heads/foo/bar")):
    check("a child ref is not mistaken for the parent that does not exist",
          m.ref_object(Path("."), "refs/heads/foo"), None)
with patch.object(m, "run", git_ref_run("refs/heads/foo", "refs/heads/foo/bar")):
    check("a parent that does exist is read past its children",
          m.ref_object(Path("."), "refs/heads/foo"), OID)
with patch.object(m, "run", git_ref_run("refs/heads/foo/a", "refs/heads/foo/b")):
    check("several children still do not manufacture a parent",
          m.ref_object(Path("."), "refs/heads/foo"), None)

# --------------------------------------------------------------------------------------
# Branch identity. `gh issue develop` branches from the base as the SERVER sees it, which is not
# necessarily the base this run fetched — so a fresh branch can start at a commit the run never
# saw while the command reports the recorded base as its own.
# --------------------------------------------------------------------------------------

A, B = "a" * 40, "b" * 40
check("a fresh branch is coherent only at the base this run recorded",
      m.branch_identity_verdict(fresh=True, local=A, remote=A, base=A,
                                remote_reachable_from_local=True)["coherent"], True)
check("a base that moved during native creation is reported, not absorbed",
      m.branch_identity_verdict(fresh=True, local=A, remote=B, base=A,
                                remote_reachable_from_local=False),
      {"coherent": False, "reason": "base-moved-during-creation"})
check("a local-only branch has nothing to disagree with",
      m.branch_identity_verdict(fresh=False, local=A, remote=None, base=B,
                                remote_reachable_from_local=None)["coherent"], True)
check("unpushed local work on a resumed branch is ordinary",
      m.branch_identity_verdict(fresh=False, local=A, remote=B, base=B,
                                remote_reachable_from_local=True)["reason"], "resumed-local-ahead")
# Named for what was actually established — unreachable covers "ahead" AND "force-pushed away
# from under us", and pointing the reader at commits to fast-forward that may not exist is worse
# than saying less.
check("an unreachable remote head would build on a commit this checkout never saw",
      m.branch_identity_verdict(fresh=False, local=A, remote=B, base=B,
                                remote_reachable_from_local=False),
      {"coherent": False, "reason": "remote-not-reachable-from-local"})
check("an unestablished ancestry is not a pass",
      m.branch_identity_verdict(fresh=False, local=A, remote=B, base=B,
                                remote_reachable_from_local=None),
      {"coherent": False, "reason": "ancestry-unknown"})

# --------------------------------------------------------------------------------------
# start-branch, end to end against an in-memory git. The review probe for this issue produced
# two successful concurrent `git worktree add` calls for ONE branch on Git 2.55.0.windows.3, so
# git's own "already used by worktree at" check cannot be the thing that serializes them.
# --------------------------------------------------------------------------------------

tempfile_module = __import__("tempfile")


class Clone:
    """A throwaway clone: real directories for the lock and the ownership marker, faked git."""

    def __init__(self, root: str, *, refs=None, develop_rc=0, develop_timeout=False,
                 linked=(), link_read_fails=False, base=A, publish=None, ancestor=True,
                 fail_worktree_add=False):
        self.root = Path(root)
        self.common = self.root / "clone" / ".git"
        self.admin = self.root / "clone" / ".git" / "worktrees" / "wt"
        self.admin.mkdir(parents=True, exist_ok=True)
        self.worktrees = self.root / "trees"
        self.refs = dict(refs or {})
        self.develop_rc, self.develop_timeout = develop_rc, develop_timeout
        self.linked, self.link_read_fails = set(linked), link_read_fails
        self.base, self.publish, self.ancestor = base, publish, ancestor
        self.fail_worktree_add = fail_worktree_add
        self.log: list[list[str]] = []

    def run(self, argv, cwd=None, check=True, writes=False, binary=False, timeout=None):
        self.log.append(list(argv))
        if argv[:3] == ["git", "rev-parse", "--path-format=absolute"]:
            target = self.common if argv[3] == "--git-common-dir" else self.admin
            return SimpleNamespace(returncode=0, stdout=f"{target}\n", stderr="")
        if argv[:2] == ["git", "for-each-ref"]:
            # Pattern semantics here too, for the same reason `git_ref_run` has them: a dict
            # lookup on the argument would quietly re-introduce the assumption under test.
            pattern = argv[-1]
            stdout = "".join(f"{name}\t{oid}\n" for name, oid in sorted(self.refs.items())
                             if name == pattern or name.startswith(pattern.rstrip("/") + "/"))
            return SimpleNamespace(returncode=0, stdout=stdout, stderr="")
        if argv[:3] == ["gh", "issue", "develop"]:
            if self.develop_timeout:
                raise m.Timeout("gh issue develop did not finish within 120s")
            if self.develop_rc == 0:
                self.linked.add(argv[argv.index("--name") + 1])
                self.refs[f"refs/remotes/origin/{argv[argv.index('--name') + 1]}"] = (
                    self.publish or self.base)
            return SimpleNamespace(returncode=self.develop_rc, stdout="", stderr="already exists")
        if argv[:3] == ["git", "worktree", "add"]:
            if self.fail_worktree_add:
                raise m.WriteFailure("git worktree add failed (128): already used by worktree")
            Path(argv[-2]).mkdir(parents=True, exist_ok=True)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[:2] == ["git", "merge-base"]:
            return SimpleNamespace(returncode=0 if self.ancestor else 1, stdout="", stderr="")
        if argv[:2] == ["git", "rev-parse"]:
            head = self.refs.get("refs/heads/fix/6") or self.base
            return SimpleNamespace(returncode=0,
                                   stdout=(head if argv[2] == "HEAD" else self.base) + "\n",
                                   stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def branch_linked(self, issue, branch, cwd):
        if self.link_read_fails:
            raise m.ReadFailure("linked-branch GraphQL response is partial or malformed")
        return branch in self.linked

    def start(self, *, run_id=ME, issue=6, template=None, registry=None):
        args = SimpleNamespace(issue=issue, run_id=run_id, expect_state="in-progress",
                               worktree_root=template or f"{self.worktrees}/<branch>",
                               branch="fix/6", base="main")
        with patch.multiple(m, run=self.run, do_verify_claim=lambda *_a: {},
                            repo_identity=lambda _cwd: ("owner", "repo"),
                            is_branch_linked=self.branch_linked,
                            registered_worktrees=lambda _cwd: dict(registry or {})):
            return m.cmd_start_branch(args, {}, Path("."))

    def index_of(self, prefix):
        return next((i for i, argv in enumerate(self.log) if argv[:len(prefix)] == prefix), None)


with tempfile_module.TemporaryDirectory() as root:
    clone = Clone(root)
    result = clone.start()
    check("a branch-only template delivers a run-scoped worktree",
          (Path(result["worktree"]).name, result["template_migrated"]), (f"fix-6~{ME}", True))
    check("the local checkout is reserved before the first remote mutation",
          clone.index_of(["git", "worktree", "add"]) < clone.index_of(["gh", "issue", "develop"]),
          True)
    check("a fresh native creation proves local, remote and base tell one story",
          (result["identity"], result["natively_linked"]), ("fresh-at-base", True))
    with patch.object(m, "run", clone.run):
        check("the run records durable ownership of the checkout it created",
              m.read_ownership(Path(result["worktree"]))["run_id"], ME)
        check("the branch lock is released once the reservation ends",
              m.branch_lock_path(Path("."), "fix/6").exists(), False)

# A local reservation that fails must leave NOTHING on GitHub — the old order created the
# server-side branch and the sidebar link first, then stopped on the local check.
with tempfile_module.TemporaryDirectory() as root:
    clone = Clone(root, fail_worktree_add=True)
    check("a failed local reservation is a failed local reservation",
          refused(lambda: clone.start()), "WriteFailure")
    check("a failed local reservation leaves no remote state",
          clone.index_of(["gh", "issue", "develop"]), None)

# Resume needs PROOF, and "registered for this branch at this path" is not proof: two runs of one
# branch satisfy it equally well.
with tempfile_module.TemporaryDirectory() as root:
    clone = Clone(root)
    path = clone.worktrees / f"fix-6~{ME}"
    path.mkdir(parents=True)
    registry = {m.normalise_path(path): "fix/6"}
    check("a registered checkout with no ownership marker is not clearance",
          refused(lambda: clone.start(registry=registry)), "worktree-ownership-unproven")
    with patch.object(m, "run", clone.run):
        m.write_ownership(path, "another-run", 6, "fix/6")
    check("a checkout another run owns is refused rather than shared",
          refused(lambda: clone.start(registry=registry)), "worktree-owned-by-another-run")
    with patch.object(m, "run", clone.run):
        m.write_ownership(path, ME, 6, "fix/6")
    resumed = clone.start(registry=registry)
    check("a checkout this run owns resumes without a second checkout",
          (resumed["resumed_existing_worktree"], clone.index_of(["git", "worktree", "add"])),
          (True, None))
    check("a path holding somebody else's branch is still refused outright",
          refused(lambda: clone.start(registry={m.normalise_path(path): "fix/other"})),
          "worktree-path-occupied")

# The branch-only migration's one hard case: a legacy checkout that is still registered.
with tempfile_module.TemporaryDirectory() as root:
    clone = Clone(root)
    legacy = clone.worktrees / "fix-6"
    legacy.mkdir(parents=True)
    check("a registered legacy checkout stops instead of being silently orphaned",
          refused(lambda: clone.start(registry={m.normalise_path(legacy): "fix/6"})),
          "legacy-worktree-registered")
    check("nothing was created remotely while that decision was pending",
          clone.index_of(["gh", "issue", "develop"]), None)
    check("an unregistered leftover directory does not block the migration",
          clone.start(registry={})["template_migrated"], True)

# --------------------------------------------------------------------------------------
# The branch lock. A stale lock is never broken on elapsed time: the case a timeout gets wrong is
# a slow-but-live run whose checkout is taken while it is writing into it.
# --------------------------------------------------------------------------------------

with tempfile_module.TemporaryDirectory() as root:
    clone = Clone(root)
    with patch.object(m, "run", clone.run):
        lock_file = m.branch_lock_path(Path("."), "fix/6")
        with m.branch_reservation(Path("."), "fix/6", ME, 6):
            check("a held lock exists while the reservation is held", lock_file.exists(), True)
            check("a second run cannot take a held branch lock",
                  refused(lambda: m.branch_reservation(Path("."), "fix/6", OTHER, 6).__enter__()),
                  "branch-locked-by-another-run")
        check("the lock is released when the reservation ends", lock_file.exists(), False)

        # A crashed run leaves its lock behind. The retry must be able to proceed AND must end up
        # owning the cleanup — proceeding without adopting is how that lock outlives every retry
        # and blocks a different run forever, with nobody left entitled to remove it.
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock_file.write_text(json.dumps({"run_id": ME, "branch": "fix/6", "pid": 1,
                                         "acquired_at": "2026-07-30T00:00:00Z"}), encoding="utf-8")
        with m.branch_reservation(Path("."), "fix/6", ME, 6):
            check("a run's own leftover lock is its own retry, not a race it lost",
                  lock_file.exists(), True)
        check("an adopted lock is released rather than leaked", lock_file.exists(), False)

        # A raise inside the reservation must not strand the lock either.
        try:
            with m.branch_reservation(Path("."), "fix/6", ME, 6):
                raise RuntimeError("the work blew up")
        except RuntimeError:
            pass
        check("the lock is released when the work inside it raises", lock_file.exists(), False)

        # Age is not evidence. This lock is a year old and still stops the next run.
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock_file.write_text(json.dumps({"run_id": OTHER, "branch": "fix/6", "pid": 1,
                                         "acquired_at": "2025-01-01T00:00:00Z"}), encoding="utf-8")
        check("an old lock is still a held lock",
              refused(lambda: m.branch_reservation(Path("."), "fix/6", ME, 6).__enter__()),
              "branch-locked-by-another-run")
        lock_file.write_text("{ not json", encoding="utf-8")
        check("an unreadable lock is an unknown holder, not an absent one",
              refused(lambda: m.branch_reservation(Path("."), "fix/6", ME, 6).__enter__()),
              "branch-locked-by-another-run")
        lock_file.unlink()

# --------------------------------------------------------------------------------------
# `gh issue develop` mutates a ref AND a link. Its nonzero exit used to mean "not linked", which
# is false for the two states where it matters: created-the-ref-but-not-the-link, and
# committed-both-then-dropped-the-connection.
# --------------------------------------------------------------------------------------

with tempfile_module.TemporaryDirectory() as root:
    clone = Clone(root, develop_rc=1, linked=["fix/6"])
    with patch.multiple(m, run=clone.run, is_branch_linked=clone.branch_linked):
        check("an already-existing branch is read back as linked, not guessed at",
              m.develop_link(6, "fix/6", "main", Path("."))["outcome"], "already-linked")

    clone = Clone(root, develop_rc=1, linked=[])
    with patch.multiple(m, run=clone.run, is_branch_linked=clone.branch_linked):
        check("a conclusive read that no link exists is allowed to say so",
              m.develop_link(6, "fix/6", "main", Path("."))["linked"], False)

    clone = Clone(root, develop_rc=1, link_read_fails=True)
    with patch.multiple(m, run=clone.run, is_branch_linked=clone.branch_linked):
        check("a failed read-back is a failed read, not an absent link",
              refused(lambda: m.develop_link(6, "fix/6", "main", Path("."))), "ReadFailure")

    # The sidebar read decides "not linked", so a TRUNCATED page has not answered the question —
    # the same completeness contract issue #28 established for closing references.
    def sidebar(nodes, has_next):
        return {"data": {"repository": {"issue": {"linkedBranches": {
            "nodes": [{"ref": {"name": name}} for name in nodes],
            "pageInfo": {"hasNextPage": has_next}}}}}}

    with patch.multiple(m, gh_json=lambda *_a, **_k: sidebar(["fix/6"], False),
                        repo_identity=lambda _cwd: ("o", "r")):
        check("a complete sidebar page answers the question",
              m.is_branch_linked(6, "fix/6", Path(".")), True)
    with patch.multiple(m, gh_json=lambda *_a, **_k: sidebar(["other"], False),
                        repo_identity=lambda _cwd: ("o", "r")):
        check("a complete page without the branch is a real absence",
              m.is_branch_linked(6, "fix/6", Path(".")), False)
    with patch.multiple(m, gh_json=lambda *_a, **_k: sidebar(["other"], True),
                        repo_identity=lambda _cwd: ("o", "r")):
        check("a truncated page that lacks the branch cannot prove it absent",
              refused(lambda: m.is_branch_linked(6, "fix/6", Path("."))), "ReadFailure")
    # Only ONE of the two answers needs a complete page. A later page cannot un-link a branch that
    # is already on this one, so refusing here would turn a definite yes into an unretryable exit 3.
    with patch.multiple(m, gh_json=lambda *_a, **_k: sidebar(["fix/6"], True),
                        repo_identity=lambda _cwd: ("o", "r")):
        check("a truncated page that CONTAINS the branch is still a definite yes",
              m.is_branch_linked(6, "fix/6", Path(".")), True)
    with patch.multiple(m, gh_json=lambda *_a, **_k: {"data": {"repository": {"issue": {
                            "linkedBranches": {"nodes": []}}}}},
                        repo_identity=lambda _cwd: ("o", "r")):
        check("a sidebar page with no page metadata is a failed read",
              refused(lambda: m.is_branch_linked(6, "fix/6", Path("."))), "ReadFailure")
    # Every node is validated before membership decides anything, so a malformed neighbour cannot
    # ride along unnoticed just because the branch we asked about happened to appear first.
    for label, nodes in (("a node with no ref", [{"ref": None}]),
                         ("a ref with no name", [{"ref": {}}]),
                         ("a name that is not a string", [{"ref": {"name": 7}}])):
        malformed = {"data": {"repository": {"issue": {"linkedBranches": {
            "nodes": [{"ref": {"name": "fix/6"}}] + nodes,
            "pageInfo": {"hasNextPage": False}}}}}}
        with patch.multiple(m, gh_json=lambda *_a, _m=malformed, **_k: _m,
                            repo_identity=lambda _cwd: ("o", "r")):
            check(f"{label} is a failed read even when the branch is present",
                  refused(lambda: m.is_branch_linked(6, "fix/6", Path("."))), "ReadFailure")

    clone = Clone(root, develop_timeout=True, linked=[])
    with patch.multiple(m, run=clone.run, is_branch_linked=clone.branch_linked):
        check("a timeout whose ref may exist is an ambiguous write, not a no-op",
              refused(lambda: m.develop_link(6, "fix/6", "main", Path("."))), "WriteFailure")

    clone = Clone(root, develop_timeout=True, link_read_fails=True)
    with patch.multiple(m, run=clone.run, is_branch_linked=clone.branch_linked):
        check("a timeout that cannot be re-read is still an ambiguous write",
              refused(lambda: m.develop_link(6, "fix/6", "main", Path("."))), "WriteFailure")

    clone = Clone(root, develop_timeout=True, linked=["fix/6"])
    with patch.multiple(m, run=clone.run, is_branch_linked=clone.branch_linked):
        check("a timeout that the sidebar proves landed is success",
              m.develop_link(6, "fix/6", "main", Path("."))["linked"], True)

# A concurrent push to the base means the server branched from a commit this run never fetched.
with tempfile_module.TemporaryDirectory() as root:
    clone = Clone(root, base=A, publish=B)
    check("a branch published at a head this run never saw is refused, not reported delivered",
          refused(lambda: clone.start()), "base-moved-during-creation")

# --------------------------------------------------------------------------------------
# Aliasing. An ancestor link redirects every run identically and is fine; the run's OWN directory
# being a link is two run IDs resolving into one checkout.
# --------------------------------------------------------------------------------------

with tempfile_module.TemporaryDirectory() as root:
    real = Path(root) / "real"
    real.mkdir()
    check("a plain directory is canonicalised, not refused",
          m.same_location(m.canonical_worktree_path(real), real), True)
    link = Path(root) / "alias"
    try:
        link.symlink_to(real, target_is_directory=True)
        aliased = refused(lambda: m.canonical_worktree_path(link))
    except (OSError, NotImplementedError) as exc:
        aliased = f"this platform refused to create the link: {exc}"
    check("a run directory that is itself a link is refused", aliased, "aliased-worktree-path")

    # No platform guard here: `link` either exists by now or the check above already reported why
    # not, and nothing in this block creates one. A guard that cannot fire is a claim of robustness
    # the code does not make.
    try:
        ancestor_link = m.same_location(m.canonical_worktree_path(link / "sub"), real / "sub")
    except m.Stop as stop:
        ancestor_link = f"an ordinary ancestor link was refused: {stop.payload.get('reason')}"
    check("an ancestor link is resolved rather than refused", ancestor_link, True)

# The collapse a single path CANNOT prove, and the layer that can. Two junctions — `run-a` and
# `run-b` both pointing at one directory — fold two runs together while every leaf resolves
# cleanly, and no amount of inspecting ONE run's spelling can see the other run's junction. Three
# path-only heuristics were tried here and each shipped a defect in a different direction.
#
# What decides it is resolution followed by ownership, and resolution happens TWICE on purpose:
# `canonical_worktree_path` before anything is created, and `normalise_path` when the registry is
# consulted. Neither is load-bearing alone — established by mutating them, not by argument: break
# either one and the collapse is still refused; break BOTH and it degrades only to
# `worktree-path-occupied`, which is still a refusal. Both steps are asserted below.
with tempfile_module.TemporaryDirectory() as root:
    shared = Path(root) / "shared"
    shared.mkdir()
    clone = Clone(root)
    try:
        (Path(root) / "run-a").symlink_to(shared, target_is_directory=True)
        (Path(root) / "run-b").symlink_to(shared, target_is_directory=True)
        (shared / "fix-6").mkdir()
        # The first of the two resolutions, asserted directly: two aliased SPELLINGS of one
        # directory canonicalise to one identical string, so `git worktree add` and the registry
        # are handed one key rather than two keys for one directory. `normalise_path` would also
        # supply this at lookup time; asserting it here pins the earlier one, which is the only
        # place the CREATED checkout's spelling is decided.
        one_spelling = (m.canonical_worktree_path(Path(root) / "run-a" / "fix-6")
                        == m.canonical_worktree_path(Path(root) / "run-b" / "fix-6"))
    except m.Stop as stop:
        # Reported rather than raised: a canonicaliser that refuses these paths outright is a
        # failure of this check, and a crashing test says less than a failing one.
        one_spelling = f"canonicalisation refused the path: {stop.payload.get('reason')}"
    except (OSError, NotImplementedError) as exc:
        one_spelling = f"this platform refused to create the link: {exc}"
    check("two aliased spellings of one directory canonicalise to one string", one_spelling, True)

    try:
        with patch.object(m, "run", clone.run):
            m.write_ownership(shared / "fix-6", "run-a", 6, "fix/6")
        # `run-b` computes `<root>/run-b/fix-6` — a different spelling of run-a's real directory.
        # Its own path betrays nothing; the registry lookup resolves onto run-a's checkout.
        with patch.multiple(m, run=clone.run, registered_worktrees=lambda _cwd: {
                m.normalise_path(shared / "fix-6"): "fix/6"}):
            collapsed = refused(lambda: m.resolve_worktree_ownership(
                Path("."), m.canonical_worktree_path(Path(root) / "run-b" / "fix-6"),
                SimpleNamespace(run_id="run-b", branch="fix/6")))
    except (OSError, NotImplementedError) as exc:
        collapsed = f"this platform refused to create the link: {exc}"
    check("two junctions folding two runs into one checkout are caught by ownership, not by the path",
          collapsed, "worktree-owned-by-another-run")


# --------------------------------------------------------------------------------------
# Runtime-contract boundaries (issue #7). `SKILL.md` is loaded on EVERY invocation, so its
# structure is a runtime property, not a style preference — and the failure it guards is the one
# the migration ledger exists for: prose is deleted from the contract and nobody notices that its
# new owner was never linked, or that a slice was marked retired while its text is still there.
# --------------------------------------------------------------------------------------

SKILL = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
SKILL_LINES = SKILL.split("\n")
INVENTORY = (REPO_ROOT / "references" / "migration-inventory.md").read_text(encoding="utf-8")

front = SKILL.split("---", 2)
check("the frontmatter declares name, description, licence, author and version",
      [key in front[1] for key in ("name:", "description:", "license:", "author:", "version:")],
      [True] * 5)

# The prescribed order, and nothing between the headings that reintroduces a retired section.
check("the runtime contract keeps the required section order",
      [line for line in SKILL_LINES if line.startswith("## ")],
      ["## Activation Contract", "## Hard Rules", "## Decision Gates", "## Execution Steps",
       "## Output Contract", "## References", "## Operator configuration"])

# Every reference and asset the contract names must exist, and every file that exists must be
# named — a reference nobody loads is knowledge that was moved out of reach.
linked = set(re.findall(r"\]\((references/[\w.-]+\.md|assets/[\w.-]+\.md)\)", SKILL))
on_disk = {f"references/{p.name}" for p in (REPO_ROOT / "references").glob("*.md")} | \
          {f"assets/{p.name}" for p in (REPO_ROOT / "assets").glob("*.md")}
check("every companion the contract links exists on disk", sorted(linked - on_disk), [])
check("every companion on disk is reachable from the contract",
      sorted(on_disk - linked - {"references/migration-inventory.md"}), [])

# The nine invariants issue #7 requires to stay DIRECTLY actionable in the contract itself.
for name, needle in [
    ("analyst repository read-only", "read-only for the repository"),
    ("at most one finding", "at most one evidenced issue"),
    ("state exclusivity", "Keep exactly one of"),
    ("claim verification and renewal", "verify_claim"),
    ("stale-work recovery", "Reclaimable from"),
    ("isolated repository work", "isolated checkout"),
    ("independent SHA-bound review", "every push invalidates both"),
    ("verified tracker projections", "read back"),
    ("immutable delivery gates", "annotated tag"),
]:
    check(f"the contract still states {name} directly", needle in SKILL, True)

# The ledger and the contract must agree. A row marked retired whose heading is still in SKILL.md
# is the exact bookkeeping lie the ledger exists to prevent.
retired_headings = ["What the analyst produces", "Working in a repository", "Abandoned work",
                    "Where to put work you cannot finish", "Optional: a board view"]
check("no retired section heading survives in the contract",
      [h for h in retired_headings if h in SKILL], [])
check("the migration ledger records every slice as retired",
      re.findall(r"^\| S\d\d \|.*\| (no) \|\s*$", INVENTORY, re.M), [])

print()
print(f"{CHECKS - len(FAILURES)}/{CHECKS} checks passed" + (f"; failures: {FAILURES}" if FAILURES else ""))
sys.exit(1 if FAILURES else 0)
