"""Refuse a push to the default branch or a tag and a branch name outside <type>/<slug> in pre-push."""

import os
import re
import subprocess
import sys
from typing import Final

# ── Constants ─────────────────────────────────────────────────────────────────

TYPES: Final[tuple[str, ...]] = (
    "feat",
    "fix",
    "perf",
    "refactor",
    "test",
    "docs",
    "build",
    "ci",
    "revert",
)
"""The types of the commit standard, which also start a branch name."""

BRANCH_NAME: Final[re.Pattern[str]] = re.compile(rf"(?:{'|'.join(TYPES)})/[a-z0-9][a-z0-9-]*")
"""A branch name: a commit type, a slash and a slug of lowercase letters, digits and hyphens."""

HEADS: Final[str] = "refs/heads/"
"""The ref prefix of a branch."""

TAGS: Final[str] = "refs/tags/"
"""The ref prefix of a tag."""

DEFAULT_BRANCH: Final[str] = "main"
"""The default branch name when origin/HEAD is not set."""

DEFAULT_REMOTE: Final[str] = "origin"
"""The remote whose HEAD names the default branch."""

SKILL_HINT: Final[str] = "use the ship skill: load it and follow its path (branch, pull request, ship.py merge)"
"""The last line of every rejection, so an agent finds the rules of the change path."""

# ── Input ─────────────────────────────────────────────────────────────────────


# jscpd:ignore-start
def git_output(*args: str) -> str:
    """Return the stripped output of a git command, or '' when it fails."""
    run: subprocess.CompletedProcess[bytes] = subprocess.run(["git", *args], capture_output=True, check=False)
    return run.stdout.decode("utf-8", errors="replace").strip() if run.returncode == 0 else ""


def default_branch() -> str:
    """Return the default branch name from origin/HEAD, or main."""
    ref: str = git_output("symbolic-ref", "--short", f"refs/remotes/{DEFAULT_REMOTE}/HEAD")
    return ref.split("/", 1)[1] if "/" in ref else DEFAULT_BRANCH


# jscpd:ignore-end


def pushed_refs(stdin: str) -> list[str]:
    """Return the remote refs a push updates, from the git input or the one ref that prek passes."""
    refs: list[str] = []
    for line in stdin.splitlines():
        parts: list[str] = line.split()
        if len(parts) == 4 and set(parts[1]) != {"0"}:
            refs.append(parts[2])
    if refs or stdin.strip():
        return refs
    ref: str = os.environ.get("PRE_COMMIT_REMOTE_BRANCH", "")
    if not ref:
        return []
    return [ref if ref.startswith("refs/") else HEADS + ref]


# ── Rules ─────────────────────────────────────────────────────────────────────


def check_branch_name(name: str) -> list[str]:
    """Return the error for a branch name outside <type>/<slug>, or nothing for a good name."""
    if BRANCH_NAME.fullmatch(name):
        return []
    return [
        (
            f"branch: {name!r} is not <type>/<slug>: start a new branch "
            f"named like 'fix/missing-file-error', with a type from {', '.join(TYPES)}"
        )
    ]


def check_ref(ref: str, default: str) -> list[str]:
    """Return the errors for one pushed remote ref."""
    if ref == HEADS + default:
        return [
            (
                f"push: the push updates {default!r}, which changes only through a merged pull request: "
                "push your branch and open a pull request"
            )
        ]
    if ref.startswith(TAGS):
        return [
            (
                f"push: the push sends the tag {ref!r}, and a tag is a published release identifier that must never "
                "move, so a person or the release procedure of the repository creates it: push the branch only"
            )
        ]
    if ref.startswith(HEADS):
        return check_branch_name(ref.removeprefix(HEADS))
    return []


# ── Command line ──────────────────────────────────────────────────────────────


def main() -> int:
    """Check the pushed refs; return 1 on any error."""
    stdin: str = sys.stdin.read() if sys.stdin is not None and not sys.stdin.isatty() else ""
    refs: list[str] = pushed_refs(stdin)
    default: str = default_branch() if refs else DEFAULT_BRANCH
    errors: list[str] = [error for ref in refs for error in check_ref(ref, default)]
    if not errors:
        return 0
    for error in errors:
        print(error, file=sys.stderr)
    print(SKILL_HINT, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
