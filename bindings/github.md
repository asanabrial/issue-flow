# Binding: GitHub Issues

How this workflow's operations are performed against GitHub, and which of its assumptions GitHub
satisfies natively rather than by convention. `SKILL.md` describes WHAT happens; this file is the
only place that says HOW, and the only place a `gh` command appears.

Read this alongside `SKILL.md` when the operator configuration names `github` as the tracker.

## What GitHub provides

The workflow asks a tracker for six things. What a binding must declare is not only which of them
exist, but what happens where they do not — a capability that is absent and undeclared is how a rule
silently stops applying.

| The workflow needs | GitHub | Consequence |
|---|---|---|
| A single-valued **state** per item | ✗ — labels are multi-valued | "exactly one `status:*`" is a **discipline, not a constraint**. Nothing stops two; every transition must remove the old label in the same call |
| A **claim** applied server-side | ⚠️ — assignees are a set, **but agents usually share one account** | then the set has one element however many runs claimed, and re-reading it can never show a collision — see *The claim hazard* |
| **Last activity**, timestamped by the server | ✓ — `updatedAt` and per-comment timestamps | the stale-claim rule works as written |
| A stable, short **identity** | ✓ — the issue number | usable verbatim in branch and worktree names |
| **Comments**, append-only and ordered | ✓ | heartbeats, blockers and hand-offs all land here |
| A native **priority** | ✗ | encoded as a label `<scale>:<value>`; the domain names the scale |

GitHub has a closed state of its own, independent of labels — but `done` is still carried as a
sixth label, and the two are set together. Relying on closed alone would leave the finished issue
wearing `status:review`, so every query for work awaiting verification would keep returning it, and
a board grouped by status would never move the card: closing an issue emits `issues.closed`, which
is not a label event and fires no mirror.

## The claim hazard — read this before implementing `claim`

The obvious reading of GitHub is that assignees are a set, so two agents claiming leaves two
assignees and the collision is visible. **That is true only when the agents are different accounts,
and they usually are not.** Agents authenticate as one shared account, so `--add-assignee @me` twice
leaves exactly one assignee, and the re-read shows a clean issue assigned to you — while another run
is already building it.

So verification here does not read the assignee. It reads the **comment timeline**, which the server
orders and which no later writer can reorder:

1. **Claiming writes a comment**, carrying the run-id. It is not decoration — it is the only
   artefact that records *which* run claimed and *when*.
2. **Then read the comments back.** An earlier claim comment than yours, with a different run-id and
   no back-off after it, means you lost: release and take the next item.

This is the same mechanism the Linear binding needs, arrived at from the opposite direction — there
because the assignee is overwritten, here because it is shared. The lesson generalises further than
either: **an identity field cannot adjudicate a race between runs that share that identity.**

## Prerequisites

**`gh` is the preferred path, not the only one.** It is a separate install — nothing in this skill
can put it there, and the installer deliberately makes no network calls, so it checks for `gh` and
tells you, rather than fetching it. Get it from your platform's package manager
(`winget install GitHub.cli`, `brew install gh`, `apt install gh`).

Where `gh` cannot be installed — a locked-down image, a sandbox with no package manager — **the
REST API with a token does everything below**, at the cost of longer invocations. Keep that route
in mind rather than concluding the workflow is unavailable: an analyst reaching the API over HTTPS
is exactly as capable as one running `gh`, which is the whole reason the analyst role was defined
as network-only in the first place.

## State names

The workflow's states are `analysis`, `ready`, `in-progress`, `review`, `blocked` and `done`. Here
each one is stored as the label **`status:<name>`** — the prefix is this binding's convention, not the
workflow's, and it exists so a glance at an issue separates state labels from attribution and domain
ones.

## Operations

`<n>` is the issue number throughout.

