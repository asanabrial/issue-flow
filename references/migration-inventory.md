# Runtime contract migration inventory

This ledger prevents the issue #7 refactor from silently deleting policy, rationale, or incident knowledge. Source citations resolve against the frozen pre-migration revision `16dc4afe271109eca0767779c1f94777866d5da9`; a row is complete only when its final owner contains the knowledge and that baseline source is separately retired.

| Status | Meaning |
|---|---|
| Owner copied | This migration has copied the complete knowledge into its planned final owner. |
| Old source retired | The corresponding text has been removed from its current `SKILL.md` location. |

## Current sections

| ID | Current source | Final owner/path | Intentional-consolidation rationale | Owner copied | Old source retired |
|---|---|---|---|---|---|
| S01 | What this skill is, and what it is NOT (`SKILL.md:11-67`) | `SKILL.md` | Condense activation, bounded scope, and optional domain behavior into the runtime contract. | yes | yes |
| S02 | Review is this role (`SKILL.md:68-85`) | `references/runtime-notes.md` | Keep the independent-context rule actionable in `SKILL.md`; consolidate runtime acquisition details here. | yes | yes |
| S03 | Composition contract (`SKILL.md:86-105`) | `references/domain-composition.md` | Own the transport/business-rule boundary once. | yes | yes |
| S04 | What the domain hands over (`SKILL.md:106-147`) | `references/domain-composition.md` | Consolidate finding fields and priority-scale semantics with the boundary they implement. | yes | yes |
| S05 | Tracker binding operations (`SKILL.md:148-192`) | `SKILL.md` | Retain the abstract operation contract; tracker commands remain in each binding. | yes | yes |
| S06 | Attribution (`SKILL.md:193-255`) | `references/domain-composition.md` | Preserve run/runtime cardinality, label lifecycle, and shared-account rationale together. | yes | yes |
| S07 | Domain routing (`SKILL.md:256-291`) | `references/domain-composition.md` | Keep label, metadata arrow, and side-selection rules under one owner. | yes | yes |
| S08 | Why the split exists (`SKILL.md:292-305`) | `references/domain-composition.md` | Preserve role-boundary rationale without loading it on every invocation. | yes | yes |
| S09 | Role: ANALYST (`SKILL.md:308-322`) | `SKILL.md` | The read-only boundary and one-finding limit remain direct runtime rules. | yes | yes |
| S10 | Drain analysis and blocked (`SKILL.md:323-365`) | `SKILL.md` | Queue order and analyst tool boundary remain executable steps. | yes | yes |
| S11 | Autonomous discovery (`SKILL.md:366-384`) | `references/domain-composition.md` | Consolidate the built-in fallback domain contract and deduplication bar. | yes | yes |
| S12 | What the analyst produces (`SKILL.md:385-436`) | `SKILL.md` | Keep create/blocked instructions at runtime; the body schema moves to the asset. | yes | no |
| S13 | Role: DEV (`SKILL.md:440-602`) | `SKILL.md` | Preserve resume, claim, build, review, and delivery as the dev execution path. | yes | no |
| S14 | Repository default flow (`SKILL.md:603-742`) | `references/repository-delivery.md` | Consolidate branching, worktree, integration, review, and cleanup rationale. | yes | no |
| S15 | Abandoned work (`SKILL.md:743-840`) | `references/safety-incidents.md` | Keep horizon, heartbeat, reclaim, and retention failure modes together. | yes | no |
| S16 | Work that cannot finish (`SKILL.md:841-921`) | `references/safety-incidents.md` | Consolidate handoff-state rationale while retaining the decision gate in `SKILL.md`. | yes | no |
| S17 | State machine (`SKILL.md:922-972`) | `SKILL.md` | State meanings, exclusivity, blockers, and explicit done remain runtime decisions. | yes | yes |
| S18 | Optional board view (`SKILL.md:973-1006`) | `references/safety-incidents.md` | Preserve authoritative-label and verified-projection rationale once; bindings own mechanics. | yes | no |
| S19 | In-session agent team (`SKILL.md:1007-1036`) | `references/runtime-notes.md` | Isolate runtime-specific team lifetime and hook behavior. | yes | yes |
| S20 | Honest limits (`SKILL.md:1037-1065`) | `references/runtime-notes.md` | Keep heuristic, credential, resource, and judgement limits visible as one safety boundary. | yes | yes |
| S21 | Operator configuration (`SKILL.md:1069-1090`) | `SKILL.md` | Preserve script-readable markers and portable defaults; local overrides remain uncommitted. | no | no |

