# Repository delivery

Use this fallback when a repository does not define a stricter isolation or integration policy. The
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

## Start from a fresh base

1. Read the repository instructions before choosing branch, worktree, integration, test, or merge
   behavior. History can reveal naming style, but it cannot define concurrency safety.
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

The worktree path MUST be unique per run. Version-control registration protects a branch, not a
process: during reclaim, a displaced process may still be writing its registered checkout until its
next renewal. Resume only the current run's registered path; a branch registered anywhere else needs
an explicit handoff and removal after its work is preserved. Reject every foreign or orphaned
directory. Tools with per-checkout indexes or caches get a fresh local instance rather than one
copied or linked from another checkout.

Confirm isolation before the first edit and whenever the tree changes unexpectedly. Stop writing and
renew the claim. A losing run leaves the tree untouched and records where its own work is; the winning
run adopts useful foreign work instead of deleting it. Editing around an unexplained writer creates a
combined build owned by nobody.

Fresh worktrees contain only tracked files. Restore required ignored environment files, credentials,
and local settings before tests or tools depend on them. Reuse a proven interpreter or dependency
environment when safe, but keep the worktree as the working directory and ensure it loads that
worktree's code.

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
