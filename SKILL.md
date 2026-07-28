---
name: issue-flow
description: "Trigger: issue-flow analyst [conditions], issue-flow dev, analyst, developer, claim issue, move task states. Analysts file at most one evidenced issue; without conditions, audit the repository critically."
license: GPL-2.0
metadata:
  author: asanabrial
  version: "1.12.0"
---

# Issue Flow — Analyst / Dev over a shared issue tracker

## Activation Contract

Use for `issue-flow analyst [conditions]`, `issue-flow dev [domain-rules] [issue-number]`, analyst or developer work, claims, and workflow-state transitions.
Analyst conditions and dev domains are optional: a bounded target is the scope; otherwise the analyst audits the repository and the dev implements the selected issue.
Read repository instructions first and load only the tracker binding selected by operator configuration before tracker work.
Load [domain composition](references/domain-composition.md) for handoff, priority, or routing rules; [runtime notes](references/runtime-notes.md) for invocation, delegation, or team mechanics; and [safety incidents](references/safety-incidents.md) for race, reclaim, handoff, or failure rationale.

## Hard Rules

- Issue Flow owns roles, states, claims, and transitions; routed domains own value, priority, evidence, and done criteria. Neither side invents the other's policy.
- An analyst, including an adversarial reviewer, is read-only for the repository and every external system except the configured tracker operations required below: it creates no branch/worktree/commit/PR and runs no migration, sync, database write, or delivery action. It files at most one evidenced issue. Review MUST use a context that did not write the change.
- Mint one `<runtime>-<session-prefix>` run-id and reuse it for every write. Keep bounded runtime attribution in `analyst:<runtime>`/`dev:<runtime>` labels and unbounded run-id attribution in text; analyst labels persist, while dev labels are removed on release but retained on delivery.
- Resume this run's held issue before consulting queues. Use the selected binding's timeline-adjudicated claim; the earliest live claim wins. Run `verify_claim` before the first repository write, every heartbeat, and every expensive or irreversible boundary; an unreadable control surface permits no write.
- Keep exactly one of `analysis`, `ready`, `in-progress`, `review`, `blocked`, or `done`. The binding's workflow state is authoritative; every configured projection is updated and read back, and `done` is explicit before tracker closure.
- The selected binding MUST map `ensure_states`, `create`, `list_state`, `claim`, `reclaim`, `verify_claim`, `heartbeat`, `transition`, `comment`, `last_activity`, `label`, `unassign`, `publish_version`, and `close`. Run executable reversible operations instead of reconstructing them; bindings MUST declare unsupported capabilities and fail closed.
- Issue acceptance criteria, routed domain rules, and repository rules define done. Never invent missing gates or reinterpret the issue silently.
- Aim for no more than 800 changed lines per pull request, but treat size as reviewability guidance rather than a gate. A larger coherent change is valid when splitting would reduce safety or review quality; the developer records why before review and the independent reviewer assesses that rationale without relaxing any quality gate.

## Decision Gates

| Situation | Action |
|---|---|
| Analyst has conditions or a bounded diff/PR/issue | Analyse only that scope under applicable repository and domain rules. |
| Analyst has no conditions or domain | Use the autonomous `general` contract in [domain composition](references/domain-composition.md); file only the strongest deduplicated finding or nothing. |
| A domain route is recorded | Analyst loads the left rule book; dev loads the right. State explicitly when implementation routing is absent. |
| Runtime mechanics or independent-context acquisition vary | Follow [runtime notes](references/runtime-notes.md); runtime limits change the mechanism, never the invariant. |
| Claim, reclaim, or handoff safety needs rationale | Follow the active binding and [safety incidents](references/safety-incidents.md), without copying incident narrative here. |
| State must be chosen | Invalid specification -> `analysis`; implementable and unassigned -> `ready`; claimed build -> `in-progress`; built/published -> `review`; unbuilt external wait -> `blocked` with condition and discharger; merged and verified -> `done`. |

## Execution Steps