## Analyst issue template

| ID | Current source | Final owner/path | Intentional-consolidation rationale | Owner copied | Old source retired |
|---|---|---|---|---|---|
| T01 | Description for dumb humans (`SKILL.md:391-402`) | `assets/analyst-issue-template.md` | Preserve the fixed heading, configured sentence language, and GitHub NOTE syntax. | yes | no |
| T02 | Problem (`SKILL.md:404-406`) | `assets/analyst-issue-template.md` | Preserve the evidence-first problem contract verbatim. | yes | no |
| T03 | Why it matters (`SKILL.md:408-410`) | `assets/analyst-issue-template.md` | Preserve explicit impact or honest low-impact disclosure. | yes | no |
| T04 | Proposed approach (`SKILL.md:412-414`) | `assets/analyst-issue-template.md` | Preserve alternatives and uncertainty disclosure. | yes | no |
| T05 | Acceptance criteria (`SKILL.md:416-418`) | `assets/analyst-issue-template.md` | Preserve measurable outcomes and non-change invariants. | yes | no |
| T06 | Out of scope (`SKILL.md:420-422`) | `assets/analyst-issue-template.md` | Preserve the scope-creep boundary. | yes | no |
| T07 | Evidence (`SKILL.md:424-431`) | `assets/analyst-issue-template.md` | Preserve reproducibility plus trailing attribution and analysis-marker placeholders. | yes | no |

## Named incidents and failure cases

