---
name: issue-flow
description: "Trigger: issue-flow analyst [conditions], issue-flow dev, analyst, developer, claim issue, move task states. Analysts file at most one evidenced issue; without conditions, audit the repository critically."
license: GPL-2.0
metadata:
  author: asanabrial
  version: "1.13.0"
---

# Issue Flow — Analyst / Dev over a shared issue tracker

## Activation Contract

Use for `issue-flow analyst [conditions]`, `issue-flow dev [domain-rules] [issue-number]`, analyst or developer work, claims, and workflow-state transitions.
Analyst conditions and dev domains are optional: a bounded target is the scope; otherwise the analyst audits the repository and the dev implements the selected issue.
Read repository instructions first and load only the tracker binding selected by operator configuration before tracker work.
Resolve this skill's immutable filesystem directory once per activation and load every local reference from that resolved directory; never reopen companions through the stable installation alias during a concurrent upgrade.
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
| A renewal answers "stop" | A control message naming this run, or an item closed or no longer in the expected state. Stop repository work, acknowledge once where the binding asks, release only this runtime's `dev:<runtime>` projection, and change no workflow state — it belongs to whoever moved it. |
| A renewal's read fails | Nothing was answered. Write nothing — no heartbeat, evaluation, review, or delivery — and retry the read. Never a stand-down and never clearance. |
| A holder looks abandoned | Reclaimable from **any** state once attributed activity passes the declared horizon plus the binding's renewal window, or its legacy window when no horizon was declared. Use the binding's reclaim operation with exact target discovery; never infer authority from a label, assignee, prose, or an observed worktree. Record who was displaced and the timestamped evidence, retain their work, and transition to the state that evidence supports rather than the state the label shows. |
| Work must be put down | Repeated a known mistake, three hypotheses without narrowing, or more editing than measuring. Persist diagnosis, ruled-out hypotheses, and branch/worktree location on the issue, then route by the row above. |

## Execution Steps

Before either role's first tracker write in an unfamiliar project, run the selected binding's `ensure_states`; then use that binding's executable operations and readbacks rather than reconstructing them.
Load [domain composition](references/domain-composition.md) for routing, scale validation, queue partitioning, or autonomous fallback; [runtime notes](references/runtime-notes.md) for invocation, read-only enforcement, independent-context acquisition, or runtime limits; [safety incidents](references/safety-incidents.md) only when claim, reclaim, handoff, or failure rationale is needed; and [repository delivery](references/repository-delivery.md) when repository rules do not fully define isolation, integration, or review-unit sizing.

### Analyst

1. Keep the repository and non-tracker external systems read-only. Drain `analysis` oldest first, then inspect `blocked`: move discharged conditions to `ready` with evidence, date conditions that still hold, and name how long a person has owed any pending decision.
2. Analyse only the bounded scope under the left-hand domain rule book. With no conditions or domain, use the autonomous `general` contract; verify evidence and duplicates, then keep at most the strongest defensible finding or report that none survived.
3. Validate the fields, route, scale, and body contract for queued and new findings. For queued work, add missing handoff material with `comment` and transition only that same complete item; never create a replacement. For a new finding, fill the [analyst issue template](assets/analyst-issue-template.md) and `create` it with every marker plus `analyst:<runtime>` and run-id attribution. Use `ready`, or `blocked` with the exact missing condition and discharger; then STOP without implementing or assigning it.

### Developer

1. Resume this run's held issue at its recorded state before consulting queues. Otherwise drain unassigned `review` before `ready`, rechecking the delivery blocker before claiming published work.
2. For `ready`, partition valid candidates by stable domain and declared scale, rank only within each partition, and choose the oldest partition head; a selected domain limits selection to its highest-ranked oldest item. Use oldest-first only when no candidate has a valid scale contract.
3. `claim` with the run-id and horizon, then adjudicate the server-timestamped timeline; the earliest live claim wins, and a loser comments, releases, and selects again. Keep claimed review work in `review` unless a code change requires `in-progress` and a fresh publish/review/CI cycle; for ready work, transition once to `in-progress` with `dev:<runtime>` and no second claim event.
4. Load the route's right-hand implementation rule book before planning; if absent, state that none applies and build to written acceptance criteria. Follow repository rules, use the repository-delivery fallback for missing isolation, integration, or review-unit sizing policy, verify the claim immediately before the first branch/worktree/file write, and implement in the isolated checkout.
5. Renew before publishing, publish or reuse the shared review target, then transition to `review`. Acquire an independent context only when `Review delegation` authorises this run; otherwise request a separately started review. Require its verdict and green CI on the same exact head/base; every push invalidates both. Leave blocked delivery in `review`, restore local state changed by the failed attempt, record the precise blocker, and unassign rather than bypassing it; conflicting repository rules get a separate issue.
6. Renew at each irreversible boundary; merge only when reviewed, green, and current head/base identities match. For a merge-commit route, require the delivered SHA's exact parents to be the reviewed base and head; other configured strategies require their binding-declared equivalent topology and any delivered-SHA gate. For each changed version, publish and remotely verify its immutable annotated tag and required Release; renew again, transition to `done`, record review/CI/tests/measurements/PR/delivering SHA/publications with the run-id, and close. On handoff, preserve all evidence: wrong specification goes to `analysis`, unbuilt external wait to `blocked`, built-undeliverable work to `review`, and a useful partial diagnosis to `ready`.

## Output Contract

- Analyst: return the created issue, state, identity, priority, domain route, and attribution, or state that no issue was filed and name the evidence checked; return no repository artifacts.
- Developer: return the issue and exact state, holder, branch/worktree, review URL and immutable head/base/delivery identities, tests/review/CI/publications, and any blocker, handoff, rollback, or next owner. Surface claim, horizon, credential, capability, and resource limits from the focused references; never reinterpret an unknown result as clearance.

## References

Load a reference when its condition holds; do not preload them, and do not copy their contents back
into this file. Each owns its subject once, so a rule that changes is changed in one place.

| Load | When |
|---|---|
| [domain composition](references/domain-composition.md) | A finding is handed over or routed, a priority scale must be validated, a queue is partitioned, or no domain applies and the autonomous `general` contract is needed. |
| [repository delivery](references/repository-delivery.md) | The repository's own rules do not fully define isolation, integration, review-unit sizing, or cleanup — the default fallback for repository work. |
| [safety incidents](references/safety-incidents.md) | Claim, reclaim, handoff, board-projection, or failure rationale is needed, or an incident's evidence must be cited. It is the ledger of *why*; the rules it justifies are already above. |
| [runtime notes](references/runtime-notes.md) | Invocation sigils, delegation, independent-context acquisition, agent-team lifetime, or a runtime's honest limits are in question. |
| [analyst issue template](assets/analyst-issue-template.md) | Filling a new finding's body. It owns the heading order, the fixed first heading, alert syntax, attribution, and the machine marker. |

The selected tracker binding under `bindings/` owns exact commands, capability differences,
unsupported behaviour, and tracker-specific readbacks. Its executable owns mechanical operations and
their output contract; state the contract here and call the command rather than re-narrating it.

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
