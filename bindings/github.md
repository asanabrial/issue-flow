# Binding: GitHub Issues

How this workflow's operations are performed against GitHub, and which of its assumptions GitHub
satisfies natively rather than by convention. `SKILL.md` describes WHAT happens; this file is the
only place that says HOW.

Read this alongside `SKILL.md` when the operator configuration names `github` as the tracker.

## The mechanical half runs as a script

**Every reversible operation below is executed by `scripts/github.py`, not by an agent composing
`gh` calls.** That is not an optimisation — it is the fix for a specific, repeated failure:

> *"not a missing permission, not an unclear config, not a tracker limitation. The instruction was
> present and the run simply never executed it."* — `SKILL.md`, on a run that moved labels correctly
> through the entire state machine and mirrored the board **zero times in a whole session**.

Prose cannot fix a run that does not execute prose. So the steps that are **mechanical and
verifiable** — label swaps, the board mirror and its read-back, claim adjudication, the renewal,
branch and worktree creation, PR reuse, the closing-keyword scan — are code. The steps that need
**judgement** — what is worth analysing, whether a blocker is discharged, whether a diff passes
review — are not, and stay in the prose where they belong.

```bash
python <skill>/scripts/github.py --repo-dir <repo> <operation> [options]
```

`<skill>` is the directory holding `SKILL.md`. The script reads the SAME operator configuration the
prose does (the block between the config markers in `SKILL.md`, overridden by `operator.local.md`),
so it never becomes a third source of truth. `python <skill>/scripts/github.py config` prints what it
resolved — run it once if you are unsure what the board or worktree row currently says.

**Exit codes carry the distinction `SKILL.md` calls *a failed read is not a failed answer*:**

| Exit | Meaning | What you do |
|---|---|---|
| `0` | the operation completed **and its read-back verified** | continue |
| `1` | **STOP** — a check answered stop (lost race, stand-down, wrong state, closed issue) | follow the JSON's `action`; do not retry |
| `2` | usage or configuration error; nothing was attempted | fix the invocation |
| `3` | the **READ itself** failed — the control surface answered NOTHING | fail closed: write nothing, retry the read. Never clearance, never a stand-down |
| `4` | internal error — a defect in the script | state is **UNKNOWN**; re-read the issue before doing anything |
| `5` | a **WRITE** failed | **re-read** to establish what landed, then decide. Do not retry blindly |

Exit `3` is the one to read carefully. Treating a timeout as a stand-down lets a flaky network halt
every run; treating it as clearance lets a run write deaf, which is the defect the renewal exists to
close. The script never collapses the two.

`5` exists because `3`'s advice is actively wrong for a write. "Nothing was learned, retry" is right
when a read failed; a write may have landed in the instant before the failure surfaced, so retrying
blindly duplicates it. `4` exists because without it an unhandled exception exits `1` with a
traceback and no JSON — indistinguishable from a deliberate STOP, with no `action` to follow.

**What is deliberately NOT scripted**: `merge`, `publish_version`, `close`, and the interpretation of
`review_status` / `ci_status`. Those write irreversibly to the remote or require a verdict, and a
defect in a script must not be able to merge, tag or close anything. The agent performs them itself,
by the prose further down, after verifying SHAs.

**Where `gh` cannot be installed** — a locked-down image, a sandbox with no package manager — the
REST API with a token does everything below at the cost of longer invocations. An analyst reaching
the API over HTTPS is exactly as capable as one running `gh`, which is the whole reason the analyst
role was defined as network-only. `gh` itself is a separate install
(`winget install GitHub.cli`, `brew install gh`, `apt install gh`); the installer deliberately makes
no network calls, so it checks and tells you rather than fetching it.

## What GitHub provides

The workflow asks a tracker for six things. What a binding must declare is not only which of them
exist, but what happens where they do not — a capability that is absent and undeclared is how a rule
silently stops applying.

| The workflow needs | GitHub | Consequence |
|---|---|---|
| A single-valued **state** per item | ✗ — labels are multi-valued | "exactly one `status:*`" is a **discipline, not a constraint**. Nothing stops two; every transition must remove the old label in the same call |
| A **claim** applied server-side | ⚠️ — assignees are a set, **but agents usually share one account** | then the set has one element however many runs claimed, and re-reading it can never show a collision — see *The claim hazard* |
| **Last activity**, timestamped by the server | ✓ — per-comment timestamps | only verified holder liveness markers renew a claim; unrelated `updatedAt` changes do not |
| A stable, short **identity** | ✓ — the issue number | usable verbatim in branch and worktree names |
| **Comments**, append-only and ordered | ✓ | heartbeats, blockers and hand-offs all land here |
| A native **priority** | ✗ | encoded as a label `<scale>:<value>`; the domain names the scale |

