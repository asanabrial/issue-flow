#!/usr/bin/env python3
r"""issue-flow — mechanical transport operations for the GitHub binding.

WHY THIS FILE EXISTS
--------------------
`SKILL.md` and `bindings/github.md` already described every step below correctly, in prose,
and runs still skipped them. The two incidents this script was written against:

  * SKILL.md "Optional: a board view over this workflow" (2026-07-25, issues #61/#62) — a run
    moved labels correctly through the whole state machine, closed, merged and tagged, and
    mirrored the project board ZERO times in an entire session with the `project` scope present
    the whole way. Not a missing permission, not an unclear config: the instruction was present
    and the run never executed it.
  * SKILL.md "Abandoned work" — five issues claimed and commented, none ever relabeled, because
    `claim` and `transition` are two separate calls and nothing forced the second.

Prose cannot fix a run that does not execute prose. Everything here is a step that is
MECHANICAL AND VERIFIABLE, so it is executed by a program instead of remembered by an agent.
Steps that need JUDGEMENT — what is worth analysing, whether a blocker is discharged, whether a
diff passes review — are deliberately NOT here and stay in the prose where they belong.

SCOPE — reversible operations only. `merge`, `publish_version` and `close` are intentionally
absent: they write irreversibly to the remote, and a defect in this file must not be able to
merge, tag or close anything. The agent performs those three itself after verifying SHAs, per
`bindings/github.md`.

WHY PYTHON AND NOT POWERSHELL/BASH
----------------------------------
`bindings/github.md` documents live markdown corruption from inline `--body` on a PowerShell
runtime: backtick is PowerShell's escape character, so `` `status:blocked` `` arrived as
`\status:blocked\` and newlines arrived as the two literal characters `\n`. That corruption
happens one layer below where the text is composed, so no care in the text prevents it. Every
subprocess call here passes an argument LIST with no shell, and every markdown body goes through
a temp file — which removes that failure class structurally instead of warning about it. A
PowerShell + bash pair would also be two implementations of one mechanical contract, and they
would drift, which is the exact failure this file exists to remove.

OUTPUT CONTRACT
---------------
Every subcommand prints exactly one JSON object on stdout and nothing else. Exit codes carry the
distinction `SKILL.md` calls "a failed read is not a failed answer":

  0  the operation completed AND its read-back verified
  1  STOP — a check answered "stop" (lost race, stand-down, wrong state, closed issue).
     This is a decision from a successful read. Do not retry; follow the JSON's `action`.
  2  usage or configuration error — nothing was attempted
  3  the READ itself failed (network, auth, rate limit). The control surface answered NOTHING.
     Fail closed: write nothing, retry the read. Never treat this as clearance or as a stand-down.
  4  internal error — a defect in this script. State is UNKNOWN; re-read before retrying.
  5  a WRITE failed. Not the same as 3, and the difference is the point: a write may have landed
     before it failed, so "retry" is the wrong instruction. RE-READ to establish what happened.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG_START = "<!-- issue-flow:config:start -->"
CONFIG_END = "<!-- issue-flow:config:end -->"
STATES = ["analysis", "ready", "in-progress", "review", "blocked", "done"]

# Machine-readable control markers.
#
# The prose flow adjudicates races and stand-downs by reading comment text, and SKILL.md itself
# warns that "parsing prose for the same answer is fragile - any rewording breaks it". These
# markers make the same facts exact. They are HTML comments, so they are invisible in rendered
# markdown and cannot be reworded by a later run editing the surrounding sentence.
#
# Reading falls back to the prose forms for comments written before this script existed; writing
# always emits both, so a human reading the timeline still sees a sentence.
MARKER_RE = re.compile(r"<!--\s*issue-flow:\s*(?P<kind>[a-z-]+)\s+(?P<attrs>[^>]*?)\s*-->")

# The two marker vocabularies, named once.
#
# These were originally two inline literal sets that overlapped in only ONE element, `standdown`.
# The consequence was not cosmetic: a `reclaim` marker satisfied the control-message check (so the
# displaced run stood down) while never releasing the dead run's claim (so `claim` still reported a
# loss to a run that no longer existed). Two sets that must agree about the same words cannot be
# spelled out at two call sites.
#
# RELEASE — this run-id no longer holds the item, so its earlier claim stops counting.
# CONTROL — a message addressed to a run-id that instructs it to stop.
RELEASE_KINDS = frozenset({"standdown", "release", "unassign", "reclaim"})
CONTROL_KINDS = frozenset({"standdown", "reclaim", "adjudication"})
# Attributes that can carry the run-id a marker is ABOUT, rather than the one that wrote it.
TARGET_ATTRS = ("run-id", "target", "from")


class ReadFailure(Exception):
    """A read against the control surface did not answer. Never a stand-down."""


class WriteFailure(Exception):
    """A WRITE failed. Distinct from ReadFailure, because the advice is opposite.

    "The read answered nothing, so write nothing and retry" is correct for a read and actively
    harmful for a write: the write may have landed before the failure surfaced, so a blind retry
    can duplicate it. The caller must RE-READ to establish what actually happened before deciding.
    """


class Stop(Exception):
    """A successful read answered "stop". Carries the payload the agent must act on."""

    def __init__(self, payload: dict):
        super().__init__(payload.get("reason", "stop"))
        self.payload = payload


# ---------------------------------------------------------------------------
# process plumbing
# ---------------------------------------------------------------------------

def run(args: list[str], cwd: Path | None = None, check: bool = True,
        writes: bool = False) -> subprocess.CompletedProcess:
    """Run a command with an argument LIST and no shell.

    No shell means no PowerShell backtick expansion, no word splitting, no quoting rules — the
    corruption documented in bindings/github.md cannot occur through this path.

    `writes=True` marks a command that MUTATES something. It changes only which exception is
    raised, and that distinction matters more than it looks: a failed read means "nothing was
    learned, retry"; a failed write means "something may already have happened, re-read before you
    decide". Reporting a failed write under the read contract tells the caller to retry blindly,
    which is how a single command posts two claim comments.
    """
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise ReadFailure(f"{args[0]} not found on PATH: {exc}") from exc
    if check and proc.returncode != 0:
        detail = f"{' '.join(args[:3])} failed ({proc.returncode}): {proc.stderr.strip()}"
        raise (WriteFailure if writes else ReadFailure)(detail)
    return proc


def gh_json(args: list[str], cwd: Path | None = None):
    proc = run(["gh", *args], cwd=cwd)
    text = proc.stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReadFailure(f"gh returned unparseable JSON for {' '.join(args[:2])}: {exc}") from exc


@contextlib.contextmanager
def body_file(body: str):
    """Write a markdown body to a temp file, and always remove it.

    Every operation that accepts markdown goes through a file, with no exception for "this one's
    short" — that inconsistency is what let evidence-bearing comments arrive silently damaged.

    A context manager rather than a create/unlink pair: the `gh` call between the two raises on any
    transient failure, which the design treats as routine, and a plain unlink after it is simply
    skipped on that path. Leaking a file per failed comment is small, but it is the kind of small
    that accumulates silently for months.
    """
    fd, path = tempfile.mkstemp(suffix=".md", prefix="issue-flow-", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
        yield path
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# operator configuration
# ---------------------------------------------------------------------------

def _clean_value(raw: str) -> str:
    """Normalise one cell of the operator-configuration table.

    Cells carry human decoration the prose needs and a program does not: bold, a backticked
    token followed by an explanation, an arrow to the binding file. Take the backticked token
    when there is one, because that is where the machine-usable value always lives.
    """
    value = raw.strip()
    backticked = re.findall(r"`([^`]+)`", value)
    if backticked:
        return backticked[0].strip()
    value = value.replace("**", "").strip()
    # Drop a trailing parenthetical or em-dash explanation.
    value = re.split(r"\s+—\s+|\s+\(", value, maxsplit=1)[0]
    return value.strip()


def _parse_config_block(text: str) -> dict[str, str]:
    if CONFIG_START not in text or CONFIG_END not in text:
        return {}
    block = text.split(CONFIG_START, 1)[1].split(CONFIG_END, 1)[0]
    config: dict[str, str] = {}
    for line in block.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        key = cells[0].lower().strip()
        if key in ("setting", "") or set(key) <= {"-", ":"}:
            continue
        config[key] = _clean_value(cells[1])
    return config


def load_config() -> dict[str, str]:
    """Resolve the operator configuration: versioned defaults, overridden by the local file.

    Deliberately the SAME two files the prose reads. A script with its own config file would be
    a third source of truth, and the whole point of this change was removing the second one.
    """
    config: dict[str, str] = {}
    skill_md = SKILL_DIR / "SKILL.md"
    if skill_md.is_file():
        config.update(_parse_config_block(skill_md.read_text(encoding="utf-8")))
    local = SKILL_DIR / "operator.local.md"
    if local.is_file():
        config.update(_parse_config_block(local.read_text(encoding="utf-8")))
    return config


def cfg(config: dict[str, str], key: str, default: str = "") -> str:
    for name, value in config.items():
        if name.startswith(key.lower()):
            return value
    return default


# ---------------------------------------------------------------------------
# repository identity
# ---------------------------------------------------------------------------

def repo_identity(cwd: Path) -> tuple[str, str]:
    data = gh_json(["repo", "view", "--json", "owner,name"], cwd=cwd)
    if not data:
        raise ReadFailure("gh repo view returned nothing — is this a GitHub checkout?")
    return data["owner"]["login"], data["name"]


# ---------------------------------------------------------------------------
# markers
# ---------------------------------------------------------------------------

def marker(kind: str, **attrs) -> str:
    body = " ".join(f"{k.replace('_', '-')}={v}" for k, v in attrs.items() if v)
    return f"<!-- issue-flow: {kind} {body} -->"


def parse_markers(body: str) -> list[dict[str, str]]:
    found = []
    for match in MARKER_RE.finditer(body or ""):
        attrs = dict(
            pair.split("=", 1)
            for pair in match.group("attrs").split()
            if "=" in pair
        )
        attrs["kind"] = match.group("kind")
        found.append(attrs)
    return found


# Prose fallback for comments written before markers existed. Deliberately narrow: a comment that
# merely MENTIONS a run-id is not a control message, and treating one as a stand-down would have a
# run abandon work nobody asked it to drop.
STANDDOWN_PROSE = re.compile(
    r"\b(stand(?:ing)?\s*down|standing down|backing off|back off|reclaiming from|"
    r"adjudicat\w*|you lost|release the item)\b",
    re.IGNORECASE,
)
# Legacy claim detection, anchored on purpose.
#
# An unanchored "claimed by <x>" matches ordinary prose — "this was already claimed by
# @old-contributor months ago" is a plausible comment on any issue with organic history or a
# triage bot. That phantom carries a real server timestamp, so it sorts EARLIEST, and `claim`
# concludes it lost a race that never happened: it posts a stand-down and strips its own
# `dev:<runtime>` label on the strength of text written by someone else entirely.
#
# So the fallback demands the shape of an actual claim comment, not the words: the phrase must
# OPEN the comment, and the horizon clause must be present. `SKILL.md` prescribes exactly one
# form — `Claimed by <run-id>, expect to report by <time>` — and every binding writes it, so
# requiring both costs no real claim and rejects every mention. This mirrors the deliberate
# narrowness of STANDDOWN_PROSE below, which the original of this pattern lacked.
CLAIM_PROSE = re.compile(r"^\s*(?:[*_>#\s]*)claimed by\s+(?P<run>[\w.-]+)", re.IGNORECASE)
CLAIM_HORIZON_PROSE = re.compile(r"expect(?:s|ing)?\s+to\s+report\s+by", re.IGNORECASE)


def claim_comments(comments: list[dict]) -> list[tuple[str, str, dict]]:
    """Every claim on the timeline as (created_at, run_id, comment), server order preserved."""
    claims = []
    for comment in comments:
        body = comment.get("body", "")
        run_id = None
        for mark in parse_markers(body):
            if mark.get("kind") == "claim" and mark.get("run-id"):
                run_id = mark["run-id"]
                break
        if not run_id:
            # Both conditions, never one: the phrase must open the comment AND the horizon clause
            # must be present. A mention like "already claimed by @someone months ago" satisfies
            # neither, and must not be able to unseat a real claim.
            match = CLAIM_PROSE.match(body or "")
            if match and CLAIM_HORIZON_PROSE.search(body or ""):
                run_id = match.group("run")
        if run_id:
            claims.append((comment.get("createdAt", ""), run_id, comment))
    claims.sort(key=lambda item: item[0])
    return claims


def released_run_ids(comments: list[dict]) -> set[str]:
    """Run-ids that no longer hold the item, so their earlier claim stops counting.

    A `reclaim` marker names the run it took over FROM, not the run that wrote it, which is why
    every target attribute is read rather than just `run-id`. Miss that and a reclaimed item still
    adjudicates in favour of the run that was reclaimed from.
    """
    released = set()
    for comment in comments:
        for mark in parse_markers(comment.get("body", "")):
            if mark.get("kind") not in RELEASE_KINDS:
                continue
            for attr in TARGET_ATTRS:
                if mark.get(attr):
                    released.add(mark[attr])
    return released


def last_activity_by(comments: list[dict], run_id: str) -> str:
    """The newest timestamp on which this run said anything. '' when it never did."""
    stamps = [
        comment.get("createdAt", "")
        for comment in comments
        if any(
            mark.get(attr) == run_id
            for mark in parse_markers(comment.get("body", ""))
            for attr in TARGET_ATTRS
        )
        or run_id in (comment.get("body") or "")
    ]
    return max(stamps) if stamps else ""


def parse_stamp(value: str):
    """Parse an ISO-8601 UTC stamp into a datetime, or None when it is not one.

    Timestamps here arrive at two precisions — GitHub writes `2026-07-25T21:54:35Z`, a declared
    horizon is written `2026-07-26T00:54Z` — and comparing those as STRINGS is wrong in exactly
    the case that matters: `00:54:00Z` sorts BEFORE `00:54Z`, because '0' precedes 'Z'. A run that
    had just spoken would be read as silent. And a horizon is free text a human may have written
    in prose, which must compare as "unknown" rather than as some accidental ordering.
    """
    import datetime

    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    for suffix in ("", ":00"):
        try:
            parsed = datetime.datetime.fromisoformat(text if not suffix else text.replace("+00:00", f"{suffix}+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return parsed
    return None


def stale_claims(claims, comments: list[dict], now: str) -> set[str]:
    """Claims whose declared horizon has passed with no word from the holder since.

    This is the mechanical half of SKILL.md's reclaim rule. Without it a run that died mid-build
    holds its issue forever: `claim` re-reads the timeline, finds that never-released claim as the
    earliest live entry, and tells a live run it lost a race to a process that no longer exists —
    reproducing the exact "abandoned work" incident this script was written against.

    Deliberately conservative. A horizon is a heuristic, not a lease: only a claim that BOTH
    declared a horizon and has been silent past it counts as stale. A claim with no horizon is
    left alone here, because judging "a few hours is long enough" belongs to a human or to the
    explicit `reclaim` command, not to an automatic adjudication.
    """
    moment = parse_stamp(now)
    if moment is None:
        return set()
    stale = set()
    for _created_at, run_id, comment in claims:
        horizon = None
        for mark in parse_markers(comment.get("body", "")):
            if mark.get("kind") == "claim" and mark.get("horizon"):
                horizon = parse_stamp(mark["horizon"])
        # An unparseable or absent horizon is "unknown", never "expired".
        if horizon is None or horizon >= moment:
            continue
        spoke_at = parse_stamp(last_activity_by(comments, run_id))
        if spoke_at is None or spoke_at < horizon:
            stale.add(run_id)
    return stale


def utc_now_stamp() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


# ---------------------------------------------------------------------------
# board mirror
# ---------------------------------------------------------------------------

BOARD_FIELDS_QUERY = """
query($login: String!, $number: Int!) {
  OWNER(login: $login) {
    projectV2(number: $number) {
      id
      fields(first: 20) {
        nodes {
          ... on ProjectV2SingleSelectField {
            id
            name
            options { id name description }
          }
        }
      }
    }
  }
}
"""

BOARD_ITEMS_QUERY = """
query($login: String!, $number: Int!, $cursor: String) {
  OWNER(login: $login) {
    projectV2(number: $number) {
      items(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          content { ... on Issue { number labels(first: 20) { nodes { name } } } }
          fieldValueByName(name: "Status") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
        }
      }
    }
  }
}
"""

BOARD_SET_MUTATION = """
mutation($project: ID!, $item: ID!, $field: ID!, $option: String!) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $project
    itemId: $item
    fieldId: $field
    value: { singleSelectOptionId: $option }
  }) {
    projectV2Item { id }
  }
}
"""


class Board:
    """The project-board mirror.

    Every method is best-effort by design: the `status:*` label is the authoritative store, so a
    board that cannot be reached is a quiet skip and never blocks a transition. What is NOT
    allowed is not trying — every skipped attempt is how the board was found five states behind.
    """

    def __init__(self, spec: str, cwd: Path, use_cache: bool = True):
        self.enabled = bool(spec) and spec.lower() != "none" and "/" in spec
        self.cwd = cwd
        self.skip_reason = None
        self.owner = self.number = None
        if self.enabled:
            self.owner, number = spec.split("/", 1)
            try:
                self.number = int(number)
            except ValueError:
                self.enabled = False
                self.skip_reason = f"unparseable board spec {spec!r}"
        self.use_cache = use_cache
        self._meta = None

    def _cache_path(self) -> Path:
        # A private subdirectory, not the shared temp root. On a host with a world-writable /tmp
        # another local account could pre-plant this file and point the mirror's mutation at
        # project and field ids of its choosing — bounded by what our own token can already write,
        # so not privilege escalation, but a confused deputy writing to the wrong board.
        directory = Path(tempfile.gettempdir()) / f"issue-flow-{os.getuid() if hasattr(os, 'getuid') else os.getlogin()}"
        try:
            directory.mkdir(mode=0o700, exist_ok=True)
        except OSError:
            directory = Path(tempfile.gettempdir())
        return directory / f"board-{self.owner}-{self.number}.json"

    def _graphql(self, query: str, **variables):
        args = ["api", "graphql", "-f", f"query={query}"]
        for key, value in variables.items():
            flag = "-F" if isinstance(value, int) else "-f"
            args.extend([flag, f"{key}={value}"])
        return gh_json(args, cwd=self.cwd)

    def _query_owner_scoped(self, query: str, **variables):
        """Try user-owned, then organisation-owned.

        bindings/github.md records this as a quiet failure mode: for an organisation project
        `user(login:)` returns null rather than erroring, so the mirror silently never fires.
        Trying both removes the silence.
        """
        for owner_type in ("user", "organization"):
            data = self._graphql(query.replace("OWNER", owner_type), **variables)
            node = (data or {}).get("data", {}).get(owner_type)
            if node and node.get("projectV2"):
                return node["projectV2"], owner_type
        return None, None

    def meta(self):
        """Project id, Status field id and option ids — resolved once and cached.

        bindings/github.md names per-transition discovery as "the overhead that tempts a run to
        skip the mirror", so the cache exists to remove the temptation, not to save API calls.
        Ids are stable; a renamed option is caught by the transition read-back anyway.
        """
        if self._meta is not None:
            return self._meta
        cache = self._cache_path()
        if self.use_cache and cache.is_file():
            try:
                cached = json.loads(cache.read_text(encoding="utf-8"))
                if time.time() - cached.get("cached_at", 0) < 86400:
                    self._meta = cached["meta"]
                    return self._meta
            except (json.JSONDecodeError, KeyError, OSError):
                pass
        project, _ = self._query_owner_scoped(BOARD_FIELDS_QUERY, login=self.owner, number=self.number)
        if not project:
            self.skip_reason = "project not visible (missing `project` scope, or wrong owner/number)"
            return None
        status = next(
            (f for f in project["fields"]["nodes"] if f and f.get("name") == "Status"),
            None,
        )
        if not status:
            self.skip_reason = "project has no single-select `Status` field"
            return None
        self._meta = {
            "project_id": project["id"],
            "field_id": status["id"],
            "options": [
                {"id": o["id"], "name": o["name"], "description": o.get("description") or ""}
                for o in status["options"]
            ],
        }
        try:
            cache.write_text(
                json.dumps({"cached_at": time.time(), "meta": self._meta}),
                encoding="utf-8",
            )
        except OSError:
            pass
        return self._meta

    @staticmethod
    def option_for(meta: dict, state: str) -> dict | None:
        """Match a workflow state to a board column by option NAME or DESCRIPTION.

        Not by description alone: real boards describe `Analysis`..`Blocked` with their exact
        `status:*` labels and then describe `Done` as `closed`, because that column also tracks
        the tracker's own closed flag. A matcher demanding `status:done` there would fail on the
        one transition that matters most.
        """
        wanted = state.lower()
        for option in meta["options"]:
            name = option["name"].lower().replace(" ", "-")
            description = option["description"].lower()
            if name == wanted or description == f"status:{wanted}" or description == wanted:
                return option
        return None

    def _iter_items(self):
        """Yield every board item, following pagination.

        `items(first:100)` stops finding issues once the board passes a hundred items, and the
        three callers below all need the same walk. Writing that loop three times is three places
        for the cursor handling to drift apart.
        """
        cursor = None
        while True:
            variables = {"login": self.owner, "number": self.number}
            if cursor:
                variables["cursor"] = cursor
            project, _ = self._query_owner_scoped(BOARD_ITEMS_QUERY, **variables)
            if not project:
                self.skip_reason = "project not visible while listing items"
                return
            items = project["items"]
            yield from items["nodes"]
            if not items["pageInfo"]["hasNextPage"]:
                return
            cursor = items["pageInfo"]["endCursor"]

    def item_id(self, issue: int) -> str | None:
        for node in self._iter_items():
            content = node.get("content") or {}
            if content.get("number") == issue:
                return node["id"]
        return None

    def set_status(self, issue: int, state: str) -> dict:
        """Attempt the mirror. Returns what happened; never raises."""
        if not self.enabled:
            return {"attempted": False, "skipped": "no board configured"}
        try:
            meta = self.meta()
            if not meta:
                return {"attempted": True, "skipped": self.skip_reason}
            option = self.option_for(meta, state)
            if not option:
                return {"attempted": True, "skipped": f"no board column mirrors {state!r}"}
            item = self.item_id(issue)
            if not item:
                return {"attempted": True, "skipped": f"issue #{issue} is not on the board"}
            self._graphql(
                BOARD_SET_MUTATION,
                project=meta["project_id"],
                item=item,
                field=meta["field_id"],
                option=option["id"],
            )
            return {"attempted": True, "set_to": option["name"]}
        except ReadFailure as exc:
            return {"attempted": True, "skipped": str(exc)}

    def read_status(self, issue: int) -> str | None:
        if not self.enabled:
            return None
        for node in self._iter_items():
            content = node.get("content") or {}
            if content.get("number") == issue:
                return (node.get("fieldValueByName") or {}).get("name")
        return None

    def all_cards(self) -> list[dict]:
        cards = []
        for node in self._iter_items():
            content = node.get("content") or {}
            if not content.get("number"):
                continue
            cards.append(
                {
                    "issue": content["number"],
                    "column": (node.get("fieldValueByName") or {}).get("name"),
                    "labels": [n["name"] for n in content.get("labels", {}).get("nodes", [])],
                }
            )
        return cards


# ---------------------------------------------------------------------------
# label helpers
# ---------------------------------------------------------------------------

def ensure_label(name: str, color: str, cwd: Path) -> None:
    """Create before attaching. `gh` refuses to attach a label that does not exist, and the
    error arrives at `gh issue create` — the analyst's last step, after all the analysis is done.
    `--force` is idempotent, so this costs one call and removes the failure mode entirely."""
    run(["gh", "label", "create", name, "--color", color, "--force"], cwd=cwd, check=False)


def status_labels(labels: list[str]) -> list[str]:
    return [name for name in labels if name.startswith("status:")]


def issue_view(issue: int, fields: str, cwd: Path) -> dict:
    data = gh_json(["issue", "view", str(issue), "--json", fields], cwd=cwd)
    if data is None:
        raise ReadFailure(f"gh issue view {issue} returned nothing")
    return data


def label_names(data: dict) -> list[str]:
    return [label["name"] for label in data.get("labels", [])]


# ---------------------------------------------------------------------------
# verify_claim — the renewal
# ---------------------------------------------------------------------------

def do_verify_claim(issue: int, run_id: str, expect_state: str, cwd: Path,
                    allow_closed_by_pr: int | None = None) -> dict:
    """One read, three checks. A failed CHECK is a stop instruction; a failed READ is nothing.

    That distinction is the whole point: SKILL.md records a run that lost a claim race by five
    seconds, was told so 33 seconds later, and then worked another ~48 minutes because nothing in
    its heartbeat loop ever read the timeline again.

    `allow_closed_by_pr` exists for exactly one moment: the renewal that runs immediately before
    `close`, after your own merge. Under `gh issue develop` the merge auto-closes the issue, so a
    closed issue there is the expected outcome of your own delivery rather than evidence that
    somebody else took it. Without this, the renewal would hard-stop and the run would skip the
    `transition --to done` that the auto-close makes mandatory — the rule blocking itself.

    It is deliberately narrow: the issue must have been closed BY THE PR YOU MERGED. A closed issue
    with any other closer is still a stop, because that is the case the check exists for.
    """
    data = issue_view(issue, "state,labels,comments", cwd=cwd)

    closed_by_own_pr = False
    if data.get("state") != "OPEN":
        if allow_closed_by_pr is not None:
            closers = {ref.get("number") for ref in closing_refs(issue, cwd)}
            closed_by_own_pr = allow_closed_by_pr in closers
        if not closed_by_own_pr:
            raise Stop(
                {
                    "ok": False,
                    "reason": "issue-not-open",
                    "detail": f"issue #{issue} is {data.get('state')} — someone delivered or killed it",
                    "action": "stop; change nothing about the state, it is not yours",
                }
            )

    present = status_labels(label_names(data))
    if len(present) != 1 or present[0] != f"status:{expect_state}":
        raise Stop(
            {
                "ok": False,
                "reason": "unexpected-state",
                "detail": f"expected exactly [status:{expect_state}], found {present}",
                "action": "stop; the item moved without you — leave the new state alone",
            }
        )

    comments = data.get("comments", [])
    mine = [c for at, rid, c in claim_comments(comments) if rid == run_id]
    watermark = mine[0].get("createdAt", "") if mine else ""

    for comment in comments:
        if comment.get("createdAt", "") <= watermark:
            continue
        body = comment.get("body", "")
        marks = parse_markers(body)
        controlled = any(
            mark.get("kind") in ("standdown", "reclaim", "adjudication")
            and (mark.get("run-id") == run_id or mark.get("target") == run_id
                 or mark.get("from") == run_id)
            for mark in marks
        )
        if not controlled and not marks:
            # Legacy prose fallback: a control message must both name the run-id AND instruct.
            controlled = run_id in body and bool(STANDDOWN_PROSE.search(body))
        if controlled:
            raise Stop(
                {
                    "ok": False,
                    "reason": "control-message",
                    "detail": f"a control message after your claim names {run_id}",
                    "comment_url": comment.get("url"),
                    "action": "stop; acknowledge once, drop your dev:<runtime> label, write nothing else",
                }
            )

    return {
        "ok": True,
        "issue": issue,
        "run_id": run_id,
        "state": expect_state,
        "claim_watermark": watermark or None,
        "checked": ["issue-open", "single-expected-state", "no-control-message"],
    }


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------

def cmd_config(args, config, cwd) -> dict:
    board = Board(cfg(config, "project board"), cwd)
    return {
        "ok": True,
        "skill_dir": str(SKILL_DIR),
        "config": config,
        "board": {"enabled": board.enabled, "owner": board.owner, "number": board.number},
        "scripted_operations": [
            "ensure-states", "create", "list-state", "claim", "reclaim", "verify-claim",
            "transition", "comment", "heartbeat", "start-branch", "publish-review", "unassign",
            "check-closing-keywords", "audit-board",
        ],
        "not_scripted": {
            "merge": "irreversible remote write — the agent runs it after verifying SHAs",
            "publish_version": "irreversible remote write — annotated tags are never moved",
            "close": "kept with merge/publish_version so delivery stays one prose-owned sequence",
            "review_status/ci_status": "verdict and CI interpretation stay with the agent",
        },
    }


def cmd_ensure_states(args, config, cwd) -> dict:
    for state in STATES:
        ensure_label(f"status:{state}", "ededed", cwd)
    return {"ok": True, "ensured": [f"status:{s}" for s in STATES]}


def cmd_create(args, config, cwd) -> dict:
    for state in STATES:
        ensure_label(f"status:{state}", "ededed", cwd)
    labels = [f"status:{args.state}"]
    if args.priority:
        ensure_label(args.priority, "d93f0b", cwd)
        labels.append(args.priority)
    if args.domain:
        ensure_label(f"domain:{args.domain}", "0e8a16", cwd)
        labels.append(f"domain:{args.domain}")
    if args.runtime:
        ensure_label(f"analyst:{args.runtime}", "c5def5", cwd)
        labels.append(f"analyst:{args.runtime}")

    body = Path(args.body_file).read_text(encoding="utf-8")
    if args.run_id:
        body = body.rstrip() + f"\n\n{marker('analysis', run_id=args.run_id)}\n"
    with body_file(body) as path:
        create_args = ["issue", "create", "--title", f"{args.identity}: {args.title}",
                       "--body-file", path]
        for label in labels:
            create_args.extend(["--label", label])
        proc = run(["gh", *create_args], cwd=cwd, writes=True)

    # `gh issue create` prints the issue URL, but its stdout is not a contract. Parse defensively
    # and say so plainly on failure: an unguarded index here would crash out of the JSON contract
    # entirely, after the issue was already filed.
    match = re.search(r"/issues/(\d+)\s*$", proc.stdout.strip())
    if not match:
        raise WriteFailure(
            "the issue may have been created, but its URL could not be parsed from "
            f"`gh issue create` output: {proc.stdout.strip()[:200]!r} — re-read before retrying, "
            "a retry would file a duplicate"
        )
    url = proc.stdout.strip().splitlines()[-1]
    number = int(match.group(1))

    # The case everyone forgets: a fresh issue reaches the board with an empty Status and no
    # transition ever follows to correct it.
    board = Board(cfg(config, "project board"), cwd, use_cache=not args.no_cache)
    mirror = board.set_status(number, args.state)

    return {"ok": True, "issue": number, "url": url, "labels": labels, "board": mirror}


def cmd_list_state(args, config, cwd) -> dict:
    data = gh_json(
        ["issue", "list", "--label", f"status:{args.state}", "--search", "no:assignee",
         "--json", "number,title,labels,createdAt", "--limit", str(args.limit)],
        cwd=cwd,
    ) or []

    # Partition by domain, and order each partition OLDEST FIRST — that is the workflow's
    # tie-break, and it is the only ordering this script is entitled to apply. Ranking by priority
    # INSIDE a partition needs the domain's own scale contract, which this script does not know
    # and must not invent: SKILL.md forbids manufacturing a global rank across scales. So every
    # label is returned raw alongside the age order, and the agent applies the contract it loaded.
    partitions: dict[str, list] = {}
    for item in data:
        names = [label["name"] for label in item["labels"]]
        domain = next((n.split(":", 1)[1] for n in names if n.startswith("domain:")), "unassigned")
        partitions.setdefault(domain, []).append(
            {"number": item["number"], "title": item["title"],
             "createdAt": item["createdAt"], "labels": names}
        )
    for items in partitions.values():
        items.sort(key=lambda i: i["createdAt"])
    return {
        "ok": True,
        "state": args.state,
        "count": len(data),
        "partitions": partitions,
        "note": "ordering inside a partition needs the domain's scale contract — apply it yourself",
    }


def cmd_claim(args, config, cwd) -> dict:
    """Assign, comment, then adjudicate by the comment timeline.

    Re-reading the assignee cannot adjudicate this: agents authenticate as ONE shared account, so
    `--add-assignee @me` twice leaves exactly one assignee and the re-read shows a clean issue
    assigned to you while another run is already building it.
    """
    ensure_label(f"dev:{args.runtime}", "bfd4f2", cwd)
    run(["gh", "issue", "edit", str(args.issue), "--add-assignee", "@me"], cwd=cwd, writes=True)

    # Idempotent on retry. `claim` is four writes and a read; if it dies after the comment lands,
    # the caller re-runs it — and a second claim comment for the same run muddies the very record
    # the race is adjudicated from. So look for our own claim first and reuse it. This is also
    # what makes the retry advice on a failed write safe to follow at all.
    existing = claim_comments(issue_view(args.issue, "comments", cwd=cwd).get("comments", []))
    already_mine = [at for at, rid, _ in existing if rid == args.run_id]

    if not already_mine:
        body = (
            f"Claimed by {args.run_id}, expect to report by {args.horizon}.\n\n"
            f"{marker('claim', run_id=args.run_id, horizon=args.horizon)}\n"
        )
        with body_file(body) as path:
            run(["gh", "issue", "comment", str(args.issue), "--body-file", path],
                cwd=cwd, writes=True)

    data = issue_view(args.issue, "comments", cwd=cwd)
    comments = data.get("comments", [])
    claims = claim_comments(comments)
    released = released_run_ids(comments)
    # A claim that blew its own declared horizon in silence is not a competitor, it is a corpse.
    # Without this the earliest claim wins forever and a dead run holds its issue permanently.
    expired = stale_claims(claims, comments, utc_now_stamp())
    dead = released | (expired - {args.run_id})
    live = [(at, rid) for at, rid, _ in claims if rid not in dead]

    if not live:
        # The write succeeded but the timeline has not caught up. Say so as a WRITE failure: the
        # comment exists, so "retry the read" is the right action and "retry the whole command"
        # is not. The idempotence guard above makes even a full retry safe, but the caller should
        # still be told which of the two happened.
        raise WriteFailure(
            "the claim comment was written but is not visible on the timeline yet — re-read; "
            "re-running `claim` will reuse the existing comment rather than post a second one"
        )

    winner_at, winner = live[0]
    if winner != args.run_id:
        # Losing is cheap and takes seconds; two runs building the same issue is not.
        with body_file(
            f"{args.run_id} standing down: {winner} claimed at {winner_at}, earlier than this run. "
            f"Nothing was created.\n\n{marker('standdown', run_id=args.run_id)}\n"
        ) as path:
            run(["gh", "issue", "comment", str(args.issue), "--body-file", path],
                cwd=cwd, writes=True)
        # Only the per-runtime label comes off. `@me` is the shared account and removing it would
        # strip the winner, who is holding the item right now.
        run(["gh", "issue", "edit", str(args.issue), "--remove-label", f"dev:{args.runtime}"],
            cwd=cwd, check=False)
        raise Stop(
            {
                "ok": False,
                "reason": "lost-claim-race",
                "winner": winner,
                "winner_claimed_at": winner_at,
                "action": "stood down and released your dev label; take the next item",
            }
        )

    run(["gh", "issue", "edit", str(args.issue), "--add-label", f"dev:{args.runtime}"],
        cwd=cwd, writes=True)
    return {
        "ok": True,
        "issue": args.issue,
        "run_id": args.run_id,
        "reused_existing_claim": bool(already_mine),
        "superseded_expired_claims": sorted(expired - {args.run_id}),
        "claimed_at": winner_at,
        "horizon": args.horizon,
        "next": "transition to in-progress before any repository write",
    }


def cmd_verify_claim(args, config, cwd) -> dict:
    return do_verify_claim(args.issue, args.run_id, args.expect_state, cwd,
                           allow_closed_by_pr=args.allow_closed_by_pr)


def cmd_reclaim(args, config, cwd) -> dict:
    """Take over work whose holder never came back.

    The record of the takeover matters more than the takeover, so the comment goes FIRST and names
    who it was taken from. If the original holder was alive and merely slow, its next renewal reads
    this and backs off exactly like the loser of a claim race — which is why the marker carries
    `from`, and why `released_run_ids` treats a reclaim as releasing that run rather than this one.

    The dead run's `dev:<runtime>` marker is removed as ours goes on. It cannot do that itself,
    which is the whole reason this command exists; skip it and the item claims two runtimes hold it.
    """
    data = issue_view(args.issue, "state,labels,comments", cwd=cwd)
    if data.get("state") != "OPEN":
        raise Stop({"ok": False, "reason": "issue-not-open",
                    "action": "nothing to reclaim on a closed issue"})

    comments = data.get("comments", [])
    claims = claim_comments(comments)
    released = released_run_ids(comments)
    holders = [(at, rid) for at, rid, _ in claims if rid not in released]
    if not holders:
        raise Stop({"ok": False, "reason": "nothing-to-reclaim",
                    "action": "no live claim on this issue — use `claim` instead"})

    held_at, holder = holders[0]
    if holder == args.run_id:
        raise Stop({"ok": False, "reason": "already-yours",
                    "action": f"{args.run_id} is already the live claimant"})

    expired = stale_claims(claims, comments, utc_now_stamp())
    if holder not in expired and not args.force:
        raise Stop(
            {
                "ok": False,
                "reason": "holder-not-stale",
                "holder": holder,
                "claimed_at": held_at,
                "action": "the holder's declared horizon has not passed, or it has spoken since. "
                          "Reclaiming early costs someone a duplicated hour — pass --force only "
                          "with a reason you can defend on the issue",
            }
        )

    with body_file(
        f"Reclaiming from `{holder}`; last activity {last_activity_by(comments, holder) or 'none'}, "
        f"claimed at {held_at}.\n\n"
        f"Taken over by `{args.run_id}`. Nothing the previous run left will be discarded — its "
        f"branch, diagnosis and ruled-out hypotheses stay valid.\n\n"
        f"{marker('reclaim', run_id=args.run_id, **{'from': holder})}\n"
    ) as note:
        run(["gh", "issue", "comment", str(args.issue), "--body-file", note], cwd=cwd, writes=True)

    ensure_label(f"dev:{args.runtime}", "bfd4f2", cwd)
    edit = ["gh", "issue", "edit", str(args.issue), "--add-assignee", "@me",
            "--add-label", f"dev:{args.runtime}"]
    for label in label_names(data):
        if label.startswith("dev:") and label != f"dev:{args.runtime}":
            edit.extend(["--remove-label", label])
    run(edit, cwd=cwd, writes=True)

    return {
        "ok": True,
        "issue": args.issue,
        "reclaimed_from": holder,
        "run_id": args.run_id,
        "forced": bool(args.force),
        "next": "read what the dead run left on the issue — the work may be further along than the label",
    }


def cmd_transition(args, config, cwd) -> dict:
    """Board mirror first, then the label swap in ONE call, then read BOTH back.

    Order is deliberate. The fragile, easily-skipped half runs before anything can short-circuit
    it; the reliable one-call label edit follows. And the read-back covers the board because that
    is the half with no other feedback loop — a wrong label is caught by the very next
    list_state, a column nobody looks at stays wrong forever.
    """
    board = Board(cfg(config, "project board"), cwd, use_cache=not args.no_cache)
    mirror = board.set_status(args.issue, args.to)

    edit = ["gh", "issue", "edit", str(args.issue), "--add-label", f"status:{args.to}"]
    if args.from_state:
        edit.extend(["--remove-label", f"status:{args.from_state}"])
    else:
        current = status_labels(label_names(issue_view(args.issue, "labels", cwd=cwd)))
        for stale in current:
            if stale != f"status:{args.to}":
                edit.extend(["--remove-label", stale])
    run(edit, cwd=cwd)

    after = status_labels(label_names(issue_view(args.issue, "labels", cwd=cwd)))
    result = {
        "ok": True,
        "issue": args.issue,
        "to": args.to,
        "labels_after": after,
        "board": mirror,
    }

    if after != [f"status:{args.to}"]:
        raise Stop(
            {
                "ok": False,
                "reason": "label-readback-failed",
                "detail": f"expected exactly [status:{args.to}], found {after}",
                "action": "fix on the spot — a two-state item poisons every query touching either state",
            }
        )

    if board.enabled:
        column = board.read_status(args.issue)
        meta = board.meta()
        expected = Board.option_for(meta, args.to) if meta else None
        result["board_column_after"] = column
        if column is None:
            result["board_note"] = (
                f"issue #{args.issue} is not on the board (or not visible) — labels carry the truth"
            )
        elif expected and column != expected["name"]:
            # Before believing the mirror failed, distrust the CACHE. The mutation addresses the
            # column by stable id, so renaming a column on the board leaves the board correct while
            # the cached option NAME goes stale — and comparing a live name against a stale one
            # then reports a failure that never happened, blocking every transition on this issue
            # for as long as the cache lives. Re-resolve with fresh ids and compare again; only a
            # mismatch that survives that is real.
            fresh = Board(cfg(config, "project board"), cwd, use_cache=False)
            fresh_expected = Board.option_for(fresh.meta(), args.to) if fresh.meta() else None
            if fresh_expected and column == fresh_expected["name"]:
                result["board_note"] = (
                    "the cached column name was stale (the board column was renamed); the mirror "
                    "itself had landed correctly and the cache is now refreshed"
                )
            else:
                retry = fresh.set_status(args.issue, args.to)
                recheck = fresh.read_status(args.issue)
                result["board_repair"] = {"retried": retry, "column_now": recheck}
                if fresh_expected and recheck != fresh_expected["name"]:
                    raise Stop(
                        {
                            "ok": False,
                            "reason": "board-readback-failed",
                            "detail": f"board says {recheck!r}, labels say status:{args.to}",
                            "action": "the mirror is not landing — investigate before continuing",
                        }
                    )
    return result


def cmd_comment(args, config, cwd) -> dict:
    body = Path(args.body_file).read_text(encoding="utf-8")
    if args.run_id and args.kind:
        body = body.rstrip() + f"\n\n{marker(args.kind, run_id=args.run_id)}\n"
    with body_file(body) as path:
        run(["gh", "issue", "comment", str(args.issue), "--body-file", path], cwd=cwd, writes=True)
    return {"ok": True, "issue": args.issue, "kind": args.kind or "note"}


def cmd_heartbeat(args, config, cwd) -> dict:
    """Read before you write. A heartbeat that only writes is deaf to the one channel that can
    revoke the claim — which is exactly how a stand-down sat unread for 48 minutes."""
    verdict = do_verify_claim(args.issue, args.run_id, args.expect_state, cwd)
    body = Path(args.body_file).read_text(encoding="utf-8")
    body = body.rstrip() + f"\n\n{marker('heartbeat', run_id=args.run_id)}\n"
    with body_file(body) as path:
        run(["gh", "issue", "comment", str(args.issue), "--body-file", path], cwd=cwd, writes=True)
    return {"ok": True, "issue": args.issue, "renewed": verdict["checked"], "posted": True}


def worktree_path(template: str, repo: str, branch: str, run_id: str, issue: int) -> Path:
    """Resolve the configured worktree template.

    Every substituted value is flattened, not just the branch. The branch is the one that carries
    a `/` in normal use, but the reason — a separator in a substituted value silently restructures
    the path instead of naming a directory — applies identically to the run-id, and applying it to
    only one of them is the inconsistency that becomes a traversal later. `..` segments are
    rejected outright for the same reason.

    Path uniqueness comes from the run-id, and `cmd_start_branch` refuses a path that already
    exists. Why that is not paranoia — the 2026-07-24 collision — is told once, in
    `bindings/github.md` under *Branch, worktree and the linked issue*. It is deliberately NOT
    retold here: an incident narrated in three files goes stale in two of them.
    """
    def flatten(value: str) -> str:
        cleaned = re.sub(r"[/\\]+", "-", str(value)).strip()
        if not cleaned or cleaned != cleaned.replace("..", ""):
            raise Stop(
                {
                    "ok": False,
                    "reason": "unsafe-worktree-component",
                    "component": str(value),
                    "action": "a worktree path component may not be empty or contain `..`",
                }
            )
        return cleaned

    resolved = (
        template.replace("<repo>", flatten(repo))
        .replace("<branch>", flatten(branch))
        .replace("<run-id>", flatten(run_id))
        .replace("<issue>", flatten(issue))
    )
    return Path(resolved)


def cmd_start_branch(args, config, cwd) -> dict:
    """Create the branch server-side (already linked to the issue), then an isolated worktree.

    `gh issue develop` is one command that replaces branch creation AND recording: it branches
    from the fresh base and links it in the issue's Development sidebar. A branch nobody can find
    from the issue is work nobody can follow.
    """
    # A claim binds only what the tracker can see, so renew it before the first thing it cannot.
    # Nothing has been created yet, so standing down here costs one comment.
    do_verify_claim(args.issue, args.run_id, args.expect_state, cwd)

    template = args.worktree_root or cfg(config, "worktree location")
    if not template or template.lower() == "unset":
        raise Stop(
            {
                "ok": False,
                "reason": "no-worktree-location",
                "action": "set the `Worktree location` row in operator.local.md, or pass --worktree-root",
            }
        )

    _, repo_name = repo_identity(cwd)
    path = worktree_path(template, repo_name, args.branch, args.run_id, args.issue)
    if path.exists():
        raise Stop(
            {
                "ok": False,
                "reason": "worktree-path-exists",
                "path": str(path),
                "action": "another run may hold it — do not write into it; verify the claim and the holder",
            }
        )

    developed = run(
        ["gh", "issue", "develop", str(args.issue), "--name", args.branch, "--base", args.base],
        cwd=cwd,
        check=False,
    )
    linked = developed.returncode == 0
    if not linked:
        # The branch may already exist from a resumed run; fall back to a local branch off the
        # fresh remote base and record it in prose, which is what the binding prescribes.
        #
        # `--` closes option parsing. Without it a branch name beginning with `-` is read as a
        # flag: `git branch --force -D origin/main` deletes the branch literally named
        # `origin/main` instead of creating one called `-D`. Nothing upstream validates the value,
        # and this call runs with check=False, so the damage would be silent.
        run(["git", "fetch", "origin", "--", args.base], cwd=cwd)
        run(["git", "branch", "--force", "--", args.branch, f"origin/{args.base}"],
            cwd=cwd, check=False, writes=True)

    run(["git", "fetch", "origin"], cwd=cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "worktree", "add", "--", str(path), args.branch], cwd=cwd, writes=True)

    head = run(["git", "rev-parse", "HEAD"], cwd=path).stdout.strip()
    base_sha = run(["git", "rev-parse", f"origin/{args.base}"], cwd=cwd).stdout.strip()

    with body_file(
        f"Branch: `{args.branch}` (base `{args.base}` @ `{base_sha}`)\n"
        f"Worktree: `{path}`\n"
        f"Held by `{args.run_id}`.\n\n"
        f"{marker('branch', run_id=args.run_id, branch=args.branch, base=base_sha)}\n"
    ) as note:
        run(["gh", "issue", "comment", str(args.issue), "--body-file", note], cwd=cwd, writes=True)

    return {
        "ok": True,
        "issue": args.issue,
        "branch": args.branch,
        "natively_linked": linked,
        "worktree": str(path),
        "head": head,
        "base_sha": base_sha,
        "reminder": "gitignored files (.env, credentials, local settings) are NOT in a fresh worktree",
    }


CLOSING_KEYWORDS_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      closedByPullRequestsReferences(first: 5) {
        nodes { number state }
      }
    }
  }
}
"""