Before either role's first tracker write in an unfamiliar project, run the selected binding's `ensure_states`; then use that binding's executable operations and readbacks rather than reconstructing them.
Load [domain composition](references/domain-composition.md) for routing, scale validation, queue partitioning, or autonomous fallback; [runtime notes](references/runtime-notes.md) for invocation, read-only enforcement, independent-context acquisition, or runtime limits; [safety incidents](references/safety-incidents.md) only when claim, reclaim, handoff, or failure rationale is needed; and [repository delivery](references/repository-delivery.md) when repository rules do not fully define isolation, integration, or review-unit sizing.

### Analyst

1. Keep the repository and non-tracker external systems read-only. Drain `analysis` oldest first, then inspect `blocked`: move discharged conditions to `ready` with evidence, date conditions that still hold, and name how long a person has owed any pending decision.
2. Analyse only the bounded scope under the left-hand domain rule book. With no conditions or domain, use the autonomous `general` contract; verify evidence and duplicates, then keep at most the strongest defensible finding or report that none survived.
3. Validate the fields, route, scale, and body contract for queued and new findings. For queued work, add missing handoff material with `comment` and transition only that same complete item; never create a replacement. For a new finding, fill the [analyst issue template](assets/analyst-issue-template.md) and `create` it with every marker plus `analyst:<runtime>` and run-id attribution. Use `ready`, or `blocked` with the exact missing condition and discharger; then STOP without implementing or assigning it.

### What the analyst produces

An issue a developer can pick up **without redoing the analysis**. If the dev has to re-derive your
reasoning, the analysis bought nothing.

```markdown
## Description for dumb humans

> [!NOTE]
> One or two sentences, plain language, no jargon, no file paths, no metrics — what this issue is
> about, for someone who will never read past this line. Written in whatever language the operator
> configuration names for this installation (see the table at the end of this file). The header
> itself is the fixed, literal title above — always in English, always that exact phrase, never
> reworded per issue. `> [!NOTE]` is GitHub's native alert syntax — it renders as a bordered,
> coloured callout, which is the point: this has to be visually impossible to miss on an issue
> otherwise full of technical prose.

This is the first thing on the issue, before `## Problem`.

## Problem
What is wrong or missing, and how you know. Evidence, not assertion:
file:line references, measured numbers, logs, reproduction steps.

## Why it matters
Impact if left alone. If you cannot state one, say so plainly — a documented
"low impact, filed for completeness" is honest and lets the dev deprioritise.

## Proposed approach
The design. Alternatives you considered and why you rejected them.
If you are NOT confident, say which part is uncertain and what would settle it.

## Acceptance criteria
Checkable statements. "Faster" is not a criterion; "p95 under 200 ms measured
by X" is. Include what must NOT change (invariants, byte-identical outputs).

## Out of scope
What a dev should explicitly not do here. This prevents scope creep more
reliably than any amount of prose in the sections above.

