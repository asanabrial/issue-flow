# Domain composition and routing

Issue Flow owns transport. Domain rule books own business judgement. Keep that boundary explicit so
the same workflow can serve unrelated projects and the same domain can move between transports.

## Activation and scope

- The role belongs to Issue Flow. A domain is a rule book read by a role, not a role itself.
- Analyst conditions are optional. Conditions and repository rules constrain analysis; they do not
  replace its scope.
- A bounded target such as a diff, review, or issue is already the scope. Do not widen it.
- Without conditions or a repository domain, the analyst uses the autonomous `general` contract
  below. Never stop merely because no domain was supplied.
- A standalone dev invocation is complete. Build the selected issue to its acceptance criteria and
  repository conventions without inventing domain gates.
- Add dev-side domain rules only for requirements an issue should not repeat, such as measurement
  discipline, mandatory benchmarks, or ship gates.

## Ownership boundary

| Transport owns | Domain owns |
|---|---|
| workflow roles, states, claims, and transitions | what is worth doing and why |
| the issue skeleton | how findings are prioritised |
| how work is taken, released, stored, and moved | what evidence a finding must carry |
| parallel-dev isolation | what `done`, `correct`, and worth shipping mean |
| creating and projecting markers | naming the marker values the finding requires |

A domain MUST NOT name a transport, command, state, label implementation, issue field, or body
section. It supplies domain values and evidence; transport decides how to store and move them.
Issue Flow MUST NOT name a project domain or decide whether a domain finding is valuable.

Analysis is cheap, parallel, and repository-read-only. Implementation is expensive, serial, and
risky because it needs isolated repository work, verification, and review. This split lets a runtime
without filesystem write access remain a first-class analyst while keeping implementation isolated.

## Analyst handoff

Every domain finding MUST provide all of these values:

| Field | Contract |
|---|---|
| `identity` | Stable domain-controlled key. Equivalent findings collide even when wording or run changes. |
| `title` | One line stating what would change. |
| `priority` | A value from the declared domain scale plus a finding-specific rationale. |
| `body` | The required issue sections completed in domain terms. |
| `metadata` | Freshness or provenance data rendered verbatim; transport does not interpret it. |
| `domain` | Stable domain identifier plus the analysis and implementation rule-book route. |

The domain names marker values but does not create or project them. If any handoff field is missing,
name the missing field and STOP rather than inventing a convention.

### Priority scale contract

A priority value alone is invalid. Its scale contract MUST:

1. name the scale;
2. enumerate every allowed value;
3. define a strict ordering over those values;
4. define what every value means.

Before filing, verify that the finding uses one allowed value and gives a case-specific rationale.
Do not infer vocabulary such as `priority`, `severity`, or `tier` from familiar-looking values.

Priority is comparable only within one declared scale. Partition mixed-domain work by stable domain
identifier and scale, order each partition by its own contract, and use oldest first only among the
partition heads unless the caller selected a domain. Never manufacture a global rank: `tier:2` is
neither above nor below `priority:high`.

For dev work, the domain is optional. It contributes equal-priority tie-breaking and requirements
that must be true before completion. Without an implementation rule book, issue acceptance criteria
define done and repository conventions define how to build.

Acceptance criteria MUST be explicit before implementation starts. A partial improvement does not
satisfy an issue that specifies a complete outcome and MUST NOT ship as complete.

## Stable routing

Routing is recorded data, not inference:

- `domain:<name>` is the transport's query label. `<name>` is a stable subsystem identifier, never a skill name.
- `domain: <analysis-rules> -> <implementation-rules>` records the loadable rule books.

Reuse an existing stable domain identifier rather than minting one per rule book. One subsystem can
route through several rule books while retaining one query identity.

The arrow has non-interchangeable sides. The analyst loads the left-hand rule book; the dev loads the
right-hand rule book. Analysis rules define what is worth filing and its evidence bar. Implementation
rules define what makes the change done. Loading the wrong side gives a role the wrong job's rules.

When routing names only one rule book, it records the analysis book. The dev MUST NOT load it and
proceeds as if implementation routing were absent. When routing is absent, state which rules apply,
including `none, building to the acceptance criteria as written`. A domain label alone is not
routing because it need not name a loadable rule book. Single-domain projects may keep the route
implicit; shared multi-domain queues may not.

## Autonomous `general` fallback

Use this contract only when neither caller nor repository supplies a domain scale:

- Stable domain identifier: `general`.
- Routing: `domain: issue-flow#autonomous-discovery -> none`.
- Scale: `priority`, ordered `critical > high > medium > low`.
- `critical`: immediate material harm or unusability.
- `high`: major correctness, security, reliability, or performance impact.
- `medium`: bounded user or engineering impact.
- `low`: worthwhile improvement with minor impact.
- Identity: derive a stable key from subsystem plus failure or opportunity, never current run or
  wording.

Inspect code, tests, configuration, documentation, history, and tracker context. Treat security as a
first-class axis: authentication and authorization boundaries, validation and injection paths,
secret or sensitive-data exposure, insecure defaults, supply-chain risk, cryptographic misuse,
privilege escalation, and abuse cases. Also inspect correctness, reliability, performance,
maintainability, missing tests, architectural debt, developer experience, failure paths, invariants,
hotspots, and mismatches between documentation and behaviour.

Challenge assumptions and compare intent with behaviour, but do not manufacture a backlog. Search
broadly and converge on ONE strongest evidenced finding. Confirm it is not already tracked,
intentionally accepted, or contradicted by repository history. Prefer root causes and leverage over
cosmetic symptoms. If no defensible finding survives, file nothing and report the areas and evidence
checked; never lower the bar merely to produce an item.

## Attribution boundary

Attribution belongs to transport, not the domain. Mint one identity per run and reuse it for every
write: `<runtime>-<session-prefix>`. Derive the suffix from a runtime session id when available so an
issue can be joined to its transcript; otherwise use a short random suffix. Per-runtime identity is
insufficient because concurrent sessions of one runtime must remain distinguishable.

Shared tracker accounts make native author and assignee fields ambiguous, so attribution has two
cardinalities:

| Value | Storage contract | Lifecycle |
|---|---|---|
| bounded runtime, such as `codex` | label `analyst:<runtime>` or `dev:<runtime>` | analyst history persists; dev holding is live-state data |
| unbounded run-id, such as `codex-b91c` | body and comment text | persists as the audit trail |

Do not create one label per run; that produces unbounded dead labels. Analyst output ends
with `Analysed by <run-id> on <date>`. Dev claim and close evidence carry the same run-id, and branch
or review names reuse it where repository conventions permit.

Remove `dev:<runtime>` whenever the run releases work, loses a race, is reclaimed, or hands work
back. Keep it on delivery because the runtime held the work through close. Analyst markers are
historical and never removed. If label-grouped boards become noisy, group by the state field rather
than dropping attribution; attribution must survive board replacement.
