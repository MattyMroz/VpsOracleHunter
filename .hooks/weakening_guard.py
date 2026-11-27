"""Print the signals of weakened tests and checks from PRE_COMMIT_FROM_REF to PRE_COMMIT_TO_REF or HEAD; never fail."""

import argparse
import ast
import difflib
import fnmatch
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import PurePath, PurePosixPath
from typing import Final, TypeIs

# jscpd:ignore-start
BASE_VARIABLE: Final[str] = "PRE_COMMIT_FROM_REF"
"""The variable that prek run --from-ref sets to the base of the range."""
# jscpd:ignore-end

HEAD_VARIABLE: Final[str] = "PRE_COMMIT_TO_REF"
"""The variable that prek run --to-ref sets to the end of the range; without it the range ends at HEAD."""

HEAD: Final[str] = "HEAD"
"""The end of the range without HEAD_VARIABLE: the checked-out commit."""

SUMMARY_VARIABLE: Final[str] = "GITHUB_STEP_SUMMARY"
"""The variable that GitHub Actions sets to the file of the job summary."""

HOOK_CONFIG: Final[str] = ".pre-commit-config.yaml"
"""The hook config whose entries name the check scripts."""

CHECK_PATTERNS: Final[tuple[str, ...]] = (".github/workflows/*", HOOK_CONFIG)
"""Paths of the CI workflows and the hook config, whose every change is a signal."""

ENTRY: Final[re.Pattern[str]] = re.compile(r"^\s*(?:-\s+)?entry:\s*(.+)$")
"""A hook entry line of the hook config."""

TEST_DIRS: Final[frozenset[str]] = frozenset({"test", "tests", "__tests__", "spec", "specs"})
"""Directory names whose files are tests whatever they are called, as in prose_guard."""

TEST_INFIXES: Final[tuple[str, ...]] = (".test.", ".spec.")
"""File name infixes of the *.test.* and *.spec.* conventions, as in prose_guard."""

TEST_CONFIG_NAMES: Final[tuple[str, ...]] = (
    "pyproject.toml",
    "pytest.ini",
    "tox.ini",
    "setup.cfg",
    "package.json",
    "bunfig.toml",
    "nextest.toml",
    "vitest.config.*",
    "vitest.workspace.*",
    "jest.config.*",
    "playwright*.config.*",
)
"""File name patterns of the test runner configuration."""

PY_SUFFIXES: Final[frozenset[str]] = frozenset({".py"})
"""Extensions of Python tests, as in prose_guard."""

JS_SUFFIXES: Final[frozenset[str]] = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"})
"""Extensions of JavaScript and TypeScript tests, as in prose_guard."""

LANG: Final[dict[str, str]] = {**dict.fromkeys(PY_SUFFIXES, "py"), **dict.fromkeys(JS_SUFFIXES, "ts"), ".rs": "rs"}
"""Test language by file extension."""

SKIP: Final[re.Pattern[str]] = re.compile(
    r"@pytest\.mark\.(skip|skipif|xfail)\b|\bpytest\.(skip|xfail|importorskip)\s*\(|mark\.(skip|xfail)\b"
    r"|@unittest\.(skip|skipIf|skipUnless|expectedFailure)\b|\.skipTest\s*\(|\bSkipTest\b"
    r"|\b(it|test|describe|suite|bench)(\.\w+)*\.(only|skip|todo|fixme|fails|fail|skipIf|runIf)\b"
    r"|\b(x(?:it|test|describe)|f(?:it|describe))\s*\(|#\[\s*ignore\b|cfg_attr\([^)]*\bignore\b"
)
"""A skipped, expected-to-fail or focused test."""

RETRY_TEST: Final[re.Pattern[str]] = re.compile(
    r"@pytest\.mark\.flaky|@flaky\b|\bretryTimes\s*\(|\bretry\s*:\s*\d|\bretries\s*:\s*\d|\.retries\s*\("
    r"|--reruns|--retries"
)
"""A retry or flaky marker in a test file."""

RETRY_CONFIG: Final[re.Pattern[str]] = re.compile(
    r"\breruns?\b|\bretries\b|\bretry\b|only_rerun|\bflaky\b", re.IGNORECASE
)
"""A retry setting in a configuration file."""

IGNORE_FAILURE: Final[re.Pattern[str]] = re.compile(
    r"continue-on-error\s*:\s*true|\|\|\s*(true|exit 0|:)\s*$|allow_failure|passWithNoTests|--exit-zero|\bset \+e\b"
    r"|\bSKIP\s*[:=]"
)
"""A setting that lets a failing step pass."""