| Operation | GitHub |
|---|---|
| `ensure_states` | `gh label create "status:<s>" --color ededed --force` for each state (see below) |
| `create` | ensure every label exists (below), then `gh issue create --title "<identity>: <title>" --body-file <file> --label "status:ready" --label "<scale>:<value>" --label "domain:<name>" --label "analyst:<runtime>"` |
| `list_state` | `gh issue list --label "status:<state>" --search "no:assignee" --json number,title,labels,createdAt` |
| `claim` | `gh issue edit <n> --add-assignee @me`, write the claim comment, then **read the comment timeline** — an earlier claim than yours means you lost. Re-reading assignees is not enough; see below |
| `transition` | `gh issue edit <n> --add-label "status:<new>" --remove-label "status:<old>"` — one call, both halves. **If the issue is on a project board, mirroring the board is part of this operation** — see *Keeping a board in sync* |
| `comment` | `gh issue comment <n> --body "<text>"` |
| `last_activity` | `gh issue view <n> --json updatedAt,comments` |
| `label` | `gh label create "<key>:<value>" --force` **then** `gh issue edit <n> --add-label "<key>:<value>"` |
| `unassign` | `gh issue edit <n> --remove-assignee @me --remove-label "dev:<runtime>"` — one call, both halves |
| `close` | `transition` to `done` **first**, then `gh issue close <n> --comment "<what was verified>"` — the label is what the board and every query read; closing is GitHub's own bookkeeping |

**Link the branch to the issue the moment it exists.** GitHub has a native way that also happens to
fit this workflow's git flow exactly: `gh issue develop <n> --name <branch> --base <base>` creates
the branch **server-side, from the fresh base, already linked** in the issue's Development sidebar —
then `git fetch origin <branch>` and build the worktree from it. One command replaces branch
creation AND recording. Where the branch already exists locally, push it and comment
`Branch: <name>` instead — prose is the fallback, the sidebar is the record. And the closing comment
names the delivering commit SHA: branches get deleted after merge, and the SHA is the join that
survives deletion.

**`transition` must add and remove in the same invocation.** Two calls leave a window in which the
issue carries two states, and any run reading the board during that window sees an ambiguous item.
**Then re-read the labels**: exactly one `status:*` must remain. Two means the removal failed or
another run interleaved — fix it on the spot, because a two-state item poisons every query that
touches either state, and it has happened in live use.

## Setup

State labels, once per repository:

```bash
for s in analysis ready in-progress review blocked done; do
  gh label create "status:$s" --color ededed --force
done
```

**Adding `done` to a repository that already ran without it** leaves closed issues still wearing the
state they were in when someone closed them. They keep showing up in `review` queries and, on a
board, keep sitting in the column they never left. Repair once, one pass per state — deliberately
plainer than a single clever query, because this runs against real history and you want to be able
to read it before you trust it:

```bash
for s in analysis ready in-progress review blocked; do
  gh issue list --state closed --label "status:$s" --json number --jq '.[].number' |
  while read -r n; do
    gh issue edit "$n" --add-label "status:done" --remove-label "status:$s"
  done
done
```

An issue carrying two state labels is handled by the pass for each of them; an issue already on
`status:done` is matched by none of them and left alone.

**`gh` refuses to attach a label that does not exist**, and the error arrives at
`gh issue create` — the analyst's last step, after all the analysis is done. That is why `create` and
`label` create first and attach second: the domain names its own priority scale and rule book, so no
setup script can have created them in advance. `gh label create --force` is idempotent, so running it
every time costs one call and removes the failure mode entirely.

Attribution labels are created on demand — the first run of a given runtime creates its own, so the
set stays exactly as wide as the runtimes actually in use:

```bash
gh label create "analyst:<runtime>" --color c5def5 --force
gh label create "dev:<runtime>"     --color bfd4f2 --force
```

Labels are what make attribution *queryable*: `gh issue list --label "dev:codex"` answers "what is
that runtime holding right now" in one call, where parsing prose for the same answer breaks on any
rewording.

## Credentials

`gh auth status` may report a token stored in a system keyring that a sandboxed runtime cannot read;
there, use `GH_TOKEN` instead. **Verify with `gh issue list` before relying on the workflow** — an
analyst that cannot file its issue has done the work and lost it.

