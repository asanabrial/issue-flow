# Safety incidents and failure cases

This ledger owns the historical evidence behind Issue Flow's safety rules. IDs are stable: keep each
incident here exactly once, preserve its dates and issue links, and change the protection only when
the surviving executable or document owner changes.

## Omitted mechanics and stale projections

| ID | Failure mode and evidence | Protection or invariant | Surviving executable or document owner |
|---|---|---|---|
| I01 | Correct prose was repeatedly remembered incompletely: board mirroring was skipped for a whole session, five claimed issues were never relabeled, and heartbeat loops wrote without reading. | Put reversible, mechanical operations and their readbacks in the selected binding executable; leave judgement in prose. | `scripts/github.py`; each selected binding's operations table |
| I04 | A correctly assigned and transitioned issue received its `dev:<runtime>` marker only at close, so live-holder queries could not find it during the build. | Project the bounded runtime marker when work becomes held; do not add a second claim event. | `SKILL.md` dev step 3; binding claim/transition operations |
| I06 | Five runs died after claim and before transition, leaving assigned issues labeled `ready` after their horizons expired. | Treat claim and state as separate evidence, reclaim from any state, and transition to the state supported by retained work. | `SKILL.md` abandoned-work rules; binding claim/reclaim/transition operations |
| I11 | On 2026-07-25, issues #61 and #62 traversed labels through delivery while their configured board cards stayed on `Ready`; the mirror ran zero times. | A transition includes every configured projection and readback; both surfaces must agree before success. | Binding transition executable and board audit; `SKILL.md` state contract |
| X02 | A tracker that rejects unknown labels can fail only after the analyst has completed all judgement and attempts the first create. | Provision the state vocabulary and supplied marker labels before attaching them; make setup idempotent. | Binding ensure/create operations; `SKILL.md` Hard Rules and Execution Steps |

## Claim ownership and abandoned runs

| ID | Failure mode and evidence | Protection or invariant | Surviving executable or document owner |
|---|---|---|---|
| I02 | On 2026-07-26, a five-minute dev loop repeatedly re-entered selection while already holding one issue and nearly started additional work on every tick. | Resume self-held work before consulting any queue; one run holds one task. | `SKILL.md` dev step 1 |
| I03 | On 2026-07-24, issue #58 was claimed by two runs 1m46s apart; the loser did not renew before filesystem work and wrote a model, migration, and tests into the winner's checkout. | Verify the live claim immediately before branch, worktree, or file creation; stop if the timeline says another run won. | Binding verify-claim and start-branch operations; `SKILL.md` dev step 4 |
| I07 | On 2026-07-22, a run lost a claim race by five seconds, was told 33 seconds later, then posted three heartbeats and worked about 48 more minutes without rereading control messages. | Every heartbeat reads before it writes, and the same renewal precedes expensive or irreversible boundaries. | Binding heartbeat/verify-claim operations; `SKILL.md` abandoned-work rules |
| I08 | Agent-team tasks have remained incomplete and blocked dependants after their session disappeared, requiring manual status repair. | Treat in-session task lists as ephemeral; persist progress on the issue and recover abandoned work from server-timestamped evidence. | `references/runtime-notes.md`; `SKILL.md` abandoned-work rules |

## Repository isolation and immutable evidence

| ID | Failure mode and evidence | Protection or invariant | Surviving executable or document owner |
|---|---|---|---|
| I05 | On 2026-07-24, issue #58 runs derived the same worktree directory; files appeared in a checkout its owner had created clean minutes earlier. | Use one issue branch and an external checkout whose construction or registered branch prevents live runs from sharing it; reject foreign or orphaned paths. | `references/repository-delivery.md`; binding start-branch operation |
| X06 | Fresh worktrees omit ignored credentials, environment files, and local settings, so a familiar tool can start normally and fail only when an absent value is needed. | Restore required ignored inputs and run the worktree's code while reusing the established environment when safe. | `references/repository-delivery.md`; selected binding's worktree guidance |
| X07 | Rebasing rewrites SHAs already named by reviews, CI, signatures, or deployments and can require a force-push over refs another actor fetched. | Integrate the current base without rewriting published evidence unless repository policy explicitly requires otherwise; reverify after integration. | `references/repository-delivery.md`; `SKILL.md` delivery steps |

## Runtime and routing mismatches

| ID | Failure mode and evidence | Protection or invariant | Surviving executable or document owner |
|---|---|---|---|
| X01 | Codex rejects the `/` skill sigil because it resolves only built-in commands there. | Use the runtime's documented invocation sigil without changing role or argument semantics. | `references/runtime-notes.md` |
| X03 | Familiar-looking values from different priority scales invite a fake global ordering, such as comparing `tier:2` with `priority:high`. | Validate each declared scale and rank only within its domain/scale partition. | `references/domain-composition.md` |
| X04 | Loading the analysis side of a routing arrow for implementation applies evidence and filing rules as though they defined done. | Analysts load the left rule book; devs load the right, and an absent implementation side is stated rather than guessed. | `references/domain-composition.md` |
| X05 | A shell can bypass a read-only tool allowlist, and teammate runtimes may not inherit a skill declaration even when they inherit tool restrictions. | Keep the prose write boundary authoritative and place required rule books explicitly in delegated prompts. | `references/runtime-notes.md` |
| X10 | A parallel A/B evaluation exhausted memory while another run measured on the same machine; the sequential rerun passed. | Announce heavy phases, inspect concurrent work, and rerun alone before treating a contention death as evidence about the change. | `references/runtime-notes.md` |

## Handoff, completion, and blockers

| ID | Failure mode and evidence | Protection or invariant | Surviving executable or document owner |
|---|---|---|---|
| I09 | Recoverable work was buried by choosing state from the obstacle rather than the evidence: external outages went to analysis, incoherent specifications to blocked, and built work could be hidden as unbuilt. | Route wrong specifications to analysis, unbuilt external waits to blocked, and built-undeliverable work to review; record the evidence and owner of the next action. | `SKILL.md` handoff and state decision gates |
| I10 | During the session that produced the handoff threshold, a long-running agent repeated its already-documented `$?`-after-a-pipe mistake while debugging. | Hand off after repeating a known mistake, three non-narrowing hypotheses, or more editing than measuring; preserve the diagnosis on the issue. | `SKILL.md` handoff rules |
| X08 | An issue without acceptance criteria forces the implementer to invent the bar it later claims to have met, and a partial improvement can then masquerade as completion. | Write criteria before implementation or return the issue to analysis; never ship a partial fix as complete. | `SKILL.md` dev/handoff rules; `references/domain-composition.md` |
| X09 | A blocked item has no natural holder or selection queue; vague conditions and unnamed human decisions therefore become abandoned work. | Name the exact exit condition and its discharger, surface overdue human decisions, and move discharged blockers back to ready. | `SKILL.md` Analyst steps and Decision Gates |