CLOSING_KEYWORD_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b\s*:?\s*"
    r"(?:#|https://github\.com/[\w.-]+/[\w.-]+/issues/)(?P<number>\d+)",
    re.IGNORECASE,
)


def closing_refs(issue: int, cwd: Path) -> list[dict]:
    owner, name = repo_identity(cwd)
    data = gh_json(
        ["api", "graphql", "-f", f"query={CLOSING_KEYWORDS_QUERY}",
         "-f", f"owner={owner}", "-f", f"name={name}", "-F", f"number={issue}"],
        cwd=cwd,
    )
    node = (((data or {}).get("data") or {}).get("repository") or {}).get("issue") or {}
    return (node.get("closedByPullRequestsReferences") or {}).get("nodes") or []


def keyword_sources(issue: int, prs: list[dict], base: str | None, branch: str | None,
                    cwd: Path) -> list[dict]:
    """Find where an actual closing keyword is written, if anywhere.

    A non-empty `closedByPullRequestsReferences` proves the issue WILL auto-close, but not WHY, and
    the remedy differs completely by cause. Text you wrote you can edit; a link GitHub derived from
    the branch you cannot. Reporting both as "fix the PR body" sends a run to edit prose that never
    contained the keyword — observed live on issue #118, whose body opened with
    `Refs #118 — deliberately NOT a closing keyword` while the reference was live anyway.
    """
    hits = []
    for pr in prs:
        body = gh_json(["pr", "view", str(pr["number"]), "--json", "body"], cwd=cwd) or {}
        for match in CLOSING_KEYWORD_RE.finditer(body.get("body") or ""):
            if int(match.group("number")) == issue:
                hits.append({"where": f"pr#{pr['number']} body", "text": match.group(0)})
    if base and branch:
        log = run(["git", "log", f"origin/{base}..{branch}", "--format=%B"], cwd=cwd, check=False)
        for match in CLOSING_KEYWORD_RE.finditer(log.stdout or ""):
            if int(match.group("number")) == issue:
                hits.append({"where": "commit message", "text": match.group(0)})
    return hits