GitHub has a closed state of its own, independent of labels — but `done` is still carried as a
sixth label, and the two are set together. Relying on closed alone would leave the finished issue
wearing `status:review`, so every query for work awaiting verification would keep returning it, and
a board grouped by status would never move the card: closing an issue emits `issues.closed`, which
is not a label event and fires no mirror.

## The claim hazard — why the assignee cannot adjudicate

The obvious reading of GitHub is that assignees are a set, so two agents claiming leaves two
assignees and the collision is visible. **That is true only when the agents are different accounts,
and they usually are not.** Agents authenticate as one shared account, so `--add-assignee @me` twice
leaves exactly one assignee, and the re-read shows a clean issue assigned to you — while another run
is already building it.

So verification here does not read the assignee. It reads the **comment timeline**, which the server
orders and which no later writer can reorder. `claim` writes its comment first and then adjudicates
from that timeline; the earliest live claim wins.

This is the same mechanism the Linear binding needs, arrived at from the opposite direction — there
because the assignee is overwritten, here because it is shared. The lesson generalises further than
either: **an identity field cannot adjudicate a race between runs that share that identity.**

### Control markers

`SKILL.md` warns that "parsing prose for the same answer is fragile — any rewording breaks it", and
adjudication used to depend on exactly that. So every control comment the script writes carries a
machine-readable trailer alongside the sentence a human reads:

```
<!-- issue-flow: claim run-id=claude-code-60fabae1 horizon=2026-07-25T23:00Z -->
<!-- issue-flow: standdown run-id=claude-code-d7d8a22e -->
<!-- issue-flow: heartbeat run-id=claude-code-60fabae1 -->
<!-- issue-flow: reclaim run-id=claude-code-3f1a0b2c from=claude-code-60fabae1 -->
```

Two vocabularies read these, and they must agree about the same words. **Release** kinds
(`standdown`, `release`, `unassign`, `reclaim`) mean a run-id no longer holds the item, so its
earlier claim stops counting. **Control** kinds (`standdown`, `reclaim`, `adjudication`) mean a
message instructs a run-id to stop. Note that `reclaim` names the run it took over **from**, not the
run that wrote it — a marker's subject is not always its author.

They are HTML comments, so they are invisible in rendered markdown and a later run rewording the
surrounding prose cannot break them. Reading falls back to the prose forms (`Claimed by <run-id>`)
for comments written before markers existed, and that fallback is deliberately narrow: a control
message must both **name the run-id AND instruct**. A heartbeat that mentions your run-id in passing
("waiting on `<run-id>`'s measurement phase") instructs you to do nothing, and classifying it as a
stand-down would have you abandon work nobody asked you to drop.

Only `viewerDidAuthor=true` comments are control input. Reclaims must match the stale prior holder
unless marked `forced=true`; horizonless acquisitions expire four hours after trusted activity.

**Worked example — a real claim race, 2026-07-22.** The timeline, from the issue's comment trail:

| Time (UTC) | Event |
|---|---|
| 14:40:03 | `claude-code-cb8d3f2c` writes its claim comment |
| 14:40:08 | `claude-code-d7d8a22e` writes its claim comment, 5 s later, before the collision is visible |
| 14:40:41 | adjudication comment: the earliest claim wins, `claude-code-d7d8a22e` is told to stand down |
| 14:49–15:24 | `claude-code-d7d8a22e` posts heartbeats 1, 2 and 3 and keeps building — none of its writes re-read the timeline |
| 15:24:21 | the winner delivers |
| 15:28:52 | `claude-code-d7d8a22e` finally stands down, retracting a measurement taken on work it no longer held |

Under write-only heartbeats the stand-down sat unread for 48 minutes. The script makes that timeline
impossible: `heartbeat` runs the renewal **before** it posts, and refuses to post when the renewal
says stop — so the loser's next heartbeat at 14:49:22 exits `1` instead of writing. The renewal does
not prevent losing the race; it caps the cost of having lost it at one renewal interval.

## State names

