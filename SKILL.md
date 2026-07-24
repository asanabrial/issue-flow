---
name: issue-flow
description: "Trigger: issue-flow analyst [conditions], issue-flow dev, analyst, developer, claim issue, move task states. Analysts file at most one evidenced issue; without conditions, audit the repository critically."
metadata:
  author: asanabrial
  version: "1.9.0"
---

# Issue Flow — Analyst / Dev over a shared issue tracker

## What this skill is, and what it is NOT

It defines the **workflow**: two roles, one state machine, one shared board. It is deliberately
**domain-agnostic** — it does not know what your project considers worth analysing or what "done"
means.

**The ROLE belongs to this skill. The domain is a RULE BOOK, not a role.** A domain skill does not
"act as the analyst"; it states what analysis means for its subject, and this skill performs the
role by those rules. The same domain can be read by either role, and a project supplies one rule
book per side rather than one skill per role.

```
/issue-flow analyst [domain-rules | instructions]    ← conditions OPTIONAL
/issue-flow dev     [domain-rules] [issue-number]    ← domain OPTIONAL
```

**Runtime sigil.** Claude Code invokes skills with `/`; **Codex uses `$` and rejects `/` outright**
(there, `/` resolves built-in commands only, so `/issue-flow` returns "Unrecognized command"). Same
skill, same arguments, different prefix:

```
$issue-flow analyst [$<domain-rules> | instructions]
$issue-flow dev
```


**The two roles use conditions asymmetrically.** The analyst accepts optional conditions to focus
its judgement; without them it audits the repository under the autonomous discovery contract below.
The dev's input is one already-specified issue, so the issue itself bounds the work. **The
specification IS the scope.**

- **Analyst conditions are optional.** Always obey applicable repository instructions and domain
  rules; they constrain how analysis is performed and never replace its scope. Use explicit
  conditions or a bounded target such as a diff, PR or issue as the scope. If none was supplied,
  enter the autonomous repository-discovery mode below. Never stop merely because the caller
  supplied no domain.
- **A bounded target is already a scope.** Analyse that target without inventing wider scope.
- **With no conditions, audit the repository itself.** First discover and obey applicable local
  rules. Then inspect current code, tests, configuration, documentation, history and tracker context
  for defensible bugs, security or reliability risks, performance and maintainability problems,
  missing tests, architectural debt, and concrete product or developer-experience improvements.
  Treat security as a first-class search axis: inspect authentication and authorization boundaries,
  input validation and injection paths, secret and sensitive-data exposure, insecure defaults,
  dependency and supply-chain risk, cryptographic misuse, privilege escalation and abuse cases.
  Be constructively dissatisfied: challenge assumptions, trace consequences, compare intent with
  behaviour, and look for evidence that the current solution can be better. Do not manufacture a
  backlog; file only a finding supported by reproducible evidence and meaningful impact.
- **`/issue-flow dev` alone is a complete, valid invocation.** Take the highest-priority ready
  issue and build exactly what it specifies, following the target repo's own conventions (its agent
  instructions, or the default flow below where those are silent). Most work needs nothing more.

Add domain rules to the dev only when the project has extra requirements for what counts as a
*real* change — a measurement discipline, mandatory benchmarks, ship gates, a versioning policy the
issue cannot restate every time. Then the issue says WHAT, and the domain says what makes it TRUE.
If you were given no domain, do not invent those requirements either: build what the issue asks and
say what you verified.

### Review is this role, not a separate capability

Worth stating because it is easy to miss: **an adversarial review of a change is an analyst run over
a bounded target.** Read-only, produces findings rather than commits, safe to run several of in
parallel, and it must not be the context that wrote the code — which is the entire definition of this
role, arrived at from a different direction.

So a project that requires review before delivery is not asking for a capability this workflow
lacks. It is asking for a **second analyst context** pointed at the diff. What it must NOT be is the
same run reviewing itself: a context that just argued its way to a design will find that design
convincing, which is precisely what the gate exists to catch.

**How that second context is obtained is the runtime's business, not this skill's.** A sub-agent, a
teammate on a team, or simply another session started against the same issue all satisfy it equally.
Where a runtime forbids spawning helpers without the operator asking, the separate session is always
available and costs nothing but wall-clock — so "I cannot delegate" is never a reason to skip the
review, only a reason to run it differently.

## Composition contract — who owns what

This split is the whole design, so it is worth stating precisely:

| This skill owns — the TRANSPORT | The domain owns — the BUSINESS RULES |
|---|---|
| the tracker, its states, claiming and transitions | what is worth doing, and why |
| the issue skeleton every finding fills | how findings are prioritised |
| how a dev takes and releases work | what "done" and "correct" mean |
| where work is stored and how it moves | what evidence a finding must carry |
| keeping parallel devs from corrupting each other | what makes a change worth shipping |

**The domain must never name a transport.** A domain skill says "a finding carries a priority, an
identity and this evidence"; it does not know those become a label, a workflow state, an issue
title or a body section. That is what lets the same domain run over a different tracker later, and what lets
this skill serve projects that share no vocabulary.

Symmetrically, **this skill must never name a domain**. It has no opinion on what your project
considers worth doing.

### What the domain hands over

**For the ANALYST role**, each finding it produces must supply:

- `identity` — a stable key the domain controls, so two analysts naming the same thing collide
  deterministically. Reused as the issue title prefix.
- `title` — one line, what would change.
- `priority` — a value on the domain's own scale, plus the reason for it. Mirrored as a label
  `<scale>:<value>`; the domain names the scale.
- `body` — the sections below, filled in domain terms.
- `metadata` — whatever the domain needs to judge freshness or provenance later. Rendered verbatim
  into the issue; this skill never interprets it.
- `domain` — the name of the rule book this analysis ran under, and of its implementation
  counterpart if the project has one. Recorded so the dev does not have to guess (see below).

**The domain names these markers; it does not create them.** Some trackers refuse to attach a label
that does not already exist, and the domain's priority scale and rule-book name are by definition not
in any setup script — so `create` and `label` must bring their own labels into existence first. A
binding that skips this fails on the analyst's very first filing, which is the worst possible moment
to discover it.

If a domain omits any of these for the ANALYST, say which one is missing and stop. Filling the gap
by inventing a convention is how two agents end up with two incompatible boards.

**A priority value is not a scale contract.** A scale contract MUST name the scale, enumerate every
allowed value, define their strict ordering, and state what each value means. Before filing, verify
that the finding's value belongs to that contract and carries a case-specific rationale. If any part
is absent or ambiguous, STOP: do not infer `priority`, `severity`, `tier`, or any other vocabulary
from familiar-looking values.

**Priority is ordered only inside one declared scale.** Never compare values from different scales:
`tier:2` is neither above nor below `priority:high`. For a mixed-domain `list_state`, partition the
queue by `domain:<name>` and priority scale, order each partition by its own contract, and use oldest
first only to choose among partition heads unless the caller selected a domain. This preserves a
deterministic queue without pretending unrelated business scales share meaning. A binding may expose
the partitions differently, but it must not manufacture a global priority rank.

