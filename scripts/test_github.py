#!/usr/bin/env python3
"""Regression tests for `github.py`'s pure logic.

    python scripts/test_github.py

No network, no `gh`, no fixtures: every check below is a decision function fed hand-built comment
timelines. That is deliberate — the functions tested here are the ones where a wrong answer causes
a run to stand down, strip a label, or take over someone else's work, and those are exactly the
paths that are hardest to exercise live and worst to get wrong.

Each check names the defect it exists to prevent. All of them were REAL: every one comes from an
adversarial review of the first version of that file, not from imagination.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("gh", Path(__file__).with_name("github.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

FAILURES: list[str] = []


def check(name: str, got, want) -> None:
    ok = got == want
    if not ok:
        FAILURES.append(name)
    print(f"{'OK  ' if ok else 'FAIL'} {name} -> {got!r} (want {want!r})")


def comment(body: str, at: str = "2026-01-01T00:00:00Z") -> dict:
    return {"createdAt": at, "url": "https://example/1", "body": body}


# --------------------------------------------------------------------------------------
# Claim detection. The defect: an unanchored "claimed by <x>" matched ordinary prose, and a
# phantom claim carries a REAL server timestamp — so it sorted earliest and `claim` concluded
# it had lost a race that never happened, posting a stand-down and stripping its own label.
# --------------------------------------------------------------------------------------

check("a prose mention is not a claim",
      m.claim_comments([comment("this was already claimed by @old-contributor months ago")]), [])

check("claim-shaped prose without a horizon is not a claim",
      m.claim_comments([comment("Claimed by mistake, reverting")]), [])

check("a real legacy claim still parses",
      [r for _, r, _ in m.claim_comments(
          [comment("Claimed by codex-b91c, expect to report by 2026-01-01T06:00Z.")])],
      ["codex-b91c"])

check("a marker claim parses",
      [r for _, r, _ in m.claim_comments(
          [comment("<!-- issue-flow: claim run-id=kimi-3b1d horizon=2026-01-01T06:00Z -->")])],
      ["kimi-3b1d"])


# --------------------------------------------------------------------------------------
# Release vocabulary. The defect: `released_run_ids` and the control-message check shared only
# ONE marker kind, and nothing emitted `reclaim` — so a reclaimed issue still adjudicated in
# favour of the run it had been taken from, forever.
# --------------------------------------------------------------------------------------

RECLAIMED = [
    comment("<!-- issue-flow: claim run-id=dead-run horizon=2026-01-01T06:00Z -->"),
    comment("<!-- issue-flow: reclaim run-id=live-run from=dead-run -->", "2026-01-02T00:00:00Z"),
]

check("reclaim releases the run it took over FROM, not its author",
      "dead-run" in m.released_run_ids(RECLAIMED), True)

check("standdown counts as both release and control",
      "standdown" in m.RELEASE_KINDS and "standdown" in m.CONTROL_KINDS, True)

check("reclaim counts as both release and control",
      "reclaim" in m.RELEASE_KINDS and "reclaim" in m.CONTROL_KINDS, True)


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

check("a claim whose holder spoke past its horizon is not stale",
      m.stale_claims(DEAD_CLAIMS,
                     DEAD + [comment("<!-- issue-flow: heartbeat run-id=dead-run -->",
                                     "2026-01-01T09:00:00Z")],
                     "2026-01-05T00:00Z"),
      set())

NO_HORIZON = [comment("<!-- issue-flow: claim run-id=r1 -->")]
check("a claim with no declared horizon is never automatically stale",
      m.stale_claims(m.claim_comments(NO_HORIZON), NO_HORIZON, "2027-01-01T00:00Z"), set())


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

print()
print(f"{len(FAILURES)} failure(s)" + (f": {FAILURES}" if FAILURES else ""))
sys.exit(1 if FAILURES else 0)
