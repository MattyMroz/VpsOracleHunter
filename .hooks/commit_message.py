"""Check commit messages in the commit-msg hook, the pre-push hook and CI; print "<rule>: <what>: <fix>" errors."""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
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
"""The nine allowed types."""

PROBLEM: Final[str] = "Problem:"
"""The label of the section that states the problem."""

CHANGE: Final[str] = "Change:"
"""The label of the section that lists the decisions of the change."""

VERIFICATION: Final[str] = "Verification:"
"""The label of the section that lists the checks of the change."""

LABELS: Final[tuple[str, ...]] = (PROBLEM, CHANGE, VERIFICATION)
"""The section labels in their required order."""

REPO_SCOPE: Final[str] = "repo"
"""The scope of a change that is not inside one part of the program, valid in every repository."""

FINAL_VARIABLE: Final[str] = "COMMIT_MESSAGE_FINAL"
"""The variable that, set to 1, marks a message that lands on the default branch as it is, such as a squash message."""

BREAKING_KEY: Final[str] = "BREAKING-CHANGE:"
"""The trailer key of a breaking change."""

SCISSORS: Final[str] = "# ------------------------ >8 ------------------------"
"""The line below which git ignores an edited message in scissors cleanup mode."""

GIT_COMMENTS: Final[tuple[str, ...]] = (SCISSORS, "# Conflicts:")
"""The lines of the comment block git adds after a conflict, which a commit without an editor stores as text."""

NO_EDITOR: Final[str] = ":"
"""The GIT_EDITOR value that git gives a hook when the message is not edited in an editor."""

GIT_SPACE: Final[str] = " \t\r"
"""The characters git strips from the end of a message line: other whitespace, such as U+00A0, stays text."""

SKILL_HINT: Final[str] = "use the commit skill: load it and write the message again by its rules"
"""The last line of every rejection, because an agent that skipped the skill repeats the same errors."""

ZERO_SHA: Final[str] = "0" * 40
"""The SHA that git puts in the pre-push input for a ref that does not exist."""

DEFAULT_BRANCH: Final[str] = "main"
"""The default branch name when origin/HEAD is not set."""

DEFAULT_REMOTE: Final[str] = "origin"
"""The remote name when prek does not give one."""

NO_PUSH_INPUT: Final[str] = (
    "pre-push: the hook got no push input on stdin and no PRE_COMMIT_TO_REF: "
    "run it as the git pre-push hook or through prek"
)
"""The refusal when neither git nor prek gives the hook the pushed refs."""

FIELD_SEPARATOR: Final[str] = "\x1f"
"""The separator between the fields of one commit in the git log output."""

RECORD_SEPARATOR: Final[str] = "\x1e"
"""The separator between commits in the git log output."""

LOG_FORMAT: Final[str] = "--format=" + FIELD_SEPARATOR.join(("%H", "%P", "%an <%ae>", "%B")) + RECORD_SEPARATOR
"""The git log format: sha, parents, author and message of each commit."""

HEADS: Final[str] = "refs/heads/"
"""The ref prefix of a branch."""

SHORT_SHA: Final[int] = 12
"""The number of SHA characters that name a commit in an error."""

HEADER: Final[re.Pattern[str]] = re.compile(
    r"(?P<type>[^\s():!]+)(?:\((?P<scope>[^()]*)\))?(?P<bang>!)?: (?P<summary>.*)"
)
"""The header shape <type>(<scope>)!: <summary>."""

SQUASH_SUFFIX: Final[re.Pattern[str]] = re.compile(r" \(#\d+\)$")
"""The pull request number GitHub appends to a squash header."""

SCOPE_NAME: Final[re.Pattern[str]] = re.compile(r"[a-z0-9-]+")
"""A valid scope name."""

SUMMARY_START: Final[re.Pattern[str]] = re.compile(r"[a-z]")
"""The first character of a summary: a lowercase letter a-z."""

BULLET: Final[str] = "- "
"""The start of every section item."""

UNSAFE_STARTS: Final[tuple[str, ...]] = ("#", "---", "diff -", "Index: ")
"""Line starts that git drops on edit or where git am cuts the message."""