`gh issue` needs no `project` scope. The board mirroring below does.

---

## Keeping a board in sync

**A GitHub Project (v2) board's `Status` field does not follow your labels.** Nothing connects the
two: project items are references, so title, state, labels and assignees are always live, but a
custom field lives on the project item and no label touches it. Left alone, a board shows whatever
someone set by hand the day they set it.

**Whether to mirror at all is read from the operator configuration, not guessed.** The `Project
board` row names `owner/number` or `none`. `none` means no board anywhere: skip this section
entirely. A named board means **an operation that sets a state — `create` as much as `transition` — is not
finished until the mirror was attempted** —
attempted, not necessarily succeeded: an issue not yet on the board, or a missing column, is skipped
quietly, because the label already carries the truth. `create` is the case everyone forgets: a fresh
issue reaches the board through auto-add with its Status empty, and no transition ever follows to
correct it — the analyst that filed it sets the initial column, or the item sits in no column at all. What is not acceptable is not trying: every
skipped attempt is how the board was found lying five states behind the labels.

Resolve the project, field and option ids **once per run** and reuse them for every transition in
that run — they are stable within a session, and per-transition discovery is the overhead that
tempts a run to skip the mirror.

**The mirror is part of `transition`, done by the same run that moves the label.** No server-side
machinery: when this workflow changes a `status:*` label on an issue that has a project item, it
moves the board field in the same step. That costs the agent's token the `project` scope — `gh issue`
alone never needs it — so prove it once with `gh project list --owner <owner>` before relying on the
mirror, and where the scope is missing, fall back to label-only exactly as rule 1 below prescribes.

### Why a mirror is needed at all

**The constraints below were verified against the GitHub API in July 2026** — they are product
limitations, not laws, so re-check them before designing around one.

**Board columns cannot be labels.** In `BOARD_LAYOUT` the columns come from a **single-select**
field; `Labels` is multi-value, and a card cannot sit in two columns, so GitHub does not offer it.
The option is simply absent — you are not failing to find it. `Group by → Labels` exists only in
`TABLE_LAYOUT`.

**View configuration is UI-only.** The API exposes no view mutation — only `createProjectV2Field`,
`updateProjectV2Field` and `updateProjectV2ItemFieldValue`. No agent can set a layout or a grouping
for you; a human has to click it once.

So a board needs the built-in `Status` field, reshaped to mirror your states with
`updateProjectV2Field` — one option per state, each option's description naming the label it mirrors.
Then the board reads like the workflow, and only the syncing is left.

### How the mirror runs


Two quiet failure modes worth knowing: for an **organisation-owned** project, `user(login:)` must
become `organization(login:)` — the query returns null rather than erroring, so the mirror silently
never fires; and `items(first:100)` stops finding issues once the board passes a hundred items —
paginate before it grows into that.

**If your agents cannot hold the `project` scope**, the same mirroring can run server-side in a
repository Action on `issues.labeled`/`unlabeled` — it needs a PAT stored as a secret, because the
automatic `GITHUB_TOKEN` cannot write Projects v2. One or the other, never both: two mirrors is two
things to debug when the board lags.

**Whatever you do, the labels stay authoritative.** They are what agents read and write; the board
is a view. Invert that and every agent needs the `project` scope and the workflow's transport has to
be rewritten — a board that is state costs a redesign, a board that is a view costs nothing. If the
Action ever breaks, the board goes stale while the work stays correct, which is the right failure
direction.

**GitHub now renders agent activity of its own**, and it is a third view, not a second state. When a
coding agent is assigned to an issue, its session shows under the assignee with its own live status —
queued, working, waiting for review, completed. That reports what a runtime said about itself, not
what the state machine says, and the two legitimately disagree: a session can read as *completed*
while its issue is correctly still `status:in-progress`, because the run ended and the work did not.
Same rule as the board — read it, do not trust it, and never move a label to make it agree.