def assess_autoclose(issue: int, cwd: Path, base: str | None = None,
                     branch: str | None = None) -> dict:
    """Will this issue auto-close on merge, and if so, from what?

    Two distinct causes, one symptom:

      * a **closing keyword** in the PR body or a commit message — avoidable, and a hard stop,
        because the text is yours to fix;
      * a **branch link** created by `gh issue develop` — GitHub converts the Development-sidebar
        link into a closing reference the moment a PR opens from that branch, and empties
        `linkedBranches` in the same move. No keyword is involved and no edit removes it.

    The second is not a defect in the run: it is what the recommended linking command does. Making
    it a hard stop would make the recommended path permanently un-shippable, and a gate that always
    fires is a gate that gets ignored. So it is reported loudly with the follow-up it mandates —
    the auto-close is NOT the workflow's `close`, so `transition --to done` must still run after
    the merge or the label and the board freeze wherever they were.
    """
    refs = closing_refs(issue, cwd)
    if not refs:
        return {"will_autoclose": False, "cause": None, "linked_prs": []}
    hits = keyword_sources(issue, refs, base, branch, cwd)
    return {
        "will_autoclose": True,
        "cause": "closing-keyword" if hits else "branch-link",
        "keyword_sources": hits,
        "linked_prs": refs,
    }