BREAKING_MARK: Final[re.Pattern[str]] = re.compile(r"(?:\* |- )?breaking[ -]change\s*:", re.IGNORECASE)
"""A breaking change marker that changelog tools read anywhere in the body."""

ISSUE_TRAILER: Final[re.Pattern[str]] = re.compile(r"(?:Closes|Refs): (?:#\d+|[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#\d+)")
"""The Closes and Refs trailers."""

BREAKING_TRAILER: Final[re.Pattern[str]] = re.compile(r"BREAKING-CHANGE: \S.*")
"""The BREAKING-CHANGE trailer."""

SIGNOFF_TRAILER: Final[re.Pattern[str]] = re.compile(
    r"Signed-off-by: (?P<person>[^<>\s](?:[^<>]*[^<>\s])? <[^<>\s@]+@[^<>\s@]+>)"
)
"""The Signed-off-by trailer with the name and email of a person."""

ATTRIBUTION: Final[re.Pattern[str]] = re.compile(
    r"\s*(?:-\s+)?(?:(?:co-authored|assisted|generated)-by:|(?:\U0001F916\s*)?generated (?:with|by) \[)",
    re.IGNORECASE,
)
"""A line, also a list item, that credits a co-author or tool: Co-authored-by, Assisted-by, Generated-by or a footer."""

TRAILER_START: Final[re.Pattern[str]] = re.compile(r"[A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*: ")
"""A line start shaped like a git trailer key, such as 'Signed-off-by: '."""

CHERRY_PICK: Final[re.Pattern[str]] = re.compile(r"\(cherry picked from commit (?:[0-9a-f]{40}|[0-9a-f]{64})\)")
"""The line git cherry-pick -x appends."""

REVERT_LINE: Final[re.Pattern[str]] = re.compile(r"- revert commit (?:[0-9a-f]{40}|[0-9a-f]{64})")
"""The first item of Change: in a revert."""

ALLOWED_TRAILERS: Final[str] = (
    "Closes: #<number>, Refs: #<number>, BREAKING-CHANGE: <text>, Signed-off-by: <name> <email> or the cherry-pick line"
)
"""The trailer list quoted in trailers errors."""


class GitError(Exception):
    """git could not read the commits to check."""


# ── Message ───────────────────────────────────────────────────────────────────


def normalize(raw: str, *, scissors: bool = False) -> list[str]:
    """Return the message lines as git stores them, keeping # lines; scissors cuts at the scissors line."""
    lines: list[str] = []
    for line in raw.split("\n"):
        line = line.rstrip(GIT_SPACE)
        if scissors and line == SCISSORS:
            break
        if line or (lines and lines[-1]):
            lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    return lines


def paragraphs(lines: list[str]) -> list[list[str]]:
    """Split normalized lines into paragraphs separated by one empty line."""
    result: list[list[str]] = [[]]
    for line in lines:
        if line:
            result[-1].append(line)
        else:
            result.append([])
    return [paragraph for paragraph in result if paragraph]


def check_summary(summary: str) -> list[str]:
    """Check the summary after the squash suffix is cut."""
    if not summary:
        return ["summary: the summary is empty: write an imperative verb and what changes after ': '"]
    errors: list[str] = []
    if not SUMMARY_START.prefixmatch(summary):
        errors.append(
            f"summary: the summary {summary!r} does not start with a lowercase letter a-z: "
            "start with a lowercase imperative verb, such as 'add', 'remove' or 'handle'"
        )
    if summary.endswith("."):
        errors.append(f"summary: the summary {summary!r} ends with a period: remove it")
    return errors


def check_header(header: str, scopes: tuple[str, ...]) -> tuple[list[str], re.Match[str] | None]:
    """Check the header line and return its match."""
    header = SQUASH_SUFFIX.sub("", header)
    match: re.Match[str] | None = HEADER.fullmatch(header)
    if not match:
        return [
            (
                f"header: the header {header!r} is not '<type>(<scope>): <summary>': "
                "write a type, a scope in parentheses, ': ' and the summary, "
                "for example 'fix(parser): keep the last line'"
            )
        ], None
    errors: list[str] = []
    kind: str = match.group("type")
    scope: str | None = match.group("scope")
    if kind not in TYPES:
        errors.append(f"type: the type {kind!r} is not allowed: use one of {', '.join(TYPES)}")
    allowed: str = ", ".join((*scopes, REPO_SCOPE))
    if scope is None:
        errors.append(f"scope: the header has no scope: write one of {allowed} in parentheses after the type")
    elif scope == kind:
        errors.append(f"scope: the scope {scope!r} repeats the type: use one of {allowed}")
    elif scope != REPO_SCOPE and scope not in scopes:
        errors.append(f"scope: the scope {scope!r} is not on the list: use one of {allowed}")
    errors += check_summary(match.group("summary"))
    return errors, match


