# Repository delivery

Use this fallback when a repository does not define stricter isolation, integration, or review-unit
sizing policy. The
repository's instructions always win; this reference supplies a floor, not a competing convention.
The runtime contract still owns role and state decisions, and the
[abstract binding contract](../SKILL.md#hard-rules) selects
the operation implementation and exact tracker commands.

## Delivery invariants

- Treat the issue, diff, or review named by the caller as the bounded scope while still obeying local
  repository and routed domain rules.
- Keep the base checkout read-only. One issue uses one traceable branch and one isolated worktree.
- Renew ownership before the first repository write and every publish, merge, tag, or close boundary.
- Publish shared bytes before review. Independent review and CI name the exact head and base; any
  changed byte invalidates prior evidence.
- Preserve unfinished work and its diagnosis. A handoff is safer than an unverified merge.

## Keep review units coherent

Aim for no more than 800 changed lines in one pull request so an independent reviewer can inspect the
complete delivery target without losing context. This is a recommendation, not a gate. Use a larger
coherent pull request when splitting would create unsafe intermediate states, duplicate migrations,
or otherwise reduce review quality. Before review, the developer records that rationale on the issue
or review target; the independent reviewer assesses scope coherence and the risk of splitting. Size
never waives exact-SHA review, tests, CI, authority checks, or any stricter repository rule.

## Start from a fresh base

1. Read the repository instructions — conventionally `AGENTS.md` or `CLAUDE.md` at the root, plus
   anything they include — before choosing branch, worktree, integration, test, or merge behavior.
   **The repository's own rules win, always**, and this document is only the fallback for what they
   do not define. History can reveal naming style, but it cannot define concurrency safety.
2. Fetch the configured base and create the issue branch from that fresh remote revision. Record the
   branch and base through the selected binding as soon as they exist.
3. Do not edit or commit on the base branch. Move unpublished accidental commits to the issue branch;
   if base history was already shared, repair it without rewriting shared history.

The branch name should carry the issue identity because it survives local run attribution and joins
the eventual review and delivery record back to the work item.

## Isolate the worktree

Place each implementation checkout outside the base working tree and include the repository identity
in its parent path. A shared parent keyed only by branch can collide across repositories, while an
in-tree worktree pollutes status and ignore behavior for every run.

The worktree strategy MUST prevent two live runs from writing the same directory, and the path is
made unique per run. Relying instead on the version-control system's registration of one common
issue branch was tried and does not hold: that check is a read followed by a write with no lock
between them, and two checkout processes have been observed registering one branch concurrently. A
path that is unique per run does not depend on winning that race.

**Registration is not ownership.** "This path holds this branch" is satisfied equally well by two
runs of the same branch, so a resume additionally requires durable evidence that THIS run created
the checkout. Absent evidence is not a permissive default — a registered checkout of your branch
with nobody's name on it cannot be told apart from one another run is writing into right now. Every
other case — a foreign checkout, an orphan directory, an unreadable ownership record — stops.

Tools with per-checkout indexes or caches get a fresh local instance rather than one copied or
linked from another checkout.

Confirm isolation before the first edit and whenever the tree changes unexpectedly. Stop writing and
renew the claim. A losing run leaves the tree untouched and records where its own work is; the winning
run adopts useful foreign work instead of deleting it. Editing around an unexplained writer creates a
combined build owned by nobody.

Fresh worktrees contain only tracked files. Restore required ignored environment files, credentials,
and local settings before tests or tools depend on them. Reuse a proven interpreter or dependency
environment when safe, but keep the worktree as the working directory and ensure it loads that
worktree's code.

## Review the whole delivery, not the part you were looking at

**The review target is the exact recorded base through `HEAD`, plus whatever is still uncommitted.**
Those two halves are easy to review separately and disastrous to review separately: an approval over
the committed prefix authorises nothing about the dirty suffix, and an approval over the suffix
authorises nothing about the commits already on the branch. Seen live (Investora #70): the approved
lineage covered 12 workspace paths while the pull request delivered 15, and 35 of 65 final hunks
carried no authority at all — while every artefact looked exactly like a reviewed delivery.

Derive the target once, mechanically, before any reviewer runs, and compare it against whatever the
reviewer is actually being given. Where the binding scripts it, that is `expected-target`: it emits
the path/mode/blob manifest and one digest over it, and fails closed when a supplied target differs
— naming the paths that would ship unreviewed. Mode is part of it, because a file that becomes
executable, or a regular file replaced by a symlink, changes the delivery while every content byte
stays identical.

## Do not merge the base as a routine step

**Branch from an exact, freshly fetched base and leave it there.** Merging the base branch back in
"to stay current" rewrites the review target for no reason, and invalidates a head that review and
CI are already bound to. What is required instead is to CLASSIFY later movement:

| The base moved and… | Then |
|---|---|
| it did not move | nothing to decide |
| every path it touched is disjoint from this delivery, and a read-only merge reports no conflict | leave the candidate branch alone |
| it touched paths this delivery also touches, or the merge conflicts | integrate before final review |
| the merge result could not be established | integrate; an unanswered merge is never "compatible" |

Textual disjointness is evidence, not proof: two files that never overlap can still have to change
together. The script reports refs, changed paths and the read-only merge result; whether the change
still MEANS what it did is a judgement, and it stays with the reviewer.

An operator may override any of this. When `~/.agents/AGENTS.md` exists it is the canonical
cross-runtime policy and it wins; this section is the default for when it does not. Its text is
deliberately not copied here — a second copy is a second owner, and the two drift.

## Integrate before publishing

Fetch and integrate the current base immediately before publication. Resolve conflicts as code
changes: understand both sides, retain both intents where required, and rerun every affected check.
Choosing one side only to clear markers can silently delete a delivered fix.

Merge the base into the issue branch by default. Rebasing rewrites commit identities already used by
review, CI, signatures, or deployments, and a published rebase can require a force-push over work
another actor fetched. Follow an explicit repository rebase policy only while no evidence or shared
consumer depends on the old identities.

## Publish, review, and verify

Publish the configured review target before moving work to review. Reuse its existing review request
when resuming; duplicate review targets split evidence. Capture both head and base revisions, require
the independent verdict and CI result to identify them, and reread both afterward. A push or base
movement makes the old verdict and result stale.

The required CI set is the union of host-required checks and every applicable repository lane. Each
expected lane must exist once and succeed; absent, skipped, queued, cancelled, or failed work is not
green. A repository that requires the exact delivered revision also runs its post-merge gate.

Never bypass a failed gate, missing permission, conflicting rule, or unavailable reviewer. Published
work that cannot be delivered remains review work with the exact blocker, expected condition, and
next owner recorded.

## Merge and publish versions

Merge only when current head/base equal the reviewed and green head/base, and bind the operation to
that head. Use the configured strategy and verify the delivered revision's topology rather than
trusting the requested flag. A topology-preserving strategy retains branch ancestry; squash or rebase
creates different history and is valid only when repository policy selected it before review.

Delivery authorization belongs to repository or operator policy. A configured review route does not
silently grant merge permission, and an override does not cure a stale or failed gate.

After the delivered-revision gate, inspect every version changed by the diff. Publish an annotated,
immutable tag using the repository's one established product or component convention, then verify the
remote tag object and its peeled commit. Never move or force an existing version tag. Publish a host
release artifact only when repository history or configuration requires one, and build it from the
already-verified tag.

## Handoff and cleanup

Choose handoff state from evidence:

| Evidence at handoff | Destination |
|---|---|
| The issue premise or acceptance criteria are wrong | Analysis, with the contradiction and evidence |
| Nothing is built and an external condition must change | Blocked, with the condition and discharger |
| Work is built or published but cannot ship | Review, with branch/review target, checks, and blocker |
| Diagnosis is useful but no fix exists | Ready, with hypotheses tested and the narrowed next step |

Do not discard another run's branch, diagnosis, measurements, or ruled-out hypotheses. Record all of
them on the durable work item so the next run resumes rather than reconstructs.

Clean up only after delivery is verified or abandonment is explicitly recorded. Remove the worktree
and local branch created by the run only after confirming they contain no unpublished work. Preserve
remote review, merge, and tag evidence for audit and rollback.