**For the DEV role the domain is optional**, and supplies only what an issue cannot carry per-issue:
tie-breaking when priorities are equal, and what must be TRUE before work is called done — a
measurement discipline, required benchmarks, ship gates. With no domain, the issue's own acceptance
criteria are the definition of done, and the repo's conventions govern how you build.

## Tracker binding — the operations this workflow needs

Everything below is written in **operations**, not commands. Which tracker performs them, and how,
lives in one file per tracker under `bindings/`, selected by the operator configuration at the end of
this file. Load that one file and ignore the rest; no command for any tracker appears outside it.

| Operation | What it must do |
|---|---|
| `ensure_states` | make the state vocabulary exist, once per project |
| `create` | file a new item in `ready` (or `blocked`) carrying the analyst's body **and every marker the finding supplies** — priority, domain and attribution, not just the state |
| `list_state` | list unclaimed items in a given state; order within a declared domain/scale, and expose mixed-domain partitions without globally comparing their values |
| `claim` | take the item server-side, then **verify you actually hold it** by the means the binding names |
| `verify_claim` | re-read the authoritative control surface for an item you hold and answer two questions: am I still its holder, and has anything been said to this run-id since my claim? — what every heartbeat and every irreversible step checks **before** it writes |
| `transition` | move to exactly one state, dropping the previous one in the same call |
| `comment` | append a note the server timestamps |
| `last_activity` | when the item was last touched — what the stale-claim rule reads |
| `label` | attach a `<key>:<value>` marker queryable without opening the item, **creating the label first where the tracker requires one to exist** |
| `unassign` | release the item without changing its state, **and drop your runtime's attribution marker** — it records who is holding the work, not who once touched it |
| `publish_version` | for every delivered version bump, publish and remotely verify an immutable annotated component tag on the delivered revision; declare the capability unavailable and fail closed when the repository host cannot do it |
| `close` | mark it delivered |

**A binding must also declare what its tracker does NOT provide.** An absent capability that goes
undeclared is how a rule silently stops applying: a tracker with no server-side `last_activity` has
no stale-claim rule at all, and leaving that unsaid means abandoned work sits forever while this
document cheerfully claims it cannot. Name the gap in the binding, so a missing rule is visible
instead of merely untrue.

## Attribution — which agent did what

Every run mints ONE identity at start and reuses it for everything it writes:

```
<runtime>-<session-prefix>      e.g. codex-b91c, claude-code-60fabae1, kimi-3b1d
```

**Per RUN, not per runtime.** Two sessions of the same runtime working the same hour must be
distinguishable, or "claude-code did it" tells you nothing about which of the three did.

**Derive the suffix from the runtime's own session id when it exposes one** — the first characters
are enough. A random suffix identifies a run but leads nowhere; a derived one **joins the issue to
the local record of that run**, so a stuck claim or a wrong close can be traced back to the
transcript that produced it. Claude Code exposes its session id in the paths it hands the session and
keys its own team and task directories on the same prefix (`~/.claude/tasks/session-<8 chars>/`), so
matching that convention costs nothing and buys the join. Where a runtime exposes nothing usable,
fall back to a short random suffix and carry on — an untraceable identity still beats a shared one.

**Why this cannot be left to the tracker.** Agents typically authenticate as the SAME account, so
the author and assignee fields show one human for everything. Attribution that is not written
explicitly does not exist.

Record it at **two levels, split by cardinality** — this is the part that matters:

| What | Distinct values | Where | Why there |
|---|---|---|---|
| **runtime** — `codex`, `claude-code`, `kimi` | a handful, stable | **label**: `analyst:<runtime>`, `dev:<runtime>` | queryable and visible without opening the card |
| **run-id** — `codex-b91c` | one per run, unbounded | **text** in the body and comments | a label per run would leave hundreds of dead labels in a week |

Labels are what make attribution *usable*: one `label` query answers "what is that runtime holding
right now" without opening a single item. Parsing prose for the same answer is fragile — any
rewording breaks it. Reserve labels for the bounded set and text for the unbounded one; mixing that up is how
a label list rots.

Concretely:

- **Analyst** — add `analyst:<runtime>`, and close the issue body with
  `— Analysed by <run-id> on <date>`.
- **Dev** — add `dev:<runtime>` when claiming, and carry the run-id in the claim and close comments:
  `Claimed by <run-id>, expect to report by <time>` … `Verified by <run-id>: <what was actually
  checked>`.

Use the same run-id in the branch or PR name where the repo's conventions allow it, so the code and
the issue can be joined later without guessing.

**`dev:<runtime>` means holding, so it comes off the moment you stop.** Every path that releases work
— losing a claim race, hitting a blocker, being reclaimed, sending an issue back to `analysis`,
returning a partial diagnosis — removes it along with the assignee. Leave it on and the one query the
label exists for, *what is that runtime holding right now*, answers with work that runtime let go of
days ago; and an issue that changes hands twice ends up wearing two of them, so the answer is not
merely stale but ambiguous. **Delivery is not release**: on `close` the marker stays, because the
runtime did hold the work through to the end and closed items fall outside the "what is open and
held" question the label answers. The analyst's marker is different again — it records who analysed,
which stays true forever, so it is never removed and a second analyst simply adds its own.

**One caveat, stated rather than hidden.** On a board grouped by labels, every label family becomes
its own set of columns, so these two will appear alongside the state and priority families. That is
a property of the grouping choice, not of attribution: any project already carrying a priority label
has the same effect today. If the board gets noisy, the fix is to group by the state field and keep
labels for querying — do not drop the attribution, which is the only record that survives a board
being reconfigured or replaced.

## Routing — the issue names its own domain

A dev picking up work needs to know **which rule book applies**, and inferring it from the issue
text is not good enough: skills load by matching their description against the request, which
usually works and sometimes does not. When it does not, the dev builds without the project's
measurement discipline or ship gates and nothing announces the omission — the silent failure this
whole split exists to prevent.

So the routing is **data, not inference**. The analyst records the domain on the issue:

- a label `domain:<name>` — queryable, and lets a dev filter to work it is equipped for;
- a line in the metadata block: `domain: <analysis-rules> → <implementation-rules>`.

**The label's `<name>` is a stable domain identifier, never a skill name.** `<analysis-rules>` and
`<implementation-rules>` are the two rule books the arrow already carries — they do not need a second
home. Check `list_state`'s tracker for an existing `domain:<name>` before minting one: reusing
`domain:engine` across every engine issue is what lets a dev filter to it at all, and a fresh
`domain:propose-engine` label — a skill name wearing the domain field's shape — is invisible to that
filter and answers a question nobody asked. One label per subsystem, however many rule books route
into it.

**The arrow has sides, and the sides are not interchangeable.** The analyst loads the LEFT-hand
book; the dev loads the RIGHT-hand one. An analysis rule book defines what is worth *filing* — its
priorities and its evidence bar — not what makes an implementation *done*; a dev that loads it has
not made a smaller mistake than loading nothing, it has adopted the wrong job's rules while
believing itself covered. When the line carries only one name, that name is the book the analysis
ran under: the dev does NOT load it. It proceeds as if the field were absent, and says so.