def list_items(lines: list[str]) -> list[str]:
    """Join each list item with the lines that continue it."""
    items: list[str] = []
    for line in lines:
        if line.startswith(BULLET) or not items:
            items.append(line)
        else:
            items[-1] += " " + line.strip()
    return items


def check_items(label: str, lines: list[str]) -> list[str]:
    """Check that the lines of a section form list items without a final period."""
    errors: list[str] = []
    for index, line in enumerate(lines):
        if line in LABELS:
            errors.append(f"sections: no empty line before {line!r}: leave one empty line before each label")
        elif line.strip() == BULLET.strip():
            errors.append(f"sections: an item under {label!r} is empty: write the text after '- '")
        elif index == 0 and not line.startswith(BULLET):
            errors.append(
                f"sections: {line!r} under {label!r} is not a list item: start the line with '- ' and the text"
            )
        elif line.startswith(UNSAFE_STARTS):
            errors.append(
                f"sections: {line!r} starts like '#' or a patch, so git drops it or git am cuts the message there: "
                "join it to the item above or reword its start"
            )
        elif CHERRY_PICK.fullmatch(line):
            errors.append(
                f"sections: {line!r} stands under {label!r}: put the cherry-pick line after an empty line, "
                "in the last paragraph"
            )
        elif not line.startswith(BULLET) and TRAILER_START.prefixmatch(line):
            errors.append(
                f"sections: {line!r} under {label!r} looks like a trailer: put trailers in the last paragraph "
                "after an empty line, or join the line to the item above"
            )
    for item in list_items(lines):
        if item.endswith("."):
            errors.append(f"sections: {item!r} ends with a period: remove the period")
    return errors


def check_attribution(lines: list[str]) -> list[str]:
    """Reject lines, also text after a line break that git keeps inside one, that credit a co-author or a tool."""
    return [
        f"attribution: {line!r} credits a co-author or a generating tool, and a commit has one author and no AI "
        "attribution: remove the line"
        for line in lines + inner_lines(lines)
        if ATTRIBUTION.prefixmatch(line)
    ]


def check_comments(lines: list[str]) -> list[str]:
    """Reject the comment block git adds after a conflict, which a commit without an editor keeps as text."""
    found: list[str] = [line for line in lines if line in GIT_COMMENTS]
    if not found:
        return []
    return [
        (
            f"comments: the message keeps the git comment line {found[0]!r}, because a commit without an editor, "
            "such as 'git commit --no-edit', stores git's comment block as text: write the message in a file "
            "without git's comment lines; after a conflict: the first line from git, an empty line and one item per "
            "conflicted file '- <file>: <how it was resolved>, because <reason>' (ship skill, section \"Konflikt\"); "
            "then commit with 'git commit -F <file>'"
        )
    ]


def inner_lines(lines: list[str]) -> list[str]:
    """Return the text after a line break that git keeps inside one line, such as a lone CR or U+2028."""
    return [part for line in lines for part in line.splitlines()[1:]]


