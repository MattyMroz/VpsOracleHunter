"""Reject lines that carry a well-known API token shape, and with --pre-push the lines each pushed commit adds."""

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Final

TOKEN_KINDS: Final[dict[str, re.Pattern[bytes]]] = {
    "AWS access key": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "GitHub fine-grained token": re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    "OpenAI-style API key": re.compile(rb"\bsk-(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{20,}"),
    "Stripe live key": re.compile(rb"\bsk_live_[A-Za-z0-9]{16,}"),
    "Google API key": re.compile(rb"\bAIza[0-9A-Za-z_-]{35}"),
    "Slack token": re.compile(rb"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "Netlify token": re.compile(rb"\bnfp_[A-Za-z0-9]{20,}"),
    "Hugging Face token": re.compile(rb"\bhf_(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{30,}\b"),
}
"""Token kind by the pattern that matches its shape."""

PUSH_KINDS: Final[dict[str, re.Pattern[bytes]]] = {
    **TOKEN_KINDS,
    "private key": re.compile(rb"BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY|PuTTY-User-Key-File-\d|BEGIN OpenVPN Static key V\d"),
}
"""Kinds checked in pushed commits: the token kinds and the private key headers that detect-private-key knows."""

ZERO_SHA: Final[str] = "0" * 40
"""The SHA that git puts in the pre-push input for a ref that does not exist."""

DEFAULT_REMOTE: Final[str] = "origin"
"""The remote name when prek does not give one."""

DEFAULT_BRANCH: Final[str] = "main"
"""The default branch name when origin/HEAD is not set."""

NO_PUSH_INPUT: Final[str] = (
    "pre-push: the hook got no push input on stdin and no PRE_COMMIT_TO_REF: "
    "run it as the git pre-push hook or through prek"
)
"""The refusal when neither git nor prek gives the hook the pushed refs."""

RECORD_END: Final[bytes] = b"\0"
"""The end of each commit in the git log -z output of the messages of a push; git refuses a NUL in a message."""

FIELD_SEPARATOR: Final[str] = "\x1f"
"""The separator after the sha of a commit in the git log output of its message; the first one ends the sha."""

COMMIT_MARK: Final[str] = "\x1ecommit "
"""The line that starts each commit in the git log output of a push."""

DIFF_HEADERS: Final[tuple[bytes, bytes]] = (b"diff --git ", b"diff --cc ")
"""The starts of the header line of a plain and a combined diff in the git log output of a push."""

NEW_FILE: Final[bytes] = b"new file mode "
"""The header line of an added file, which git prints also for an empty file that has no --- and +++ lines."""

HUNK: Final[re.Pattern[bytes]] = re.compile(rb"^(@@+) (?:-\d+(?:,\d+)? )+\+(\d+)(?:,\d+)? \1")
"""A hunk header of a plain or combined diff: its at signs, one more than the parents, and the first result line."""

PUSH_ADVICE: Final[str] = (
    "push: a pushed commit adds a secret: revoke the secret at its issuer, then create the task branch again without "
    "that commit; the commit has not left this machine, so no pushed history changes"
)
"""What to do after a refused push."""

FILE_ADVICE: Final[str] = (
    "file: a file holds a token: remove it from the file and read it from an environment variable or a file that "
    "git ignores; if a pushed commit holds it, revoke it at its issuer"
)
"""What to do after a file with a token is refused."""


def push_ranges(stdin: str) -> list[list[str]] | None:
    """Return the git log arguments of each pushed ref: empty under prek without a target, None without input."""
    lines: list[tuple[str, str]] = []
    if stdin.strip():
        lines = [(parts[1], parts[3]) for parts in (line.split() for line in stdin.splitlines()) if len(parts) == 4]
    elif os.environ.get("PRE_COMMIT_TO_REF"):
        lines = [(os.environ["PRE_COMMIT_TO_REF"], os.environ.get("PRE_COMMIT_FROM_REF", "") or ZERO_SHA)]
    elif os.environ.get("PRE_COMMIT") != "1":
        return None
    remote_name: str = os.environ.get("PRE_COMMIT_REMOTE_NAME", "") or DEFAULT_REMOTE
    target: str = f"refs/remotes/{remote_name}/{default_branch()}"
    default: list[str] = [target] if git_output("rev-parse", "--verify", "--quiet", target) else []
    ranges: list[list[str]] = []
    for local, remote in lines:
        if set(local) == {"0"}:
            continue
        known: bool = set(remote) != {"0"} and _git("cat-file", "-e", f"{remote}^{{commit}}").returncode == 0
        ranges.append([local, "--not", remote, *default] if known else [local, "--not", "--remotes"])
    return ranges


def _git(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", *args], capture_output=True, check=False)


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


def _kinds(text: bytes, kinds: dict[str, re.Pattern[bytes]]) -> list[str]:
    return [kind for kind, pattern in kinds.items() if pattern.search(text)]


def header_path(header: bytes) -> bytes:
    """Return the path of a diff header line; a plain header of an added file names the same path twice."""
    rest: bytes = header.split(b" ", 2)[2]
    return rest[: (len(rest) - 1) // 2] if header.startswith(DIFF_HEADERS[0]) else rest


def added_line_problems(log: bytes) -> list[str]:
    """Return a problem for each added line and added path of the git log output with a secret in it."""
    problems: list[str] = []
    sha: str = ""
    path: str = ""
    header: bytes = b""
    parents: int = 0
    number: int = 0
    mark: bytes = COMMIT_MARK.encode()
    for line in log.split(b"\n"):
        if parents and line[:1] in (b" ", b"+", b"-", b"\\"):
            prefix: bytes = line[:parents]
            if line[:1] == b"\\" or b"-" in prefix:
                continue
            if prefix == b"+" * parents:
                problems += [f"{sha[:7]} {path}:{number}: {kind}" for kind in _kinds(line, PUSH_KINDS)]
            number += 1
            continue
        parents = 0
        if line.startswith(mark):
            sha = line[len(mark) :].decode("ascii")
        elif line.startswith(DIFF_HEADERS):
            header = line
        elif line.startswith(NEW_FILE):
            problems += [f"{sha[:7]} path: {kind}" for kind in _kinds(header_path(header), PUSH_KINDS)]
        elif line.startswith(b"+++ "):
            name: bytes = line[4:].rstrip(b"\t")
            path = "a path with a secret" if _kinds(name, PUSH_KINDS) else name.decode("utf-8", errors="replace")
        elif match := HUNK.prefixmatch(line):
            parents = len(match[1]) - 1
            number = int(match[2])
    return problems


def message_problems(log: bytes) -> list[str]:
    """Return a problem for each line of a commit message in the git log -z output with a secret in it."""
    problems: list[str] = []
    for record in log.split(RECORD_END)[:-1]:
        sha, _, message = record.partition(FIELD_SEPARATOR.encode())
        for number, line in enumerate(message.split(b"\n"), start=1):
            problems += [f"{sha.decode('ascii')[:7]} message:{number}: {kind}" for kind in _kinds(line, PUSH_KINDS)]
    return problems


def check_push(stdin: str) -> list[str]:
    """Return the secrets that the pushed commits add, or why the push cannot be read."""
    ranges: list[list[str]] | None = push_ranges(stdin)
    if ranges is None:
        return [NO_PUSH_INPUT]
    problems: list[str] = []
    for rev_args in ranges:
        log: subprocess.CompletedProcess[bytes] = _git(
            "-c",
            "core.quotepath=off",
            "log",
            "-p",
            "--cc",
            "-U0",
            "--text",
            "--no-textconv",
            "--no-color",
            "--no-renames",
            "--no-ext-diff",
            "--no-prefix",
            "--no-show-signature",
            f"--format={COMMIT_MARK}%H",
            *rev_args,
        )
        messages: subprocess.CompletedProcess[bytes] = _git(
            "log", "-z", "--no-show-signature", f"--format=%H{FIELD_SEPARATOR}%B", *rev_args
        )
        if log.returncode or messages.returncode:
            return [f"git: cannot read the pushed commits {' '.join(rev_args)}: fetch the remote and push again"]
        problems += message_problems(messages.stdout) + added_line_problems(log.stdout)
    return [*problems, PUSH_ADVICE] if problems else []


def check_files(paths: list[str]) -> list[str]:
    """Return a problem for each token-shaped value in the files."""
    problems: list[str] = []
    for arg in paths:
        path: Path = Path(arg)
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_bytes().splitlines(), start=1):
            problems += [f"{path}:{number}: {kind}" for kind in _kinds(line, TOKEN_KINDS)]
    return [*problems, FILE_ADVICE] if problems else []


def main() -> int:
    """Report token-shaped values without echoing them; return 1 if there is one."""
    args: list[str] = sys.argv[1:]
    problems: list[str] = check_push(sys.stdin.read()) if args == ["--pre-push"] else check_files(args)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
