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
- **review** — built, verified and reviewed; awaiting delivery. Unassigned items here outrank the
  whole `ready` queue: finishing beats starting.
- **blocked** — waiting on something external, with the blocker *and who can discharge it* named.
- **done** — merged and verified, stated with evidence, never just the word "done".

**Claiming is race-safe without a lock server.** Two agents that claim the same issue in the same
second are adjudicated by the comment timeline — the earliest server-timestamped claim comment wins —
because an assignee field cannot adjudicate a race between agents that authenticate as the same
account. Every run signs its work with a per-run identity (`claude-code-60fabae1`), so the board
records which run did what even when every agent shares one login.

## Supported trackers

The workflow is written as ten abstract operations (`claim`, `transition`, `comment`, …); each
tracker's binding says how its API performs them — and, just as important, what that tracker does
NOT provide, so no rule silently stops applying.

| | GitHub Issues | Linear | Trello |
|---|---|---|---|
| State model | `status:*` labels (discipline) | native workflow states (enforced) | lists — a card is in exactly one |
| Claim verification | comment timeline | state check + comment timeline | comment trail (`commentCard`) |
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

One line. It clones the skill into `~/.agents/skills/issue-flow`, links it into each runtime it
finds, and — run again later — **upgrades in place while preserving your configuration block**:

```sh
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/asanabrial/issue-flow/main/install.sh | sh
```

```powershell
# Windows
irm https://raw.githubusercontent.com/asanabrial/issue-flow/main/install.ps1 | iex
```

**The piped script is the installer itself**, and the first thing it does with no skill beside it is
clone the repository and hand over to its on-disk copy — so everything of substance executes from
files on your machine, and the settings that remove confirmation steps only ever hold the values you
put there yourself. Prefer to see everything before anything runs? Same result by hand:

```sh
git clone https://github.com/asanabrial/issue-flow ~/.agents/skills/issue-flow
~/.agents/skills/issue-flow/install.sh install
```

The skill lives in `~/.agents/skills/issue-flow/` and the installer links it into each runtime's
skill directory (`~/.claude/skills/`, `~/.codex/skills/`). Run `status` to see what is linked, and
`uninstall` to remove the links — the skill itself is never touched.

On Windows the installer tries a symlink, falls back to a directory junction (which needs no
elevation) and only then to a copy. If it copies, it says so loudly: copies stop tracking the
original.

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
agent authenticates as the same tracker account, where assignee fields cannot show a collision.

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
Settings live between two markers inside `SKILL.md`. The installer's `sync` replaces everything
outside the markers and puts what is inside back untouched, backing the file up first. Re-running
the install one-liner upgrades the same way.

## Layout

```
SKILL.md                          the workflow: roles, states, claiming, delivery
bindings/github.md                how each operation is performed, per tracker
bindings/linear.md
bindings/trello.md
examples/domain-test-coverage.md  a worked domain rule book
install.sh / install.ps1          self-acquiring installers (pipe them or run them)
```

## Configuration

Settings live in one marked block at the end of `SKILL.md` — tracker, merge strategy, worktree
location, and whether delivery is pre-authorised. It is a table with the defaults written next to
each value, so it reads on its own.

Edit it by hand, or from the installer:

```sh
./install.sh config                                              # print the table
./install.sh config --set "Worktree location=/wt/<repo>/<branch>"
```

The installer matches a setting **by its name** and carries no list of its own, so a row added to the
skill is settable immediately without touching either script. It backs the file up first, refuses a
name that matches no row or more than one, and refuses a value containing `|`, which would split the
cell and corrupt the table.

`sync` upgrades the skill while keeping that block:

```sh
./install.sh sync --from ./newer-SKILL.md
```

It backs the file up first, and refuses outright if it cannot find the markers rather than risk
dropping your settings.

**Reset the block to the defaults (or remove it) before sharing a configured copy.** Your values are
your permissions, including whether an agent may push without asking.

## Status

The workflow, the state machine and the GitHub binding are the mature parts, exercised against a
live board. **The Linear and Trello bindings are written against their official API documentation
but have not yet been exercised against a live workspace** — expect the first real run to find
something. `install.sh` has been tested under Git Bash; the logic is POSIX but it has not run on a
native Linux or macOS shell.

Licensed GPL-2.0. Issues and corrections welcome, preferably filed through the workflow itself.