def check_sections(body: list[list[str]]) -> tuple[list[str], dict[str, list[str]]]:
    """Check the sections of LABELS and return their lines by label."""
    errors: list[str] = []
    sections: dict[str, list[str]] = {}
    order: str = ", ".join(LABELS)
    trailers: int = len(LABELS)
    for index, label in enumerate(LABELS):
        if index >= len(body):
            errors.append(
                f"sections: the section {label!r} is missing: write {order} in this order, "
                "one empty line between sections"
            )
            continue
        first: str = body[index][0]
        if body[index] == [label]:
            errors.append(f"sections: the section {label!r} has no item: write '- <text>' directly under the label")
        elif first == label:
            sections[label] = body[index][1:]
        elif first.startswith(label):
            errors.append(
                f"sections: {first!r} has text after the label: "
                f"put {label!r} alone on its line and the text on the next line"
            )
        elif first.startswith(BULLET):
            errors.append(
                f"sections: an empty line splits a list before {first!r}: remove the empty lines between items"
            )
        else:
            errors.append(
                f"sections: expected {label!r} but found {first!r}: write {order} "
                "in this order, each label alone on a line, one empty line between sections"
            )
    for label, lines in sections.items():
        errors += check_items(label, lines)
    if len(body) > trailers and body[trailers][0].startswith(BULLET):
        errors.append(
            f"sections: an empty line splits a list before {body[trailers][0]!r}: remove the empty lines between items"
        )
    if len(body) > trailers + 1:
        extra: list[str] = [paragraph[0] for paragraph in body[trailers + 1 :]]
        errors.append(
            f"sections: unexpected paragraphs after the trailers, starting with {extra!r}: "
            f"keep only the {len(LABELS)} sections and one trailer paragraph"
        )
    return errors, sections


def check_verification(lines: list[str]) -> list[str]:
    """Check that every Verification: list item is '- <check>: <result>'."""
    errors: list[str] = []
    for item in list_items(lines):
        if not item.startswith(BULLET):
            continue
        check_name, separator, result = item.removeprefix(BULLET).partition(": ")
        if not separator or not check_name.strip() or not result.strip():
            errors.append(
                f"verification: {item!r} is not '- <check>: <result>': write one item per check, for example "
                "'- pytest tests/parser: 41 passed' or '- Windows runtime: not run, because the host runs macOS'"
            )
    return errors


def git_trailers(message: str) -> list[str]:
    """Return the trailers git finds in the message."""
    run: subprocess.CompletedProcess[bytes] = subprocess.run(
        ["git", "interpret-trailers", "--parse", "--no-divider"],
        input=message.encode("utf-8"),
        capture_output=True,
        check=True,
    )
    return [line.rstrip() for line in run.stdout.decode("utf-8").splitlines() if line.strip()]


def check_trailers(trailers: list[str], author_message: str) -> list[str]:
    """Check the trailer paragraph and how git reads it."""
    errors: list[str] = []
    for line in trailers:
        if not any(
            pattern.fullmatch(line) for pattern in (ISSUE_TRAILER, BREAKING_TRAILER, SIGNOFF_TRAILER, CHERRY_PICK)
        ):
            errors.append(
                f"trailers: {line!r} is not an allowed trailer: move the text into a section, "
                f"or use only {ALLOWED_TRAILERS} in the last paragraph"
            )
    breaking: list[int] = [index for index, line in enumerate(trailers) if line.startswith(BREAKING_KEY)]
    if breaking and breaking[0] != 0:
        errors.append(
            "trailers: BREAKING-CHANGE is not the first trailer: move it to the first line of the last paragraph"
        )
    expected: list[str] = [line for line in trailers if not CHERRY_PICK.fullmatch(line)]
    parsed: list[str] = git_trailers(author_message)
    if parsed != expected:
        errors.append(
            f"trailers: git reads the trailers as {parsed!r}, not as the last paragraph {expected!r}: "
            f"keep only these lines in the last paragraph, after an empty line: {ALLOWED_TRAILERS}"
        )
    return errors


def check_breaking(trailers: list[str], other_lines: list[str], *, bang: bool) -> list[str]:
    """Check that '!' and the BREAKING-CHANGE trailer go together."""
    errors: list[str] = []
    count: int = sum(line.startswith(BREAKING_KEY) for line in trailers)
    if bang and not count:
        errors.append(
            "breaking: the header has '!' but the trailers have no BREAKING-CHANGE: add "
            "'BREAKING-CHANGE: <what stops working>; <what to do instead>' as the first line of the last "
            "paragraph, or remove '!'"
        )
    if count > 1:
        errors.append("breaking: there is more than one BREAKING-CHANGE trailer: keep one sentence in one trailer")
    if not bang and count:
        errors.append(
            "breaking: the trailers have BREAKING-CHANGE but the header has no '!': put '!' right before ': '"
        )
    for line in other_lines:
        if BREAKING_MARK.prefixmatch(line):
            errors.append(
                f"breaking: {line!r} marks a breaking change outside the trailers: move it to the last paragraph as "
                "'BREAKING-CHANGE: <text>' with '!' in the header, or reword the line"
            )
    return errors