NARROW: Final[re.Pattern[str]] = re.compile(
    r"(^|[\s\"'=\[])-m\s|(^|[\s\"'=\[])-k\s|--ignore|--deselect|--cov-fail-under|-p\s*no:|--last-failed|--lf\b"
)
"""A runner option that selects fewer tests or lowers a threshold."""

RUNNER: Final[re.Pattern[str]] = re.compile(
    r"\b(pytest|unittest|vitest|jest|playwright|nextest|cargo test|bun test|npm test|pnpm test|prek|mypy|pyright|ruff"
    r"|test\.sh|check\.py)\b"
)
"""A tool name whose fewer mentions mean a removed check."""

HOOK_ID: Final[re.Pattern[str]] = re.compile(r"^\s*-\s*id\s*:")
"""A hook entry of a hook config."""

TOML_SECTION: Final[re.Pattern[str]] = re.compile(r"pytest|coverage|nextest|profile|test", re.IGNORECASE)
"""A TOML, INI or CFG section that holds test configuration."""

TOML_KEYS: Final[frozenset[str]] = frozenset(
    {
        "addopts",
        "testpaths",
        "python_files",
        "python_classes",
        "python_functions",
        "norecursedirs",
        "fail_under",
        "omit",
        "exclude",
        "exclude_lines",
        "exclude_also",
        "include",
        "source",
        "xfail_strict",
        "strict_xfail",
        "strict",
        "strict_markers",
        "strict_config",
        "filterwarnings",
        "retries",
        "default-filter",
        "filter",
        "threshold",
    }
)
"""Test configuration keys of TOML, INI and CFG files."""

JS_KEYS: Final[frozenset[str]] = frozenset(
    {
        "testMatch",
        "testRegex",
        "testPathIgnorePatterns",
        "coveragePathIgnorePatterns",
        "coverageThreshold",
        "thresholds",
        "lines",
        "branches",
        "functions",
        "statements",
        "retries",
        "retry",
        "include",
        "exclude",
        "testIgnore",
        "testDir",
        "grep",
        "grepInvert",
        "forbidOnly",
        "passWithNoTests",
        "projects",
        "test",
    }
)
"""Test configuration keys of JavaScript, TypeScript and JSON files."""

YAML_KEYS: Final[frozenset[str]] = frozenset(
    {"continue-on-error", "paths-ignore", "exclude", "retries", "retry", "timeout-minutes"}
)
"""Test configuration keys of YAML files."""

INI_SUFFIXES: Final[frozenset[str]] = frozenset({".toml", ".ini", ".cfg"})
"""Extensions of configuration files with sections."""

JS_CONFIG_SUFFIXES: Final[frozenset[str]] = frozenset({".ts", ".js", ".mjs", ".cjs", ".mts", ".json"})
"""Extensions of JavaScript-style configuration files."""

PYTEST_FILES: Final[frozenset[str]] = frozenset({"pytest.ini", "tox.ini"})
"""File names whose every section is pytest configuration."""

EXPECTED_DATA: Final[re.Pattern[str]] = re.compile(r"snap|snapshot|expected|golden|baseline", re.IGNORECASE)
"""A path of expected test data."""