If the field is absent entirely (a legacy issue, or one filed by hand), do not guess silently: say
which rules you are proceeding under — including "none, building to the acceptance criteria as
written" — so the choice is on the record and reviewable. **The `domain:<name>` label alone is not
routing**: it is a query key, and nothing guarantees the name matches any loadable rule book.

A project with a single domain can leave this implicit; a repo where several independent subsystems all file into one
board cannot, and that is the case worth designing for.

## Why the split exists

Two different constraints, deliberately separated:

- **Analysis is cheap, parallel and safe.** Several analysts can run at once. Nothing they do can
  break a build, because they write no code.
- **Implementation is expensive, serial and risky.** It needs a branch, a worktree, tests and a
  review.

Separating them lets you fan out the cheap half without paying for the risky half. It also lets a
runtime that **cannot write to the filesystem** still contribute: an analyst's only output is a
tracker item, which is network, not disk. A sandboxed agent that cannot create a worktree can still
be a first-class analyst.

---

## Role: ANALYST

**Hard boundary — the analyst NEVER:**

- edits, creates or deletes any file in the repository;
- creates a branch, a worktree, a commit or a PR;
- runs anything that mutates state (migrations, syncs, writes to a database, anything that ships).

**The analyst freely DOES:** read any file, run read-only commands and queries, run analysis tools
that write only to a scratch directory outside the repo, and read CI, logs and history. "Only
analyses" is not "only reads docs" — run whatever you need to reach a defensible conclusion, as long
as it leaves no trace in the repo.

Its tracker output is **at most one item**: file the strongest defensible finding, or file nothing when no finding survives verification.

### Before analysing anything new, drain `analysis`

`list_state(analysis)` first. Items land there two ways, and both mean somebody already did the work
of justifying them: a dev **returned** one as wrong, or a dev **stubbed** a discovery it found while
building something else and had no mandate to analyse.

**Analysing the whole project from scratch while a queue of already-motivated findings sits ignored
is backwards**, and it is not a hypothetical: `analysis` is the one state with no natural claimant.
An item in `ready` gets pulled by priority; one in `in-progress` gets rescued by the stale-claim
rule; a blocked one names its own exit condition. Nothing surfaces `analysis` unless a role goes
looking, so if this role does not, items entering it are simply never seen again.

Take them oldest first — a returned issue is blocking a dev who already tried.

**Then sweep `blocked`, before looking at the project at large.** `list_state(blocked)`, and for each
item read the condition it named and judge whether it still holds. This belongs to this role and not
to the dev: deciding whether "the vendor API is back" or "the embargo date has passed" is *judgement
over evidence*, which is what this role does, and it needs no branch and no worktree to do it.

- **Condition discharged** → move it to `ready` and say what changed. It re-enters the queue by
  priority like anything else.
- **Still holding** → leave it, and say so with the date. An item nobody has confirmed in weeks reads
  as forgotten even when it is correctly blocked.
- **Waiting on a person** → this is the one that rots. Say plainly, on the issue, how long it has
  waited and who owes the decision. You cannot make it for them, and pretending the item is merely
  blocked hides that the project is stalled on an unasked question.

Only then look at the project at large.

**Enforce the boundary rather than trusting it.** The three rules above are prose, and prose is what
an agent follows right up until it convinces itself there is a good reason not to. Where the runtime
lets you declare an agent type with a tool allowlist, declare the analyst as one and leave the
file-writing tools out: a run that was never handed `Edit` or `Write` cannot talk itself into a quick
fix. Claude Code applies a subagent definition's `tools` allowlist to a teammate spawned from it, so
one definition covers both a delegated analysis and a teammate on a team.

Two limits, stated rather than glossed over. **A shell tool is a file-writing tool** — the analyst
needs one to measure anything, so allowing it reinforces the boundary without sealing it, and the
prose rule still carries the weight for whatever runs through the shell. And **a subagent
definition's `skills` field is not applied when it runs as a teammate**, so the domain rule book
cannot be pinned there; name it in the spawn prompt instead. That is the same routing problem the
issue solves with its `domain:` field, arriving one layer earlier.

### Autonomous discovery when no conditions are supplied

Use this fallback contract only when neither the caller nor the repository supplies a domain scale:

- Domain identifier and label: `general`; routing metadata: `domain: issue-flow#autonomous-discovery → none`, naming this built-in analysis contract and explicitly declaring that no implementation rule book exists.
- Priority scale: `priority`, ordered `critical > high > medium > low`. `critical` means immediate
  material harm or unusability; `high` means major correctness, security, reliability or performance
  impact; `medium` means bounded user or engineering impact; `low` means worthwhile improvement with
  minor impact. Give a finding-specific rationale.
- Identity: derive a stable key from the affected subsystem and failure or opportunity, not from the
  current run or wording.
- Search broadly but converge on ONE strongest evidenced finding. Confirm it is not already tracked,
  intentionally accepted, or contradicted by repository history before filing.
- Prefer root causes and leverage over cosmetic symptoms. Absence of an obvious defect is not proof
  that the repository has no opportunity; inspect boundaries, failure paths, invariants, tests,
  performance hotspots and mismatches between documentation and behaviour.
- If no defensible finding survives verification, report that no issue was filed and list the areas
  and evidence checked. Never lower the evidence bar merely to produce an item.

### What the analyst produces

An issue a developer can pick up **without redoing the analysis**. If the dev has to re-derive your
reasoning, the analysis bought nothing.

```markdown
## Description for dumb humans

> [!NOTE]
> One or two sentences, plain language, no jargon, no file paths, no metrics — what this issue is
> about, for someone who will never read past this line. Written in whatever language the operator
> configuration names for this installation (see the table at the end of this file). The header
> itself is the fixed, literal title above — always in English, always that exact phrase, never
> reworded per issue. `> [!NOTE]` is GitHub's native alert syntax — it renders as a bordered,
> coloured callout, which is the point: this has to be visually impossible to miss on an issue
> otherwise full of technical prose.

This is the first thing on the issue, before `## Problem`.

## Problem
What is wrong or missing, and how you know. Evidence, not assertion:
file:line references, measured numbers, logs, reproduction steps.

## Why it matters
Impact if left alone. If you cannot state one, say so plainly — a documented
"low impact, filed for completeness" is honest and lets the dev deprioritise.

## Proposed approach
The design. Alternatives you considered and why you rejected them.
If you are NOT confident, say which part is uncertain and what would settle it.

## Acceptance criteria
Checkable statements. "Faster" is not a criterion; "p95 under 200 ms measured
by X" is. Include what must NOT change (invariants, byte-identical outputs).

## Out of scope
What a dev should explicitly not do here. This prevents scope creep more
reliably than any amount of prose in the sections above.

