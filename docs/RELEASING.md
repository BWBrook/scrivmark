# Releasing

A release is a git tag `vX.Y.Z` on `main`, with a GitHub release carrying the
changelog entry. Nothing goes to a package index; people install from the
repository with `uv tool install git+https://github.com/BWBrook/scrivmark`,
which takes the latest `main`. `scrivmark doctor` compares the installed
version with the newest tag.

Release when something lands that a collaborator should hear about, not on
every push.

1. `uv version X.Y.Z` (updates `pyproject.toml` and `uv.lock`), then set
   `__version__` in `src/scrivmark/__init__.py` and `version` and
   `date-released` in `CITATION.cff`. A test checks that all three agree and
   that the changelog has an entry.
2. Add `## X.Y.Z (date)` to `CHANGELOG.md`, written for a user rather than a
   developer.
3. Commit, push, and wait for CI.
4. `git tag -a vX.Y.Z -m "scrivmark X.Y.Z" && git push origin vX.Y.Z`
5. `gh release create vX.Y.Z --title "scrivmark X.Y.Z" --notes-file <entry>`,
   with the entry extracted from the changelog.

If the release changes anything an agent may rely on (a command, option or
exit status, the shape of `--json` output, the record format, or what a
command does to the text), add an entry to "What changed for agents" at the
top of `src/scrivmark/skill/SKILL.md`, saying what to do instead. That section
is how agents working in other projects find out: `doctor` tells them their
guide is out of date, and they re-read it. A removed or renamed command or
option must also refuse with a message naming its replacement, for at least
one minor version.

Minor versions add features, and patch versions fix things. Before 1.0,
the record format may still change between minor versions; say so in the
entry when it does.

The repository has no Zenodo webhook, so a release does not mint a DOI. If one
is added, releases become one-way doors.
