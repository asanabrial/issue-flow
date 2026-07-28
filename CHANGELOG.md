# Changelog

All notable changes to Issue Flow are documented here.

## [1.12.0] - 2026-07-28

### Changed

- Replace partial and in-place upgrades with immutable bundles bound to one verified Git commit and
  tree. POSIX activates a complete bundle through an atomic symlink replacement; Windows atomically
  retargets the canonical junction.
- Retire `sync --from <SKILL.md>` because one file cannot prove its required references and assets.
  Both shell entrypoints now delegate to one Python transaction implementation.
- Preserve operator policy and ignored top-level runtime state during the journaled legacy-clone
  migration, retain the previous bundle for `rollback`, and provide explicit `recover` handling.
- Harden bootstrap and recovery authority with portable path rejection, operating-system locks,
  isolated Python imports, disabled Git hooks, core-only repository config, Git-bound rollback
  verification and post-switch activation provenance.
- Publish Git objects, policy generations and migrated local files through durable atomic
  replacements; recover abandoned attachment temporaries and reject linked object-store parents,
  symlinked policy generations and unexplained pointer/state drift.
- Retain every activated bundle for resolved readers, verify retained bytes and attachments in
  `status`, and report deduplicated storage including corrupt and auxiliary installer state.

## [1.11.0] - 2026-07-28

### Changed

- Make 800 changed lines a reviewability recommendation rather than a delivery gate, allowing larger
  coherent pull requests when splitting would create unsafe or misleading intermediate states.