## Evidence
Commands run, files read, numbers measured. Enough that a reviewer can
re-check the analysis without repeating it.
```

Close the body with the attribution line (`— Analysed by <run-id> on <date>`), then **`create` it in
`ready`** — the operation is what carries the priority, the `domain:` marker and your attribution
onto the item, so use it rather than setting the state by hand and losing the rest. Then STOP. The
analyst does not implement, does not assign itself, and does not open a PR.

If the analysis is incomplete — blocked on data, on a decision, on something unavailable — file it
anyway as `blocked` with what is missing named explicitly. A blocked issue with a precise
blocker is useful. A silent gap is not.

---

## Role: DEV

Picks up analysed work and implements it.

1. **Select — but drain `review` first.** `list_state(review)` for unassigned items before you look
   at `ready`. Those are changes that are **already built and published**, parked somewhere in
   review, CI or delivery, and every one of them is closer to shipping than anything you could start
   today. **Finishing beats starting**: an unclaimed `review` item outranks the whole `ready` queue
   regardless of priority, because its cost is already sunk and its branch rots while it waits.
   Re-read what blocked it — a permission, a gate, a decision — and check whether it still holds
   before assuming it does; most of them are one changed condition away from closing. **Claim it
   exactly like a `ready` item** — step 2 applies unchanged — before touching anything: parked work
   is still contended work.

   Only when nothing is parked in `review`: call `list_state(ready)` for unassigned work. For every
   candidate with a valid scale contract, partition by `domain:<name>` and scale, select the highest
   value inside each partition, then choose the oldest partition head; never compare the values of
   two heads. If the caller selected a domain, choose that partition's highest-ranked oldest item
   instead. Only when no candidate has a valid scale contract, prefer the oldest ready issue over
   the newest. Do not silently treat a legacy or malformed priority marker as a valid contract.
2. **`claim`**, then **verify the claim held**. A server-side tracker is the shared board, which is
   what makes this work at all; it is not a mutex.
   **How you verify is the binding's business, and it is not the same everywhere.** Where claims
   accumulate, re-reading them shows the collision directly. Where a claim *replaces* whoever held
   it, re-reading shows you holding an item you may have just taken from someone mid-build — there
   the binding must name an append-only surface to check instead. Follow what it says; do not
   assume a re-read is proof.

   **First to the server wins — and the tie-break must be readable, or it decides nothing.** "If
   someone else is also holding it, you lost" is symmetric: both runs see the other and both reach
   the same conclusion, so either both stand down or neither does. The ordering has to come from
   something the server timestamps, and the comment timeline is it: **write your claim comment as
   part of claiming — `Claimed by <run-id>, expect to report by <time>` — then read the comments:
   the earliest claim wins.** The identity is what stops the board showing one account for every
   agent; the horizon is what lets a later run tell "working" from "died" (see *Abandoned work*
   below). If yours is not the earliest, you lost: release the item, comment that you are backing
   off, and take the next one. Losing is cheap and takes seconds; two runs building the same issue
   is not.

   **A claim binds only what the tracker can see, so renew it before the first thing it cannot.**
   Creating a branch, adding a worktree, writing any file — those are writes outside the board, and a
   run that lost the race by seconds has typically not re-read the timeline by the time it makes
   them. Run `verify_claim` immediately before that first write and treat the answer as binding:
   nothing has been created yet, so standing down costs one comment. Seen live (2026-07-24, issue
   #58): two runs claimed 1m46s apart, both derived the SAME worktree path from the same convention,
   and the loser — which never re-read after claiming — wrote its model, its migration and its tests
   into the winner's checkout while the winner was mid-build. **One task is one agent, and this
   renewal is where that is actually enforced**; the claim comment only records who asked first.
   When the claimed item already carries `review`, keep it there and resume at step 6. Transition it
   back to `in-progress` only if addressing review or CI requires a code change; after that change,
   publish the new head and repeat review plus CI. A delivery-only resume must not pretend the code
   is being built again.
3. **For a `ready` item, `transition` to `in-progress`** (dropping `ready` in the same call), **and attach
   `dev:<runtime>` in the same step** (see *Why this cannot be left to the tracker* above). Seen
   live: an issue correctly assigned, claimed and transitioned, with the `dev:<runtime>` label added
   only later at close — which means for the entire build, the one query the label exists to answer
   ("what is that runtime holding right now") could not find it. The claim comment was already
   written in step 2 — **do not write a second one**: the race is adjudicated by the EARLIEST claim
   comment, so a late "re-claim" comment muddies the very record you may need to prove you won.
4. **Load the domain named on the issue** (`domain:` label or metadata line) before touching
   anything — see Routing above — then **implement under the target repository's own rules**, whatever they are — branching, review,
   versioning, testing. Read that repo's agent instructions (`AGENTS.md` or equivalent) and follow
   them. This skill has no opinion on how a project *builds* — testing, review, versioning, release
   are all its business. Where the repo is silent about how work is **isolated and integrated**, use
   the default flow below rather than inventing one.
   **"Load" means you read it, not that you tell a delegate to.** If your plan is to hand
   implementation to a sub-agent and your only contact with the domain is a line in that sub-agent's
   prompt saying "also read X", you have not loaded the domain — you have asked someone who cannot
   see or change your delegation plan to load it after that plan is already fixed. This matters
   because a domain is not always just priorities and a definition of done: some name their OWN
   orchestration contract — a fixed phase structure, a step that must never be delegated (a
   measurement run, a git operation), a specific sub-agent division of labour. That contract can only
   shape how you build if you read it BEFORE deciding whether and how to delegate, not after handing
   the work off. Read the domain yourself, first; only then decide your delegation strategy — and if
   the domain prescribes its own orchestration, follow that structure instead of substituting a flat
   one-shot delegation that happens to produce a similar-looking diff.
   **Done is defined by the issue's acceptance criteria.** Domain rules, when supplied, add to them —
   a measurement discipline, required benchmarks, ship gates — but never replace them, and their
   absence is not licence to lower the bar the analyst set.
5. **Publish the review target, then move to `review`**, linking the branch or pull request. Renew
   the claim first — publishing review is one of the boundaries `verify_claim` covers (*A heartbeat
   is a claim renewal* below), and a run displaced during a long build must find out before it
   publishes anything. When the configured delivery route is `pull request`, push the branch and
   open or reuse the PR BEFORE transitioning: `review` must point to the head and base SHAs the
   reviewer and CI are about to judge, not to an unshared local tree. A resumed issue reuses its
   existing open PR; creating a duplicate is not recovery.
6. **Get the published change reviewed by a context that did not write it, then require green CI on
   the latest PR head.** Capture the head and base SHAs before review and require the verdict artifact
   to name both explicitly; a floating "approved" is not evidence for any particular revision or
   diff. Where the
   configuration authorises it, obtain that context yourself — a
   sub-agent or teammate; where it does not, hand the PR to a separately started analyst run and say
   on the issue that you are waiting for it. What the configuration decides is who starts the
   review, never whether it runs. Every push invalidates the prior verdict and CI result: re-review
   the changed surface and wait for the required checks again.

   If review, CI or delivery is blocked, STOP THERE and leave it in `review`. Work that is built and
   published but cannot be shipped — a reviewer requests changes, a check fails, required native
   approval cannot be supplied by a distinct identity, a permission is
   missing, two project rules contradict each other — is *finished work awaiting delivery*, which
   is exactly what `review` means. Do not move it to `done`: that state says delivered. Do not work
   around the blocker either; a rule you bypass to ship is a rule that stops meaning anything.

   Comment the blocker precisely — what refused it, what that thing expects, what the project
   requires instead — then unassign so another actor can complete the delivery. Restore any local
   state you changed trying.

   **A blocker that comes from two project rules contradicting each other deserves its own issue.**
   It will hit the next task, and the one after that, and each run will re-diagnose it from scratch.
   One filed finding turns a recurring tax into a decision someone can make once.
7. **Merge only the reviewed PR head after its required CI is green, publish any version tags, then
   `close`.** Renew the claim
   immediately before merging, require `reviewed head/base == CI head/base == current head/base`,
   bind the merge to the reviewed head SHA, and refuse if either side moved. Use the configured merge strategy; a `merge commit`
   preserves the branch boundary and PR ancestry, while squash and rebase deliberately discard that
   information. Retrieve the delivered SHA after merge and verify it has exactly the reviewed base
   and reviewed head as its parents; do not claim topology preservation from a command flag alone.
   A merge queue is eligible only when its configured method guarantees that same topology. If the
   project requires CI on the exact delivered SHA, wait for that post-merge gate.

   **A version bump is not delivered until its immutable tag exists on the remote.** After the exact
   delivered-SHA gate is green, inspect the final change for every app or project whose declared
   version increased. For each one, create an annotated tag pointing to the delivered SHA and push
   that exact tag. Reuse the repository's established component tag convention. Exactly one
   historical convention must match: competing conventions, an unclear component identity, or
   uncertainty over single-product versus independently versioned monorepo components is a naming
   blocker and stays in `review`. Only when history is empty and classification is unambiguous, use
   `vX.Y.Z` for a single-product repository or `<component>/vX.Y.Z` for an independently versioned
   component. Never move, overwrite or force-push an existing version tag. A tag that already points
   elsewhere is a delivery blocker, not permission to rewrite release history.

   A Git tag and a GitHub Release are different artifacts: the tag is mandatory; create the Release
   too only when the repository already publishes Releases for that component or its configuration
   requires one. In that case, build the Release from the already-pushed tag and verify the tag
   exists remotely rather than letting GitHub create an implicit tag against the wrong target.
   **Renew the claim again after every post-merge or publication wait and immediately before
   `close`**; merge, version publication and close are separate irreversible boundaries.

   `close` moves the state to `done` and then, on trackers that have one, sets their own closed flag.
   Carry your run identity in the closing comment and state what was actually verified: review
   verdict, CI run or checks, measured numbers, tests run, PR, **the delivering commit SHA**, and
   every version tag or Release published.
   Never write just "done"; the state already says that, and the comment exists to show what earned
   it.

### Working in a repository — the default flow

**The repository's rules win, always.** Read its agent instructions (`AGENTS.md`, `CLAUDE.md` or
equivalent) before anything else and follow them. What follows is the fallback for a repo that
prescribes nothing, and a floor under one that prescribes only part of it — never a competing
standard.

The older fallback — *use the commit history as the convention* — does not stretch this far, and it
is worth saying why. A git log tells you how commit messages are written. It cannot tell you whether
two agents may share a checkout, or whether a branch verified last hour is still valid after someone
else merged. Those answers decide whether parallel work corrupts itself, so this skill supplies
them rather than leaving them to a domain that has no reason to know.

**1. Never work in the base tree.** For a dev, the default branch is read-only: no edits, no commits.
Check before the first change instead of assuming:

```bash
git branch --show-current   # main / master -> stop, branch first
```

If you find you already committed to the base branch and have NOT pushed, move the commits to a
branch and reset. **If you already pushed, do not force-push** — revert and redo it properly.
Force-pushing a shared base is how another agent's checkout silently diverges from history it has
already built on.

**2. One issue, one branch, one isolated checkout.** Branch from the freshly fetched remote base, not
from a local copy that may be days stale, and give each dev its own worktree so that a half-written
tree is never visible to anyone else:

```bash
git fetch origin
git branch <branch> origin/<base>
git worktree add <path> <branch>
```

**Where the worktree goes is not a free choice, and one repository is not the unit to think about.**
Two constraints bind it. It must live **outside the working tree** — git allows a worktree inside the
repository and it then pollutes status and ignores for everyone. And its path must be **unique per
repository**, not merely per branch: a shared parent like `<somewhere>/worktrees/<branch>` collides
the moment two repositories both have a `fix/login`, and the second `worktree add` fails with a
message about a path that already exists, naming neither repository. Put the repository's name in the
path. Some tooling adds a third constraint — a per-worktree index or cache that must not be shared —
so check what your setup expects before settling on a pattern.

**Record the branch on the issue the moment it exists** — natively where the tracker supports it,
as a comment where it does not; the binding says which. A branch nobody can find from the issue is
work nobody can follow, and the board's whole value is that following work never requires guessing.

**Name the BRANCH after the issue; make the WORKTREE PATH unique per RUN.** The issue number is what
joins the code back to the board without anyone parsing prose, and it survives a run whose
attribution nobody can explain any more — so the branch carries it, and the branch is what the PR,
the development sidebar and the merge all key on.

The worktree path is a different question with a different answer. It is a *local directory* joined
to nothing, and deriving it purely from the issue means two runs that both want issue 58 compute the
same path and share one checkout. Git will not save you: `worktree add` refuses a branch already
checked out elsewhere, but **nothing refuses a second process writing into a directory that already
exists**. So put the run-id in the path — `<root>/<repo>/<issue>-<slug>-<run-id>` — and the collision
becomes impossible instead of merely unlikely. The cost is one stale directory when a run dies; the
cost of the shared path is two agents editing one tree with no record anywhere that it happened
(seen live on #58, 2026-07-24: model, migration and test files appearing in a checkout their author
had created clean minutes earlier).

**A fresh worktree does not have the files git never tracked.** Everything gitignored — environment
files, secrets, credentials, local settings — is simply absent, and the failure it produces is
confusing rather than obvious: the tool starts normally and then dies on a variable it has never had
trouble with, in a tree that looks identical to the one that works. Copy across whatever the project
needs before running anything. Reuse the interpreter or dependency tree the main checkout already
has, with the worktree as the working directory, instead of building a second one — per-worktree
installs are slow, drift apart, and are the reason a test can pass in one tree and fail in the other.

**Isolation is verified on both sides, exactly like a claim.** Whoever creates the worktree creates
it; the dev that starts working confirms it is in one before touching a file. Skipping that check and
working in the shared tree clobbers whoever else is building — and unlike two devs on one issue,
**nothing on the board records it**. That is precisely why this rule cannot be left to a domain.

**Verify it again the moment the tree surprises you.** A file you did not write, a modification to a
file you never touched, a tool reporting that something changed under you — inside a worktree that is
supposed to be yours alone, every one of those means another process is in your directory. Stop
writing and run `verify_claim`; the timeline, not the filesystem, decides who continues:

- **You are not the earliest claimant** → stand down. Leave what is there, change no state, and say
  on the issue what you saw and where your own work is, so the holder can pick it up.
- **You are** → say so on the issue naming the other run, and **adopt what it left rather than
  deleting it**. Its work is real even though the coordination failed, and two runs converging on one
  issue usually converge on a similar design.

What is never right is editing around the intruder. That produces one tree holding two designs and a
build that belongs to nobody — strictly worse than either run's work alone.

**3. Integrate the base before publishing the PR, then verify again.** Your branch was verified
against the base as it stood when you started; every merge landed since then ages that result. Bring
the base in, resolve honestly, and **re-run the verification**:

```bash
git fetch origin
git merge origin/<base>     # inside your worktree
```

Resolving a conflict is a code change. An unverified resolution is an untested commit with better
manners, and picking one side wholesale to make the markers go away is how a merge quietly deletes
someone else's fix.

**Merge, do not rebase — and the default is this way round for reasons, not taste.** Rebase buys a
tidier history by rewriting every commit SHA, which costs two things that matter more here. Anything
bound to those SHAs is silently invalidated: a recorded review result, a signed gate, a pinned
deployment, a CI run recorded against the commit. And rebasing a branch that is already pushed forces a
force-push, on a branch another agent may have fetched and built on — the one operation this flow
tells you never to perform on the base, applied to work someone else is holding.

Rebase only where the repository asks for it and nothing points at the SHAs. A repo rule outranks
this default like every other.

**4. Preserve the reviewed history through the configured delivery route.** For a pull-request
route, push the branch, open the PR, and bind review and required CI to its latest head SHA. Merge
with a merge commit when preservation of merge information is configured: the merge commit records
the branch boundary and the PR retains the review and CI evidence. Never squash or rebase in that
mode; both replace the reviewed topology with a different history.

The merge commit itself is a new SHA, so distinguish two gates instead of pretending otherwise:
pre-merge CI proves the PR head (and, where the host provides it, its merge candidate); a repository
that requires the exact delivered SHA must also run its post-merge gate before the issue closes.
For both gates, the expected set is the union of host-required checks and every applicable lane the
repository names; a missing or skipped expected lane is not green. If the base or PR head moves
before merge, return to step 3, re-verify, re-review the changed surface, and re-run required CI.
Never bypass the race with an administrative override.

**Whether you may land it at all is a project decision, not yours.** Some repositories require a
human decision; some pre-authorise delivery after review and CI. **Default to asking** unless the
repository or configuration says otherwise. A pull request is a configured delivery route, not a
silent workaround for a refused direct push.

**5. Clean up what you created.** Remove the worktree once the work is delivered or abandoned. An
orphaned worktree keeps its branch checked out, and the next run that tries to use that branch gets a
refusal it did not cause and cannot explain.

**Where the repo says otherwise, do it their way and say so on the issue.** And if the repo's rules
make isolation impossible while another dev is active, that is not a rule to bypass — it is a blocker
to file, exactly as in step 6.

### Abandoned work — the run that never came back

A run dies. The process is killed, the sandbox expires, the budget runs out mid-task. The issue stays
`in-progress` with an assignee, and selection never surfaces it — step 1 drains `review` and
`ready`, never `in-progress` — so **nobody ever picks it up again**. This is not an exotic failure — it is the ordinary end of
a long autonomous run, and it is the one way this board rots without anyone doing anything wrong.

**The same death can land one step earlier, before the state ever moved.** Step 2 (claim: assignee +
comment) and step 3 (`transition` to `in-progress`) are two separate calls — a run that dies between
them leaves an issue with an assignee and a claim comment that still carries `status:ready`, because
the label swap never ran. The same gap exists at the other end: a run that dies after step 4 (the work
is done) but before step 5's `transition` to `review` leaves finished, working code sitting on an issue
that still says `in-progress`. Seen live: five issues claimed and commented, none ever relabeled — the
dead runs' horizons had long passed, but every one of them still read `status:ready` to anyone
scanning labels. In every case `list_state` still filters correctly (`no:assignee` excludes it from its
queue), but the label lies to anyone reading it directly, and — unless the rule below is read for what
it actually covers — the issue looks neither claimed nor reclaimable.

Worth knowing before you assume you are missing something obvious: **nothing else solves it either.**
Claude Code's agent teams document the same failure — *"teammates sometimes fail to mark tasks as
completed, which blocks dependent tasks"* — and answer it by asking a human to fix the status by
hand. The closest GitHub-native skill set preserves worktrees and resumes by session id, but has no
rule for work whose holder is simply gone. So what follows is convention over data the tracker
already timestamps: no daemon, no lock service, nothing to run.

**The holder leaves a trail.** Two habits make a claim auditable:

- **Declare a horizon when you claim** (step 2, inside the claim comment). One extra clause in a comment you already write, and
  it turns "no activity" from a judgement call into a comparison.
- **Comment on progress, not only on completion.** From outside, a dev that has gone quiet for an
  hour and a dev that died look identical. Each comment is a heartbeat carrying a server-side
  timestamp; that is the liveness half of the mechanism. The other half is the read that precedes
  the write — see below.

**A heartbeat is a claim renewal: read before you write.** The claim check in step 2 runs once, but
the control surface it read stays authoritative for the whole build — a late adjudication, a reclaim,
a stand-down all arrive there while you work. A heartbeat that only writes is deaf to the one channel
that can revoke the claim. This happened live: a run that had lost a claim race by five seconds
was told so in an adjudication comment 33 seconds later, and then posted three more
heartbeats and worked another ~48 minutes because nothing in its heartbeat loop ever read the
timeline again. So **every heartbeat runs `verify_claim` first and is written only when the answer is
"still holder, nothing said"**. The same renewal runs immediately before any expensive or
irreversible boundary that can fall between heartbeats — starting an evaluation, launching a review,
delivering — so a long quiet phase cannot bypass it. Two answers mean stop, not retry:

- **A control message naming your run-id** — a stand-down, a reclaim, an adjudication. Stop
  repository work immediately. If the issue is still open, leave ONE acknowledgement so the record
  shows the message was received, and release your `dev:<runtime>` marker — it is per-runtime and
  yours to drop. The assignee is different: where the tracker attributes it to a shared account the
  winner holds it too, so the binding decides whether it comes off (on all three current bindings it
  stays — each one's account is shared or singular); follow it rather than assuming. Do not alter the
  workflow state and do not write a second claim comment — the race record is not yours to edit.
- **The item is closed, or no longer carries the state you are working under.** Someone else
  finished or moved it. Stop, release your marker exactly as above, and change nothing about the
  state — it belongs to whoever moved it.

Renewal does not pretend the control surface is a lock, and it does not try to. It caps the cost of
losing a race — or being legitimately displaced — at one renewal interval, and it makes "the original
holder reads your comment and backs off" in *Reclaiming is not a race won* something that actually
happens rather than something the prose hopes for.

**A failed read is not a failed answer.** If the renewal's read itself fails — network, auth, rate
limit — the control surface answered nothing, and "nothing" is not a stand-down. Fail closed on the
write: post no heartbeat, start no evaluation, launch no review, deliver nothing — and retry the
read. Only a stop answer from a *successful* read ends the work. Treating a timeout as a stand-down
lets a flaky network halt every run; treating it as clearance lets a run write deaf, which is the
defect this rule exists to close.

**Reclaiming.** An issue is reclaimable when it carries an assignee and/or a claim comment naming a
run-id — **in any state, not only `in-progress`** — and its last activity — any comment, label
change or referenced commit — is past the declared horizon, or more than a few hours old when no
horizon was declared. The state label is deliberately not part of the precondition: it is exactly
what a dead run may never have gotten to write, so a claimed issue still wearing `status:ready` or
`status:review` is reclaimable by the same rule as one caught mid-build under `in-progress`. Then:

1. **Comment before touching anything**: `Reclaiming from <run-id>; last activity <timestamp>,
   horizon <declared or none>.` The record of the takeover matters more than the takeover.
2. Replace the assignee with your own, **and remove the dead run's attribution marker as you add
   yours** — it cannot do it itself, which is the whole reason you are here. Skip this and the item
   ends up claiming two runtimes hold it. Then continue from whatever the dead run left on the issue.
3. **Do not discard its work, and do not assume the label is where the work actually got to.** A
   pushed branch, a commented diagnosis, a ruled-out hypothesis are all still valid — which is
   precisely why everything goes on the issue as it happens. If what the dead run left shows the
   work is further along than the label says — a linked PR or a finished diff sitting there while
   the issue still reads `ready` — transition straight to the state the evidence supports instead of
   restarting from `in-progress`; re-doing already-finished work is the exact waste this rule exists
   to avoid.

**Reclaiming is not a race won.** If the original holder was alive and merely slow, its next
`verify_claim` surfaces your comment — which is why the comment names who you took it from. It then
backs off exactly like the loser of a claim race in step 2. Either way no work is lost, and the
worst case is one duplicated hour rather than an issue that is stuck forever.

**A run ending deliberately needs none of this**: unassign, write the state, say so. Reclaiming is
for the runs that never got the chance.

---

### Where to put work you cannot finish

Three states can receive work a dev is putting down, and choosing wrong buries it. Two questions
separate them.

**First: is the problem inside the issue, or outside it?**

- **Inside → `analysis`.** The specification cannot be built from — it contradicts itself, its
  acceptance criteria do not match the problem it describes, or its premise turned out to be false.
  More *thinking* fixes this, and thinking is the analyst's job. Comment the evidence, hand it back.
- **Outside → read on.** Nothing anyone writes on the issue will help; something in the world has to
  change.

**Then: does the work already exist?**

- **No → `blocked`.** Nothing is built and something beyond the repository must move first: a
  decision, a date, a credential, a system that is down. Name the condition *and* its discharger.
- **Yes → `review`.** Built and verified but unable to ship. That is *finished work awaiting
  delivery*, and it earns its own state because the two are not equally recoverable: a blocked item
  costs whatever it is waiting for, while finished-but-undeliverable work is one permission away from
  shipping. **File it as `blocked` and the next run has no way to know the code already exists** — so
  it builds it again, and the first branch rots unmerged.

The failure this prevents is not theoretical in either direction. An issue parked in `analysis`
because an external system was down wastes an analyst's pass on a specification that was never wrong;
one parked in `blocked` because the spec was incoherent waits forever for a world that was never the
problem.

**If the issue turns out to be wrong**, do not silently reinterpret it. Comment with the evidence,
move it back to `analysis`, and unassign. An issue that a dev rewrote in flight is an issue
nobody analysed.

**Everything you learn goes ON THE ISSUE, not just in your reply.** The chat is ephemeral and the
next run starts from the issue. Record it as you go, not at the end:

- **a ruled-out hypothesis is worth as much as a confirmed one** — it stops the next run repeating
  your dead end. Say what you tested and why it was not the cause;
- **a diagnosis without a fix still belongs there.** If you narrowed the problem but did not solve
  it, comment the narrowing, unassign, and return it to `ready`. That is a partial delivery,
  not a failure — and infinitely better than a fresh run starting from zero;
- **a discovery outside this issue's scope becomes its own finding.** You are the dev, not the
  analyst, so you do not have to analyse it — but dropping it because "not my role" loses it. File
  a stub, or note it on this issue for an analyst to pick up;
- **methodological errors count as findings.** If your probe turned out not to measure what you
  thought, say so. Someone will otherwise repeat it and trust the wrong number.

**Handing off is a legitimate outcome, and sometimes the better one.** Judgement degrades as a run
gets long: you start repeating mistakes you already know about. That is not hypothetical — during
the session that produced this rule, a run re-made the exact `$?`-after-a-pipe error it had
documented in the project's own error table hours earlier, and did it while debugging.

Three signals that you are past your useful range:

- you repeat a mistake you had already written down;
- you are on your third failed hypothesis without narrowing the problem;
- you are editing more than you are measuring.

When any of those fire, **write the state to the issue and return it** rather than pushing on. A
fresh run with a full context budget, starting from a good diagnosis, will beat a tired one
continuing. Returning work is not giving up — carrying it badly is.

**Never ship a partial fix as if it were the fix.** If the acceptance criteria are not met, the work
is not done: say what is still failing, and do not merge. A branch discarded with a good diagnosis
attached is a better outcome than a merge that leaves the defect alive under a green comment.

**If the issue has no acceptance criteria**, it is not implementable as written — the criteria are
what "done" means, and with none you would be inventing the bar you then declare yourself to have
cleared. This happens with issues filed before the format existed, imported from another tracker, or
written by a human in a hurry. Do NOT guess silently. Either:

- **state the criteria you will treat as done, as a comment, BEFORE starting** — then build to them
  and let the close comment show you met what you announced; or
- **send it back to `analysis`** when the gap is large enough that you would be designing
  rather than implementing.

The first is usually right for a well-argued issue that simply lacks a checklist; the second when
you cannot tell what problem it solves. Either way the criteria end up written down before the work,
never reconstructed after it to match what you happened to build.

---

## State machine

| State | Meaning | Who moves it |
|---|---|---|
| `analysis` | being analysed, or returned by a dev as wrong | analyst |
| `ready` | specified, unassigned, implementable | analyst |
| `in-progress` | claimed and being built | dev |
| `review` | built and published; awaiting or undergoing independent review, CI and delivery | dev |
| `blocked` | needs something external and nothing is built yet; the blocker and its discharger are named in the issue | either |
| `done` | merged and verified | dev |

**Exactly one state at a time.** Two states is an ambiguous board, and a board nobody trusts gets
ignored. Whether the tracker enforces that or merely permits it is in its binding — where it is
only permitted, `transition` carries the whole burden.

**`blocked` is the one state nobody owns, so it must name its own exit.** An unassigned item in
`in-progress` gets reclaimed by the stale-claim rule; an item in `ready` gets picked up by priority.
A blocked item has neither: no holder to chase and no queue that will surface it. That is why filing
it demands the missing thing be named precisely — the name IS the exit condition, and any run that
notices it satisfied moves the item back to `ready` and says why. A blocker recorded as "waiting on
infra" names nothing, so nothing can ever discharge it.

**Name the discharger too, not only the condition.** Three things get filed as `blocked` and they rot
at completely different rates:

| Blocked on | Who discharges it | How it rots |
|---|---|---|
| a **decision** — a person must choose | only that person | worst: nobody is watching, and an unasked human is indistinguishable from an abandoned issue |
| a **date or accumulating data** | any run, mechanically | mildly: the date passes and nobody checks |
| an **external system** | any run, by rechecking | mildly: the outage ends quietly |

The last two a run can resolve on its own, which is why naming the condition is enough for them. The
first cannot: **if a person must decide, the item is not waiting on the project, it is waiting on
someone who has not been told.** Say so in the issue, in those words, and make it findable by them —
that is the difference between a blocker and a dead end. An item blocked on a decision that has sat
untouched for days is not blocked any more; it is abandoned, and any run that sees one should say so
rather than walk past it.

**`done` is a state, not the absence of one.** Some trackers also have a closed or archived notion of
their own; where they do, the two move **together and in that order** — the state first, then the
tracker's own bookkeeping. Leaving `done` implicit and relying only on "the item is closed" breaks
two things at once: a board grouped by state never learns the work finished, because closing an item
is not a state change and fires no state event; and a query for items in `review` keeps returning
work that shipped last week.

Creating that vocabulary is `ensure_states` — see the active binding. **Both roles run it before
their first write to a project they have not used before**, and it is idempotent, so running it when
the states already exist costs one call and changes nothing. Skipping it is not a slow degradation:
on a tracker that refuses unknown labels, the very first `create` fails, and it fails at the end of
the analyst's work rather than at the start.

## Optional: a board view over this workflow

Most trackers can render these items as a Kanban board. Whether that board is a **view** or the
state itself is the only question that matters, and the answer is always the same here: the state
machine above is authoritative and the board is a picture of it. Invert that and every agent needs
whatever extra permission the board API demands, and this workflow's transport has to be rewritten.

What that costs on a given tracker — which parts are API-reachable, which need a human to click
once, and how a board is kept in step without becoming the source of truth — belongs to its
binding, because none of it generalises.

## Optional: running this alongside an in-session agent team

Claude Code's agent teams give a lead session a shared task list of its own, which teammates claim
through file locking. That resembles this workflow and is not it: **the two coordinate at different
lifetimes, and confusing them loses work.**

| | agent-team task list | this workflow |
|---|---|---|
| Lifetime | one session; a team cannot be shared across sessions | as long as the repository |
| Coordination | file lock, local to the machine | assign server-side, then re-read |
| Survives the run ending | no | yes |
| Reachable by another runtime | no | yes |

Use each for what it is: **the team parallelises inside a run; the issue is what survives it.** A lead
can fan several analysts out as teammates over one domain and have them argue — parallel
investigation is the strongest documented case for teams — but when the session ends, everything held
only in the task list, the mailbox or a teammate's context is gone. What was written to the issue
remains. This is not a new rule, it is the existing one (*everything you learn goes ON THE ISSUE*)
meeting a new way to lose things.

Two consequences worth acting on:

- **Never treat the shared task list as the state.** It is a view of one session's slice of the
  board, carrying exactly the authority a Kanban column does: none.
- **A teammate that finishes without writing to the issue has produced nothing**, however good its
  reasoning was. Where the runtime offers hooks on task completion or on a teammate going idle —
  `TaskCompleted` and `TeammateIdle` in Claude Code, both able to refuse with exit code 2 — that rule
  can be enforced rather than hoped for. That is the mechanical version of the paragraph above, and
  the same move as declaring the analyst with a read-only tool allowlist instead of asking it nicely.

## Honest limits

- **Claiming is not a lock, and on some trackers a lost race is invisible.** A server-side claim is
  a far better shared state than anything on a local filesystem, but two agents can still claim
  within the same second — and where the claim field holds one value, the loser is overwritten
  without trace. Step 2's verification is what catches it, in seconds rather than at merge time,
  which is why the binding gets to define what counts as verification.
- **A horizon is a heuristic, not a lease.** Reclaiming reads timestamps; it cannot tell a dead run
  from a live one that is thinking hard and writing nothing. That is why the trade is deliberately
  asymmetric: the cost of reclaiming too early is one duplicated hour and a comment naming who was
  displaced, and the cost of never reclaiming is an issue that is stuck forever.
- **The tracker's credentials must be reachable from wherever the run executes.** Tokens kept in a
  system keyring are routinely invisible to a sandboxed runtime, which then fails at the last step
  rather than the first. Every binding names its own escape hatch; whichever it is, **prove access
  with a cheap read before relying on this workflow** — an analyst that cannot file its issue has
  done the work and lost it.
- **The board serializes claims, not machine resources.** Two runs holding two different issues are
  each doing exactly what the workflow asks — and when they share one machine, their heavy phases
  share its RAM and CPU, which no tracker can see. Three habits soften it: say in a heartbeat when
  you enter a machine-heavy phase; skim the other in-progress heartbeats before launching your own;
  and when a collision kills a run anyway, **re-run the work alone before reading anything into the
  failure** — a death by contention is coordination friction, not evidence about the change. It has
  happened live: a parallel A/B died of OOM against another run's measurement phase, and the
  sequential re-run passed clean.
- **This skill moves tasks; it does not judge them.** Whether something is worth doing was decided
  by the domain rules the ANALYST ran under, and is recorded in the issue. A dev running standalone
  inherits that judgement rather than re-making it — which is precisely why it needs no domain of
  its own, and why a dev that silently reinterprets an issue breaks the chain.

<!-- issue-flow:config:start -->
---

## Operator configuration

The table below contains portable defaults only. Before using it, look for `operator.local.md`
beside this file. When present, read it and let its same-named rows override this table; its
additional instructions are local policy too. That file contains permissions and machine-specific
paths, is ignored by Git, and MUST NOT be committed or published.

| Setting | Value here | Skill default |
|---|---|---|
| Delivery authorisation | ask | ask |
| Delivery route | direct | direct |
| Review delegation | ask | ask |
| Merge strategy | merge commit | merge commit |
| Worktree location | unset | unset |
| Tracker | `github` | `github` (also `linear`, `trello`) |
| Project board | none | none |
| "Description for dumb humans" sentence language | English | English |

<!-- issue-flow:config:end -->

> The two markers delimit the default template used when the installer creates
> `operator.local.md`. Configure the ignored local file, never this versioned block.