def check_header_lines(paragraph: list[str]) -> list[str]:
    """Check that the header paragraph is one line."""
    if len(paragraph) == 1:
        return []
    return [
        (
            f"header: the header continues on the next line {paragraph[1]!r}: keep the header on one line "
            "and leave one empty line after it"
        )
    ]


def check(
    raw: str, scopes: tuple[str, ...] = (), *, scissors: bool = False, final: bool = False, comments: bool = True
) -> list[str]:
    """Return the errors of a message: header, attribution, git comments, and with final the full form."""
    lines: list[str] = normalize(raw, scissors=scissors)
    if not lines:
        return ["header: the message is empty: write the header '<type>(<scope>): <summary>'"]
    parts: list[list[str]] = paragraphs(lines)
    errors, match = check_header(parts[0][0], scopes)
    errors += check_header_lines(parts[0])
    attribution: list[str] = check_attribution(lines)
    errors += attribution
    if comments:
        errors += check_comments(lines)
    if not final or attribution:
        return errors
    body: list[list[str]] = parts[1:]
    trailers: list[str] = body[len(LABELS)] if len(body) > len(LABELS) else []
    other_lines: list[str] = [line for part in parts if part is not trailers for line in part]
    if match:
        errors += check_breaking(trailers, other_lines, bang=bool(match.group("bang")))
    section_errors, sections = check_sections(body)
    errors += section_errors
    errors += check_verification(sections.get(VERIFICATION, []))
    if not section_errors:
        errors += check_trailers(trailers, "\n".join(lines) + "\n")
    if match and match.group("type") == "revert":
        change: list[str] = list_items(sections.get(CHANGE, []))
        if not change or not REVERT_LINE.fullmatch(change[0]):
            errors.append(
                f"revert: the first item of Change: is {change[0] if change else ''!r}: write exactly "
                "'- revert commit <full SHA>' with the SHA from 'git log -1 --format=%H <commit>' "
                "and nothing after it; the reason goes to Problem:"
            )
    return errors


# ── History ───────────────────────────────────────────────────────────────────


def git_output(*args: str) -> str:
    """Return the stripped output of a git command, or '' when it fails."""
    run: subprocess.CompletedProcess[bytes] = subprocess.run(["git", *args], capture_output=True, check=False)
    return run.stdout.decode("utf-8", errors="replace").strip() if run.returncode == 0 else ""


def commits(*rev_args: str) -> list[tuple[str, int, str, str]]:
    """Return sha, parent count, author and message of each commit in the revision range."""
    run: subprocess.CompletedProcess[bytes] = subprocess.run(
        ["git", "log", "--reverse", LOG_FORMAT, *rev_args], capture_output=True, check=False
    )
    if run.returncode != 0:
        raise GitError(f"{run.stderr.decode('utf-8', errors='replace').strip()}: fetch the remote and push again")
    result: list[tuple[str, int, str, str]] = []
    for record in run.stdout.decode("utf-8", errors="replace").split(RECORD_SEPARATOR):
        fields: list[str] = record.strip("\n").split(FIELD_SEPARATOR, 3)
        if fields == [""]:
            continue
        if len(fields) < 4:
            raise GitError(f"a commit message holds the separator {RECORD_SEPARATOR!r}: remove it from the message")
        sha, parents, author, message = fields
        result.append((sha, len(parents.split()), author, message))
    return result


def is_commit(sha: str) -> bool:
    """Tell whether the local repository has the commit."""
    run: subprocess.CompletedProcess[bytes] = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True, check=False
    )
    return run.returncode == 0


def is_ancestor(sha: str, commit: str) -> bool:
    """Tell whether sha is in the history of commit."""
    run: subprocess.CompletedProcess[bytes] = subprocess.run(
        ["git", "merge-base", "--is-ancestor", sha, commit], capture_output=True, check=False
    )
    return run.returncode == 0


