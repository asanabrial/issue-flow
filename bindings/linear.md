# Binding: Linear

How this workflow's operations are performed against Linear, and which of its assumptions Linear
satisfies natively rather than by convention. `SKILL.md` describes WHAT happens; this file is the
only place that says HOW.

Read this alongside `SKILL.md` when the operator configuration names `linear` as the tracker.

## What Linear provides

| The workflow needs | Linear | Consequence |
|---|---|---|
| A single-valued **state** per item | ✓ — native workflow states, one per issue | "exactly one state" is **enforced by the tracker**, not by discipline. `transition` cannot leave an ambiguous item |
| A **claim** applied server-side | ⚠️ — `assignee` is **singular**; `assigneeId` overwrites | **last write wins.** Re-reading the assignee does NOT detect a lost race — see *The claim hazard* below |
| **Last activity**, timestamped by the server | ✓ — `updatedAt`, plus per-comment timestamps | the stale-claim rule works as written |
| A stable, short **identity** | ✓ — `<team key>-<number>`, e.g. `ENG-123` | usable verbatim in branch and worktree names — and a branch carrying the identifier is what Linear's GitHub integration auto-links to the issue, so the naming rule IS the branch link |
| **Comments**, append-only and ordered | ✓ | heartbeats, blockers and hand-offs land here — and they are what makes claiming auditable |
| A native **priority** | ✓ — integer `0`–`4` (0 none, 1 urgent, 4 low) | but the DOMAIN owns the scale, so the label stays the source of truth; mirror into the native field only for human sorting |

**`close` is exactly `transition` here.** Linear expresses done as a workflow state of type
`completed`, and has no separate closed flag, so there is no second write and nothing to keep in step.

**Linear has no native "blocked".** Its state types are `triage`, `backlog`, `unstarted`, `started`,
`completed` and `canceled`. Create a workflow state named `Blocked` rather than encoding it as a
label — the single-valued state is the one real advantage this tracker has over GitHub here, and
pushing one of the five states into a label throws it away.

## The claim hazard — read this before implementing `claim`

This is the one place where Linear is **weaker** than GitHub, and it is not obvious.

On GitHub, assignees are a set and `--add-assignee` adds to it, so after a race both agents appear
and the re-read in step 2 sees the collision. On Linear, `assigneeId` is a single `String` and
setting it **replaces** whoever was there. Two agents claiming within the same second produce a
clean-looking issue assigned to the second one — and **the re-read tells that agent it won**, while
the first is already building.

So on Linear, `claim` verifies against two things the second writer cannot silently overwrite:

1. **The state, checked BEFORE writing.** If the issue is already in a `started`-type state, someone
   holds it. Do not claim; take the next item.
2. **The comment trail, checked AFTER writing.** Comments are append-only and server-ordered. Write
   the claim comment, then re-read the comments: **if any other claim comment precedes yours, you
   lost the race** — unassign, say you are backing off, take the next item.

The comment is not decoration here. On GitHub it is the audit trail; on Linear it *is* the race
detector, because it is the only append-only surface in the transaction.

## Transport — there is no `gh` for Linear

Linear ships **no first-party CLI**. Community ones exist and this binding does not use them: an
unofficial tool is someone's side project, and a workflow that depends on one inherits its
maintenance and its disappearance. Two first-party paths, in this order:

1. **The official MCP server** — `mcp.linear.app/mcp`, OAuth 2.1, tools for finding, creating and
   updating issues, projects and comments. Maintained by Linear, and the runtimes this skill targets
   speak MCP natively, so there is nothing to install and no token in an env var. Two honest costs:
   its tool definitions consume a meaningful slice of context while connected, and remote MCP
   connections are still young enough that a failed connect is worth retrying rather than treating as
   an outage.
2. **The GraphQL API directly** — always available, nothing to install, no context cost. This is the
   fallback that cannot break, and it is what the operations below are written against.

## Credentials

Endpoint: `https://api.linear.app/graphql`.

**A personal API key is sent WITHOUT a `Bearer` prefix** — `Authorization: <API_KEY>` verbatim. Only
OAuth access tokens take `Bearer`. Getting this wrong returns an authentication error that reads like
a bad key, so it burns time before anyone suspects the header shape.

Prove access with a cheap read before relying on the workflow:

```graphql
query { viewer { id name } }
```

## Setup

Discover the team and its states once, and record the ids in the operator configuration — they are
stable, and looking them up on every run costs a round trip for nothing:

```graphql
query { teams { nodes { id key name states { nodes { id name type } } } } }
```

Map the workflow's five states onto that team's workflow states by **type**, not by name — teams
rename states freely, but the type is what carries the meaning:

| Workflow state | Linear state type | Typical default name |
|---|---|---|
| `analysis` | `triage` or `backlog` | Triage / Backlog |
| `ready` | `unstarted` | Todo |
| `in-progress` | `started` | In Progress |
| `review` | `started` | In Review |
| `blocked` | `unstarted` (create it) | Blocked |
| `done` | `completed` | Done |

`in-progress` and `review` share the type `started`, so they must be told apart by state id, not by
type — resolve both at setup and store both ids.

## Operations

`<id>` is the issue's UUID; `<identifier>` is the human `ENG-123` form.

| Operation | Linear |
|---|---|
| `ensure_states` | query the team's `states`; create any missing one with `workflowStateCreate` |
| `create` | resolve or `issueLabelCreate` each label first, then `issueCreate(input: { teamId, title, description, stateId: <ready>, labelIds: [priority, domain, analyst] })` |
| `list_state` | `issues(filter: { state: { id: { eq: <state> } }, assignee: { null: true } })` |
| `claim` | check state is not `started`-type → `issueUpdate(id, input: { assigneeId, stateId: <in-progress> })` → write the claim comment → **re-read comments for an earlier claim** |
| `verify_claim` | one read — `issue(id) { state { id type } comments { nodes { createdAt body } } }` — then three checks, any failed check is a stop instruction, not a retry: the state type is not `completed`/`canceled`; the state id is the one you are working under (ids, not types — `in-progress` and `review` share `started`); and no comment created after your own claim comment names your run-id in a stand-down, reclaim or adjudication. Semantics in `SKILL.md`, *A heartbeat is a claim renewal* |
| `transition` | `issueUpdate(id, input: { stateId })` — single-valued, so nothing to remove |
| `comment` | `commentCreate(input: { issueId, body })` |
| `last_activity` | `issue(id) { updatedAt comments { nodes { createdAt body } } }` |
| `label` | resolve the label id, `issueLabelCreate` if absent, then `issueUpdate(id, input: { labelIds })` — the full set, so read-modify-write |
| `unassign` | `issueUpdate(id, input: { assigneeId: null, labelIds: <current minus dev:runtime> })`. **Exception — releasing work another run still holds** (lost race, stand-down): remove only your `dev:<runtime>` label; `assigneeId` is singular and currently names the winner, so nulling it strips the active holder |
| `close` | `transition` to the `done` state (type `completed`), with a comment stating what was verified |

**`label` replaces the whole set.** Unlike `gh issue edit --add-label`, `labelIds` is the complete
list; sending only the new one silently drops the rest, including the attribution and domain labels
this workflow depends on. Read the current ids, append, then write.