| ID | Current source | Final owner/path | Intentional-consolidation rationale | Owner copied | Old source retired |
|---|---|---|---|---|---|
| I01 | Mechanical steps omitted despite correct prose (`SKILL.md:154-162`) | `references/safety-incidents.md` | Preserve why reversible transport operations are executable. | yes | yes |
| I02 | Five-minute loop nearly selected second work (`SKILL.md:444-460`) | `references/safety-incidents.md` | Preserve why self-held work is checked before queues. | yes | yes |
| I03 | Issue #58 loser wrote into winner checkout (`SKILL.md:497-505`) | `references/safety-incidents.md` | Preserve the pre-filesystem claim-renewal incident. | yes | yes |
| I04 | Dev marker attached only at close (`SKILL.md:510-516`) | `references/safety-incidents.md` | Preserve why attribution is projected during transition. | yes | yes |
| I05 | Issue #58 shared worktree collision (`SKILL.md:656-664`) | `references/repository-delivery.md` | Preserve why worktree identity must prevent cross-run writes. | yes | no |
| I06 | Five claims never transitioned (`SKILL.md:751-759`) | `references/safety-incidents.md` | Preserve the claim/transition partial-failure case. | yes | no |
| I07 | Claim loser worked 48 minutes after adjudication (`SKILL.md:777-802`) | `references/safety-incidents.md` | Preserve why every heartbeat reads before writing. | yes | no |
| I08 | Agent-team tasks left incomplete (`SKILL.md:761-766`) | `references/runtime-notes.md` | Preserve the external-session recovery limitation. | yes | no |
| I09 | Wrong handoff state buried recoverable work (`SKILL.md:841-867`) | `references/safety-incidents.md` | Preserve why analysis, blocked, and review are distinct. | yes | no |
| I10 | Long run repeated its documented `$?` mistake (`SKILL.md:887-900`) | `references/safety-incidents.md` | Preserve the evidence-based handoff threshold. | yes | no |
| I11 | Issues #61/#62 never mirrored to the board (`SKILL.md:984-1005`) | `references/safety-incidents.md` | Preserve why transition verifies both state projections. | yes | no |
| X01 | Codex rejects the slash skill sigil (`SKILL.md:27-34`) | `references/runtime-notes.md` | Consolidate runtime invocation differences. | yes | yes |
| X02 | Unknown labels fail at issue creation (`SKILL.md:121-125,967-971`) | `references/safety-incidents.md` | Preserve why create/ensure operations provision labels first. | yes | yes |
| X03 | Different priority scales cannot be globally ranked (`SKILL.md:130-141`) | `references/domain-composition.md` | Preserve partitioned selection semantics. | yes | yes |
| X04 | Loading the wrong routing-arrow side applies the wrong job rules (`SKILL.md:277-287`) | `references/domain-composition.md` | Preserve explicit analyst/dev rule-book selection. | yes | yes |
| X05 | Shell write access and teammate skill loading limits (`SKILL.md:352-364`) | `references/runtime-notes.md` | Consolidate runtime enforcement caveats. | yes | yes |
| X06 | Fresh worktrees omit ignored credentials and settings (`SKILL.md:666-672`) | `references/repository-delivery.md` | Preserve environment reuse requirements. | yes | no |
| X07 | Rebase invalidates SHA evidence and shared refs (`SKILL.md:706-714`) | `references/repository-delivery.md` | Preserve the merge-by-default rationale. | yes | no |
| X08 | Missing acceptance criteria invites a fabricated done bar (`SKILL.md:902-918`) | `references/safety-incidents.md` | Preserve the pre-build criteria gate. | yes | no |
| X09 | Human-decision blockers become abandoned (`SKILL.md:937-958`) | `references/safety-incidents.md` | Preserve precise exit-condition and discharger requirements. | yes | yes |
| X10 | Parallel A/B died from machine contention (`SKILL.md:1053-1060`) | `references/runtime-notes.md` | Preserve resource-coordination and sequential-rerun guidance. | yes | yes |

## Invariant families

