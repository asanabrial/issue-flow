# issue-flow

**issue-flow is an agent skill that turns a shared issue tracker — GitHub Issues, Linear or Trello —
into a coordination board for AI coding agents.** It splits work into two roles: an **analyst** that
investigates and files evidence-backed issues, and a **dev** that claims a ready issue, implements it
and ships it. It runs on Claude Code, Codex, and any agent runtime that reads skills, and several
agents can work the same board concurrently — including from different machines and different
runtimes — because the tracker itself is the only shared state.

The split exists because the two halves have opposite costs. Analysis is cheap, parallel and safe —
several analysts can run at once and none of them can break a build, because they write no code. It
also lets a sandboxed runtime that cannot touch the filesystem still contribute, since an analyst's
only output is a network call. Implementation is expensive, serial and risky, and gets the branch,
the worktree, the tests and the review.

The workflow is deliberately **domain-agnostic**: it owns how work moves (states, claiming,
delivery), while a pluggable *domain rule book* owns what is worth doing and what "done" means. The
same workflow runs an engine-tuning backlog and a documentation cleanup without either knowing about
the other.

## How it works

Every task is an issue moving through six states, with exactly one state at a time:

```
analysis ──> ready ──> in-progress ──> review ──> done
                │            │            │
                └── blocked ─┴────────────┘
```

- **analysis** — being investigated, or returned by a dev as under-specified. Drained by analysts.
- **ready** — specified, unassigned, implementable. Drained by devs, highest priority first.
- **in-progress** — claimed and being built. Guarded by a stale-claim rule: a run that dies holding
  an issue is detected by its silence and the work is reclaimed, never stuck forever.
- **review** — built and published; awaiting or undergoing independent review, CI and delivery.
  Unassigned items here outrank the whole `ready` queue: finishing beats starting.
- **blocked** — waiting on something external, with the blocker *and who can discharge it* named.
- **done** — merged and verified, stated with evidence, never just the word "done".

**Claiming is race-safe without a lock server.** Two agents that claim the same issue in the same
second are adjudicated by the comment timeline — the earliest server-timestamped claim comment wins —
because an assignee field cannot adjudicate a race between agents that authenticate as the same
account. Every run signs its work with a per-run identity (`claude-code-60fabae1`), so the board
records which run did what even when every agent shares one login.