def check_revert_target(message: str, commit: str = "HEAD") -> list[str]:
    """Check that every '- revert commit <SHA>' line names a commit in the history of the given commit."""
    errors: list[str] = []
    for line in normalize(message):
        if not REVERT_LINE.fullmatch(line):
            continue
        sha: str = line.split()[-1]
        if not is_ancestor(sha, commit):
            errors.append(
                f"revert: the reverted commit {sha} is not in the history of this branch: "
                "use the full SHA of a commit on this branch from 'git log -1 --format=%H <commit>', or name "
                "a commit outside this branch by its short id instead of this line, with a type other than revert"
            )
    return errors


def check_signoffs(message: str, author: str) -> list[str]:
    """Return the errors of the Signed-off-by lines that name another person than the author of the commit."""
    errors: list[str] = []
    for line in normalize(message):
        match: re.Match[str] | None = SIGNOFF_TRAILER.fullmatch(line)
        if match and match.group("person") != author:
            errors.append(
                f"trailers: {line!r} is not the author {author!r} of the commit, and only the author signs off: "
                "'git commit -s' adds the committer, so a commit made with --author of another person gets the line "
                "from its author; remove the line or sign off as the author"
            )
    return errors


def check_commit(sha: str, parents: int, author: str, message: str, scopes: tuple[str, ...]) -> list[str]:
    """Return the errors of one commit on the default branch."""
    header: str = message.split("\n", 1)[0]
    errors: list[str]
    if parents > 1:
        errors = [
            (
                f"branch: the commit is a merge commit {header!r}: the default branch takes no merge commits: "
                "keep merge commits on the task branch; the change lands on the default branch through a squash "
                "merge of the pull request"
            )
        ]
    else:
        errors = check(message, scopes, final=True) + check_signoffs(message, author)
        errors += check_revert_target(message, sha)
    return [f"{error} (commit {sha[:SHORT_SHA]})" for error in errors]


def check_range(rev_args: list[str], scopes: tuple[str, ...]) -> list[str]:
    """Return the errors of every commit that the revision arguments select for the default branch."""
    errors: list[str] = []
    for sha, parents, author, message in commits(*rev_args):
        errors += check_commit(sha, parents, author, message, scopes)
    return errors


def check_branch_signoffs(rev_args: list[str]) -> list[str]:
    """Return the sign-off errors of every commit that the revision arguments select for another branch."""
    errors: list[str] = []
    for sha, _parents, author, message in commits(*rev_args):
        errors += [f"{error} (commit {sha[:SHORT_SHA]})" for error in check_signoffs(message, author)]
    return errors


def default_branch() -> str:
    """Return the default branch name from origin/HEAD, or main."""
    ref: str = git_output("symbolic-ref", "--short", f"refs/remotes/{DEFAULT_REMOTE}/HEAD")
    return ref.split("/", 1)[1] if "/" in ref else DEFAULT_BRANCH


def pre_push_input(stdin: str) -> str | None:
    """Return the pre-push lines from stdin or prek variables: empty under prek without a target, None without input."""
    if stdin.strip():
        return stdin
    to_ref: str = os.environ.get("PRE_COMMIT_TO_REF", "")
    if not to_ref:
        return "" if os.environ.get("PRE_COMMIT") == "1" else None
    branch: str = os.environ.get("PRE_COMMIT_REMOTE_BRANCH", "")
    if not branch:
        return ""
    if not branch.startswith("refs/"):
        branch = HEADS + branch
    from_ref: str = os.environ.get("PRE_COMMIT_FROM_REF", "") or ZERO_SHA
    lines: str = f"HEAD {to_ref} {branch} {from_ref}\n"
    name: str = default_branch()
    target: str = HEADS + name
    local: str = git_output("rev-parse", "--verify", "--quiet", target)
    if branch == target or not local:
        return lines
    remote_name: str = os.environ.get("PRE_COMMIT_REMOTE_NAME", "") or DEFAULT_REMOTE
    remote: str = git_output("rev-parse", "--verify", "--quiet", f"refs/remotes/{remote_name}/{name}") or ZERO_SHA
    return lines + f"{target} {local} {target} {remote}\n"