def cmd_check_closing_keywords(args, config, cwd) -> dict:
    """Auto-close bypasses `transition`: no state move, no mirror, labels frozen wherever they were.
    Reading your own prose is how a keyword slips through — so check it mechanically."""
    verdict = assess_autoclose(args.issue, cwd, args.base, args.branch)

    if verdict["cause"] == "closing-keyword":
        raise Stop(
            {
                "ok": False,
                "reason": "closing-keyword-live",
                **verdict,
                "action": "remove the keyword — use `Refs #<n>` — then re-run; the value can lag a few seconds",
            }
        )

    result = {"ok": True, "issue": args.issue, **verdict}
    if verdict["cause"] == "branch-link":
        result["warning"] = (
            "this issue WILL auto-close on merge because `gh issue develop` linked the branch, "
            "not because of any keyword — no edit removes it"
        )
        result["mandatory_follow_up"] = (
            "after merge, run `transition --to done` anyway: GitHub's auto-close is not the "
            "workflow's close, so without it the label and the board freeze where they are"
        )
    return result


def cmd_publish_review(args, config, cwd) -> dict:
    """Push, then reuse-or-create the PR. A resumed issue reuses its existing open PR; creating a
    duplicate is not recovery. Does NOT transition — call `transition --to review` after this, so
    `review` points at a head the reviewer and CI can actually judge."""
    do_verify_claim(args.issue, args.run_id, args.expect_state, cwd)

    worktree = Path(args.worktree) if args.worktree else cwd
    run(["git", "push", "-u", "origin", "--", args.branch], cwd=worktree, writes=True)

    existing = gh_json(
        ["pr", "list", "--head", args.branch, "--state", "open",
         "--json", "number,url,headRefOid,baseRefOid"],
        cwd=cwd,
    ) or []
    if len(existing) > 1:
        raise Stop(
            {
                "ok": False,
                "reason": "ambiguous-open-prs",
                "prs": existing,
                "action": "more than one open PR for this branch — resolve by hand",
            }
        )

    if existing:
        pr = existing[0]
        created = False
    else:
        if not args.pr_body_file:
            raise Stop({"ok": False, "reason": "missing-pr-body",
                        "action": "pass --pr-body-file for a new PR"})
        body = Path(args.pr_body_file).read_text(encoding="utf-8")
        # The safe form, written rather than improvised per PR: knowing the rule is demonstrably
        # not enough — `Fixes #<n>` is the muscle-memory opening of a PR body.
        if f"#{args.issue}" not in body:
            body = f"Refs #{args.issue} — a plain reference, deliberately NOT a closing keyword.\n\n" + body
        with body_file(body) as path:
            run(["gh", "pr", "create", "--base", args.base, "--head", args.branch,
                 "--title", args.pr_title, "--body-file", path], cwd=cwd, writes=True)
        fresh = gh_json(
            ["pr", "list", "--head", args.branch, "--state", "open",
             "--json", "number,url,headRefOid,baseRefOid"],
            cwd=cwd,
        ) or []
        if not fresh:
            raise ReadFailure("the PR was created but is not visible yet")
        pr = fresh[0]
        created = True

    verdict = assess_autoclose(args.issue, cwd, args.base, args.branch)
    if verdict["cause"] == "closing-keyword":
        raise Stop(
            {
                "ok": False,
                "reason": "closing-keyword-live",
                "pr": pr,
                **verdict,
                "action": "the issue WILL auto-close on merge, bypassing transition and the mirror — "
                          "remove the keyword (use `Refs #<n>`) and re-run",
            }
        )

    with body_file(
        f"Published for review: {pr['url']}\n\n"
        f"- head `{pr['headRefOid']}`\n- base `{pr['baseRefOid']}`\n\n"
        f"Review and CI are bound to these exact SHAs; any push invalidates both.\n\n"
        f"{marker('published', run_id=args.run_id, pr=str(pr['number']), head=pr['headRefOid'])}\n"
    ) as note:
        run(["gh", "issue", "comment", str(args.issue), "--body-file", note], cwd=cwd, writes=True)

    result = {
        "ok": True,
        "issue": args.issue,
        "pr": pr["number"],
        "url": pr["url"],
        "created": created,
        "head": pr["headRefOid"],
        "base": pr["baseRefOid"],
        "autoclose": verdict,
        "next": "transition --to review, then bind review and CI to these SHAs",
    }
    if verdict["cause"] == "branch-link":
        result["mandatory_follow_up"] = (
            "GitHub will auto-close this issue on merge because the branch was linked, not because "
            "of a keyword. Run `transition --to done` after the merge regardless — the auto-close "
            "is not the workflow's close and moves neither the label nor the board"
        )
    return result


