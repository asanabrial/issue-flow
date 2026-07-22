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

**`transition` must add and remove in the same invocation.** Two calls leave a window in which the
issue carries two states, and any run reading the board during that window sees an ambiguous item.

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

**This applies only to issues that are actually on a board.** An issue with no project item has
nothing to mirror and needs no extra step — the label is the whole state, and that is the normal
case. Check first, and skip silently when there is no item; never treat its absence as a failure.

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

**Prefer the repository Action below.** An agent-side mirror is one more step an agent can skip,
which is precisely how a board goes stale while the work stays correct, and it costs every agent the
`project` scope that `gh issue` does not need. The Action runs server-side on every label change,
cannot be forgotten, and keeps agent tokens scoped to Issues.

```yaml
# .github/workflows/mirror-status.yml
name: Mirror status label to the board
on:
  issues:
    types: [labeled, unlabeled]

jobs:
  mirror:
    runs-on: ubuntu-latest
    steps:
      - env:
          # A PAT with `project` scope. GITHUB_TOKEN cannot write Projects v2.
          GH_TOKEN: ${{ secrets.PROJECT_TOKEN }}
          OWNER: ${{ github.repository_owner }}
          PROJECT_NUMBER: '1'          # your project number
          ISSUE: ${{ github.event.issue.number }}
        run: |
          set -euo pipefail

          # The state is whichever status:* label is on the issue now. None -> nothing to mirror.
          STATE=$(gh issue view "$ISSUE" --json labels \
            --jq '[.labels[].name | select(startswith("status:"))] | first // empty')
          [ -n "$STATE" ] || { echo "no status label; nothing to mirror"; exit 0; }

          # Board columns are prose ("In Progress"); label values are slugs ("in-progress").
          NAME=$(printf '%s' "${STATE#status:}" | tr '-' ' ' | awk '{for(i=1;i<=NF;i++) $i=toupper(substr($i,1,1)) substr($i,2)}1')

          read -r PROJ_ID FIELD_ID < <(gh api graphql -f query='
            query($owner:String!, $number:Int!) {
              user(login:$owner) { projectV2(number:$number) { id
                field(name:"Status") { ... on ProjectV2SingleSelectField { id } } } } }' \
            -f owner="$OWNER" -F number="$PROJECT_NUMBER" \
            --jq '.data.user.projectV2 | "\(.id) \(.field.id)"')

          OPT_ID=$(gh api graphql -f query='
            query($owner:String!, $number:Int!) {
              user(login:$owner) { projectV2(number:$number) {
                field(name:"Status") { ... on ProjectV2SingleSelectField { options { id name } } } } } }' \
            -f owner="$OWNER" -F number="$PROJECT_NUMBER" \
            --jq --arg n "$NAME" '.data.user.projectV2.field.options[] | select(.name==$n) | .id')

          ITEM_ID=$(gh api graphql -f query='
            query($owner:String!, $number:Int!) {
              user(login:$owner) { projectV2(number:$number) { items(first:100) { nodes { id
                content { ... on Issue { number } } } } } } }' \
            -f owner="$OWNER" -F number="$PROJECT_NUMBER" \
            --jq --argjson i "$ISSUE" '.data.user.projectV2.items.nodes[] | select(.content.number==$i) | .id')

          # No item means this issue is not on the board: nothing to mirror, and that is fine.
          # No option means the board has no column for this state: say so, do not fail the run.
          # Either way the label already carries the truth.
          [ -n "$ITEM_ID" ] || { echo "issue $ISSUE is not on the board; nothing to mirror"; exit 0; }
          [ -n "$OPT_ID" ]  || { echo "board has no column named '$NAME'"; exit 0; }

          gh project item-edit --project-id "$PROJ_ID" --id "$ITEM_ID" \
            --field-id "$FIELD_ID" --single-select-option-id "$OPT_ID"
```

Three adjustments before it works, and each one fails **quietly** if you miss it:

- **`user(login:)` becomes `organization(login:)`** for an organisation-owned project. The query
  returns null rather than an error, so the mirror simply never fires and nothing tells you.
- **`items(first:100)`** stops finding issues once the board passes a hundred items. Paginate, or
  filter server-side, before the board grows into that.
- **The column names must match.** The script title-cases the label slug, so `status:in-progress`
  looks for a column called `In Progress`. Rename one side until they agree.

### If you mirror from the agent instead

Only where no Action is possible. Every agent then needs the `project` scope, and these rules stop a
cosmetic failure from breaking real work.

**Mirror every `status:*` change onto the field in the same step that changes the label**, and only
for issues that have a project item. Three rules make this safe:

1. **The label goes FIRST and the mirror is BEST-EFFORT.** The label is the state; the field is a
   picture of it. If the mirror fails — no `project` scope, no board, the item not added yet — say
   so and carry on. **Never abort the work because a picture did not update**, and never revert a
   correct label because its mirror failed.
2. **Discover the IDs; never hardcode them.** Project number, field ID and option IDs differ per
   project and change when someone edits the field. Look them up each run.
3. **Tolerate the auto-add lag.** A freshly filed issue reaches the board asynchronously, so the
   item may not exist yet when you try to set its field. That is normal: skip the mirror, note it,
   move on — the next transition will correct it.

```bash
# $1 owner  $2 project-number  $3 issue-number  $4 state (e.g. Ready, "In Progress")
read -r PROJ_ID FIELD_ID < <(gh api graphql -f query="
  query { user(login:\"$1\"){ projectV2(number:$2){ id
    field(name:\"Status\"){ ... on ProjectV2SingleSelectField { id } } } } }"   --jq '.data.user.projectV2 | "\(.id) \(.field.id)"')

OPT_ID=$(gh api graphql -f query="
  query { user(login:\"$1\"){ projectV2(number:$2){
    field(name:\"Status\"){ ... on ProjectV2SingleSelectField { options { id name } } } } } }"   --jq ".data.user.projectV2.field.options[] | select(.name==\"$4\") | .id")

ITEM_ID=$(gh api graphql -f query="
  query { user(login:\"$1\"){ projectV2(number:$2){ items(first:100){ nodes{ id
    content{ ... on Issue { number } } } } } } }"   --jq ".data.user.projectV2.items.nodes[] | select(.content.number==$3) | .id")

# Empty OPT_ID or ITEM_ID -> skip quietly; the label already carries the truth.
gh project item-edit --project-id "$PROJ_ID" --id "$ITEM_ID"   --field-id "$FIELD_ID" --single-select-option-id "$OPT_ID"
```

**This costs every agent the `project` scope**, which `gh issue` alone does not need. If you would
rather keep tokens down to Issues, do the same mirroring in a repository Action on
`issues.labeled`/`unlabeled`: identical result, server-side, zero extra permission on any agent.
Pick one — running both just means two things to debug when the board lags.

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
