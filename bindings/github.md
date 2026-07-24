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

## `verify_claim` — the renewal, made concrete

The workflow requires every heartbeat — and every expensive or irreversible step between heartbeats
— to re-read the control surface before writing. On GitHub that surface is one call, and everything
the renewal needs is in it:

```bash
gh issue view <n> --json state,labels,comments
```

Then three checks, in any order — a failed check is a **stop instruction, not a retry**:

1. **State.** `state` is `OPEN`. A closed issue means someone delivered or killed it: stop, and do
   not change anything.
2. **Workflow state.** Exactly one `status:*` label is present and it is the state you are working
   under (for a dev mid-build, `status:in-progress`). A missing or different state means the item
   moved without you: stop, and leave the new state alone — it is not yours.
3. **Control messages.** No comment created *after your own claim comment* names your run-id in a
   stand-down, a reclaim or an adjudication. Your claim comment is the watermark: anything addressed
   to your run-id below it is for you (for a reclaimer, the `Reclaiming from <run-id>` comment plays
   the watermark role — it is that run's first write to the timeline). Finding one means stop,
   acknowledge once, release your `dev:<runtime>` marker, and write nothing else. A mention is not a
   control message: another run's heartbeat that names your run-id in passing ("waiting on
   `<run-id>`'s measurement phase") instructs you to do nothing, and classifying it as a stand-down
   would have you abandon work nobody asked you to drop. What stops you is a comment that tells you.

Two distinctions keep this safe to run unattended:

- **A failed read is not a failed check.** If the `gh` call itself errors — network, auth, rate
  limit — the renewal answered nothing. The semantics (*A failed read is not a failed answer* in
  `SKILL.md`) apply verbatim: fail closed on the write, retry the read.
- **Do not remove the assignee when another run still holds the item.** Every agent authenticates
  as the same account, so the single `@me` assignee is the winner's as much as yours — removing it
  strips the active holder. On a stand-down or a lost race, your `dev:<runtime>` label is
  per-runtime and comes off; the shared assignee stays. (Plain `unassign` — handing back work you
  alone hold — still removes both; see the operations table.)

The semantics — what a stop instruction is, what release means, why no second claim comment — live
in `SKILL.md` under *A heartbeat is a claim renewal*; this section is only the mechanics.

**Worked example — a real claim race, 2026-07-22.** The timeline, from the issue's comment trail:

| Time (UTC) | Event |
|---|---|
| 14:40:03 | `claude-code-cb8d3f2c` writes its claim comment |
| 14:40:08 | `claude-code-d7d8a22e` writes its claim comment, 5 s later, before the collision is visible |
| 14:40:41 | adjudication comment: the earliest claim wins, `claude-code-d7d8a22e` is told to stand down |
| 14:49–15:24 | `claude-code-d7d8a22e` posts heartbeats 1, 2 and 3 and keeps building — none of its writes re-read the timeline |
| 15:24:21 | the winner delivers |
| 15:28:52 | `claude-code-d7d8a22e` finally stands down, retracting a measurement taken on work it no longer held |

Under write-only heartbeats the stand-down sat unread for 48 minutes. Under read-before-write
renewal, the loser's next heartbeat — 14:49:22 at the latest — runs the three checks first: check 3
finds the 14:40:41 adjudication naming its run-id, below its own claim comment. The heartbeat is
never written; the run acknowledges, releases its markers, and heartbeats 1–3 and the duplicate
build do not happen. The renewal does not prevent losing the race; it caps the cost of having lost
it at one renewal interval.

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
| `create` | ensure every label exists (below), then `gh issue create --title "<identity>: <title>" --body-file <file> --label "status:ready" --label "<scale>:<value>" --label "domain:<name>" --label "analyst:<runtime>"`. **If a project board is configured, mirroring the initial column is part of this operation** — see step 4 of *How the mirror runs* |
| `list_state` | `gh issue list --label "status:<state>" --search "no:assignee" --json number,title,labels,createdAt`; partition results by `domain:<name>` and declared priority scale, sort inside each partition by that scale, and expose each partition head with `createdAt` so the workflow can choose the oldest head without comparing scale values |
| `claim` | `gh issue edit <n> --add-assignee @me`, write the claim comment, then **read the comment timeline** — an earlier claim than yours means you lost. Re-reading assignees is not enough; see below |
| `verify_claim` | one read — `gh issue view <n> --json state,labels,comments` — then three checks, any failed check is a stop instruction, not a retry: the issue is OPEN; it carries exactly one `status:*` label and it is the state you are working under; and no comment created after your own claim comment names your run-id in a stand-down, reclaim or adjudication. See *`verify_claim` — the renewal, made concrete* below |
| `transition` | **If a project board is configured, attempt its `Status` field FIRST** — best-effort, without pre-checking whether the issue is on the board; an issue not on it (or a missing scope) is a quiet no-op, not a precondition (see *Keeping a board in sync*). **Then** move the authoritative label: `gh issue edit <n> --add-label "status:<new>" --remove-label "status:<old>"` — one call, both halves |
| `publish_review` | push the issue branch, then query `gh pr list --head <branch> --state open --json number,url,headRefOid,baseRefOid`. Reuse the single open PR when present; create one only when none exists with `gh pr create --base <base> --head <branch> --title "<title>" --body-file <file>`. More than one open PR is ambiguous: stop. Do not use closing keywords. Record the PR URL on the issue before `transition(review)` |
| `review_status` | capture `headRefOid` and `baseRefOid` with `gh pr view <pr> --json headRefOid,baseRefOid,latestReviews,reviewDecision,reviewRequests,mergeStateStatus`. The independent verdict artifact MUST contain `Reviewer-Run: <run-id>`, `Reviewed-Head: <full-head-sha>` and `Reviewed-Base: <full-base-sha>`; re-read both SHAs after review and reject the verdict if either differs. The verdict is mandatory even when every runtime authenticates as the PR author and GitHub cannot supply a distinct native approval |
| `ci_status` | capture `headRefOid` and `baseRefOid`; run `gh pr checks <pr> --watch --fail-fast`, then separately read `gh pr checks <pr> --json name,workflow,state,bucket` (`--watch` and `--json` are separate invocations). Build the expected set as the union of host-required names and every applicable repository-required lane. For the host set, `gh pr checks <pr> --required --json name` returning exactly `no required checks reported on the '<branch>' branch` means the empty set; every other command error fails closed. When repository policy names workflow job ids rather than visible checks, resolve each id through `jobs.<id>.name` in the workflow file at the captured head SHA before comparing; never compare ids directly with `gh pr checks.name`. Each expected visible name must be present exactly once with `bucket=pass`; `skipping`, `cancel`, a missing name or an unexpected duplicate is not green. Re-read both SHAs afterwards; if either changed, discard review and CI |
| `merge` | require `reviewed head/base == CI head/base == current headRefOid/baseRefOid`, then run `gh pr merge <pr> --merge --match-head-commit <head-sha>`. Never use `--admin` or `--delete-branch`: deletion can fail after a successful API merge in a multi-worktree checkout and make the result look retryable. If a merge queue owns delivery, first prove its configured method preserves merge commits or stop. Poll `gh pr view <pr> --json state,mergedAt,mergeCommit` until merged and take `.mergeCommit.oid` as the delivered SHA; fetch it and verify it has exactly two parents in order: reviewed base, then reviewed head. A mismatch means the base raced or the host used another strategy: leave the issue in `review` and invoke the repository's fix-forward/revert policy. If exact-delivered-SHA CI is required, locate every required workflow with `gh run list --commit <merge-sha> --workflow <workflow> --json databaseId,headSha,status,conclusion`, wait with `gh run watch <id> --exit-status`, then verify `gh run view <id> --json headSha,status,conclusion,jobs`: the head must equal `<merge-sha>`, the run and every applicable required job must be `completed/success`, never `skipped`. Run `verify_claim` again immediately before `close`; clean up the remote branch and worktree only after confirmed delivery |
| `publish_version` | after every required delivered-SHA gate, call `verify_claim` BEFORE the first publication write, then detect each component whose declared version changed from `<old>` to `<new>`. Enumerate `git ls-remote --tags origin` — never local tags — to derive the component's established naming convention and previous component tag. Require exactly one convention; if history is empty, require an unambiguous single-product/component classification before using `v<new>` or `<component>/v<new>`. For `<tag>`, inspect both local and remote direct plus peeled refs. A valid annotated tag has a tag object and a `refs/tags/<tag>^{}` target equal to `<merge-sha>`; a lightweight tag, another target or ambiguous state blocks without rewriting anything. If the remote tag is absent, reuse a matching annotated local tag or create it with `git tag -a "<tag>" "<merge-sha>" -m "<component> <new>"`, then attempt `git push origin "refs/tags/<tag>"`. After ANY push result — success, rejection or timeout — re-read the remote: a conclusive peeled target equal to `<merge-sha>` is idempotent success, another target is conflict, and no conclusive read fails closed. When GitHub Releases are required, first reverify the remote peeled target, then query `gh release view "<tag>" --json tagName,isDraft,isPrerelease`. A matching non-draft Release with the SemVer-derived prerelease flag is idempotent success; incompatible metadata blocks. Only a confirmed not-found result permits `gh release create "<tag>" --verify-tag --generate-notes` (add `--prerelease` for a prerelease and add `--notes-start-tag "<previous-component-tag>"` only when that remote component tag exists). After ANY create result, re-read the Release JSON and the remote peeled tag; both must match the expected metadata and `<merge-sha>`. Auth, network or API ambiguity fails closed. Tag/Release failure leaves the issue in `review` even though the merge already exists |
| `comment` | write the body to a temp file, then `gh issue comment <n> --body-file <file>` — **never inline `--body "<text>"`**; see below |
| `last_activity` | `gh issue view <n> --json updatedAt,comments` |
| `label` | `gh label create "<key>:<value>" --force` **then** `gh issue edit <n> --add-label "<key>:<value>"` |
| `unassign` | `gh issue edit <n> --remove-assignee @me --remove-label "dev:<runtime>"` — one call, both halves. **Exception — releasing work another run still holds** (lost race, stand-down): remove only your `dev:<runtime>` label; `@me` is the shared account and removing it strips the active holder |
| `close` | `transition` to `done` **first**, then post the closing note with `gh issue comment <n> --body-file <file>`, then a bare `gh issue close <n>` — `gh issue close` has NO file variant (`-c/--comment` is inline-only, confirmed against `gh issue close --help`), so the note goes through `comment`'s file-based path and the close carries no body at all. Two calls means a partial-failure case: if the comment lands but the close errors, the issue is left open with its closing note already posted — retry only the bare `gh issue close <n>`, never re-post the note |

**Inline `--body`/`--comment` corrupts markdown on a PowerShell runtime — write to a file instead.**
Seen live: three separate comments, from two different runtimes, posted with the backtick stripped
or eaten entirely — `` `floor_starvation` `` arrived as `\loor_starvation\` (the backtick-plus-`f`
was consumed as PowerShell's form-feed escape, taking the `f` with it), `` `status:blocked` `` arrived
as `\status:blocked\`, and every intended line break arrived as the two literal characters `\n`
instead of a newline. Backtick is PowerShell's escape character; a double-quoted `--body "<text
with `code spans`\nand newlines>"` gets expanded by the shell BEFORE `gh` ever sees it, and no amount
of care in the text itself prevents that — the corruption happens one layer below where the text is
composed. `create` already does this right (`--body-file <file>`); `comment` and `close` did not, and
that inconsistency is exactly what let evidence-bearing comments — the ones a stale-claim or
blocked-condition check reads back later — arrive silently damaged. Write the body to a file on every
operation that accepts markdown, with no exception for "this one's short."

**Closing keywords bypass the state machine — do not use them.** A commit or PR whose message says
`closes #34` or `fixes #34` makes GITHUB close the issue the moment it reaches the default branch:
no transition to `done`, no mirror, labels frozen wherever they were. The board then shows an open
column for a closed issue, and it was not any run's doing — it was the tracker's own automation
acting outside the workflow. Reference issues plainly (`#34` links without closing) and close
through the workflow's `close`, which moves the state first. If a closing keyword slips through
anyway, run `transition` to `done` afterwards — the auto-close is not the workflow's close, and the
state machine does not know it happened. Seen live: an issue closed by its delivery commit sat
CLOSED wearing `status:ready` until an audit caught it.

**A shared GitHub account cannot manufacture independent approval.** The workflow's reviewer is a
fresh reasoning context, but GitHub sees the authenticated account, not that context. When the same
account authored the PR, record the reviewer verdict and evidence in the PR or issue with
`Reviewer-Run: <run-id>`, `Reviewed-Head: <full-head-sha>` and
`Reviewed-Base: <full-base-sha>`; do not claim a native `APPROVED` review that GitHub refused.
Re-read both PR SHAs after the verdict — matching prose without that race check is still stale
evidence. This comment proves the workflow review only; if repository protection requires a native
approval, it does NOT substitute for one and delivery stays blocked until a distinct identity
supplies it. In repositories with a genuinely distinct reviewer identity, use the native review
decision and require it normally, still tied to the reviewed revision.

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

**The mirror only fires on a `transition` you make — it does not repair drift from before you got
there.** Seen live: five items sitting on `Ready` in the board days after their labels had moved to
`in-progress` or `done`, because whatever run transitioned them either predates this section being
written or hit the missing-scope fallback — and nothing since then ever looked back. There is no
daemon reconciling the two, by design (see *Why a mirror is needed at all*), which means staleness is
permanent unless a run that happens to be reading an item's labels for some other reason also checks
its board column. So: whenever you read an issue's labels and its board item is in view for any
reason — not only during your own `transition` — compare the two, and if they disagree, correct the
`Status` field with step 3 of *How the mirror runs* on the spot. It costs one extra mutation call
using ids you likely already resolved this run, and it is the only thing standing between "the mirror
sometimes fails" and "the board silently drifts forever."

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

**The mirror is part of `transition`, done by the same run that moves the label — and it runs first.**
No server-side machinery: on a `transition` for an issue that has a project item, this workflow sets
the board `Status` field BEFORE it moves the `status:*` label, best-effort and without pre-checking
board membership — an issue not on the board is the quiet skip below, not a precondition. The fragile,
easily-skipped half runs before anything can short-circuit it; the reliable one-call label edit
follows. This trades one failure for a rarer one: the label stays the store queries read, so a run
that dies between the board write and the label edit leaves the board ahead of a still-stale label —
far less likely than the board drift that board-last invited, which is the incident this order exists
to prevent. That costs the agent's token the `project` scope — `gh issue` alone never needs it — so
prove it once with `gh project list --owner <owner>` before relying on the mirror, and where the scope
is missing, skip the board attempt and do the label edit alone, exactly as step 1 of *How the mirror
runs* below prescribes.

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

1. **Resolve the project, its `Status` field and its options — once per run, then reuse for every
   transition in that run.**

   ```bash
   gh api graphql -f query='
     query($login: String!, $number: Int!) {
       user(login: $login) {
         projectV2(number: $number) {
           id
           fields(first: 20) {
             nodes {
               ... on ProjectV2SingleSelectField { id name options { id name description } }
             }
           }
         }
       }
     }' -f login="<owner>" -F number=<board-number>
   ```

   Keep the returned project id, the `Status` field id, and the option id for each state (matched by
   the option's name or its description, per *Why a mirror is needed at all* above). **If this call
   errors on a missing scope, stop the board attempt here and go straight to the label edit** — the
   label is the authoritative store and carries the truth; nothing on the board is worth attempting
   without the `project` scope, and the label edit still runs after this section either way.

2. **Resolve the project item id for the issue being transitioned**, paginating if the board has grown
   past a page:

   ```bash
   gh api graphql -f query='
     query($login: String!, $number: Int!, $cursor: String) {
       user(login: $login) {
         projectV2(number: $number) {
           items(first: 100, after: $cursor) {
             pageInfo { hasNextPage endCursor }
             nodes { id content { ... on Issue { number } } }
           }
         }
       }
     }' -f login="<owner>" -F number=<board-number>
   ```

   Follow `pageInfo` until the issue's `number` shows up or the pages run out — each follow-up page is
   the same call plus `-f cursor="<endCursor from the previous page>"` (the first call simply omits it,
   leaving `$cursor` null). An issue not found is the quiet skip *Whether to mirror at all* already
   allows (not yet added to the board, or added but not yet indexed).

3. **Set the field** with the ids resolved above, to the option matching the new label:

   ```bash
   gh api graphql -f query='
     mutation($project: ID!, $item: ID!, $field: ID!, $option: String!) {
       updateProjectV2ItemFieldValue(input: {
         projectId: $project, itemId: $item, fieldId: $field
         value: { singleSelectOptionId: $option }
       }) { projectV2Item { id } }
     }' -f project="<PROJECT_ID>" -f item="<ITEM_ID>" -f field="<FIELD_ID>" -f option="<OPTION_ID>"
   ```

   For a `transition`, this whole board attempt (steps 1–3) precedes the `status:*` label edit — the
   authoritative label move from the operations table runs immediately after, whether the board write
   succeeded, was skipped for a missing item, or fell back on a missing scope.

4. **`create` runs step 3 too, immediately after filing**, setting the initial column to whatever
   option mirrors `status:ready` — this is the case *Whether to mirror at all* names as the one
   everyone forgets, because no `transition` ever follows a fresh issue to correct an empty `Status`.

Two quiet failure modes worth knowing, on top of the scope check in step 1: for an
**organisation-owned** project, `user(login:)` must become `organization(login:)` in both queries
above — the query returns null rather than erroring, so the mirror silently never fires; and
`items(first:100)` in step 2 stops finding issues once the board passes a hundred items — the
pagination above is what keeps that from going quiet too.

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
