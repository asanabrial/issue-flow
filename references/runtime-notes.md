# Runtime notes

Runtime capabilities change how Issue Flow is invoked and how independent work is obtained. They do
not weaken workflow state, review independence, attribution, or durable tracker evidence.

## Invocation sigils

Claude Code invokes skills with `/`:

```text
/issue-flow analyst [domain-rules | instructions]
/issue-flow dev [domain-rules] [issue-number]
```

Codex invokes skills with `$` and rejects `/`, which resolves built-in commands only:

```text
$issue-flow analyst [$<domain-rules> | instructions]
$issue-flow dev
```

The skill and arguments are otherwise identical.

## Independent review contexts

Adversarial review is an analyst run over a bounded change. It is repository-read-only, produces
findings rather than commits, and MUST NOT be performed by the context that wrote the change.

The runtime decides how to obtain the second context: subagent, teammate, or separate session all
qualify. If the runtime or operator forbids delegation, use a separate session. Inability to spawn a
helper changes the mechanism, never the review requirement.

## Analyst enforcement and delegation

Where supported, enforce analyst read-only behavior with a tool allowlist that excludes file-writing
tools. This reduces accidental edits but does not seal the boundary: a shell can write files and is
still needed for measurements, so the prose prohibition remains authoritative.

Claude Code applies a subagent definition's tool allowlist when that definition runs as a teammate,
but does not apply the definition's `skills` field. Put required domain rule books in the teammate
prompt rather than assuming they were inherited. A delegating dev MUST load the routed domain itself
before choosing a delegation plan because that domain may prescribe orchestration constraints.

## Runtime identity derivation

Create one run-id and reuse it for the full run. Prefer `<runtime>-<session-prefix>` where the suffix
comes from the runtime's own session id. This joins remote evidence to the local transcript. Claude
Code exposes the same session prefix in its team and task paths, including
`~/.claude/tasks/session-<8 chars>/`. When no usable session id exists, use a short random suffix;
untraceable per-run identity is still safer than shared runtime identity.

## In-session teams are ephemeral

Claude Code agent teams and Issue Flow coordinate different lifetimes:

| Property | Agent-team task list | Issue Flow |
|---|---|---|
| Lifetime | one session; teams cannot be shared across sessions | repository lifetime |
| Coordination | machine-local file lock | server-side claim plus readback |
| Survives run end | no | yes |
| Reachable by another runtime | no | yes |

Use teams to parallelise within one run and issues to preserve work across runs. Parallel analyst
investigation is a strong team use case, but task status, mailbox content, and teammate context vanish
with the session. The shared task list is a temporary view, never authoritative workflow state.

A teammate that finishes without recording its result on the issue has produced no durable output.
Where available, Claude Code's `TaskCompleted` and `TeammateIdle` hooks can enforce this by refusing
with exit code 2.

Do not assume another team mechanism repairs abandonment. Claude Code teams have documented cases
where teammates fail to mark tasks complete and block dependants, requiring manual status repair.
External-session recovery therefore remains the durable workflow's responsibility.

## Honest runtime limits

- A claim is not a lock. Near-simultaneous claims can race, and single-valued or shared-account claim
  fields can hide the loser. The selected binding defines authoritative verification.
- A horizon is a heuristic, not a lease. Timestamps cannot distinguish a dead run from a live run
  thinking silently. Early reclaim costs bounded duplicate work and leaves an audit comment; never
  reclaiming leaves work stuck forever.
- Credentials must be reachable from the execution environment. Keyring tokens are often invisible
  inside sandboxes. Prove access with a cheap read before analysis that must be filed remotely.
- A tracker serializes claims, not CPU, RAM, accelerators, databases, ports, or other machine
  resources. Two valid holders can still destroy each other's heavy phases through contention.
- Issue Flow moves tasks; it does not judge them. Analyst domain rules decide value, and a standalone
  dev inherits that recorded judgement rather than silently reinterpreting it.

Before a machine-heavy phase, mention it in a heartbeat and inspect other in-progress heartbeats.
When contention kills a run, rerun the work alone before interpreting the failure. This rule follows
a live incident in which parallel A/B evaluation exhausted memory while another measurement ran;
the sequential rerun passed. Resource coordination signals are advisory and never replace durable
issue state.
