# issue-flow

An agent skill that splits work across a shared issue tracker into two roles: an **analyst** that
only analyses and files issues, and a **dev** that claims a ready issue and implements it.

The split exists because the two halves have opposite costs. Analysis is cheap, parallel and safe —
several analysts can run at once and none of them can break a build, because they write no code. It
also lets a sandboxed runtime that cannot touch the filesystem still contribute, since an analyst's
only output is a network call. Implementation is expensive, serial and risky, and gets the branch,
the worktree, the tests and the review.

Works with **GitHub, Linear and Trello**. The workflow is written in operations; each tracker's
commands live in its own binding file.

## Install

One line. It clones the skill into `~/.agents/skills/issue-flow`, links it into each runtime it
finds, and — run again later — **upgrades in place while preserving your configuration block**:

```sh
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/asanabrial/issue-flow/main/scripts/bootstrap.sh | sh
```

```powershell
# Windows
irm https://raw.githubusercontent.com/asanabrial/issue-flow/main/scripts/bootstrap.ps1 | iex
```

**What actually runs out of the pipe is a ~50-line bootstrap you can read at that URL.** It checks
for git, clones, and hands over to the installer *from disk* — nothing of substance executes that is
not on your machine first, and the settings that remove confirmation steps only ever hold the values
you put there yourself. Prefer to see everything before anything runs? The bootstrap is just this,
by hand:

```sh
git clone https://github.com/asanabrial/issue-flow ~/.agents/skills/issue-flow
~/.agents/skills/issue-flow/install.sh install
```

The skill lives in `~/.agents/skills/issue-flow/` and the installer links it into each runtime's
skill directory (`~/.claude/skills/`, `~/.codex/skills/`). Run `status` to see what is linked, and
`uninstall` to remove the links — the skill itself is never touched.

On Windows the installer tries a symlink, falls back to a directory junction (which needs no
elevation) and only then to a copy. If it copies, it says so loudly: copies stop tracking the
original.

## Use

```
/issue-flow analyst <domain-rules>     # domain REQUIRED
/issue-flow dev     [issue-number]     # domain optional
```

Codex uses `$` instead of `/`.

**The analyst needs a domain and stops without one.** The skill knows how work moves; it has no
opinion on what your project considers worth doing. That is what a domain rule book supplies, and
there is a worked example in `examples/domain-test-coverage.md` you can use as-is or copy and
repoint at your own subject.

**The dev usually needs nothing.** `/issue-flow dev` on its own is a complete invocation: the issue
already carries its scope and acceptance criteria. Add a domain only if your project has extra
requirements for what counts as done — a measurement discipline, mandatory benchmarks, ship gates.

## Layout

```
SKILL.md                          the workflow: roles, states, claiming, delivery
bindings/github.md                how each operation is performed, per tracker
bindings/linear.md
bindings/trello.md
examples/domain-test-coverage.md  a worked domain rule book
install.sh / install.ps1
```

## Configuration

Settings live in one marked block at the end of `SKILL.md` — tracker, merge strategy, worktree
location, and whether delivery is pre-authorised. It is a table with the defaults written next to
each value, so it reads on its own.

Edit it by hand, or from the installer:

```sh
./install.sh config                                              # print the table
./install.sh config --set "Worktree location=/wt/<repo>/<branch>"
```

The installer matches a setting **by its name** and carries no list of its own, so a row added to the
skill is settable immediately without touching either script. It backs the file up first, refuses a
name that matches no row or more than one, and refuses a value containing `|`, which would split the
cell and corrupt the table.

`sync` upgrades the skill while keeping that block:

```sh
./install.sh sync --from ./newer-SKILL.md
```

It backs the file up first, and refuses outright if it cannot find the markers rather than risk
dropping your settings.

**Remove the configuration block before sharing this skill with anyone.** It holds your permissions,
including whether an agent may push without asking.

## Status

The workflow, the state machine and the GitHub binding are the mature parts. **The Linear and Trello
bindings are written against their official API documentation but have not yet been exercised against
a live workspace** — expect the first real run to find something. `install.sh` has been tested under
Git Bash; the logic is POSIX but it has not run on a native Linux or macOS shell.

Issues and corrections welcome, preferably filed through the workflow itself.