def check_pre_push(stdin: str, scopes: tuple[str, ...]) -> list[str]:
    """Check every rule on the commits a push sends to the default branch and the sign-offs on other branches."""
    target: str = HEADS + default_branch()
    errors: list[str] = []
    for line in stdin.splitlines():
        parts: list[str] = line.split()
        if len(parts) != 4 or not parts[2].startswith(HEADS) or set(parts[1]) == {"0"}:
            continue
        local, remote = parts[1], parts[3]
        if parts[2] == target:
            rev_args: list[str] = [local, "--not", "--remotes"] if set(remote) == {"0"} else [f"{remote}..{local}"]
            errors += check_range(rev_args, scopes)
        else:
            known: bool = set(remote) != {"0"} and is_commit(remote)
            errors += check_branch_signoffs([f"{remote}..{local}" if known else local, "--not", "--remotes"])
    return errors


def in_progress(head: str) -> bool:
    """Tell whether the git state file, such as MERGE_HEAD, exists."""
    path: str = git_output("rev-parse", "--git-path", head)
    return bool(path) and Path(path).is_file()


def merge_in_progress() -> bool:
    """Tell whether the commit concludes a merge."""
    return in_progress("MERGE_HEAD")


def pick_in_progress() -> bool:
    """Tell whether the commit concludes a cherry-pick or a revert, whose --continue strips git's comments."""
    return in_progress("CHERRY_PICK_HEAD") or in_progress("REVERT_HEAD")


# ── Command line ──────────────────────────────────────────────────────────────


def check_message_file(message_file: str, scopes: tuple[str, ...]) -> list[str]:
    """Return the errors of the message file of the commit-msg hook, in the mode that the environment sets."""
    raw: str = Path(message_file).read_bytes().decode("utf-8", errors="replace")
    edited: bool = os.environ.get("GIT_EDITOR") != NO_EDITOR
    final: bool = os.environ.get(FINAL_VARIABLE) == "1"
    if not final and merge_in_progress():
        lines: list[str] = normalize(raw, scissors=edited)
        return check_attribution(lines) + ([] if edited else check_comments(lines))
    if (
        edited
        and git_output("config", "commit.cleanup") != "scissors"
        and any(line.startswith("#") for line in raw.splitlines())
    ):
        return [
            (
                "cleanup: the message has lines that start with '#', and commit.cleanup is not scissors, so the "
                "hook cannot tell the git template from the text: run 'git config commit.cleanup scissors' "
                "and commit again"
            )
        ]
    errors: list[str] = check(raw, scopes, scissors=edited, final=final, comments=not pick_in_progress())
    if final:
        errors += check_revert_target(raw)
    return errors


def check_push(stdin: str, scopes: tuple[str, ...]) -> list[str]:
    """Return the errors of the push that the pre-push hook gets on stdin or from prek."""
    push: str | None = pre_push_input(stdin)
    if push is None:
        return [NO_PUSH_INPUT]
    try:
        return check_pre_push(push, scopes)
    except GitError as failure:
        return [f"git: cannot read the commits to check: {failure}"]


def main(argv: list[str] | None = None) -> int:
    """Check a message file or a push; return 1 on any error."""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Check commit messages.",
        epilog=(
            "commit-msg hook: --scopes=<list> <message file>, with COMMIT_MESSAGE_FINAL=1 for a message that lands "
            "on the default branch as it is; pre-push hook: --scopes=<list> --pre-push"
        ),
    )
    parser.add_argument("--scopes", default="", help="comma-separated scope list of the repository")
    parser.add_argument("--pre-push", action="store_true", help="read the pre-push hook input from stdin")
    parser.add_argument("message_file", nargs="?", help="commit message file of the commit-msg hook")
    args: argparse.Namespace = parser.parse_args(argv)
    message_file: str | None = args.message_file
    pre_push: bool = args.pre_push
    scopes: tuple[str, ...] = tuple(name for name in args.scopes.split(",") if name)
    if (message_file is not None) == pre_push:
        parser.error("use exactly one of: a message file or --pre-push")
    invalid: list[str] = [name for name in scopes if not SCOPE_NAME.fullmatch(name)]
    errors: list[str]
    if invalid:
        errors = [
            f"scope: the hook declares invalid scope names {invalid!r}: use lowercase letters, digits and hyphens"
        ]
    elif message_file is not None:
        errors = check_message_file(message_file, scopes)
    else:
        errors = check_push(sys.stdin.read(), scopes)
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        print(SKILL_HINT, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