The workflow's states are `analysis`, `ready`, `in-progress`, `review`, `blocked` and `done`. Here
each one is stored as the label **`status:<name>`** — the prefix is this binding's convention, not the
workflow's, and it exists so a glance at an issue separates state labels from attribution and domain
ones.

## Operations

`<n>` is the issue number throughout. `SCRIPT` abbreviates
`python <skill>/scripts/github.py --repo-dir <repo>`. Give each logical ownership write a fresh 32-hex `--operation-id`, then reuse it for every retry; durable Git refs make retries at-most-once.

| Operation | Command | What it guarantees beyond the obvious |
|---|---|---|
| `ensure_states` | `SCRIPT ensure-states` | idempotent; run it before your first write to an unfamiliar project |
| `create` | `SCRIPT create --identity <id> --title <t> --body-file <f> --priority <scale:value> --domain <name> --runtime <rt> --run-id <id> [--state ready\|blocked]` | creates every label **before** attaching it, then mirrors the initial board column — the case everyone forgets, because no `transition` ever follows a fresh issue to correct an empty `Status` |
| `list_state` | `SCRIPT list-state --state <s>` | unassigned only, `--limit 200` (the default cap is 30 and silently truncates the queue), partitioned by `domain:<name>`. It returns the raw labels and **refuses to rank across partitions** — ordering inside one needs the domain's scale contract, and manufacturing a global rank is forbidden |
| `claim` | `SCRIPT claim --issue <n> --run-id <id> --runtime <rt> --horizon <UTC> --operation-id <32-hex>` | renews an expired self-claim, waits boundedly for visibility, then proves the exact sole assignee and `dev:*` set; stale foreign contenders route through `reclaim` |
| `reclaim` | `SCRIPT reclaim --issue <n> --run-id <id> --runtime <rt> --operation-id <32-hex> [--horizon <UTC>] [--force --reason-file <f>]` | waits boundedly for its event, adjudicates the winner, and proves exact projections; holder heartbeats extend liveness by at most one four-hour window |
| `verify_claim` | `SCRIPT verify-claim --issue <n> --run-id <id> --expect-state <s> [--allow-closed-by-pr <pr>]` | proves the requested run is the reducer's current live winner and uses timeline position, not second-precision timestamps, as its control-message watermark |
| `transition` | `SCRIPT transition --issue <n> --to <s> [--from <s>]` | mirrors the board **first**, swaps the label in **one** call, then reads **both** back and repairs a board that disagrees. Omitting `--from` removes whatever stale state labels it finds |
| `comment` | `SCRIPT comment --issue <n> --body-file <f> [--run-id <id> --kind note\|blocker\|diagnosis]` | file-based body, always; `--run-id` and `--kind` are a pair. Every generic comment gets a non-control marker, and quoted issue-flow markers plus claim-shaped legacy prose are escaped, so generic text cannot become a control event or fall through to the prose parser |
| `heartbeat` | `SCRIPT heartbeat --issue <n> --run-id <id> --expect-state <s> --body-file <f>` | renewal first, post second; **refuses to post** when the renewal says stop and escapes control-shaped text before appending its own heartbeat marker |
| branch + worktree | `SCRIPT start-branch --issue <n> --branch <b> --base <base> --run-id <id>` | renews first, fetches before fallback discovery, resumes local or remote-only branch heads without moving them, and creates from the fetched base only when the branch is absent everywhere |
| `publish_review` | `SCRIPT publish-review --issue <n> --branch <b> --base <base> --run-id <id> --pr-title <t> --pr-body-file <f> [--worktree <p>]` | pushes, **reuses** the single open PR or creates one, refuses on more than one, scans for closing keywords, and records the PR URL with its exact head and base SHAs |
| `changelog-notes` | `SCRIPT changelog-notes --version <x.y.z> --file <changelog> [--out <f>]` | read-only. Extracts the version's entry for its tag and Release, anchored on the version **opening** the heading. Fails closed on a missing or empty entry — a tag is immutable, so notes invented at tag time are permanent |
| `check closing keywords` | `SCRIPT check-closing-keywords --issue <n>` | run again before merging: the branch's commit messages can introduce one after the body is already clean |
| `unassign` | `SCRIPT unassign --issue <n> --runtime <rt> --run-id <id> --operation-id <32-hex> [--held-by-other]` | binds retries to landed runtime provenance, waits boundedly for release visibility, then proves exact projections |
| board audit | `SCRIPT audit-board [--fix]` | compares every card's column against its own `status:*` label. **Zero cards is reported as a failed read, not a clean board** |
| `review_status` | *(agent, not scripted)* | `gh pr view <pr> --json headRefOid,baseRefOid,latestReviews,reviewDecision,reviewRequests,mergeStateStatus`. The independent verdict artifact MUST contain `Reviewer-Run: <run-id>`, `Reviewed-Head: <full-head-sha>` and `Reviewed-Base: <full-base-sha>`; re-read both SHAs after review and reject the verdict if either differs. The verdict is mandatory even when every runtime authenticates as the PR author and GitHub cannot supply a distinct native approval |
| `ci_status` | *(agent, not scripted)* | see *CI, merge and delivery* below |
| `merge` | *(agent, not scripted)* | see *CI, merge and delivery* below |
| `publish_version` | *(agent, not scripted)* | see *Version tags* below |
| `last_activity` | `gh issue view <n> --json comments` | only trusted heartbeat/branch/published markers authored by the holder extend liveness beyond acquisition; mentions and control targets do not |
| `close` | *(agent, not scripted)* | see *Closing* below |

