# Changelog

All notable changes to Issue Flow are documented here.

## [1.12.0] - 2026-07-29

### Changed

- Replace partial and in-place upgrades with immutable bundles bound to one verified Git commit and
  tree. POSIX activates a complete bundle through an atomic symlink replacement; Windows atomically
  retargets the canonical junction.
- Retire `sync --from <SKILL.md>` because one file cannot prove its required references and assets.
  Both shell entrypoints now delegate to one Python transaction implementation.
- Preserve operator policy and ignored top-level runtime state during the journaled legacy-clone
  migration, restoring that standalone clone after an interrupted move without exposing its old
  in-place installer as an immutable rollback target.
- Harden bootstrap and recovery authority with portable path rejection, operating-system locks,
  isolated Python imports, disabled Git hooks, core-only repository config, Git-bound rollback
  verification, verified local-helper loading, in-memory execution from the canonical Git blob,
  system-only CA trust and fsynced post-switch activation provenance. Git 2.36 or newer is now
  required for reference fsync support, Windows POSIX layers require native Windows Python, and the
  wrapper-selected absolute Git identity is reused after every directory change. Python runs in
  UTF-8 mode, and later bootstraps recover owner-marked quarantines only after acquiring their
  released lifetime lock; a stable guard serializes owner publication through final quarantine
  deletion, where the repository is durably removed before its owner marker. Bare repositories
  reject redirected common directories, alternates, linked reference authority and symbolic refs;
  installer refs use raw direct-commit reads and no-dereference compare-and-swap updates.
- Publish Git objects, policy generations and migrated local files through durable atomic
  replacements; recover abandoned attachment temporaries and reject linked object-store parents,
  symlinked policy generations, unowned installer-shaped cleanup candidates and unexplained
  pointer/state drift, including a missing canonical pointer with durable activation state but no
  recovery journal. Target trees reject case-only collisions, Windows control characters and all
  reserved device aliases at every path prefix. A stable
  pre-state lock serializes first install with first uninstall. POSIX fsyncs each newly created
  directory entry and persists cross-directory destinations before source deletion; Windows uses
  write-through atomic file and junction replacements. Runtime
  and installer ancestors must remain real directories under the resolved home; cleanup also
  recovers private stale Git ref lockfiles and rejects read-only Windows attachment sources.
- Keep `operator.local.md` as an independently editable private file while bundles expose immutable
  content-addressed snapshots. `config` adopts arbitrary manual instructions, and recovery dry-run
  validates journals, owned hard-link temporaries, cleanup ownership, existing lock identity and
  raw activation refs without mutation.
  Policy recovery accepts only journal endpoints while repairing first publication and both sides of
  an authorized in-place visible edit. Legacy migration rejects reserved
  attachment names, linked policy, config includes, worktree-scoped config and
  promisor/partial-clone authority before publishing state. Migration preserves a concurrent v1.11
  policy write before the clone move and adopts provisional policy after an interrupted attempt;
  migration now explicitly requires all lockless v1.11 commands to be stopped. `config` normalizes
  BOM-marked PowerShell 5.1 UTF-16 edits to UTF-8, including journal recovery.
- Retain every activated bundle for resolved readers, verify retained bytes and attachments in
  `status`, atomically tombstone never-activated targets for crash-resumable cleanup, and report
  deduplicated storage including corrupt, unactivated, incoming and auxiliary installer state.
- Revalidate rollback targets against canonical `main`; rollback becomes available after the first
  subsequent immutable upgrade creates a safe predecessor.
- Run the shared installer acceptance state machine once per operating-system lane, with compact
  wrapper smoke tests for Windows PowerShell 5.1 and Git Bash instead of repeating every case; test
  scripts now report failures and aggregate totals instead of printing every passing assertion.
- Refuse Windows quarantine junctions, create installer directories as private `0700` even under
  `umask 000`, validate legacy authority before dry-run object reads, and make dry recovery probe the
  real POSIX lock while accepting only verified stable-policy hard-link temporaries.
- Apply raw direct-commit activation provenance in installed wrappers, validate both activation
  endpoints during recovery dry-run, reject linked `skills` ancestors before fresh dry-run success,
  and recognize verified file-attachment hard-link temporaries with the same dry/live behavior.
- Remove provisional migration policy when a restored v1.11 checkout returns to portable defaults;
  legacy post-switch dry recovery no longer requires an intentionally absent immutable predecessor.

## [1.11.0] - 2026-07-28

### Changed

- Make 800 changed lines a reviewability recommendation rather than a delivery gate, allowing larger
  coherent pull requests when splitting would create unsafe or misleading intermediate states.