def cmd_unassign(args, config, cwd) -> dict:
    """Release work. `dev:<runtime>` means HOLDING, so it comes off the moment you stop.

    The `--held-by-other` form is the exception: on a lost race or a stand-down, `@me` is the
    shared account and removing it would strip the run that is actually holding the item.
    """
    edit = ["gh", "issue", "edit", str(args.issue), "--remove-label", f"dev:{args.runtime}"]
    if not args.held_by_other:
        edit.extend(["--remove-assignee", "@me"])
    run(edit, cwd=cwd, writes=True)
    if args.run_id:
        with body_file(
            f"`{args.run_id}` releasing this item.\n\n{marker('standdown', run_id=args.run_id)}\n"
        ) as note:
            run(["gh", "issue", "comment", str(args.issue), "--body-file", note],
                cwd=cwd, writes=True)
    return {
        "ok": True,
        "issue": args.issue,
        "assignee_kept": bool(args.held_by_other),
    }


def cmd_audit_board(args, config, cwd) -> dict:
    """Compare every card's column against its own `status:*` label.

    The mirror only fires on a transition you make; it does not repair drift a previous run left
    behind. This is one pass over data a single query already returns, which is why the binding
    says a run that finds its own drift should check for the others while the response is in hand.
    """
    board = Board(cfg(config, "project board"), cwd, use_cache=not args.no_cache)
    if not board.enabled:
        return {"ok": True, "audited": False, "skipped": "no board configured"}
    meta = board.meta()
    if not meta:
        return {"ok": True, "audited": False, "skipped": board.skip_reason}

    cards = board.all_cards()

    # An empty read and a clean board produce the same `drift: []`, and reporting that as a pass
    # would be the exact failure this whole change removes: a step that silently did nothing while
    # looking like it succeeded. A board that is configured and reachable always has cards, so
    # zero means the read did not answer — treat it as a failed read, not a clean result.
    if not cards:
        # An empty read and a clean board produce the same `drift: []`, and reporting that as a
        # pass would reproduce the exact failure this file exists to remove: a step that silently
        # did nothing while looking like it succeeded.
        #
        # But it is not a failed READ either — a brand-new project genuinely has no cards, and
        # telling the caller to retry forever would be its own wrong answer. The honest verdict is
        # INCONCLUSIVE: the audit ran and could not conclude anything.
        raise Stop(
            {
                "ok": False,
                "reason": "board-empty-inconclusive",
                "detail": "the board returned zero cards",
                "action": "this is NOT a clean board. Either the project is genuinely empty (fine, "
                          "nothing to audit) or the query was misdirected — wrong owner type, "
                          "missing `project` scope, wrong board number. Confirm which before "
                          "treating the board as verified",
            }
        )

    drift, missing_column = [], []
    for card in cards:
        labels = status_labels(card["labels"])
        if len(labels) != 1:
            drift.append({**card, "problem": f"expected one status label, found {labels}"})
            continue
        state = labels[0].split(":", 1)[1]
        expected = Board.option_for(meta, state)
        if card["column"] is None:
            missing_column.append({**card, "expected": expected["name"] if expected else state})
        elif expected and card["column"] != expected["name"]:
            drift.append({**card, "expected": expected["name"], "problem": "column disagrees with label"})

    repaired = []
    if args.fix:
        for card in drift + missing_column:
            labels = status_labels(card["labels"])
            if len(labels) == 1:
                result = board.set_status(card["issue"], labels[0].split(":", 1)[1])
                repaired.append({"issue": card["issue"], **result})

    return {
        "ok": True,
        "audited": True,
        "cards": len(cards),
        "drift": drift,
        "missing_column": missing_column,
        "repaired": repaired,
    }


