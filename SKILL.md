---
name: issue-flow
description: "Trigger: issue-flow analyst [conditions], issue-flow dev, analyst, developer, claim issue, move task states. Analysts file at most one evidenced issue; without conditions, audit the repository critically."
license: GPL-2.0
metadata:
  author: asanabrial
  version: "1.10.0"
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
- The selected binding MUST map `ensure_states`, `create`, `list_state`, `claim`, `verify_claim`, `transition`, `comment`, `last_activity`, `label`, `unassign`, `publish_version`, and `close`. Run executable reversible operations instead of reconstructing them; bindings MUST declare unsupported capabilities and fail closed.
- Issue acceptance criteria, routed domain rules, and repository rules define done. Never invent missing gates or reinterpret the issue silently.

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
Load [domain composition](references/domain-composition.md) for routing, scale validation, queue partitioning, or autonomous fallback; [runtime notes](references/runtime-notes.md) for invocation, read-only enforcement, independent-context acquisition, or runtime limits; [safety incidents](references/safety-incidents.md) only when claim, reclaim, handoff, or failure rationale is needed; and [repository delivery](references/repository-delivery.md) only when repository rules do not fully define isolation or integration.

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
4. Load the route's right-hand implementation rule book before planning; if absent, state that none applies and build to written acceptance criteria. Follow repository rules, use the repository-delivery fallback for missing isolation/integration policy, verify the claim immediately before the first branch/worktree/file write, and implement in the isolated checkout.
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
