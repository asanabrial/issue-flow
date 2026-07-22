# Example domain: untested code with a large blast radius

A **rule book for the ANALYST role**, and a worked example of what a domain looks like. Pair it with
the workflow:

```
/issue-flow analyst examples/domain-test-coverage.md
```

Copy this file, replace what it considers worth doing, and you have your own domain. The shape is
what matters, not the subject.

**It names no tracker, no command and no storage location** — deliberately, and that is the rule that
makes a domain portable. If you find one here, it is a bug.

---

## What this domain considers worth doing

**One thing only: code that many things depend on and no test exercises.** Not "low coverage" as a
number — a percentage is a summary, and summaries do not tell you which line will break production.
A finding is one symbol, function or module that (a) has callers, and (b) has no test that would fail
if its behaviour changed.

**What this domain explicitly does NOT file**, because filing it produces a backlog nobody trusts:

- coverage percentages, per file or per project. A number is not a finding;
- untested code with **no callers** — that is dead code, which is a different domain's problem, and
  writing tests for it makes the number go up while making the repository worse;
- generated code, vendored dependencies, migrations, fixtures;
- "add more tests to X" without naming what behaviour is unprotected;
- anything the ledger already records as refused or deliberately untested.

**If the repository has no test suite at all, file nothing and say so.** One finding that reads
"there is no test infrastructure; that is a decision, not a defect I can fix one symbol at a time"
is honest and useful. Two hundred findings that each say "untested" is noise wearing a backlog's
clothes.

## Identity

`cov/<path>::<symbol>` — for example `cov/src/auth/session.py::refresh_token`.

Stable across runs, and two analysts examining the same function collide on the same key instead of
filing it twice under different names. Where a finding covers a module rather than one symbol, drop
the `::<symbol>` half and say in the body why the module is the right unit.

## Priority — the `exposure` scale

The scale is named `exposure` and takes four values. It is a claim about **what breaks if this is
wrong**, never about how hard the test is to write:

| Value | Means |
|---|---|
| `critical` | many callers **and** the failure is silent — wrong results, not a crash. Nothing downstream would notice |
| `high` | many callers, or few callers on a path that handles money, credentials, data loss or deletion |
| `medium` | some callers, ordinary logic, a failure that would surface loudly |
| `low` | one caller, or behaviour a type checker already constrains |

**State the reason next to the value, always.** `exposure:critical` on its own is an assertion;
`exposure:critical — 14 callers, returns a silently wrong balance on an empty ledger` is a finding.

A `critical` that turns out to have three callers is worse than no finding at all: the next run
learns that this domain's priorities are guesses, and starts ignoring all of them.

## Evidence a finding must carry

Enough that a reviewer can re-check the analysis **without repeating it**:

- the **caller count**, and how it was obtained — the command, not "I looked";
- the **absence proof**: which test files were searched and how, so that "no test covers this" is a
  checked claim rather than a failure to find one;
- the **failure mode in words**: what wrong behaviour would pass unnoticed today. If you cannot
  describe it, you have not found anything yet;
- any **existing test that appears to cover it but does not**, and why — a test asserting a mock was
  called protects nothing, and finding one is more valuable than finding bare absence.

## How findings are prioritised against each other

Walk this ladder top-down; the first non-empty tier wins the run:

1. **Silent-wrong-result paths** — anything where a defect produces a plausible but incorrect value.
   These are first because every other kind of failure announces itself.
2. **Irreversible operations** — deletion, payment, credential rotation, anything with no undo.
3. **Widely-called ordinary logic**, by descending caller count.
4. **Everything else**, oldest untouched area first.

Ties break toward the smaller change. A finding a dev can close in an afternoon is worth more than a
perfect one that sits unclaimed for a month.

## Metadata each finding carries

Rendered verbatim by the workflow, interpreted only by this domain:

```
domain: examples/domain-test-coverage.md
measured_at: <date>
base_sha: <commit the analysis ran against>
callers: <count>
searched: <how absence of coverage was established>
```

`base_sha` is what lets a later run tell whether the evidence is still true. A finding whose
`base_sha` is far behind and whose file has changed since is **re-measured, not re-argued**: keep the
lead, discard the numbers.

## Optional: the dev side

The workflow's DEV role needs no domain — the issue carries its own acceptance criteria. Add one only
if your project demands something an issue cannot restate every time. For this domain that would be:

- **The test must fail before the fix and pass after.** A test written against current behaviour
  proves nothing; it freezes whatever the code does today, including the bug.
- **No mock-only assertions.** Asserting that a collaborator was called is not coverage of behaviour.
- Whatever the repository already requires to ship: its suite, its gates, its review.

If none of that applies to you, run `/issue-flow dev` with no domain at all. That is the normal case.
