# Binding: Trello

How this workflow's operations are performed against Trello, and which of its assumptions Trello
satisfies natively rather than by convention. `SKILL.md` describes WHAT happens; this file is the
only place that says HOW.

Read this alongside `SKILL.md` when the operator configuration names `trello` as the tracker.

## What Trello provides

| The workflow needs | Trello | Consequence |
|---|---|---|
| A single-valued **state** per item | ✓✓ — a card is in exactly one list, by construction | the strongest of the three. `transition` is a single field write and cannot leave an ambiguous card |
| A **claim** applied server-side | ⚠️ — `idMembers` is an array and members are *added*, **but agents usually share one Trello member** | with distinct members a lost race shows two members; with a shared one the array holds a single member however many runs claimed — verify against the comment trail, exactly as the GitHub binding must |
| **Last activity**, timestamped by the server | ✓ — `dateLastActivity` on the card | the stale-claim rule works as written, and costs one field read |
| A stable, short **identity** | ⚠️ — `idShort` is numeric but **unique only within its board** | see *Identity* below; this is Trello's weak point |
| **Comments**, append-only and ordered | ✓ — card actions of type `commentCard` | heartbeats, blockers and hand-offs land here |
| A native **priority** | ✗ | encoded as a board label `<scale>:<value>`; the domain names the scale |

Worth noticing how the three trackers converge. Linear's claim field is overwritten; GitHub's and
Trello's are shared by every agent on one account; and all three end in the same place: **the
comment trail is the only surface that can adjudicate a race**, because it is the only one that is
append-only and server-ordered. What actually distinguishes Trello is elsewhere — the strongest
state model of the three against the weakest identity. Nothing is uniformly better, which is
exactly why each binding declares its own gaps instead of the workflow assuming a shape.

## Identity — the one thing to get right before using this binding

`idShort` is the number a human sees on a card, and it restarts on every board. Two boards feeding
one repository will both produce a `#42`, and a branch named after it points at two different pieces
of work.

Use one of these, and write the choice into the operator configuration:

- **`shortLink`** — an opaque, globally unique string. Safe everywhere, less readable in a branch
  name.
- **`<board key>-<idShort>`** — readable and unique, where *board key* is a short prefix you assign
  per board. Do this if humans read the branch names.

Do not use bare `idShort` unless exactly one board will ever feed the repository, and say so in the
configuration if you make that bet.

## States are lists

The workflow's five states plus *closed* map to six lists, created once per board in this order:

| Workflow state | List |
|---|---|
| `analysis` | Analysis |
| `ready` | Ready |
| `in-progress` | In Progress |
| `review` | Review |
| `blocked` | Blocked |
| `done` | Done |

**Do not use Trello's `closed` field for the workflow's `done` state.** `closed: true` means
*archived* — the card leaves the board entirely, taking its history out of sight of every agent that
might later need to read why something was done. Move the card to **Done** instead, and archive later
as housekeeping if the board gets long.

Board labels are still needed, for attribution (`analyst:<runtime>`, `dev:<runtime>`), for the domain
(`domain:<name>`) and for priority. Create them once on the board; unlike states, these are genuinely
multi-valued and belong on labels.

## Transport

Trello ships **no first-party CLI and no first-party MCP server**. Community ones exist and this
binding does not use them, for the same reason it does not elsewhere: a workflow built on someone's
side project inherits its maintenance and its disappearance. **The REST API is the whole transport
here**, and the operations below are written against it.

## Credentials

Base: `https://api.trello.com/1`. Every call authenticates with **two query parameters**, an API key
and a token: `?key=<key>&token=<token>`. There is no header form and no `Bearer` anything.

That has a consequence worth stating plainly: **credentials travel in the URL**, so they land in
shell history, in process listings and in any log that records full request lines. Keep them in
environment variables and never paste a full URL into an issue, a comment or a commit message.

Prove access with a cheap read before relying on the workflow:

```
GET /members/me?key=<key>&token=<token>
```

## Operations

`<id>` is the card id or its `shortLink`; either works wherever an id is accepted.

| Operation | Trello |
|---|---|
| `ensure_states` | `GET /boards/{board}/lists`; create any missing one with `POST /boards/{board}/lists?name=<state>` |
| `create` | ensure the board labels exist (`POST /boards/{board}/labels`), then `POST /cards?idList=<ready>&name=<identity>: <title>&desc=<body>&idLabels=<priority,domain,analyst>` |
| `list_state` | `GET /lists/<state>/cards?fields=id,idShort,shortLink,name,idMembers,dateLastActivity,labels` then keep those with an empty `idMembers` |
| `claim` | `POST /cards/{id}/idMembers` with your member id, write the claim comment, then **read the `commentCard` actions** — an earlier claim than yours means you lost. Re-reading `idMembers` catches the race only when every agent is a distinct member |
| `verify_claim` | one read — `GET /cards/{id}?fields=idList,closed&actions=commentCard` — then three checks, any failed check is a stop instruction, not a retry: `closed` is false (archived is Trello's killed); the card is in the list you are working under; and no `commentCard` action created after your own claim comment names your run-id in a stand-down, reclaim or adjudication. Semantics in `SKILL.md`, *A heartbeat is a claim renewal* |
| `transition` | `PUT /cards/{id}?idList=<target list>` — single field, nothing to remove |
| `comment` | `POST /cards/{id}/actions/comments?text=<text>` |
| `last_activity` | `GET /cards/{id}?fields=dateLastActivity` |
| `label` | `POST /boards/{board}/labels` if it does not exist yet, then `POST /cards/{id}/idLabels` with its id — **adds**, it does not replace |
| `unassign` | `DELETE /cards/{id}/idMembers/{memberId}` **and** `DELETE /cards/{id}/idLabels/{devLabelId}`. **Exception — releasing work another run still holds** (lost race, stand-down): remove only your `dev:<runtime>` label; agents usually share one Trello member, so deleting the membership strips the active holder too |
| `close` | `transition` to **Done**, with a comment stating what was verified |

Two conveniences worth knowing, because they remove the read-modify-write dance Linear forces:
`idMembers` and `idLabels` are both **additive** endpoints. Adding a label does not disturb the
others, and adding a member does not evict whoever was there — which records a race when agents are
distinct members, and either way keeps `unassign` from trampling anyone.

## Recording the branch

Trello has no development sidebar: the branch is recorded as a comment the moment it exists
(`Branch: <name>`), and the closing comment names the delivering commit SHA — the card outlives the
branch, so the SHA is the join that survives.

## Reading comments

Comments are card *actions*, not a first-class collection:

```
GET /cards/{id}/actions?filter=commentCard
```

The API's default filter is `commentCard` and `updateCard:idList` — comments and list moves. That
default is useful on its own: it returns the two things this workflow writes, in server order, which
is a ready-made audit trail of who claimed what and when it moved.