### Why markdown bodies always go through a file

Inline `--body`/`--comment` corrupts markdown on a PowerShell runtime. Seen live: three separate
comments, from two different runtimes, posted with the backtick stripped or eaten entirely —
`` `floor_starvation` `` arrived as `\loor_starvation\` (the backtick-plus-`f` was consumed as
PowerShell's form-feed escape, taking the `f` with it), `` `status:blocked` `` arrived as
`\status:blocked\`, and every intended line break arrived as the two literal characters `\n`.
Backtick is PowerShell's escape character; a double-quoted `--body "<text with `code spans`\nand
newlines>"` gets expanded by the shell BEFORE `gh` ever sees it, and no amount of care in the text
itself prevents that — the corruption happens one layer below where the text is composed.

**The script removes this class rather than warning about it**: every subprocess call passes an
argument list with no shell, and every markdown body goes through a temp file. That is also why this
binding is a Python script and not a PowerShell/bash pair — writing the fix in the language that
causes the bug, twice, in two implementations that would then drift, is not a fix.

Where you still compose a `gh` call by hand (`close`, `merge`), write the body to a file and use
`--body-file`, with no exception for "this one's short."

## Auto-close, and why it does not end the work

GitHub can close an issue on merge without the workflow's `close` ever running: no transition to
`done`, no mirror, labels frozen wherever they were. The board then shows an open column for a
closed issue, and it was not any run's doing — it was the tracker's own automation acting outside
the workflow. Seen live: an issue closed by its delivery commit sat CLOSED wearing `status:ready`
until an audit caught it.

**Two different things cause it, and only one of them is a defect.** They produce the identical
symptom — a non-empty `closedByPullRequestsReferences` — so the symptom alone must never be read as
"somebody wrote a keyword":

| Cause | Status | Remedy |
|---|---|---|
| A **closing keyword** in the PR body or a commit message | forbidden — the text is yours | remove it, use `Refs #<n>`, re-check |
| A **branch link** created by `gh issue develop` | **expected under this binding** | none — no edit removes it |