# ---------------------------------------------------------------------------
# entrypoint
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="issue-flow-github",
        description="Mechanical, reversible transport operations for the issue-flow GitHub binding.",
    )
    parser.add_argument("--repo-dir", default=".", help="repository checkout to run gh/git in")
    parser.add_argument("--no-cache", action="store_true", help="re-resolve board ids instead of reusing the cache")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("config", help="show the resolved operator configuration")
    sub.add_parser("ensure-states", help="create the six status labels (idempotent)")

    p = sub.add_parser("create", help="file an issue with every marker the finding supplies")
    p.add_argument("--identity", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--body-file", required=True)
    p.add_argument("--state", default="ready", choices=["ready", "blocked"])
    p.add_argument("--priority", help="the full label, e.g. priority:high or tier:2")
    p.add_argument("--domain")
    p.add_argument("--runtime")
    p.add_argument("--run-id")

    p = sub.add_parser("list-state", help="unclaimed items in a state, partitioned by domain")
    p.add_argument("--state", required=True, choices=STATES)
    p.add_argument("--limit", type=int, default=200)

    p = sub.add_parser("claim", help="assign, comment and adjudicate by the comment timeline")
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--runtime", required=True)
    p.add_argument("--horizon", required=True, help="when you expect to report, e.g. 2026-07-25T23:00Z")

    p = sub.add_parser("verify-claim", help="the renewal: one read, three checks")
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--expect-state", required=True, choices=STATES)
    p.add_argument("--allow-closed-by-pr", type=int,
                   help="the renewal before `close`, after your own merge auto-closed the issue: "
                        "a closed issue passes ONLY when this PR is what closed it")

    p = sub.add_parser("reclaim", help="take over work whose holder never came back")
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--runtime", required=True)
    p.add_argument("--force", action="store_true",
                   help="reclaim a holder whose horizon has NOT passed — needs a defensible reason")

    p = sub.add_parser("transition", help="mirror the board, swap the label, read BOTH back")
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--to", required=True, choices=STATES)
    p.add_argument("--from", dest="from_state", choices=STATES)

    p = sub.add_parser("comment", help="post a markdown body from a file")
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--body-file", required=True)
    p.add_argument("--run-id")
    p.add_argument("--kind", help="marker kind, e.g. note, blocker, diagnosis")

    p = sub.add_parser("heartbeat", help="verify the claim, then post progress — never the reverse")
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--expect-state", required=True, choices=STATES)
    p.add_argument("--body-file", required=True)

    p = sub.add_parser("start-branch", help="linked branch + isolated per-run worktree")
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--branch", required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--expect-state", default="in-progress", choices=STATES)
    p.add_argument("--worktree-root", help="override the configured template")

    p = sub.add_parser("publish-review", help="push, reuse-or-create the PR, record it on the issue")
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--branch", required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--expect-state", default="in-progress", choices=STATES)
    p.add_argument("--pr-title")
    p.add_argument("--pr-body-file")
    p.add_argument("--worktree", help="worktree to push from (defaults to --repo-dir)")

    p = sub.add_parser("unassign", help="release the item without changing its state")
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--runtime", required=True)
    p.add_argument("--run-id")
    p.add_argument("--held-by-other", action="store_true",
                   help="lost race or stand-down: keep the shared assignee, drop only your label")

    p = sub.add_parser("check-closing-keywords",
                       help="will this issue auto-close on merge, and from what cause")
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--base", help="base ref, so commit messages can be scanned for keywords too")
    p.add_argument("--branch", help="branch ref, paired with --base")

    p = sub.add_parser("audit-board", help="compare every card's column against its own status label")
    p.add_argument("--fix", action="store_true", help="repair the drift this pass finds")

    return parser