TS_TEST: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w.])(?:it|test)(?:\.(?:only|skip|todo|fails|concurrent|sequential))*\s*\(\s*(['\"`])((?:\\.|(?!\1).)*)\1"
)
"""A JavaScript or TypeScript test call with its name."""

TS_VALUE_ASSERT: Final[re.Pattern[str]] = re.compile(r"\bexpect\s*\(|\bassert(?:\.\w+)?\s*\(")
"""A JavaScript or TypeScript assertion on a value, the only kind whose changed literal is a changed expectation."""

TS_ASSERT: Final[re.Pattern[str]] = re.compile(
    rf"{TS_VALUE_ASSERT.pattern}|\bexpectTypeOf\s*\(|\.toMatch(?:Inline)?Snapshot\s*\("
)
"""A JavaScript or TypeScript assertion: on a value, on a type or against a snapshot."""

RS_TEST: Final[re.Pattern[str]] = re.compile(
    r"#\[(?:tokio::)?(?:test|rstest)[^\]]*\]\s*(?:#\[[^\]]*\]\s*)*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)"
)
"""A Rust test function with its name."""

RS_ASSERT: Final[re.Pattern[str]] = re.compile(r"\b(?:debug_)?assert(?:_eq|_ne|_matches)?!")
"""A Rust assertion macro."""

ASSERT_LINE: Final[dict[str, re.Pattern[str]]] = {
    "ts": TS_VALUE_ASSERT,
    "rs": RS_ASSERT,
}
"""An assertion line by language, for the languages compared line by line."""

PY_ASSERT_CALL: Final[re.Pattern[str]] = re.compile(r"assert|fail$|raises$|warns$")
"""A Python call name that asserts."""

LITERAL: Final[re.Pattern[str]] = re.compile(
    r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|\b\d\w*(?:\.\d\w*)?"
    r"|\b(?:true|false|True|False|None|null)\b"
)
"""A literal value in an assertion; a number may carry digit separators and a type suffix, as in 1_000 or 3u32."""

SIMILAR: Final[float] = 0.8
"""The body likeness from which a new test counts as the same test moved."""

BRACE_WINDOW: Final[int] = 600
"""How far after a test name its opening brace may stand."""

QUOTE_LIMIT: Final[int] = 100
"""Characters of a quoted line in a signal."""

ASSERT_LIMIT: Final[int] = 80
"""Characters of a quoted assertion in a signal."""

SHORT_SHA: Final[int] = 7
"""Characters of a shown commit id."""

TITLE: Final[str] = "test weakening"
"""The prefix of every output line."""

ADVICE: Final[str] = "give each a reason line in the pull request description"
"""What the author does with the signals."""

OWNER_ADVICE: Final[str] = "merge only on the owner's word, with a reason line in the pull request description"
"""What the author does with a changed path that the owner guards."""


@dataclass
class TestBody:
    """One test function: where it lives, its body text and its assertions."""

    path: str
    name: str
    body: str
    asserts: list[str] = field(default_factory=list[str])


@dataclass
class Signal:
    """One signal that needs a reason in the pull request description."""

    path: str
    kind: str
    reason: str


@dataclass
class Change:
    """One changed test, test configuration, check or owner file of the range."""

    status: str
    old: str
    new: str
    kind: str
    owned: bool
    removed: list[str] = field(default_factory=list[str])
    added: list[str] = field(default_factory=list[str])


def git(*args: str) -> str:
    """Return the stdout of a git command, raising on failure."""
    done: subprocess.CompletedProcess[str] = subprocess.run(
        ["git", *args], capture_output=True, check=True, text=True, errors="replace"
    )
    return done.stdout


def blobs(specs: list[str]) -> dict[str, str]:
    """Return the content of each rev:path spec, empty when missing, in one git cat-file call."""
    if not specs:
        return {}
    done: subprocess.CompletedProcess[bytes] = subprocess.run(
        ["git", "cat-file", "--batch"], input="\n".join(specs).encode() + b"\n", capture_output=True, check=True
    )
    out: bytes = done.stdout
    pos: int = 0
    result: dict[str, str] = {}
    for spec in specs:
        end: int = out.index(b"\n", pos)
        header: list[bytes] = out[pos:end].split()
        pos = end + 1
        if header[-1] == b"missing":
            result[spec] = ""
            continue
        size: int = int(header[2])
        result[spec] = out[pos : pos + size].decode("utf-8", "replace")
        pos += size + 1
    return result


# jscpd:ignore-start
def _is_test_file(path: PurePath) -> bool:
    """Return whether the path is a test file by the naming convention of prose_guard."""
    if {part.lower() for part in path.parts} & TEST_DIRS:
        return True
    name: str = path.name.lower()
    stem: str = name.split(".")[0]
    return (
        name == "conftest.py"
        or stem.startswith("test_")
        or stem.endswith("_test")
        or any(infix in name for infix in TEST_INFIXES)
    )


# jscpd:ignore-end


def is_test_path(path: str, tests: tuple[str, ...]) -> bool:
    """Return whether a path is a test file by the naming convention of prose_guard or the declared patterns."""
    return _is_test_file(PurePosixPath(path)) or any(fnmatch.fnmatchcase(path, glob) for glob in tests)


def kind_of(path: str, tests: tuple[str, ...], scripts: set[str]) -> str:
    """Return check, config, test or an empty string for a path."""
    if path in scripts or any(fnmatch.fnmatchcase(path, glob) for glob in CHECK_PATTERNS):
        return "check"
    if any(fnmatch.fnmatchcase(PurePosixPath(path).name, glob) for glob in TEST_CONFIG_NAMES):
        return "config"
    return "test" if is_test_path(path, tests) else ""


def entry_scripts(config: str) -> set[str]:
    """Return every word of the hook entries, so a changed path among them is a script that a hook runs."""
    words: set[str] = set()
    for line in config.splitlines():
        if match := ENTRY.prefixmatch(line):
            words |= {word.strip("\"'").removeprefix("./") for word in match[1].split()}
    return words


def changes(base: str, head: str, tests: tuple[str, ...], owner: tuple[str, ...]) -> tuple[list[Change], list[str]]:
    """Return the changed test, configuration, check and owner files with their lines, and the lines added elsewhere."""
    raw: list[str] = git("diff", "--name-status", "-M", "-z", base, head).split("\0")
    configs: dict[str, str] = blobs([f"{base}:{HOOK_CONFIG}", f"{head}:{HOOK_CONFIG}"])
    scripts: set[str] = {word for text in configs.values() for word in entry_scripts(text)}
    found: list[Change] = []
    index: int = 0
    while index < len(raw) - 1:
        status: str = raw[index]
        old: str
        new: str
        if status.startswith(("R", "C")):
            old, new, index = raw[index + 1], raw[index + 2], index + 3
        else:
            old = new = raw[index + 1]
            index += 2
        kind: str = kind_of(new, tests, scripts) or kind_of(old, tests, scripts)
        owned: bool = any(fnmatch.fnmatchcase(path, glob) for path in (old, new) for glob in owner)
        if kind or owned:
            found.append(Change(status[0], old, new, kind, owned))
    code_added: list[str] = []
    if not found:
        return found, code_added
    text: str = git(
        "-c",
        "core.quotePath=false",
        "diff",
        "-U0",
        "-M",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        base,
        head,
    )
    by_new: dict[str, Change] = {change.new: change for change in found}
    by_old: dict[str, Change] = {change.old: change for change in found}
    current: Change | None = None
    header: bool = False
    for line in text.splitlines():
        if line.startswith("diff --git "):
            header, current = True, None
        elif header:
            if line.startswith("--- a/"):
                current = by_old.get(line[6:].rstrip("\t"))
            elif line.startswith("+++ b/"):
                current = by_new.get(line[6:].rstrip("\t")) or current
            elif line.startswith("@@"):
                header = False
        elif line.startswith("+"):
            (current.added if current else code_added).append(line[1:])
        elif current and line.startswith("-"):
            current.removed.append(line[1:])
    return found, code_added


class _BlankLiterals(ast.NodeTransformer):
    """Replace every literal with one name, so assertions that differ only in values compare equal."""

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        """Return the placeholder name for a constant."""
        return ast.Name(id="C", ctx=ast.Load())

    def visit_JoinedStr(self, node: ast.JoinedStr) -> ast.AST:
        """Return the placeholder name for an f-string."""
        return ast.Name(id="C", ctx=ast.Load())


def _is_assert(node: ast.AST) -> bool:
    """Return whether a Python node is an assert statement or an assertion call."""
    if isinstance(node, ast.Assert):
        return True
    if isinstance(node, ast.Call):
        func: ast.expr = node.func
        name: str = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
        return bool(PY_ASSERT_CALL.prefixmatch(name))
    return False


def py_tests(path: str, source: str) -> list[TestBody]:
    """Return the tests of a Python file, none when it does not parse."""
    try:
        tree: ast.Module = ast.parse(source)
    except SyntaxError, ValueError:
        return []
    found: list[TestBody] = []

    def walk(nodes: list[ast.stmt], prefix: str) -> None:
        """Collect the test functions of the nodes and of the classes among them."""
        for node in nodes:
            if isinstance(node, ast.ClassDef):
                walk(node.body, f"{prefix}{node.name}.")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                asserts: list[str] = [ast.unparse(item) for item in ast.walk(node) if _is_assert(item)]
                body: str = "\n".join(ast.unparse(item) for item in node.body)
                found.append(TestBody(path, prefix + node.name, body, asserts))

    walk(tree.body, "")
    return found


def _block(source: str, start: int) -> str:
    """Return the brace block that opens soon after a position, or an empty string."""
    open_at: int = source.find("{", start, start + BRACE_WINDOW)
    if open_at < 0:
        return ""
    depth: int = 0
    for pos in range(open_at, len(source)):
        if source[pos] == "{":
            depth += 1
        elif source[pos] == "}":
            depth -= 1
            if depth == 0:
                return source[open_at : pos + 1]
    return source[open_at:]


def text_tests(path: str, source: str, lang: str) -> list[TestBody]:
    """Return the tests of a TypeScript, JavaScript or Rust file by pattern and brace counting."""
    pattern, asserts = (TS_TEST, TS_ASSERT) if lang == "ts" else (RS_TEST, RS_ASSERT)
    found: list[TestBody] = []
    seen: Counter[str] = Counter()
    for match in pattern.finditer(source):
        name: str = match[2] if lang == "ts" else match[1]
        seen[name] += 1
        key: str = name if seen[name] == 1 else f"{name}#{seen[name]}"
        body: str = _block(source, match.end())
        lines: list[str] = [line.strip() for line in body.splitlines() if asserts.search(line)]
        found.append(TestBody(path, key, re.sub(r"\s+", " ", body), lines))
    return found


def tests_of(path: str, source: str) -> list[TestBody]:
    """Return the tests of a test file in a known language."""
    lang: str | None = LANG.get(PurePosixPath(path).suffix)
    if not source or not lang:
        return []
    return py_tests(path, source) if lang == "py" else text_tests(path, source, lang)


def skeleton(text: str) -> str:
    """Return an assertion with every literal blanked."""
    try:
        return ast.unparse(_BlankLiterals().visit(ast.parse(text)))
    except SyntaxError, ValueError:
        return re.sub(r"\s+", "", LITERAL.sub("C", text))


def changed_expectations(old: list[str], new: list[str]) -> list[tuple[str, str]]:
    """Return pairs of assertions that keep their shape but change a literal value."""
    gone: list[str] = list((Counter(old) - Counter(new)).elements())
    came: list[str] = list((Counter(new) - Counter(old)).elements())
    pairs: list[tuple[str, str]] = []
    for before in gone:
        shape: str = skeleton(before)
        match: str | None = next((after for after in came if skeleton(after) == shape), None)
        if match is not None:
            came.remove(match)
            pairs.append((before, match))
    return pairs


def config_key(lines: list[str], index: int, suffix: str) -> tuple[str, str]:
    """Return the section and key that a configuration line belongs to."""
    section: str = ""
    key: str = ""
    for line in lines[: index + 1]:
        stripped: str = line.strip()
        if suffix in INI_SUFFIXES:
            if stripped.startswith("[") and stripped.endswith("]"):
                section, key = stripped.strip("[]"), ""
            elif match := re.prefixmatch(r"^\s*([\w.\"-]+)\s*=", line):
                key = match[1].strip('"')
        elif match := re.prefixmatch(r"^\s*-?\s*[\"']?([\w-]+)[\"']?\s*:", line):
            key = match[1]
    return section, key


def watched(suffix: str, section: str, key: str, path: str) -> bool:
    """Return whether a configuration key is part of the test configuration."""
    if suffix in INI_SUFFIXES:
        in_section: bool = bool(TOML_SECTION.search(section)) or PurePosixPath(path).name in PYTEST_FILES
        return in_section and key in TOML_KEYS
    if suffix in (".yml", ".yaml"):
        return key in YAML_KEYS
    if suffix in JS_CONFIG_SUFFIXES:
        return key in JS_KEYS or key.startswith("test:")
    return False


def line_indexes(lines: list[str], wanted: list[str]) -> list[int]:
    """Return the line numbers of the wanted lines in order, one per occurrence."""
    pool: dict[str, list[int]] = {}
    for number, line in enumerate(lines):
        pool.setdefault(line, []).append(number)
    return [pool[line].pop(0) for line in wanted if pool.get(line)]


def follows(before: str, after: str, code_text: str) -> str:
    """Say whether every new literal of an assertion also appears in the lines the range adds to other files."""
    new: set[str] = set(LITERAL.findall(after)) - set(LITERAL.findall(before))
    texts: set[str] = {value.strip("\"'`") for value in new} - {""}
    if not code_text:
        return "product code unchanged"
    if texts and all(text in code_text for text in texts):
        return "new value appears in the code diff"
    return "new value not in the code diff"


def similar(first: str, second: str) -> float:
    """Return how alike two test bodies are, from 0 to 1."""
    matcher: difflib.SequenceMatcher[str] = difflib.SequenceMatcher(None, first, second, autojunk=False)
    if matcher.real_quick_ratio() >= SIMILAR and matcher.quick_ratio() >= SIMILAR:
        return matcher.ratio()
    return 0.0


# jscpd:ignore-start
def _is_bare_string(node: object) -> TypeIs[ast.Expr]:
    """Return whether a node is a statement made of one string literal, such as a docstring."""
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


# jscpd:ignore-end


def code_only(source: str) -> str:
    """Return Python source without docstrings and bare strings, so prose naming a tool is not a check."""
    try:
        tree: ast.Module = ast.parse(source)
    except SyntaxError, ValueError:
        return source
    for node in ast.walk(tree):
        body: object = getattr(node, "body", None)
        if isinstance(body, list) and body and _is_bare_string(body[0]):
            body.pop(0)
        if isinstance(node, (ast.Module, ast.ClassDef)):
            node.body = [stmt for stmt in node.body if not _is_bare_string(stmt)]
    return ast.unparse(tree)


def unmoved(lines: list[str], moved: Counter[str]) -> list[str]:
    """Return the lines whose text did not only move between files: each moved text cancels one line."""
    left: Counter[str] = Counter(line.strip() for line in lines) - moved
    kept: list[str] = []
    for line in lines:
        if left[line.strip()] > 0:
            left[line.strip()] -= 1
            kept.append(line)
    return kept


def check_signal(change: Change) -> Signal:
    """Return the signal of a changed workflow, hook config, script that a hook runs or path that the owner guards."""
    if change.status == "A":
        what: str = "added"
    elif change.status == "D":
        what = "deleted"
    elif change.old != change.new:
        what = f"renamed from {change.old}"
    else:
        what = f"{len(change.added)} lines added, {len(change.removed)} removed"
    if change.owned:
        return Signal(change.new, "check_change", f"a path that the owner guards: {what}; {OWNER_ADVICE}")
    return Signal(change.new, "check_change", f"a workflow, the hook config or a script that a hook runs: {what}")


def _count_lines(text: str, pattern: re.Pattern[str]) -> int:
    """Return how many lines of the text the pattern finds a match in."""
    return sum(1 for line in text.splitlines() if pattern.search(line))


def config_signals(change: Change, before: str, after: str, added: list[str], removed: list[str]) -> list[Signal]:
    """Return the signals of a changed test configuration or check file."""
    signals: list[Signal] = [check_signal(change)] if change.kind == "check" else []
    suffix: str = PurePosixPath(change.new).suffix
    keys: set[tuple[str, str]] = set()
    for lines, changed in ((before.splitlines(), removed), (after.splitlines(), added)):
        for index in line_indexes(lines, changed):
            section, key = config_key(lines, index, suffix)
            if watched(suffix, section, key, change.new):
                keys.add((section, key))
    for section, key in sorted(keys):
        signals.append(Signal(change.new, "test_config", f"changed `{key}`" + (f" in [{section}]" if section else "")))
    code_before, code_after = (code_only(text) if suffix == ".py" else text for text in (before, after))
    for word in sorted(set(RUNNER.findall(code_before))):
        named: re.Pattern[str] = re.compile(rf"\b{re.escape(word)}\b")
        was: int = _count_lines(code_before, named)
        now: int = _count_lines(code_after, named)
        if now < was:
            signals.append(Signal(change.new, "removed_check", f"lines naming `{word}`: {was} -> {now}"))
    was_hooks: int = _count_lines(before, HOOK_ID)
    now_hooks: int = _count_lines(after, HOOK_ID)
    if now_hooks < was_hooks:
        signals.append(Signal(change.new, "removed_check", f"hook entries: {was_hooks} -> {now_hooks}"))
    return signals


def line_signals(change: Change, added: list[str]) -> list[Signal]:
    """Return the signals of the added lines of a file: skips, retries, ignored failures and narrowing options."""
    signals: list[Signal] = []
    retry: re.Pattern[str] = RETRY_TEST if change.kind == "test" else RETRY_CONFIG
    for line in added:
        quoted: str = f"added `{line.strip()[:QUOTE_LIMIT]}`"
        if SKIP.search(line):
            signals.append(Signal(change.new, "skip", quoted))
        if retry.search(line):
            signals.append(Signal(change.new, "retry", quoted))
        if change.kind != "test" and IGNORE_FAILURE.search(line):
            signals.append(Signal(change.new, "ignore_failure", quoted))
        if change.kind != "test" and NARROW.search(line):
            signals.append(Signal(change.new, "test_config", f"narrowing option in `{line.strip()[:QUOTE_LIMIT]}`"))
    return signals


def expectation_signals(change: Change, added: list[str], removed: list[str], code_text: str) -> list[Signal]:
    """Return the signals of the changed lines of a test file: changed expected values and expected data."""
    signals: list[Signal] = []
    lang: str | None = LANG.get(PurePosixPath(change.new).suffix)
    if lang in ASSERT_LINE:
        pattern: re.Pattern[str] = ASSERT_LINE[lang]
        pairs: list[tuple[str, str]] = changed_expectations(
            [line.strip() for line in removed if pattern.search(line)],
            [line.strip() for line in added if pattern.search(line)],
        )
        for before, after in pairs:
            reason: str = f"`{before[:ASSERT_LIMIT]}` -> `{after[:ASSERT_LIMIT]}`, {follows(before, after, code_text)}"
            signals.append(Signal(change.new, "changed_expectation", reason))
    if not lang and change.status == "M" and removed and EXPECTED_DATA.search(change.new):
        signals.append(Signal(change.new, "changed_expectation", f"expected data: {len(removed)} lines replaced"))
    if not lang and change.status == "D" and EXPECTED_DATA.search(change.old):
        signals.append(Signal(change.old, "deleted_test", "expected data file deleted"))
    return signals


def pair_signals(
    old_tests: dict[tuple[str, str], TestBody], new_tests: dict[tuple[str, str], TestBody], code_text: str
) -> list[Signal]:
    """Return the signals of deleted tests, fewer assertions and changed Python expectations."""
    signals: list[Signal] = []
    gone: list[TestBody] = [test for key, test in old_tests.items() if key not in new_tests]
    came: list[TestBody] = [test for key, test in new_tests.items() if key not in old_tests]
    moved: list[tuple[TestBody, TestBody]] = []
    unpaired: list[TestBody] = []
    for test in gone:
        short: str = test.name.split(".")[-1]
        twin: TestBody | None = next(
            (other for other in came if other.name.split(".")[-1] == short or (other.body and other.body == test.body)),
            None,
        )
        if twin is None:
            unpaired.append(test)
        else:
            came.remove(twin)
            moved.append((test, twin))
    for test in unpaired:
        ratio, closest = max(
            ((similar(test.body, other.body), other) for other in came), default=(0.0, None), key=lambda pair: pair[0]
        )
        if closest is None or ratio < SIMILAR:
            gone_reason: str = f"`{test.name}` removed, closest new test body {ratio:.2f}"
            signals.append(Signal(test.path, "deleted_test", gone_reason))
        else:
            came.remove(closest)
            moved.append((test, closest))
    kept: list[tuple[TestBody, TestBody]] = [
        (test, new_tests[key]) for key, test in old_tests.items() if key in new_tests
    ] + moved
    for before, after in kept:
        if len(after.asserts) < len(before.asserts):
            reason: str = f"`{after.name}`: {len(before.asserts)} -> {len(after.asserts)} assertions"
            signals.append(Signal(after.path, "fewer_assertions", reason))
        if LANG.get(PurePosixPath(after.path).suffix) == "py":
            for old_line, new_line in changed_expectations(before.asserts, after.asserts):
                reason = (
                    f"`{after.name}`: `{old_line[:ASSERT_LIMIT]}` -> `{new_line[:ASSERT_LIMIT]}`, "
                    f"{follows(old_line, new_line, code_text)}"
                )
                signals.append(Signal(after.path, "changed_expectation", reason))
    return signals


def analyse(base: str, head: str, tests: tuple[str, ...], owner: tuple[str, ...]) -> list[Signal]:
    """Return the signals of the range from base to head."""
    found, code_added = changes(base, head, tests, owner)
    code_text: str = "\n".join(code_added)
    specs: list[str] = [f"{base}:{change.old}" for change in found if change.status != "A"]
    specs += [f"{head}:{change.new}" for change in found if change.status != "D"]
    content: dict[str, str] = blobs(specs)
    gone_all: Counter[str] = Counter(line.strip() for change in found for line in change.removed if line.strip())
    came_all: Counter[str] = Counter(line.strip() for change in found for line in change.added if line.strip())
    moved: Counter[str] = gone_all & came_all
    signals: list[Signal] = []
    old_tests: dict[tuple[str, str], TestBody] = {}
    new_tests: dict[tuple[str, str], TestBody] = {}
    for change in found:
        if change.owned and change.kind != "check":
            signals.append(check_signal(change))
        if not change.kind:
            continue
        before: str = content.get(f"{base}:{change.old}", "")
        after: str = content.get(f"{head}:{change.new}", "")
        added: list[str] = unmoved(change.added, moved)
        removed: list[str] = unmoved(change.removed, moved)
        signals += line_signals(change, added)
        if change.kind != "test":
            signals += config_signals(change, before, after, added, removed)
            continue
        old_list: list[TestBody] = tests_of(change.old, before) if change.status != "A" else []
        new_list: list[TestBody] = tests_of(change.new, after) if change.status != "D" else []
        old_tests.update({(test.path, test.name): test for test in old_list})
        new_tests.update({(test.path, test.name): test for test in new_list})
        signals += expectation_signals(change, added, removed, code_text)
    return signals + pair_signals(old_tests, new_tests, code_text)


def _count(number: int, noun: str) -> str:
    """Return a number with its noun in the singular or plural."""
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def report(signals: list[Signal], base: str, head_ref: str) -> tuple[list[str], list[str]]:
    """Return the text and Markdown report of the signals, grouped by file and kind."""
    short: str = base[:SHORT_SHA]
    if not signals:
        line: str = f"{TITLE}: no signal from {short} to {head_ref}"
        return [line], [line]
    files: list[str] = sorted({signal.path for signal in signals})
    counts: str = f"{_count(len(signals), 'signal')} in {_count(len(files), 'file')}"
    head: str = f"{TITLE}: {counts} from {short} to {head_ref}; {ADVICE}"
    text: list[str] = [head]
    markdown: list[str] = [f"### {TITLE.capitalize()}", "", f"{head}.", ""]
    for path in files:
        text.append(path)
        markdown += [f"#### `{path}`", ""]
        for signal in sorted((signal for signal in signals if signal.path == path), key=lambda signal: signal.kind):
            text.append(f"  {signal.kind}: {signal.reason}")
            markdown.append(f"- **{signal.kind}**: {signal.reason}")
        markdown.append("")
    return text, markdown


def emit(text: list[str], markdown: list[str]) -> None:
    """Print the text report and append the Markdown report to the job summary when there is one."""
    print("\n".join(text))
    summary: str = os.environ.get(SUMMARY_VARIABLE, "")
    if summary:
        with open(summary, "a") as handle:
            handle.write("\n".join(markdown) + "\n")


def main(argv: list[str] | None = None) -> int:
    """Print the signals from PRE_COMMIT_FROM_REF to PRE_COMMIT_TO_REF or HEAD; return 0 unless arguments are wrong."""
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Print the signals of weakened tests and checks; never fail on a signal.",
        epilog="manual hook: prek run --hook-stage manual --from-ref <base> --to-ref HEAD",
    )
    parser.add_argument("--tests", default="", help="comma-separated globs of test files beyond the conventions")
    parser.add_argument(
        "--owner-paths", default="", help="comma-separated globs of files that change only on the owner's word"
    )
    args: argparse.Namespace = parser.parse_args(argv)
    tests: tuple[str, ...] = tuple(glob for glob in str(args.tests).split(",") if glob)
    owner: tuple[str, ...] = tuple(glob for glob in str(args.owner_paths).split(",") if glob)
    base_ref: str = os.environ.get(BASE_VARIABLE, "")
    if not base_ref:
        line: str = f"{TITLE}: no base, {BASE_VARIABLE} is unset; run prek with --from-ref <base> --to-ref HEAD"
        emit([line], [line])
        return 0
    head_ref: str = os.environ.get(HEAD_VARIABLE, "") or HEAD
    try:
        head: str = git("rev-parse", "--verify", f"{head_ref}^{{commit}}").strip()
        base: str = git("merge-base", base_ref, head).strip()
        signals: list[Signal] = analyse(base, head, tests, owner)
    except subprocess.CalledProcessError as error:
        detail: object = error.stderr
        reason: str = detail.decode("utf-8", "replace") if isinstance(detail, bytes) else str(detail)
        line = (
            f"{TITLE}: cannot read the range from {base_ref} to {head_ref}: {reason.strip()}; "
            "fetch the base branch with its full history (git fetch --unshallow in a shallow clone) and run again"
        )
        emit([line], [line])
        return 0
    emit(*report(signals, base, head_ref))
    return 0


if __name__ == "__main__":
    sys.exit(main())