## Evidence
Commands run, files read, numbers measured. Enough that a reviewer can
re-check the analysis without repeating it.
```

### Developer

1. Resume this run's held issue at its recorded state before consulting queues. Otherwise drain unassigned `review` before `ready`, rechecking the delivery blocker before claiming published work.
2. For `ready`, partition valid candidates by stable domain and declared scale, rank only within each partition, and choose the oldest partition head; a selected domain limits selection to its highest-ranked oldest item. Use oldest-first only when no candidate has a valid scale contract.
3. `claim` with the run-id and horizon, then adjudicate the server-timestamped timeline; the earliest live claim wins, and a loser comments, releases, and selects again. Keep claimed review work in `review` unless a code change requires `in-progress` and a fresh publish/review/CI cycle; for ready work, transition once to `in-progress` with `dev:<runtime>` and no second claim event.
4. Load the route's right-hand implementation rule book before planning; if absent, state that none applies and build to written acceptance criteria. Follow repository rules, use the repository-delivery fallback for missing isolation, integration, or review-unit sizing policy, verify the claim immediately before the first branch/worktree/file write, and implement in the isolated checkout.
5. Renew before publishing, publish or reuse the shared review target, then transition to `review`. Acquire an independent context only when `Review delegation` authorises this run; otherwise request a separately started review. Require its verdict and green CI on the same exact head/base; every push invalidates both. Leave blocked delivery in `review`, restore local state changed by the failed attempt, record the precise blocker, and unassign rather than bypassing it; conflicting repository rules get a separate issue.
6. Renew at each irreversible boundary; merge only when reviewed, green, and current head/base identities match. For a merge-commit route, require the delivered SHA's exact parents to be the reviewed base and head; other configured strategies require their binding-declared equivalent topology and any delivered-SHA gate. For each changed version, publish and remotely verify its immutable annotated tag and required Release; renew again, transition to `done`, record review/CI/tests/measurements/PR/delivering SHA/publications with the run-id, and close. On handoff, preserve all evidence: wrong specification goes to `analysis`, unbuilt external wait to `blocked`, built-undeliverable work to `review`, and a useful partial diagnosis to `ready`.

#### Detailed review gate retained until the final retirement slice

6. **Get the published change reviewed by a context that did not write it, then require green CI on
   the latest PR head.** Capture the head and base SHAs before review and require the verdict artifact
   to name both explicitly; a floating "approved" is not evidence for any particular revision or
   diff. Where the
   configuration authorises it, obtain that context yourself — a
   sub-agent or teammate; where it does not, hand the PR to a separately started analyst run and say
   on the issue that you are waiting for it. What the configuration decides is who starts the
   review, never whether it runs. Every push invalidates the prior verdict and CI result: re-review
   the changed surface and wait for the required checks again.

   If review, CI or delivery is blocked, STOP THERE and leave it in `review`. Work that is built and
   published but cannot be shipped — a reviewer requests changes, a check fails, required native
   approval cannot be supplied by a distinct identity, a permission is
   missing, two project rules contradict each other — is *finished work awaiting delivery*, which
   is exactly what `review` means. Do not move it to `done`: that state says delivered. Do not work
   around the blocker either; a rule you bypass to ship is a rule that stops meaning anything.

   Comment the blocker precisely — what refused it, what that thing expects, what the project
   requires instead — then unassign so another actor can complete the delivery. Restore any local
   state you changed trying.

   **A blocker that comes from two project rules contradicting each other deserves its own issue.**
   It will hit the next task, and the one after that, and each run will re-diagnose it from scratch.
   One filed finding turns a recurring tax into a decision someone can make once.
7. **Merge only the reviewed PR head after its required CI is green, publish any version tags, then
   `close`.** Renew the claim
   immediately before merging, require `reviewed head/base == CI head/base == current head/base`,
   bind the merge to the reviewed head SHA, and refuse if either side moved. Use the configured merge strategy; a `merge commit`
   preserves the branch boundary and PR ancestry, while squash and rebase deliberately discard that
   information. Retrieve the delivered SHA after merge and verify it has exactly the reviewed base
   and reviewed head as its parents; do not claim topology preservation from a command flag alone.
   A merge queue is eligible only when its configured method guarantees that same topology. If the
   project requires CI on the exact delivered SHA, wait for that post-merge gate.

### Working in a repository — the default flow

**The repository's rules win, always.** Read its agent instructions (`AGENTS.md`, `CLAUDE.md` or
equivalent) before anything else and follow them. What follows is the fallback for a repo that
prescribes nothing, and a floor under one that prescribes only part of it — never a competing
standard.

The older fallback — *use the commit history as the convention* — does not stretch this far, and it
is worth saying why. A git log tells you how commit messages are written. It cannot tell you whether
two agents may share a checkout, or whether a branch verified last hour is still valid after someone
else merged. Those answers decide whether parallel work corrupts itself, so this skill supplies
them rather than leaving them to a domain that has no reason to know.

**1. Never work in the base tree.** For a dev, the default branch is read-only: no edits, no commits.
Check before the first change instead of assuming:

```bash
git branch --show-current   # main / master -> stop, branch first
```

If you find you already committed to the base branch and have NOT pushed, move the commits to a
branch and reset. **If you already pushed, do not force-push** — revert and redo it properly.
Force-pushing a shared base is how another agent's checkout silently diverges from history it has
already built on.

**2. One issue, one branch, one isolated checkout.** Branch from the freshly fetched remote base, not
from a local copy that may be days stale, and give each dev its own worktree so that a half-written
tree is never visible to anyone else:

```bash
git fetch origin
git branch <branch> origin/<base>
git worktree add <path> <branch>
```

**Where the worktree goes is not a free choice, and one repository is not the unit to think about.**
Two constraints bind it. It must live **outside the working tree** — git allows a worktree inside the
repository and it then pollutes status and ignores for everyone. And its path must be **unique per
repository**, not merely per branch: a shared parent like `<somewhere>/worktrees/<branch>` collides
the moment two repositories both have a `fix/login`, and the second `worktree add` fails with a
message about a path that already exists, naming neither repository. Put the repository's name in the
path. Some tooling adds a third constraint — a per-worktree index or cache that must not be shared —
so check what your setup expects before settling on a pattern.

**Record the branch on the issue the moment it exists** — natively where the tracker supports it,
as a comment where it does not; the binding says which. A branch nobody can find from the issue is
work nobody can follow, and the board's whole value is that following work never requires guessing.

**Name the BRANCH after the issue; make the WORKTREE PATH unique per RUN.** The issue number is what
joins the code back to the board without anyone parsing prose, and it survives a run whose
attribution nobody can explain any more — so the branch carries it, and the branch is what the PR,
the development sidebar and the merge all key on.

The worktree path is a different question with a different answer. It is a *local directory* joined
to nothing, and deriving it purely from the issue means two runs that both want issue 58 compute the
same path and share one checkout. Git will not save you: `worktree add` refuses a branch already
checked out elsewhere, but **nothing refuses a second process writing into a directory that already
exists**. So put the run-id in the path — `<root>/<repo>/<issue>-<slug>-<run-id>` — and the collision
becomes impossible instead of merely unlikely. The cost is one stale directory when a run dies; the
cost of the shared path is two agents editing one tree with no record anywhere that it happened
(seen live on #58, 2026-07-24: model, migration and test files appearing in a checkout their author
had created clean minutes earlier).

**A fresh worktree does not have the files git never tracked.** Everything gitignored — environment
files, secrets, credentials, local settings — is simply absent, and the failure it produces is
confusing rather than obvious: the tool starts normally and then dies on a variable it has never had
trouble with, in a tree that looks identical to the one that works. Copy across whatever the project
needs before running anything. Reuse the interpreter or dependency tree the main checkout already
has, with the worktree as the working directory, instead of building a second one — per-worktree
installs are slow, drift apart, and are the reason a test can pass in one tree and fail in the other.

**Isolation is verified on both sides, exactly like a claim.** Whoever creates the worktree creates
it; the dev that starts working confirms it is in one before touching a file. Skipping that check and
working in the shared tree clobbers whoever else is building — and unlike two devs on one issue,
**nothing on the board records it**. That is precisely why this rule cannot be left to a domain.

**Verify it again the moment the tree surprises you.** A file you did not write, a modification to a
file you never touched, a tool reporting that something changed under you — inside a worktree that is
supposed to be yours alone, every one of those means another process is in your directory. Stop
writing and run `verify_claim`; the timeline, not the filesystem, decides who continues:

- **You are not the earliest claimant** → stand down. Leave what is there, change no state, and say
  on the issue what you saw and where your own work is, so the holder can pick it up.
- **You are** → say so on the issue naming the other run, and **adopt what it left rather than
  deleting it**. Its work is real even though the coordination failed, and two runs converging on one
  issue usually converge on a similar design.

What is never right is editing around the intruder. That produces one tree holding two designs and a
build that belongs to nobody — strictly worse than either run's work alone.

**3. Integrate the base before publishing the PR, then verify again.** Your branch was verified
against the base as it stood when you started; every merge landed since then ages that result. Bring
the base in, resolve honestly, and **re-run the verification**:

```bash
git fetch origin
git merge origin/<base>     # inside your worktree
```

Resolving a conflict is a code change. An unverified resolution is an untested commit with better
manners, and picking one side wholesale to make the markers go away is how a merge quietly deletes
someone else's fix.

**Merge, do not rebase — and the default is this way round for reasons, not taste.** Rebase buys a
tidier history by rewriting every commit SHA, which costs two things that matter more here. Anything
bound to those SHAs is silently invalidated: a recorded review result, a signed gate, a pinned
deployment, a CI run recorded against the commit. And rebasing a branch that is already pushed forces a
force-push, on a branch another agent may have fetched and built on — the one operation this flow
tells you never to perform on the base, applied to work someone else is holding.

Rebase only where the repository asks for it and nothing points at the SHAs. A repo rule outranks
this default like every other.

**4. Preserve the reviewed history through the configured delivery route.** For a pull-request
route, push the branch, open the PR, and bind review and required CI to its latest head SHA. Merge
with a merge commit when preservation of merge information is configured: the merge commit records
the branch boundary and the PR retains the review and CI evidence. Never squash or rebase in that
mode; both replace the reviewed topology with a different history.

The merge commit itself is a new SHA, so distinguish two gates instead of pretending otherwise:
pre-merge CI proves the PR head (and, where the host provides it, its merge candidate); a repository
that requires the exact delivered SHA must also run its post-merge gate before the issue closes.
For both gates, the expected set is the union of host-required checks and every applicable lane the
repository names; a missing or skipped expected lane is not green. If the base or PR head moves
before merge, return to step 3, re-verify, re-review the changed surface, and re-run required CI.
Never bypass the race with an administrative override.

**Whether you may land it at all is a project decision, not yours.** Some repositories require a
human decision; some pre-authorise delivery after review and CI. **Default to asking** unless the
repository or configuration says otherwise. A pull request is a configured delivery route, not a
silent workaround for a refused direct push.

**5. Clean up what you created.** Remove the worktree once the work is delivered or abandoned. An
orphaned worktree keeps its branch checked out, and the next run that tries to use that branch gets a
refusal it did not cause and cannot explain.

**Where the repo says otherwise, do it their way and say so on the issue.** And if the repo's rules
make isolation impossible while another dev is active, that is not a rule to bypass — it is a blocker
to file, exactly as in step 6.

### Abandoned work — the run that never came back

A run dies. The process is killed, the sandbox expires, the budget runs out mid-task. The issue stays
`in-progress` with an assignee, and selection never surfaces it — step 1 drains `review` and
`ready`, never `in-progress` — so **nobody ever picks it up again**. This is not an exotic failure — it is the ordinary end of
a long autonomous run, and it is the one way this board rots without anyone doing anything wrong.

**The same death can land one step earlier, before the state ever moved.** Step 2 (claim: assignee +
comment) and step 3 (`transition` to `in-progress`) are two separate calls — a run that dies between
them leaves an issue with an assignee and a claim comment that still carries `status:ready`, because
the label swap never ran. The same gap exists at the other end: a run that dies after step 4 (the work
is done) but before step 5's `transition` to `review` leaves finished, working code sitting on an issue
that still says `in-progress`. Seen live: five issues claimed and commented, none ever relabeled — the
dead runs' horizons had long passed, but every one of them still read `status:ready` to anyone
scanning labels. In every case `list_state` still filters correctly (`no:assignee` excludes it from its
queue), but the label lies to anyone reading it directly, and — unless the rule below is read for what
it actually covers — the issue looks neither claimed nor reclaimable.

Worth knowing before you assume you are missing something obvious: **nothing else solves it either.**
Claude Code's agent teams document the same failure — *"teammates sometimes fail to mark tasks as
completed, which blocks dependent tasks"* — and answer it by asking a human to fix the status by
hand. The closest GitHub-native skill set preserves worktrees and resumes by session id, but has no
rule for work whose holder is simply gone. So what follows is convention over data the tracker
already timestamps: no daemon, no lock service, nothing to run.

**The holder leaves a trail.** Two habits make a claim auditable:

- **Declare a horizon when you claim** (step 2, inside the claim comment). One extra clause in a comment you already write, and
  it turns "no activity" from a judgement call into a comparison.
- **Post progress through the binding's heartbeat operation, not a generic note.** From outside, a
  quiet dev and a dead one look identical; the verified heartbeat carries the server timestamp that
  extends liveness. A note, blocker or diagnosis records context but cannot renew ownership.

**A heartbeat is a claim renewal: read before you write.** The claim check in step 2 runs once, but
the control surface it read stays authoritative for the whole build — a late adjudication, a reclaim,
a stand-down all arrive there while you work. A heartbeat that only writes is deaf to the one channel
that can revoke the claim. This happened live: a run that had lost a claim race by five seconds
was told so in an adjudication comment 33 seconds later, and then posted three more
heartbeats and worked another ~48 minutes because nothing in its heartbeat loop ever read the
timeline again. So **every heartbeat runs `verify_claim` first and is written only when the answer is
"still holder, nothing said"**. The same renewal runs immediately before any expensive or
irreversible boundary that can fall between heartbeats — starting an evaluation, launching a review,
delivering — so a long quiet phase cannot bypass it. Two answers mean stop, not retry:

- **A control message naming your run-id** — a stand-down, a reclaim, an adjudication. Stop
  repository work immediately. If the issue is still open, leave ONE acknowledgement so the record
  shows the message was received, and release your `dev:<runtime>` marker — it is per-runtime and
  yours to drop. The assignee is different: where the tracker attributes it to a shared account the
  winner holds it too, so the binding decides whether it comes off (on all three current bindings it
  stays — each one's account is shared or singular); follow it rather than assuming. Do not alter the
  workflow state and do not write a second claim comment — the race record is not yours to edit.
- **The item is closed, or no longer carries the state you are working under.** Someone else
  finished or moved it. Stop, release your marker exactly as above, and change nothing about the
  state — it belongs to whoever moved it.

Renewal does not pretend the control surface is a lock, and it does not try to. It caps the cost of
losing a race — or being legitimately displaced — at one renewal interval, and it makes "the original
holder reads your comment and backs off" in *Reclaiming is not a race won* something that actually
happens rather than something the prose hopes for.

**A failed read is not a failed answer.** If the renewal's read itself fails — network, auth, rate
limit — the control surface answered nothing, and "nothing" is not a stand-down. Fail closed on the
write: post no heartbeat, start no evaluation, launch no review, deliver nothing — and retry the
read. Only a stop answer from a *successful* read ends the work. Treating a timeout as a stand-down
lets a flaky network halt every run; treating it as clearance lets a run write deaf, which is the
defect this rule exists to close.

**Reclaiming.** An issue is reclaimable when it carries an assignee and/or a claim comment naming a
run-id — **in any state, not only `in-progress`** — and the holder's last attributed activity is past
the declared horizon plus its bounded renewal window, or more than a few hours old when no horizon
was declared. Unrelated activity and control targets do not renew a holder. The state label is exactly
what a dead run may never have gotten to write, so a claimed issue still wearing `status:ready` or
`status:review` is reclaimable by the same rule as one caught mid-build under `in-progress`. Then:

1. **Comment before touching anything**: `Reclaiming from <run-id>; last activity <timestamp>,
   horizon <declared or none>.` The record of the takeover matters more than the takeover.
2. Replace the assignee with your own, **and remove the dead run's attribution marker as you add
   yours** — it cannot do it itself, which is the whole reason you are here. Skip this and the item
   ends up claiming two runtimes hold it. Then continue from whatever the dead run left on the issue.
3. **Do not discard its work, and do not assume the label is where the work actually got to.** A
   pushed branch, a commented diagnosis, a ruled-out hypothesis are all still valid — which is
   precisely why everything goes on the issue as it happens. If what the dead run left shows the
   work is further along than the label says — a linked PR or a finished diff sitting there while
   the issue still reads `ready` — transition straight to the state the evidence supports instead of
   restarting from `in-progress`; re-doing already-finished work is the exact waste this rule exists
   to avoid.

**Reclaiming is not a race won.** If the original holder was alive and merely slow, its next
`verify_claim` surfaces your comment — which is why the comment names who you took it from. It then
backs off exactly like the loser of a claim race in step 2. Either way no work is lost, and the
worst case is one duplicated hour rather than an issue that is stuck forever.

**A run ending deliberately needs none of this**: unassign, write the state, say so. Reclaiming is
for the runs that never got the chance.

---

### Where to put work you cannot finish

Three states can receive work a dev is putting down, and choosing wrong buries it. Two questions
separate them.

**First: is the problem inside the issue, or outside it?**

- **Inside → `analysis`.** The specification cannot be built from — it contradicts itself, its
  acceptance criteria do not match the problem it describes, or its premise turned out to be false.
  More *thinking* fixes this, and thinking is the analyst's job. Comment the evidence, hand it back.
- **Outside → read on.** Nothing anyone writes on the issue will help; something in the world has to
  change.

**Then: does the work already exist?**

- **No → `blocked`.** Nothing is built and something beyond the repository must move first: a
  decision, a date, a credential, a system that is down. Name the condition *and* its discharger.
- **Yes → `review`.** Built and verified but unable to ship. That is *finished work awaiting
  delivery*, and it earns its own state because the two are not equally recoverable: a blocked item
  costs whatever it is waiting for, while finished-but-undeliverable work is one permission away from
  shipping. **File it as `blocked` and the next run has no way to know the code already exists** — so
  it builds it again, and the first branch rots unmerged.

The failure this prevents is not theoretical in either direction. An issue parked in `analysis`
because an external system was down wastes an analyst's pass on a specification that was never wrong;
one parked in `blocked` because the spec was incoherent waits forever for a world that was never the
problem.

**If the issue turns out to be wrong**, do not silently reinterpret it. Comment with the evidence,
move it back to `analysis`, and unassign. An issue that a dev rewrote in flight is an issue
nobody analysed.

**Everything you learn goes ON THE ISSUE, not just in your reply.** The chat is ephemeral and the
next run starts from the issue. Record it as you go, not at the end:

- **a ruled-out hypothesis is worth as much as a confirmed one** — it stops the next run repeating
  your dead end. Say what you tested and why it was not the cause;
- **a diagnosis without a fix still belongs there.** If you narrowed the problem but did not solve
  it, comment the narrowing, unassign, and return it to `ready`. That is a partial delivery,
  not a failure — and infinitely better than a fresh run starting from zero;
- **a discovery outside this issue's scope becomes its own finding.** You are the dev, not the
  analyst, so you do not have to analyse it — but dropping it because "not my role" loses it. File
  a stub, or note it on this issue for an analyst to pick up;
- **methodological errors count as findings.** If your probe turned out not to measure what you
  thought, say so. Someone will otherwise repeat it and trust the wrong number.

**Handing off is a legitimate outcome, and sometimes the better one.** Judgement degrades as a run
gets long: you start repeating mistakes you already know about. That is not hypothetical — during
the session that produced this rule, a run re-made the exact `$?`-after-a-pipe error it had
documented in the project's own error table hours earlier, and did it while debugging.

Three signals that you are past your useful range:

- you repeat a mistake you had already written down;
- you are on your third failed hypothesis without narrowing the problem;
- you are editing more than you are measuring.

When any of those fire, **write the state to the issue and return it** rather than pushing on. A
fresh run with a full context budget, starting from a good diagnosis, will beat a tired one
continuing. Returning work is not giving up — carrying it badly is.

**Never ship a partial fix as if it were the fix.** If the acceptance criteria are not met, the work
is not done: say what is still failing, and do not merge. A branch discarded with a good diagnosis
attached is a better outcome than a merge that leaves the defect alive under a green comment.

**If the issue has no acceptance criteria**, it is not implementable as written — the criteria are
what "done" means, and with none you would be inventing the bar you then declare yourself to have
cleared. This happens with issues filed before the format existed, imported from another tracker, or
written by a human in a hurry. Do NOT guess silently. Either:

- **state the criteria you will treat as done, as a comment, BEFORE starting** — then build to them
  and let the close comment show you met what you announced; or
- **send it back to `analysis`** when the gap is large enough that you would be designing
  rather than implementing.

The first is usually right for a well-argued issue that simply lacks a checklist; the second when
you cannot tell what problem it solves. Either way the criteria end up written down before the work,
never reconstructed after it to match what you happened to build.

---

## Optional: a board view over this workflow

Most trackers can render these items as a Kanban board. Whether that board is a **view** or the
state itself is the only question that matters, and the answer is always the same here: the workflow
state defined by **Hard Rules** and **Decision Gates** is authoritative. Invert that and every agent needs
whatever extra permission the board API demands, and this workflow's transport has to be rewritten.

What that costs on a given tracker — which parts are API-reachable, which need a human to click
once, and how a board is kept in step without becoming the source of truth — belongs to its
binding, because none of it generalises.

**A board the configuration names is part of `transition`, and it is VERIFIED, not remembered.**
This is the one projection of state with no natural feedback: a wrong label is caught by the very
next `list_state`, a wrong claim by the next `verify_claim`, but a column nobody looks at simply
stays wrong forever, and the run that skipped it sees nothing. Prose asking a run to remember an
extra call is exactly what a long run drops. So the rule is mechanical: **after every `transition`,
re-read the state from BOTH surfaces and require them to agree** — the same read-back the label
already gets, extended to cover the board. Where no board is configured, that half is a no-op and
costs nothing.

Seen live (2026-07-25, issues #61 and #62): a run moved labels correctly through `ready` →
`in-progress` → `review` → `done`, closed the issue, merged and tagged — and mirrored the board
**zero times in an entire session**, with the `project` scope present the whole way. Both cards sat
on `Ready` while one issue was closed-and-delivered and the other was in review. Nothing in the
flow noticed, because nothing read the board back. The operator only found out by looking at it.
That is the failure this rule exists to make impossible, and note what it was NOT: not a missing
permission, not an unclear config, not a tracker limitation. The instruction was present and the
run simply never executed it, which is the failure mode a read-back catches and a reminder does not.

**And a read-back only catches it if the read-back itself runs**, which is the same problem one
level up. Where the binding implements `transition` as an executable, the mirror and both reads are
inside it and cannot be dropped separately from the label edit, as required by **Hard Rules**. That
is the durable fix; this section is why it was needed.

## Output Contract

- Analyst: return the created issue, state, identity, priority, domain route, and attribution, or state that no issue was filed and name the evidence checked; return no repository artifacts.
- Developer: return the issue and exact state, holder, branch/worktree, review URL and immutable head/base/delivery identities, tests/review/CI/publications, and any blocker, handoff, rollback, or next owner. Surface claim, horizon, credential, capability, and resource limits from the focused references; never reinterpret an unknown result as clearance.

<!-- issue-flow:config:start -->
---

## Operator configuration

The table below contains portable defaults only. Before using it, look for `operator.local.md`
beside this file. When present, read it and let its same-named rows override this table; its
additional instructions are local policy too. That file contains permissions and machine-specific
paths, is ignored by Git, and MUST NOT be committed or published.

| Setting | Value here | Skill default |
|---|---|---|
| Delivery authorisation | ask | ask |
| Delivery route | direct | direct |
| Review delegation | ask | ask |
| Merge strategy | merge commit | merge commit |
| Worktree location | unset | unset |
| Tracker | `github` | `github` (also `linear`, `trello`) |
| Project board | none | none |
| "Description for dumb humans" sentence language | English | English |

<!-- issue-flow:config:end -->

> The two markers delimit the default template used when the installer creates
> `operator.local.md`. Configure the ignored local file, never this versioned block.