Seen live (2026-07-26, issue #118 / PR #119): the PR body's first line read
`Refs #118 — a plain reference, deliberately NOT a closing keyword`, no commit message on the branch
carried a keyword either, and `closingIssuesReferences` still returned `[118]`. GitHub converts the
Development-sidebar link into a closing reference the moment a PR opens from that branch, and
empties `linkedBranches` in the same move. A check that blamed the prose sent the run to edit text
that never contained the offence.

**This binding accepts the branch-link close deliberately.** The native link is the most durable
join between an issue and its code, and `gh issue develop` is recommended here precisely because it
creates that link. Treating its consequence as a violation would make the recommended path
permanently un-shippable, and a gate that fires on every correct delivery is a gate that gets
ignored — at which point it also stops catching the keyword, which is the avoidable case that
actually matters.

**So the rule is not "prevent the auto-close". It is: `transition` to `done` after the merge
REGARDLESS of whether GitHub already closed the issue.** Closing an issue emits `issues.closed`,
which is not a label event: it moves neither the `status:*` label nor the board column. The
auto-close is the tracker's bookkeeping, never the workflow's `close`, and the state machine does
not know it happened. Run the transition afterwards and the end state is correct; skip it because
"the issue is already closed" and you have reproduced the exact incident above.

`check-closing-keywords` and `publish-review` report the cause, not just the symptom: a keyword is a
hard stop (exit `1`), a branch link is reported with the follow-up it mandates and does not block.

**Write this, so the wording is not improvised per PR.** Knowing the rule is demonstrably not enough
— `Fixes #<n>` is the muscle-memory opening of a PR body, and a run reaches for it while composing
the caveat that forbids it. Seen live (2026-07-25, PR #97): the first line read, verbatim,
`Fixes #61 (link only — closing keywords are not used per project workflow; state is moved via the
tracker)`. The parenthetical states the rule and the sentence breaks it. The safe form:

```
Refs #<n> — a plain reference, deliberately NOT a closing keyword.
```

The forbidden set is not just `fixes`: GitHub honours **`close`, `closes`, `closed`, `fix`, `fixes`,
`fixed`, `resolve`, `resolves`, `resolved`**, case-insensitively, in a PR body or any commit message
on the branch. `Refs`, `Implements`, `Part of` and a bare `#<n>` all link without closing.

**Then verify it mechanically, because reading your own prose is how it slipped through.**
`publish-review` runs the check as part of publishing and refuses to finish while a keyword is live;
`check-closing-keywords` runs it standalone. Run it again before merging — the branch's commit
messages can introduce one after the body is already clean. Editing the body does remove the link,
though the value can lag a few seconds, so re-run rather than trusting the first response.

**One consequence to state rather than rediscover.** A board's native *Linked pull requests* column
is populated by these references, so under `gh issue develop` it fills itself and under a
hand-created branch it stays empty. Either way, never add a keyword to populate it — that trades a
cosmetic gap for the one cause this section actually forbids. The durable joins are the timeline
cross-reference (`#<n>` in the PR body), the branch linkage, and the delivering SHA in the close
comment.

## Branch, worktree and the linked issue

`gh issue develop <n> --name <branch> --base <base>` creates the branch **server-side, from the fresh
base, already linked** in the issue's Development sidebar — one command replacing branch creation AND
recording. `start-branch` uses it, then fetches and resumes an existing local or remote-only branch
when it fails; only a branch absent everywhere starts from the fetched base. It records branch, base SHA, worktree
path and holding run-id on the issue either way. A branch nobody can find from the issue is work
nobody can follow.

The worktree path comes from the `Worktree location` configuration row, with `<repo>`, `<branch>`,
`<issue>` and `<run-id>` substituted; every component is flattened, so a `docs/113-…` branch does not
create a stray `docs/` directory under the worktree root.

**What the path must guarantee is that no two live runs share a directory** — not that it contains
any particular token. Two templates achieve that differently, and the choice is a real trade:

| Template | Collision is prevented by | Cost |
|---|---|---|
| `…/<branch>-<run-id>` | construction — no two runs ever compute the same path | one orphan directory per run that dies; they accumulate |
| `…/<branch>` | git itself — `worktree add` refuses a branch already checked out elsewhere (`fatal: '<branch>' is already used by worktree at …`) | needs the resume check below to be correct |

The second is safe only because the branch carries the issue number, so two runs on one issue compute
the same BRANCH and git blocks the second checkout. It was NOT safe in the original convention, where
the path was derived from the issue while the branch varied — that is how, on 2026-07-24, two runs
derived the same directory and the loser wrote its model, its migration and its tests into the
winner's checkout mid-build.

**So `start-branch` asks whether the directory is YOURS, not whether it exists.** A registered
worktree for this exact branch is a resume: it is reused and reported as `resumed_existing_worktree`.
Anything else — a stranger's checkout, or an orphan left by a dead run — is refused, because writing
into either is the failure above. Merely refusing every existing path would make resume impossible
under a run-id-free template while protecting against nothing git had not already caught.

**A fresh worktree does not have the files git never tracked.** Everything gitignored — environment
files, secrets, credentials, local settings — is simply absent, and the failure it produces is
confusing rather than obvious: the tool starts normally and then dies on a variable it has never had
trouble with, in a tree that looks identical to the one that works. The script says so in its output;
copying them across is still yours to do.

## `transition` — and why the read-back covers the board

**Add and remove in the same invocation.** Two calls leave a window in which the issue carries two
states, and any run reading the board during that window sees an ambiguous item. Then re-read: a
two-state item poisons every query that touches either state, and it has happened in live use.

The label half has always been verified. **The board half is verified by the same read**, because it
is the half with no other feedback loop — a wrong label is caught by the very next `list_state`, a
wrong claim by the next `verify_claim`, but a column nobody looks at simply stays wrong forever, and
the run that skipped it sees nothing. `transition` reads both, and where they disagree it re-sets the
column with the ids it already resolved and reads again; a mirror that still will not land exits `1`
rather than reporting success.

Matching a state to a column is by option **name OR description**, not description alone. Real boards
do not label that last column consistently: one observed board describes `Analysis`…`Blocked` with
their exact `status:*` labels and then describes `Done` as `closed`, because that column also tracks
the tracker's own closed flag. A verifier demanding `status:done` there would fail on the one
transition that matters most and send a run chasing a mirror that had worked.

## Setup

`SCRIPT ensure-states` creates the six state labels. `gh` refuses to attach a label that does not
exist, and the error arrives at issue creation — the analyst's last step, after all the analysis is
done. That is why `create` creates every label first and attaches second: the domain names its own
priority scale and rule book, so no setup script can have created them in advance. Label creation is
idempotent, so it costs one call and removes the failure mode entirely.

Attribution labels are created on demand by `claim` and `create`, so the set stays exactly as wide as
the runtimes actually in use. Labels are what make attribution *queryable*:
`gh issue list --label "dev:codex"` answers "what is that runtime holding right now" in one call,
where parsing prose for the same answer breaks on any rewording.

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

## Credentials

`gh auth status` may report a token stored in a system keyring that a sandboxed runtime cannot read;
there, use `GH_TOKEN` instead. **Verify with `gh issue list` before relying on the workflow** — an
analyst that cannot file its issue has done the work and lost it.

`gh issue` needs no `project` scope. The board mirroring below does — prove it once with
`gh project list --owner <owner>`, or just run `SCRIPT config` and then any `transition`, which
reports a missing scope as a skip rather than failing.

---

## Keeping a board in sync

**A GitHub Project (v2) board's `Status` field does not follow your labels.** Nothing connects the
two: project items are references, so title, state, labels and assignees are always live, but a
custom field lives on the project item and no label touches it. Left alone, a board shows whatever
someone set by hand the day they set it.

**Whether to mirror at all is read from the configuration, not guessed.** The `Project board` row
names `owner/number` or `none`. `none` means no board anywhere and every board step becomes a no-op.

**The mirror is part of `transition` and it runs FIRST**, before the label edit, best-effort and
without pre-checking board membership — an issue not on the board is a quiet skip, not a
precondition. The fragile, easily-skipped half runs before anything can short-circuit it; the
reliable one-call label edit follows. This trades one failure for a rarer one: the label stays the
store queries read, so a run that dies between the board write and the label edit leaves the board
ahead of a still-stale label — far less likely than the board drift that board-last invited, which is
the incident this order exists to prevent.

### Drift a previous run left behind

**The mirror only fires on a `transition` you make — it does not repair drift from before you got
there.** Drift from your OWN transitions is now caught inside the operation by its read-back. What
remains uncovered is what an earlier run left, and there is no daemon reconciling the two by design
(see *Why a mirror is needed at all*), which means staleness is permanent unless somebody looks.

Seen live: five items sitting on `Ready` days after their labels had moved to `in-progress` or
`done`, because whatever run transitioned them either predates this section or hit the missing-scope
fallback — and nothing since then ever looked back.

`SCRIPT audit-board` is that look: one paginated pass comparing every card's column against its own
`status:*` label, `--fix` to repair what it finds. On 2026-07-25 that pass over 83 cards found
exactly the two the run itself had left stale, which is also how you learn the problem was yours and
not systemic. **A zero-card result is reported as a failed read, not a clean board** — a configured
board is never empty, and reporting an empty read as a pass would reproduce the exact failure this
whole file exists to remove.

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
That reshaping is the one board step no script performs, because it is the one a human clicks.

### Quiet failure modes the script already handles

Worth knowing, because they are invisible when they happen and you will meet them on another board:

- For an **organisation-owned** project, `user(login:)` returns null rather than erroring, so a
  mirror written against it silently never fires. The script tries `user` then `organization`.
- `items(first:100)` stops finding issues once the board passes a hundred items. Every board query
  in the script paginates.
- Board field and option ids are resolved **once and cached for a day**. Per-transition discovery is
  the overhead that tempts a run to skip the mirror, so the cache exists to remove the temptation,
  not to save API calls. `--no-cache` re-resolves.

**If your agents cannot hold the `project` scope**, the same mirroring can run server-side in a
repository Action on `issues.labeled`/`unlabeled` — it needs a PAT stored as a secret, because the
automatic `GITHUB_TOKEN` cannot write Projects v2. One or the other, never both: two mirrors is two
things to debug when the board lags.

**Whatever you do, the labels stay authoritative.** They are what agents read and write; the board
is a view. Invert that and every agent needs the `project` scope and the workflow's transport has to
be rewritten — a board that is state costs a redesign, a board that is a view costs nothing.

**GitHub now renders agent activity of its own**, and it is a third view, not a second state. When a
coding agent is assigned to an issue, its session shows under the assignee with its own live status —
queued, working, waiting for review, completed. That reports what a runtime said about itself, not
what the state machine says, and the two legitimately disagree: a session can read as *completed*
while its issue is correctly still `status:in-progress`, because the run ended and the work did not.
Same rule as the board — read it, do not trust it, and never move a label to make it agree.

---

## CI, merge and delivery — the agent's half

These are not scripted. Each one either writes irreversibly to the remote or turns evidence into a
verdict, and both are decisions a script must not make on an agent's behalf.

**`ci_status`.** Capture `headRefOid` and `baseRefOid`; run `gh pr checks <pr> --watch --fail-fast`,
then separately read `gh pr checks <pr> --json name,workflow,state,bucket` (`--watch` and `--json` are
separate invocations). Build the expected set as the union of host-required names and every
applicable repository-required lane. For the host set, `gh pr checks <pr> --required --json name`
returning exactly `no required checks reported on the '<branch>' branch` means the empty set; every
other command error fails closed. When repository policy names workflow job ids rather than visible
checks, resolve each id through `jobs.<id>.name` in the workflow file at the captured head SHA before
comparing; never compare ids directly with `gh pr checks.name`. Each expected visible name must be
present exactly once with `bucket=pass`; `skipping`, `cancel`, a missing name or an unexpected
duplicate is not green. Re-read both SHAs afterwards; if either changed, discard review and CI.

**`merge`.** Require `reviewed head/base == CI head/base == current headRefOid/baseRefOid`, then run
`gh pr merge <pr> --merge --match-head-commit <head-sha>`. Never use `--admin` or `--delete-branch`:
deletion can fail after a successful API merge in a multi-worktree checkout and make the result look
retryable. If a merge queue owns delivery, first prove its configured method preserves merge commits
or stop. Poll `gh pr view <pr> --json state,mergedAt,mergeCommit` until merged and take
`.mergeCommit.oid` as the delivered SHA; fetch it and verify it has exactly two parents in order:
reviewed base, then reviewed head. A mismatch means the base raced or the host used another strategy:
leave the issue in `review` and invoke the repository's fix-forward/revert policy. If
exact-delivered-SHA CI is required, locate every required workflow with
`gh run list --commit <merge-sha> --workflow <workflow> --json databaseId,headSha,status,conclusion`,
wait with `gh run watch <id> --exit-status`, then verify
`gh run view <id> --json headSha,status,conclusion,jobs`: the head must equal `<merge-sha>`, the run
and every applicable required job must be `completed/success`, never `skipped`.

**Run `SCRIPT verify-claim` again immediately before merging, before publishing tags, and before
closing.** Merge, version publication and close are separate irreversible boundaries, and a long
quiet phase between them cannot be allowed to bypass the renewal.

### Version tags

**`publish_version`.** After every required delivered-SHA gate, renew the claim BEFORE the first
publication write, then detect each component whose declared version changed from `<old>` to `<new>`.
Enumerate `git ls-remote --tags origin` — never local tags — to derive the component's established
naming convention and previous component tag. Require exactly one convention; if history is empty,
require an unambiguous single-product/component classification before using `v<new>` or
`<component>/v<new>`.

**A tag carries what a human wrote about the version.** Before creating it, extract the version's
changelog entry:

```bash
SCRIPT changelog-notes --version <new> --file <component-changelog> --out <notes-file>
```

It **fails closed** when the version has no entry, or has a heading with nothing under it. That is
not an obstacle to route around: the entry is part of what "delivered" means, and a tag is immutable,
so notes improvised at tag time are permanent. Write the entry first.

The extraction anchors on the version OPENING the heading, which matters more than it sounds. Seen
live: an entry headed `### 2026-07-25 — (sin bump de versión) … (sigue en v6.9.8)` — whose entire
point is that 6.9.8 did *not* ship in it — was matched for 6.9.8 ahead of the genuine entry by a
looser pattern. That would have produced a tag whose notes describe a different change and disclaim
the version they are named after.

For `<tag>`, inspect both local and remote direct plus peeled refs. A valid annotated tag has a tag
object and a `refs/tags/<tag>^{}` target equal to `<merge-sha>`; a lightweight tag, another target or
ambiguous state blocks without rewriting anything. If the remote tag is absent, reuse a matching
annotated local tag or create it with `git tag -a "<tag>" -F "<notes-file>" "<merge-sha>"`, then
attempt `git push origin "refs/tags/<tag>"`. After ANY push result — success, rejection or timeout —
re-read the remote: a conclusive peeled target equal to `<merge-sha>` is idempotent success, another
target is conflict, and no conclusive read fails closed.

When GitHub Releases are required, first reverify the remote peeled target, then query
`gh release view "<tag>" --json tagName,isDraft,isPrerelease`. A matching non-draft Release with the
SemVer-derived prerelease flag is idempotent success; incompatible metadata blocks. Only a confirmed
not-found result permits `gh release create "<tag>" --verify-tag --notes-file "<notes-file>"` — the
same notes the tag carries (add `--prerelease` for a prerelease).

**Do not use `--generate-notes`.** It substitutes a list of commit subjects for the entry a human
wrote, which reads like documentation without being any — and it does so silently, so a component
whose changelog was never updated still gets a Release that looks complete. The `changelog-notes`
failure is the signal you want there. After ANY create result, re-read the Release JSON and the remote peeled
tag; both must match the expected metadata and `<merge-sha>`. Auth, network or API ambiguity fails
closed. Tag or Release failure leaves the issue in `review` even though the merge already exists.

A Git tag and a GitHub Release are different artifacts: the tag is mandatory; the Release is created
only when the repository already publishes them for that component. Never move, overwrite or
force-push an existing version tag — a tag that already points elsewhere is a delivery blocker, not
permission to rewrite release history.

### Closing

`close` moves the state to `done` **first** — `SCRIPT transition --issue <n> --to done` — then posts
the closing note with `gh issue comment <n> --body-file <file>`, then a bare `gh issue close <n>`.
`gh issue close` has NO file variant (`-c/--comment` is inline-only, confirmed against
`gh issue close --help`), so the note goes through the file-based comment path and the close carries
no body at all.

**Run the transition even when GitHub already closed the issue.** Under `gh issue develop` the merge
auto-closes it (see *Auto-close, and why it does not end the work*), and the temptation is to skip
the transition because the issue is already closed. That is precisely the failure: the auto-close
moved neither the label nor the board, so skipping leaves a CLOSED issue wearing `status:review` and
a card parked in the wrong column. `transition` is idempotent, so running it against an
already-closed issue costs one call. The final `gh issue close` is then a no-op and may report the
issue as already closed — that is success, not an error.

Two calls means a partial-failure case: if the comment lands but the close errors, the issue is left
open with its closing note already posted — retry only the bare `gh issue close <n>`, never re-post
the note.

Carry your run identity in the closing comment and state what was actually verified: review verdict,
CI run or checks, measured numbers, tests run, PR, **the delivering commit SHA**, and every version
tag or Release published. Never write just "done"; the state already says that, and the comment
exists to show what earned it.

**A shared GitHub account cannot manufacture independent approval.** The workflow's reviewer is a
fresh reasoning context, but GitHub sees the authenticated account, not that context. When the same
account authored the PR, record the reviewer verdict and evidence in the PR or issue with
`Reviewer-Run: <run-id>`, `Reviewed-Head: <full-head-sha>` and `Reviewed-Base: <full-base-sha>`; do
not claim a native `APPROVED` review that GitHub refused. Re-read both PR SHAs after the verdict —
matching prose without that race check is still stale evidence. This comment proves the workflow
review only; if repository protection requires a native approval, it does NOT substitute for one and
delivery stays blocked until a distinct identity supplies it.