Delivery aims for pull requests of at most 800 changed lines, but that is reviewability guidance, not
a gate. A larger coherent change is preferable when splitting it would make intermediate states less
safe or make the complete result harder to review; all normal review and verification still apply.
Repository-specific limits win; the fallback contract lives in [repository delivery](references/repository-delivery.md#keep-review-units-coherent).

## Supported trackers

The workflow is written as fourteen abstract operations (`claim`, `transition`, `comment`, …); each
tracker's binding says how its API performs them — and, just as important, what that tracker does
NOT provide, so no rule silently stops applying.

| | GitHub Issues | Linear | Trello |
|---|---|---|---|
| State model | `status:*` labels (discipline) | native workflow states (enforced) | lists — a card is in exactly one |
| Claim verification | comment timeline | state check + comment timeline | comment trail (`commentCard`) |
| Reclaim / heartbeat | executable | unsupported / fail-closed | unsupported / fail-closed |
| Stable identity | issue number | `ENG-123` identifiers | `shortLink` / board-key prefix |
| Transport | `gh` CLI or REST API | official MCP server or GraphQL | REST API |

## How it compares

| | issue-flow | [troykelly/claude-skills](https://github.com/troykelly/claude-skills) | [Backlog.md](https://github.com/MrLesk/Backlog.md) | Claude Code agent teams |
|---|---|---|---|---|
| What it is | one workflow skill | 50+ skill framework | markdown kanban CLI | runtime feature |
| Work lives in | GitHub / Linear / Trello | GitHub | files in your repo | in-session task list |
| Survives the session | yes — the tracker is the state | yes | yes (via git) | no — teammates are ephemeral |
| Methodology | none imposed; pluggable domain rule books | TDD, style guides and typing mandated | none | none |
| Cross-runtime | Claude Code, Codex, any skill reader | Claude Code | several CLIs | Claude Code |
| Sandboxed analyst | yes — analysis is network-only | — | no (needs file writes) | — |

These solve overlapping but different problems: agent teams parallelise *inside* one session,
Backlog.md keeps tasks *inside* one repository, and troykelly's framework bundles a full opinionated
methodology. issue-flow is the thin layer for **durable, cross-runtime coordination over a tracker
you already use** — and it composes with agent teams rather than competing (spawn analysts as
teammates; only what lands on the issue survives the session, which is exactly the discipline the
workflow already demands).

## Install

The installer needs Git 2.36 or newer and Python 3.10 or newer, the same Python runtime used by the GitHub binding. One line
acquires the current commit into quarantine, materializes its complete Git tree as a new immutable
bundle, and points `~/.agents/skills/issue-flow` at that bundle:

```sh
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/asanabrial/issue-flow/main/install.sh | sh
```

```powershell
# Windows
irm https://raw.githubusercontent.com/asanabrial/issue-flow/main/install.ps1 | iex
```

**The piped script is only a bootstrap.** It runs Python in isolated mode, disables inherited Git
configuration and hooks, acquires canonical `main` into a temporary bare repository, extracts only
the commit's shared installer, and hands over to it. Standard proxy and CA variables remain available
to Git; Git-specific authority variables do not. The
installer reads blobs directly from the fetched Git objects, rejects unsafe paths and broken local
Markdown links, verifies every materialized byte against the commit tree, and activates only after
the complete bundle is durable. Prefer to inspect the files first? The legacy clone layout is a
supported one-time migration source:

```sh
git clone https://github.com/asanabrial/issue-flow ~/.agents/skills/issue-flow
~/.agents/skills/issue-flow/install.sh install
```

The stable public path remains `~/.agents/skills/issue-flow/`. Its bundles, Git object store, local
policy generations, activation receipt, completed-activation refs and retained rollback targets live under
`~/.agents/skills/.issue-flow/`.
POSIX atomically replaces the public symlink; Windows atomically retargets its directory junction,
which needs no elevation. Runtime paths under `~/.claude/skills/` and `~/.codex/skills/` point at the
stable public path. Independent copies are refused because they would remain on stale policy.

The store retains every activated immutable bundle so an already-running load can keep using the immutable path
it resolved at activation across any number of later upgrades. Materialization and activation have
separate Git refs, and the installer requires Git reference fsync before removing a recovery journal:
`rollback` accepts the recorded previous generation only when a completed
post-switch activation marker exists. `status` verifies tracked bytes and local attachments for every
retained bundle, reports corrupt generations, and measures all deduplicated installer-state storage
for deliberate operator cleanup. A new load sees one complete generation and must not reopen
companions through the stable alias. The byte-exact v1.11 clone is not an immutable rollback target:
its old installer performs in-place writes that are unsafe against the new store. A successful first
migration therefore has no `rollback` target until the next immutable upgrade. If migration is
interrupted before activation, recovery validates and restores the original standalone clone instead.
Run the one-time directory move between agent sessions because it has a brief availability gap.

Run `status` to verify the active commit, tree, runtime targets and pending recovery state; `rollback`
fetches canonical `main`, proves the retained activated predecessor is still in its history, then
reactivates it. `recover` completes an interrupted transaction only
when the pointer is one of that journal's declared endpoints. Unexplained pointer/state drift fails
closed instead of being normalized. `uninstall`
removes only installer-owned runtime links; it never removes bundles, policy or rollback state.

## Use

```
/issue-flow analyst <domain-rules>     # domain REQUIRED for project-wide analysis
/issue-flow dev     [issue-number]     # domain optional
```

Codex uses `$` instead of `/`.

**The analyst needs a domain and stops without one.** The skill knows how work moves; it has no
opinion on what your project considers worth doing. That is what a domain rule book supplies, and
there is a worked example in `examples/domain-test-coverage.md` you can use as-is or copy and
repoint at your own subject. Pointed at a *bounded* target instead — a diff, a pull request — the
analyst needs no domain, for the same reason the dev needs none: the target is the scope.

**The dev usually needs nothing.** `/issue-flow dev` on its own is a complete invocation: the issue
already carries its scope and acceptance criteria. Add a domain only if your project has extra
requirements for what counts as done — a measurement discipline, mandatory benchmarks, ship gates.

## FAQ

**Which AI coding agents does it work with?**
Any runtime that loads `SKILL.md`-style agent skills — Claude Code and Codex are the tested ones.
Different runtimes can share one board: attribution labels record which runtime holds what.

**What happens when two agents claim the same issue at the same time?**
Both write a claim comment as part of claiming, then read the timeline: the earliest
server-timestamped claim wins and the loser backs off with a comment. This works even when every
agent authenticates as the same tracker account, where assignee fields cannot show a collision. The
timeline stays the control channel for the whole build: every heartbeat re-reads it before writing,
so a stand-down issued later is actually received.

**What happens when an agent dies mid-task?**
Claims carry a self-declared report-by horizon and heartbeat comments. An `in-progress` issue whose
last activity is past its horizon is reclaimable: the next dev takes over on the record, keeping
whatever the dead run already pushed or diagnosed.

**Can an agent without filesystem access participate?**
Yes — as an analyst. Its only output is an issue on the tracker, which is a network call. That is a
design goal, not an accident: sandboxed runtimes are first-class analysts.

**Do I need GitHub?**
No. Bindings exist for GitHub Issues, Linear (official MCP server or GraphQL) and Trello (REST).
The workflow itself never names a tracker; you pick one in the configuration block.

**How do I write a domain rule book?**
Copy `examples/domain-test-coverage.md` and replace what it considers worth doing. A domain names
its priorities, its evidence requirements and its identity scheme — and never names a tracker,
which is what keeps it portable.

**How is my configuration kept across upgrades?**
Portable defaults live between two markers inside `SKILL.md`. Operator values live in stable local
state and are hard-linked into the active bundle as the ignored `operator.local.md`, so updating or
publishing the skill cannot disclose permissions, machine paths or tracker identifiers.

## Layout

```
SKILL.md                          the workflow: roles, states, claiming, delivery
bindings/github.md                how each operation is performed, per tracker
bindings/linear.md
bindings/trello.md
scripts/github.py                 the GitHub binding's reversible operations, executable
examples/domain-test-coverage.md  a worked domain rule book
install.sh / install.ps1          thin self-acquiring bootstrap adapters
scripts/install_bundle.py         shared immutable-bundle transaction implementation
```

**Why an operation is a script and not a paragraph.** The failures this workflow keeps recording are
not wrong decisions — they are steps that were never executed. A run moved labels correctly through
the whole state machine and mirrored the project board zero times in an entire session, with the
permission it needed present the whole way. Five issues were claimed, commented, and never relabeled.
Every instruction involved was present and correct. So the operations that are **mechanical and
verifiable** — state written to two surfaces and read back, a claim race adjudicated by timestamp, a
per-run worktree path, PR reuse, the closing-keyword scan — execute as code, and the ones that need
**judgement** stay in prose. Irreversible remote writes (merge, version tags, close) are deliberately
left to the agent: a defect in a script must not be able to merge, tag or close anything.

The script needs Python 3.10 or newer and `gh`. Its exit codes separate "a check said stop" from "the read
failed" — treating a timeout as a stand-down halts every run, treating it as clearance lets a run
write deaf, and it never collapses the two.

## Configuration

Settings appear as the ignored `operator.local.md` beside `SKILL.md` and persist as immutable local
generations under `~/.agents/skills/.issue-flow/` across bundle switches. They include tracker, delivery route, merge
strategy, worktree location, and whether delivery is pre-authorised. The `config` command creates
the file from the marked defaults in `SKILL.md` when needed. A pull-request route publishes the branch
for independent review, waits for required CI on the latest head, and then merges using the selected
strategy. When that delivery changes an app or project version, issue-flow creates an annotated,
immutable tag on the delivered commit and pushes it to the remote before closing the work. GitHub
Releases remain a separate publication layer and follow the repository's existing convention. The
configuration is a table with the defaults written next to each value, so it reads on its own.

Read or update it through the installer. The visible file is managed state; editors that replace it
would sever the generation link and are deliberately rejected on the next verification:

```sh
./install.sh config                                              # print the table
./install.sh config --set "Worktree location=/wt/<repo>/<branch>"
```

The installer matches a setting **by its name** and carries no setting list of its own, so a default
row added to the skill is settable immediately. It verifies every destination, publishes the new
content-addressed generation through a fsynced temporary file, then atomically changes each visible
hard link. Recovery removes abandoned link temporaries before finishing the journal. It refuses a
name that matches no row or more than one, and refuses a value containing `|`, which would split the
cell and corrupt the table.

`sync` fetches canonical `main`, validates the complete target tree, and atomically activates its
bundle while leaving `operator.local.md` byte-identical:

```sh
./install.sh sync
```

Single-file `--from`/`-From` sync fails before mutation because it cannot prove that required
references and assets come from the same contract. Remote acquisition uses a one-shot bare clone
before any destination-local config exists, disables executable hooks and protocols outside the
configured transport, and atomically copies verified objects into real store directories. A failed
fetch, validation or materialization leaves the active pointer unchanged. After two immutable
generations exist, the previous complete bundle remains available through `rollback`; `recover`
restores the standalone clone or reconciles the bundle journal when an operation was interrupted.
Never force-add `operator.local.md`: its values are permissions, including whether an agent may
publish or merge without asking.

## Status

The workflow, the state machine and the GitHub binding are the mature parts, exercised against a
live board. **The Linear and Trello bindings are written against their official API documentation
but have not yet been exercised against a live workspace** — expect the first real run to find
something. The installer acceptance suite runs the same bootstrap, migration, update, drift,
authority, crash-recovery, rollback and policy-preservation cases through PowerShell 7, Windows
PowerShell 5.1 and Git Bash. The POSIX adapter
has not yet run on a native Linux or macOS shell.

The Python and Git executables selected from the operator's `PATH` are trust roots, as is canonical
GitHub `main`. Local receipts, refs and journals detect partial drift and interrupted writes; they are
not signatures against a fully compromised account that can coherently rewrite every object, bundle,
pointer and state file. That stronger threat requires OS account isolation or signed upstream
artifacts rather than another unsigned local marker.

Licensed GPL-2.0. Issues and corrections welcome, preferably filed through the workflow itself.