COMMANDS = {
    "config": cmd_config,
    "ensure-states": cmd_ensure_states,
    "create": cmd_create,
    "list-state": cmd_list_state,
    "claim": cmd_claim,
    "reclaim": cmd_reclaim,
    "verify-claim": cmd_verify_claim,
    "transition": cmd_transition,
    "comment": cmd_comment,
    "heartbeat": cmd_heartbeat,
    "start-branch": cmd_start_branch,
    "publish-review": cmd_publish_review,
    "unassign": cmd_unassign,
    "check-closing-keywords": cmd_check_closing_keywords,
    "audit-board": cmd_audit_board,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not shutil.which("gh"):
        print(json.dumps({
            "ok": False,
            "reason": "gh-not-found",
            "action": "install the GitHub CLI (winget install GitHub.cli / brew install gh / apt install gh), "
                      "or use the REST API route described in bindings/github.md",
        }))
        return 2

    cwd = Path(args.repo_dir).resolve()
    if not cwd.is_dir():
        print(json.dumps({"ok": False, "reason": "bad-repo-dir", "path": str(cwd)}))
        return 2

    try:
        result = COMMANDS[args.command](args, load_config(), cwd)
    except Stop as stop:
        print(json.dumps(stop.payload, indent=2))
        return 1
    except ReadFailure as failure:
        # The control surface answered NOTHING. "Nothing" is not a stand-down and not clearance.
        print(json.dumps({
            "ok": False,
            "reason": "read-failed",
            "detail": str(failure),
            "action": "fail closed: write nothing, retry the read. Do not treat this as a stop or a pass.",
        }, indent=2))
        return 3
    except WriteFailure as failure:
        print(json.dumps({
            "ok": False,
            "reason": "write-failed",
            "detail": str(failure),
            "action": "the write may have landed before it failed — RE-READ to establish what "
                      "actually happened, then decide. Do not retry blindly.",
        }, indent=2))
        return 5
    except Exception as exc:  # noqa: BLE001 — deliberate catch-all, see below
        # Without this, an unexpected exception exits 1 with a traceback and no JSON — which the
        # contract defines as "a check said STOP, follow the JSON's action". A caller obeying that
        # contract would find no JSON to follow and could not tell an internal bug from a genuine
        # stand-down. A distinct code, and an explicit statement that the state is UNKNOWN, is the
        # only honest answer: the failure may have occurred after a write landed.
        print(json.dumps({
            "ok": False,
            "reason": "internal-error",
            "detail": f"{type(exc).__name__}: {exc}",
            "action": "state is UNKNOWN — re-read the issue before retrying; this is a defect in "
                      "the script, not an answer from the tracker",
        }, indent=2))
        return 4

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
