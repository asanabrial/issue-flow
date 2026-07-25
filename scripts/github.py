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
  3  the read itself failed (network, auth, rate limit). The control surface answered NOTHING.
     Fail closed: write nothing, retry the read. Never treat this as clearance or as a stand-down.
"""

from __future__ import annotations

import argparse
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


class ReadFailure(Exception):
    """A read against the control surface did not answer. Never a stand-down."""


class Stop(Exception):
    """A successful read answered "stop". Carries the payload the agent must act on."""

    def __init__(self, payload: dict):
        super().__init__(payload.get("reason", "stop"))
        self.payload = payload


# ---------------------------------------------------------------------------
# process plumbing
# ---------------------------------------------------------------------------

def run(args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command with an argument LIST and no shell.

    No shell means no PowerShell backtick expansion, no word splitting, no quoting rules — the
    corruption documented in bindings/github.md cannot occur through this path.
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
        raise ReadFailure(f"{' '.join(args[:3])} failed ({proc.returncode}): {proc.stderr.strip()}")
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


def with_body_file(body: str):
    """Write a markdown body to a temp file.

    Every operation that accepts markdown goes through a file, with no exception for "this one's
    short" — that inconsistency is what let evidence-bearing comments arrive silently damaged.
    """
    fd, path = tempfile.mkstemp(suffix=".md", prefix="issue-flow-", text=True)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(body)
    return path


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
CLAIM_PROSE = re.compile(r"\bclaimed by\s+(?P<run>[\w.-]+)", re.IGNORECASE)


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
            match = CLAIM_PROSE.search(body)
            if match:
                run_id = match.group("run")
        if run_id:
            claims.append((comment.get("createdAt", ""), run_id, comment))
    claims.sort(key=lambda item: item[0])
    return claims


def released_run_ids(comments: list[dict]) -> set[str]:
    """Run-ids that already stood down, so their earlier claim no longer holds the item."""
    released = set()
    for comment in comments:
        for mark in parse_markers(comment.get("body", "")):
            if mark.get("kind") in ("standdown", "release", "unassign") and mark.get("run-id"):
                released.add(mark["run-id"])
    return released


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
        return Path(tempfile.gettempdir()) / f"issue-flow-board-{self.owner}-{self.number}.json"

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

    def item_id(self, issue: int) -> str | None:
        cursor = None
        while True:
            variables = {"login": self.owner, "number": self.number}
            if cursor:
                variables["cursor"] = cursor
            project, _ = self._query_owner_scoped(BOARD_ITEMS_QUERY, **variables)
            if not project:
                self.skip_reason = "project not visible while resolving the item"
                return None
            items = project["items"]
            for node in items["nodes"]:
                content = node.get("content") or {}
                if content.get("number") == issue:
                    return node["id"]
            if not items["pageInfo"]["hasNextPage"]:
                return None
            cursor = items["pageInfo"]["endCursor"]

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
        cursor = None
        while True:
            variables = {"login": self.owner, "number": self.number}
            if cursor:
                variables["cursor"] = cursor
            project, _ = self._query_owner_scoped(BOARD_ITEMS_QUERY, **variables)
            if not project:
                return None
            items = project["items"]
            for node in items["nodes"]:
                content = node.get("content") or {}
                if content.get("number") == issue:
                    value = node.get("fieldValueByName") or {}
                    return value.get("name")
            if not items["pageInfo"]["hasNextPage"]:
                return None
            cursor = items["pageInfo"]["endCursor"]

    def all_cards(self) -> list[dict]:
        cards, cursor = [], None
        while True:
            variables = {"login": self.owner, "number": self.number}
            if cursor:
                variables["cursor"] = cursor
            project, _ = self._query_owner_scoped(BOARD_ITEMS_QUERY, **variables)
            if not project:
                return cards
            items = project["items"]
            for node in items["nodes"]:
                content = node.get("content") or {}
                if not content.get("number"):
                    continue
                value = node.get("fieldValueByName") or {}
                cards.append(
                    {
                        "issue": content["number"],
                        "column": value.get("name"),
                        "labels": [n["name"] for n in content.get("labels", {}).get("nodes", [])],
                    }
                )
            if not items["pageInfo"]["hasNextPage"]:
                return cards
            cursor = items["pageInfo"]["endCursor"]


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

def do_verify_claim(issue: int, run_id: str, expect_state: str, cwd: Path) -> dict:
    """One read, three checks. A failed CHECK is a stop instruction; a failed READ is nothing.

    That distinction is the whole point: SKILL.md records a run that lost a claim race by five
    seconds, was told so 33 seconds later, and then worked another ~48 minutes because nothing in
    its heartbeat loop ever read the timeline again.
    """
    data = issue_view(issue, "state,labels,comments", cwd=cwd)

    if data.get("state") != "OPEN":
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
            "ensure-states", "create", "list-state", "claim", "verify-claim", "transition",
            "comment", "heartbeat", "start-branch", "publish-review", "unassign",
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
    body_file = with_body_file(body)

    create_args = ["issue", "create", "--title", f"{args.identity}: {args.title}",
                   "--body-file", body_file]
    for label in labels:
        create_args.extend(["--label", label])
    proc = run(["gh", *create_args], cwd=cwd)
    url = proc.stdout.strip().splitlines()[-1]
    number = int(url.rstrip("/").rsplit("/", 1)[-1])
    os.unlink(body_file)

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

    # Partition by domain. Ordering INSIDE a partition needs the domain's own scale contract,
    # which this script does not know and must not invent: SKILL.md forbids manufacturing a
    # global priority rank across scales. So partitions and their raw labels are returned, and
    # the agent applies the contract it loaded.
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
    run(["gh", "issue", "edit", str(args.issue), "--add-assignee", "@me"], cwd=cwd)

    body = (
        f"Claimed by {args.run_id}, expect to report by {args.horizon}.\n\n"
        f"{marker('claim', run_id=args.run_id, horizon=args.horizon)}\n"
    )
    body_file = with_body_file(body)
    run(["gh", "issue", "comment", str(args.issue), "--body-file", body_file], cwd=cwd)
    os.unlink(body_file)

    data = issue_view(args.issue, "comments", cwd=cwd)
    claims = claim_comments(data.get("comments", []))
    released = released_run_ids(data.get("comments", []))
    live = [(at, rid) for at, rid, _ in claims if rid not in released]

    if not live:
        raise ReadFailure("the claim comment was written but is not visible on the timeline yet")

    winner_at, winner = live[0]
    if winner != args.run_id:
        # Losing is cheap and takes seconds; two runs building the same issue is not.
        standdown = with_body_file(
            f"{args.run_id} standing down: {winner} claimed at {winner_at}, earlier than this run. "
            f"Nothing was created.\n\n{marker('standdown', run_id=args.run_id)}\n"
        )
        run(["gh", "issue", "comment", str(args.issue), "--body-file", standdown], cwd=cwd)
        os.unlink(standdown)
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

    run(["gh", "issue", "edit", str(args.issue), "--add-label", f"dev:{args.runtime}"], cwd=cwd)
    return {
        "ok": True,
        "issue": args.issue,
        "run_id": args.run_id,
        "claimed_at": winner_at,
        "horizon": args.horizon,
        "next": "transition to in-progress before any repository write",
    }


def cmd_verify_claim(args, config, cwd) -> dict:
    return do_verify_claim(args.issue, args.run_id, args.expect_state, cwd)


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
            # Disagree means the mirror did not land. Set it again with the ids already resolved.
            retry = board.set_status(args.issue, args.to)
            recheck = board.read_status(args.issue)
            result["board_repair"] = {"retried": retry, "column_now": recheck}
            if expected and recheck != expected["name"]:
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
    body_file = with_body_file(body)
    run(["gh", "issue", "comment", str(args.issue), "--body-file", body_file], cwd=cwd)
    os.unlink(body_file)
    return {"ok": True, "issue": args.issue, "kind": args.kind or "note"}


def cmd_heartbeat(args, config, cwd) -> dict:
    """Read before you write. A heartbeat that only writes is deaf to the one channel that can
    revoke the claim — which is exactly how a stand-down sat unread for 48 minutes."""
    verdict = do_verify_claim(args.issue, args.run_id, args.expect_state, cwd)
    body = Path(args.body_file).read_text(encoding="utf-8")
    body = body.rstrip() + f"\n\n{marker('heartbeat', run_id=args.run_id)}\n"
    body_file = with_body_file(body)
    run(["gh", "issue", "comment", str(args.issue), "--body-file", body_file], cwd=cwd)
    os.unlink(body_file)
    return {"ok": True, "issue": args.issue, "renewed": verdict["checked"], "posted": True}


def worktree_path(template: str, repo: str, branch: str, run_id: str, issue: int) -> Path:
    """Resolve the configured worktree template.

    The branch is flattened because a branch name carries `/` and a nested `docs/` directory
    under the worktree root is a surprise nobody configured. Path uniqueness comes from the
    run-id: two runs that both want issue 58 must not compute the same directory. Git refuses a
    branch already checked out elsewhere, but NOTHING refuses a second process writing into a
    directory that already exists — which is how, on 2026-07-24, a losing run wrote its model,
    its migration and its tests into the winner's checkout mid-build.
    """
    flat = branch.replace("/", "-")
    resolved = (
        template.replace("<repo>", repo)
        .replace("<branch>", flat)
        .replace("<run-id>", run_id)
        .replace("<issue>", str(issue))
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
        run(["git", "fetch", "origin", args.base], cwd=cwd)
        run(["git", "branch", "--force", args.branch, f"origin/{args.base}"], cwd=cwd, check=False)

    run(["git", "fetch", "origin"], cwd=cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "worktree", "add", str(path), args.branch], cwd=cwd)

    head = run(["git", "rev-parse", "HEAD"], cwd=path).stdout.strip()
    base_sha = run(["git", "rev-parse", f"origin/{args.base}"], cwd=cwd).stdout.strip()

    body = with_body_file(
        f"Branch: `{args.branch}` (base `{args.base}` @ `{base_sha}`)\n"
        f"Worktree: `{path}`\n"
        f"Held by `{args.run_id}`.\n\n"
        f"{marker('branch', run_id=args.run_id, branch=args.branch, base=base_sha)}\n"
    )
    run(["gh", "issue", "comment", str(args.issue), "--body-file", body], cwd=cwd)
    os.unlink(body)

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


def closing_keyword_refs(issue: int, cwd: Path) -> list[dict]:
    owner, name = repo_identity(cwd)
    data = gh_json(
        ["api", "graphql", "-f", f"query={CLOSING_KEYWORDS_QUERY}",
         "-f", f"owner={owner}", "-f", f"name={name}", "-F", f"number={issue}"],
        cwd=cwd,
    )
    node = (((data or {}).get("data") or {}).get("repository") or {}).get("issue") or {}
    return (node.get("closedByPullRequestsReferences") or {}).get("nodes") or []


def cmd_check_closing_keywords(args, config, cwd) -> dict:
    """A closing keyword makes GitHub close the issue on merge: no transition, no mirror, labels
    frozen. Reading your own prose is how it slipped through — so check it mechanically."""
    refs = closing_keyword_refs(args.issue, cwd)
    if refs:
        raise Stop(
            {
                "ok": False,
                "reason": "closing-keyword-live",
                "linked_prs": refs,
                "action": "edit the PR body/commits to `Refs #<n>` and re-run; the value can lag a few seconds",
            }
        )
    return {"ok": True, "issue": args.issue, "closing_keyword_refs": []}


def cmd_publish_review(args, config, cwd) -> dict:
    """Push, then reuse-or-create the PR. A resumed issue reuses its existing open PR; creating a
    duplicate is not recovery. Does NOT transition — call `transition --to review` after this, so
    `review` points at a head the reviewer and CI can actually judge."""
    do_verify_claim(args.issue, args.run_id, args.expect_state, cwd)

    worktree = Path(args.worktree) if args.worktree else cwd
    run(["git", "push", "-u", "origin", args.branch], cwd=worktree)

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
        body_file = with_body_file(body)
        run(["gh", "pr", "create", "--base", args.base, "--head", args.branch,
             "--title", args.pr_title, "--body-file", body_file], cwd=cwd)
        os.unlink(body_file)
        fresh = gh_json(
            ["pr", "list", "--head", args.branch, "--state", "open",
             "--json", "number,url,headRefOid,baseRefOid"],
            cwd=cwd,
        ) or []
        if not fresh:
            raise ReadFailure("the PR was created but is not visible yet")
        pr = fresh[0]
        created = True

    refs = closing_keyword_refs(args.issue, cwd)
    if refs:
        raise Stop(
            {
                "ok": False,
                "reason": "closing-keyword-live",
                "pr": pr,
                "linked_prs": refs,
                "action": "the issue WILL auto-close on merge, bypassing transition and the mirror — "
                          "fix the PR body/commits to `Refs #<n>` and re-run",
            }
        )

    body = with_body_file(
        f"Published for review: {pr['url']}\n\n"
        f"- head `{pr['headRefOid']}`\n- base `{pr['baseRefOid']}`\n\n"
        f"Review and CI are bound to these exact SHAs; any push invalidates both.\n\n"
        f"{marker('published', run_id=args.run_id, pr=str(pr['number']), head=pr['headRefOid'])}\n"
    )
    run(["gh", "issue", "comment", str(args.issue), "--body-file", body], cwd=cwd)
    os.unlink(body)

    return {
        "ok": True,
        "issue": args.issue,
        "pr": pr["number"],
        "url": pr["url"],
        "created": created,
        "head": pr["headRefOid"],
        "base": pr["baseRefOid"],
        "next": "transition --to review, then bind review and CI to these SHAs",
    }


def cmd_unassign(args, config, cwd) -> dict:
    """Release work. `dev:<runtime>` means HOLDING, so it comes off the moment you stop.

    The `--held-by-other` form is the exception: on a lost race or a stand-down, `@me` is the
    shared account and removing it would strip the run that is actually holding the item.
    """
    edit = ["gh", "issue", "edit", str(args.issue), "--remove-label", f"dev:{args.runtime}"]
    if not args.held_by_other:
        edit.extend(["--remove-assignee", "@me"])
    run(edit, cwd=cwd)
    if args.run_id:
        body = with_body_file(
            f"`{args.run_id}` releasing this item.\n\n{marker('standdown', run_id=args.run_id)}\n"
        )
        run(["gh", "issue", "comment", str(args.issue), "--body-file", body], cwd=cwd)
        os.unlink(body)
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
        raise ReadFailure(
            "the board returned zero cards — a configured board is never empty, so this is a "
            "failed read (wrong owner type, missing `project` scope, or an API hiccup), not a pass"
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

    p = sub.add_parser("check-closing-keywords", help="prove no closing keyword will bypass the state machine")
    p.add_argument("--issue", type=int, required=True)

    p = sub.add_parser("audit-board", help="compare every card's column against its own status label")
    p.add_argument("--fix", action="store_true", help="repair the drift this pass finds")

    return parser


COMMANDS = {
    "config": cmd_config,
    "ensure-states": cmd_ensure_states,
    "create": cmd_create,
    "list-state": cmd_list_state,
    "claim": cmd_claim,
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

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