| ID | Invariant family | Final owner/path | Intentional-consolidation rationale | Owner copied | Old source retired |
|---|---|---|---|---|---|
| K01 | Bounded target is scope; local rules still apply. | `SKILL.md` | Activation must remain direct. | yes | no |
| K02 | Analyst conditions are optional; no-condition runs discover autonomously. | `SKILL.md` | Preserve valid standalone invocation. | yes | no |
| K03 | Review uses a context that did not write the change. | `SKILL.md` | Keep independence non-negotiable. | yes | no |
| K04 | Transport and business rules remain separate owners. | `references/domain-composition.md` | Prevent domain/tracker coupling. | yes | no |
| K05 | Analyst findings carry identity, title, priority, body, metadata, and domain. | `references/domain-composition.md` | Preserve complete handoff data. | yes | no |
| K06 | Priority scales are declared, ordered only internally, and never globally compared. | `references/domain-composition.md` | Preserve deterministic mixed-domain selection. | yes | no |
| K07 | Mechanical reversible operations use the selected binding executable. | `SKILL.md` | Prevent remembered-but-unexecuted steps. | yes | no |
| K08 | Bindings declare unsupported capabilities and fail closed. | `SKILL.md` | Make transport gaps visible. | yes | no |
| K09 | One stable run-id is reused for every write. | `references/domain-composition.md` | Preserve traceable ownership. | yes | no |
| K10 | Runtime is a bounded label; run-id remains text. | `references/domain-composition.md` | Prevent unbounded label growth. | yes | no |
| K11 | Dev markers track live holding and are removed on release, not delivery. | `references/domain-composition.md` | Preserve query correctness. | yes | no |
| K12 | Domain label plus metadata arrow route each role to the correct rule book. | `references/domain-composition.md` | Prevent silent routing inference. | yes | no |
| K13 | Analyst repository work is read-only and yields at most one issue. | `SKILL.md` | Keep the hard role boundary loaded. | yes | no |
| K14 | Analyst drains analysis, then blocked, before new discovery. | `SKILL.md` | Preserve recovery-first queue order. | yes | no |
| K15 | Analyst tool restrictions enforce the write boundary where possible. | `references/runtime-notes.md` | Consolidate runtime-specific enforcement. | yes | no |
| K16 | Autonomous discovery deduplicates and files only the strongest evidenced finding. | `references/domain-composition.md` | Preserve the built-in domain bar. | yes | no |
| K17 | The fixed NOTE is first and uses the configured sentence language. | `assets/analyst-issue-template.md` | Give the body contract one owner. | yes | no |
| K18 | Create carries every marker; incomplete analysis names a precise blocker. | `SKILL.md` | Preserve filing semantics. | yes | no |
| K19 | A dev resumes self-held work before selecting another issue. | `SKILL.md` | Enforce one task per run. | yes | no |
| K20 | Unassigned review work is selected before ready work. | `SKILL.md` | Finish published work first. | yes | no |
| K21 | Claim ownership is timeline-adjudicated; earliest live claim wins. | `SKILL.md` | Preserve race resolution. | yes | no |
| K22 | Claim is renewed before the first repository write and every irreversible boundary. | `SKILL.md` | Cap displaced-work cost. | yes | no |
| K23 | Ready transitions once to in-progress with the dev marker and no duplicate claim. | `SKILL.md` | Preserve state and race evidence. | yes | no |
| K24 | Issue criteria plus routed domain and repository rules define done. | `SKILL.md` | Prevent invented implementation scope. | yes | no |
| K25 | Review target is published before transition to review. | `SKILL.md` | Bind review to shared bytes. | yes | no |
| K26 | Review and CI name exact head/base SHAs; every push invalidates both. | `SKILL.md` | Prevent stale verdicts. | yes | no |
| K27 | Built but undeliverable work stays in review and is never bypassed. | `SKILL.md` | Preserve honest delivery state. | yes | no |
| K28 | Merge only the reviewed/green head and verify delivered topology. | `SKILL.md` | Preserve reviewed history. | yes | no |
| K29 | Version tags are immutable, remote-verified, and release-aware. | `SKILL.md` | Keep publication a delivery gate. | yes | no |
| K30 | Base is read-only; each issue uses one branch and isolated worktree. | `references/repository-delivery.md` | Consolidate repository isolation. | yes | no |
| K31 | Worktree paths cannot be shared; the path is unique per run, and a resume needs durable evidence this run owns the checkout — registration alone is not ownership. | `references/repository-delivery.md` | Preserve collision safety. | yes | no |
| K32 | Fresh worktrees restore required ignored inputs and reuse the established environment. | `references/repository-delivery.md` | Prevent checkout-only failures. | yes | no |
| K33 | Integrate current base and merge rather than rebase by default. | `references/repository-delivery.md` | Preserve SHA-bound evidence. | yes | no |
| K34 | Horizons, read-before-write heartbeats, and reclaim retain prior work. | `references/safety-incidents.md` | Consolidate abandoned-work recovery. | yes | no |
| K35 | Analysis, blocked, and review handoffs remain distinct and documented on the issue. | `references/safety-incidents.md` | Prevent recoverable work burial. | yes | no |
| K36 | Acceptance criteria precede implementation; partial fixes never ship as complete. | `references/safety-incidents.md` | Preserve the done bar. | yes | no |
| K37 | Exactly one workflow state exists; done is explicit before tracker closure. | `SKILL.md` | Keep state integrity direct. | yes | no |
| K38 | Labels are authoritative; configured board projections are mirrored and read back. | `SKILL.md` | Preserve verified state projection. | yes | no |
| K39 | Session teams and resource signals never replace durable tracker state. | `references/runtime-notes.md` | Consolidate runtime lifetime and machine-limit caveats. | yes | no |
