"""Ratchet on issue references inside Python docstrings (photo-ocr#266).

`scripts/comment_ratchet.py` ratchets `#NNN` references in comments but
explicitly treats a docstring as "not a comment" (its own module docstring,
line 15), so a docstring that tells the incident story -- issue numbers and
all -- sits in exactly that blind spot. This stage closes it: it counts
`#NNN` references inside module, class and function/method docstrings only
(never a plain string literal, never a comment -- `comment_ratchet` already
owns those), per file, compares them with a committed baseline, and fails
only when a file's count *rises*. Existing debt never blocks work, but it
cannot grow.

A reference is `#` plus one to five digits, not preceded by `&` or `#` and
ending on a word boundary (same pattern as `comment_ratchet`); a URL inside
the docstring is blanked first so an anchor never counts.

Repo-local, not vendored: the gap lives in the vendored `comment_ratchet.py`
itself, so a real fix belongs upstream in project-scaffolding; this script
is the stand-in for photo-ocr until that lands.

Opt-in via `.fleet.toml`:

    [docstring_ratchet]
    enabled  = true
    baseline = ".docstring-ratchet-baseline.json"   # default
    include  = ["app/", "src/", "scripts/"]          # path prefixes; default: whole tree
    exclude  = ["**/generated/**"]                   # globs, added to the built-in set

Scope: tracked `.py` files (`git ls-files --cached --others --exclude-standard`)
under `include`, minus tests (`tests/`, `test_*.py`, `*_test.py`, `conftest.py`),
files whose first lines say `DO NOT EDIT` or `@generated`, and every `dest` in
the adopter's own `[vendored]` table (a vendored docstring is someone else's
problem to fix, re-vendored verbatim).

Exit codes (a state is never folded into a pass):

  0  pass, or the stage is not enabled (declared, not inferred)
  1  a file's count rose, a new file carries references, or the baseline is
     missing / a mode was refused
  2  bad configuration, baseline or arguments -- fail loud
  3  NOT CONFIRMED: `git` unusable, or a file could not be read, decoded or
     parsed. The gate fails on it.

Modes: `check` (default; what the gate runs), `--init` (write the first
baseline, refuses to overwrite), `--update-baseline` (lower or drop entries;
exits 1 and writes nothing while any count rose), `--update-baseline
--allow-additions` (the explicit flag that raises or adds -- shows in review).
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parent.parent

BASELINE_VERSION = 1
DEFAULT_BASELINE = ".docstring-ratchet-baseline.json"
BUILTIN_EXCLUDES = (
    "**/tests/**",
    "**/test_*.py",
    "**/*_test.py",
    "**/conftest.py",
    "**/.venv/**",
    "**/venv/**",
    "**/__pycache__/**",
)
GENERATED_MARKERS = ("DO NOT EDIT", "@generated")
GENERATED_HEAD_LINES = 5
GIT_TIMEOUT_S = 120

EXIT_PASS, EXIT_RISE, EXIT_CONFIG, EXIT_NOT_CONFIRMED = 0, 1, 2, 3

# Not vendored, so this file derives the flag locally rather than importing a
# shared helper -- same documented exception comment_ratchet.py takes.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

_CONFIG_KEYS = {"enabled", "baseline", "include", "exclude"}
_REF = re.compile(r"(?<![&#])#(\d{1,5})\b")
_URL = re.compile(r"https?://\S+")


class ConfigError(RuntimeError):
    """The `[docstring_ratchet]` table or the baseline file is unusable."""


class NotConfirmed(RuntimeError):
    """A file could not be read, decoded or parsed, so its count is unknown."""


@dataclass(frozen=True)
class Config:
    baseline: str
    include: tuple[str, ...]
    exclude: tuple[str, ...]


@dataclass(frozen=True)
class Ref:
    line: int
    text: str


# ----------------------------------------------------------------- config

def _str_list(table: dict[str, object], name: str) -> tuple[str, ...]:
    value = table.get(name, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ConfigError(f"[docstring_ratchet] {name} must be a list of non-empty strings")
    return tuple(value)


def parse_config(toml_text: str) -> Config | None:
    table = tomllib.loads(toml_text).get("docstring_ratchet")
    if table is None:
        return None
    if not isinstance(table, dict):
        raise ConfigError("[docstring_ratchet] must be a table")
    unknown = sorted(set(table) - _CONFIG_KEYS)
    if unknown:
        raise ConfigError(f"[docstring_ratchet]: unknown key(s) {', '.join(unknown)}")
    enabled = table.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError("[docstring_ratchet] enabled must be true or false")
    if not enabled:
        return None
    baseline = table.get("baseline", DEFAULT_BASELINE)
    if not isinstance(baseline, str) or not baseline:
        raise ConfigError("[docstring_ratchet] baseline must be a non-empty string")
    return Config(baseline, _str_list(table, "include"), _str_list(table, "exclude"))


def vendored_dests(toml_text: str) -> tuple[str, ...]:
    table = tomllib.loads(toml_text).get("vendored")
    if not isinstance(table, dict):
        return ()
    dests = []
    for entry in table.values():
        dest = entry.get("dest") if isinstance(entry, dict) else None
        if isinstance(dest, str) and dest.strip("/"):
            dests.append(dest.replace("\\", "/").strip("/"))
    return tuple(sorted(dests))


def load_config(root: Path) -> tuple[Config | None, tuple[str, ...]]:
    path = root / ".fleet.toml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    try:
        return parse_config(text), vendored_dests(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc


# ------------------------------------------------------------------- globs

def _glob_regex(glob: str) -> re.Pattern[str]:
    out: list[str] = []
    i = 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def is_excluded(path: str, globs: tuple[str, ...]) -> bool:
    return any(_glob_regex(g).match(path) for g in globs)


def _under(path: str, prefix: str) -> bool:
    prefix = prefix.replace("\\", "/").strip("/")
    return path == prefix or path.startswith(prefix + "/")


def in_scope(path: str, cfg: Config, vendored: tuple[str, ...]) -> bool:
    if PurePosixPath(path).suffix.lower() != ".py":
        return False
    if cfg.include and not any(_under(path, p) for p in cfg.include):
        return False
    if any(_under(path, d) for d in vendored):
        return False
    return not is_excluded(path, BUILTIN_EXCLUDES + cfg.exclude)


def is_generated(text: str) -> bool:
    head = "\n".join(text.split("\n", GENERATED_HEAD_LINES)[:GENERATED_HEAD_LINES])
    return any(marker in head for marker in GENERATED_MARKERS)


# ---------------------------------------------------------------- counting

def find_refs(doc: str, first_line: int) -> list[Ref]:
    """`#NNN` references in one docstring's text; `first_line` is its first line."""
    blanked = _URL.sub(lambda m: " " * len(m.group()), doc)
    lines = doc.split("\n")
    refs = []
    for match in _REF.finditer(blanked):
        offset = blanked.count("\n", 0, match.start())
        refs.append(Ref(first_line + offset, f"#{match.group(1)}  | {lines[offset].strip()}"))
    return refs


def docstrings_py(text: str, path: str) -> list[tuple[int, str]]:
    """Every module/class/function docstring's (first line, raw text)."""
    try:
        tree = ast.parse(text, filename=path)
    except SyntaxError as exc:
        raise NotConfirmed(f"python parser rejected the file: {exc}") from exc
    nodes: list[ast.AST] = [tree]
    nodes.extend(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    )
    out = []
    for node in nodes:
        doc = ast.get_docstring(node, clean=False)
        if not doc:
            continue
        # The docstring Expr is always node.body[0] when get_docstring found one.
        line = node.body[0].lineno
        out.append((line, doc))
    return out


def scan_text(text: str, path: str) -> list[Ref]:
    text = text.replace("\r\n", "\n")
    refs: list[Ref] = []
    for first_line, doc in docstrings_py(text, path):
        refs.extend(find_refs(doc, first_line))
    return refs


# ------------------------------------------------------------------- files

def tracked_files(root: Path) -> list[str]:
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")  # a read-only sweep must not leave index locks
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root, capture_output=True, env=env,
            stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW, timeout=GIT_TIMEOUT_S, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise NotConfirmed(f"git ls-files timed out after {GIT_TIMEOUT_S}s") from exc
    except OSError as exc:
        raise NotConfirmed(f"git could not start: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise NotConfirmed(f"git ls-files exited {proc.returncode}: {detail[-1] if detail else '(no output)'}")
    return sorted(p for p in proc.stdout.decode("utf-8", "replace").split("\0") if p)


def count_tree(root: Path, cfg: Config, vendored: tuple[str, ...]) -> dict[str, list[Ref]]:
    found: dict[str, list[Ref]] = {}
    for path in tracked_files(root):
        if not in_scope(path, cfg, vendored):
            continue
        try:
            raw = (root / path).read_bytes()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise NotConfirmed(f"{path}: cannot be read: {exc}") from exc
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise NotConfirmed(f"{path}: is not valid UTF-8: {exc}") from exc
        if is_generated(text):
            continue
        try:
            refs = scan_text(text, path)
        except NotConfirmed as exc:
            raise NotConfirmed(f"{path}: {exc}") from exc
        if refs:
            found[path] = refs
    return found


# ---------------------------------------------------------------- baseline

def read_baseline(path: Path) -> dict[str, int]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read baseline {path}: {exc}") from exc
    except ValueError as exc:
        raise ConfigError(f"baseline {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != BASELINE_VERSION:
        raise ConfigError(f"baseline {path}: expected an object with version {BASELINE_VERSION}")
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise ConfigError(f"baseline {path}: `entries` must be a list")
    out: dict[str, int] = {}
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("key"), str)
            or isinstance(entry.get("count"), bool)
            or not isinstance(entry.get("count"), int)
            or entry["count"] < 1
        ):
            raise ConfigError(f"baseline {path}: malformed entry {entry!r}")
        out[entry["key"]] = entry["count"]
    return out


def render_baseline(counts: dict[str, int]) -> str:
    entries = [
        "    " + json.dumps({"key": key, "count": counts[key]}, ensure_ascii=False)
        for key in sorted(counts)
    ]
    body = "[\n" + ",\n".join(entries) + "\n  ]" if entries else "[]"
    return f'{{\n  "version": {BASELINE_VERSION},\n  "entries": {body}\n}}\n'


def diff(current: dict[str, int], baseline: dict[str, int]) -> tuple[list[str], list[str]]:
    rose = sorted(p for p, c in current.items() if c > baseline.get(p, 0))
    shrank = sorted(p for p, b in baseline.items() if current.get(p, 0) < b)
    return rose, shrank


# -------------------------------------------------------------------- CLI

def _report_rose(rose: list[str], current: dict[str, list[Ref]], baseline: dict[str, int]) -> None:
    print(f"docstring ratchet: {len(rose)} file(s) with MORE #NNN docstring references than the baseline:")
    for path in rose:
        was = baseline.get(path)
        print(f"  {path}: {len(current[path])} (baseline {was if was is not None else 'none -- not baselined'})")
        for ref in current[path]:
            print(f"      line {ref.line}: {ref.text}")


def run(root: Path, *, init: bool, update: bool, allow_additions: bool) -> int:
    try:
        cfg, vendored = load_config(root)
    except ConfigError as exc:
        print(f"docstring ratchet: CONFIG ERROR: {exc}")
        return EXIT_CONFIG
    if cfg is None:
        if init or update:
            print("docstring ratchet: not enabled -- set [docstring_ratchet] enabled = true in .fleet.toml first")
            return EXIT_CONFIG
        print("docstring ratchet: not enabled (no [docstring_ratchet] enabled = true in .fleet.toml)")
        return EXIT_PASS
    baseline_path = root / cfg.baseline

    baseline: dict[str, int] = {}
    if init:
        if baseline_path.exists():
            print(f"docstring ratchet: {cfg.baseline} already exists; --init never overwrites it")
            return EXIT_CONFIG
    elif baseline_path.is_file():
        try:
            baseline = read_baseline(baseline_path)
        except ConfigError as exc:
            print(f"docstring ratchet: BASELINE ERROR: {exc}")
            return EXIT_CONFIG
    else:
        print(f"docstring ratchet: FAIL: baseline {cfg.baseline} is missing -- run `python scripts/docstring_ratchet.py --init`")
        return EXIT_RISE

    try:
        refs = count_tree(root, cfg, vendored)
    except NotConfirmed as exc:
        print(f"docstring ratchet: NOT CONFIRMED: {exc}")
        print("  (the count could not be established, so it is neither a pass nor a clean tree)")
        return EXIT_NOT_CONFIRMED
    current = {path: len(found) for path, found in refs.items()}

    if init:
        baseline_path.write_text(render_baseline(current), encoding="utf-8", newline="\n")
        print(f"docstring ratchet: wrote {cfg.baseline} with {len(current)} file(s), {sum(current.values())} reference(s)")
        return EXIT_PASS

    rose, shrank = diff(current, baseline)
    if update:
        if rose and not allow_additions:
            _report_rose(rose, refs, baseline)
            print("docstring ratchet: nothing written -- remove the references, or raise the baseline with --allow-additions")
            return EXIT_RISE
        baseline_path.write_text(render_baseline(current), encoding="utf-8", newline="\n")
        print(f"docstring ratchet: {cfg.baseline} now has {len(current)} file(s), {sum(current.values())} reference(s) "
              f"({len(shrank)} lowered or dropped, {len(rose)} raised or added)")
        return EXIT_PASS

    if rose:
        _report_rose(rose, refs, baseline)
        print("  A docstring states the present-tense why; how the code got here belongs in the issue, PR and "
              "git log. Reword it, or (reviewed) raise the baseline with `--update-baseline --allow-additions`.")
        return EXIT_RISE
    print(f"docstring ratchet: PASS ({sum(current.values())} reference(s) in {len(current)} file(s), none above the baseline)")
    if shrank:
        print(f"  {len(shrank)} file(s) are below the baseline -- run "
              f"`python scripts/docstring_ratchet.py --update-baseline` to lower it")
    return EXIT_PASS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="repo root (default: this repo)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--init", action="store_true", help="write the first baseline; never overwrites")
    mode.add_argument("--update-baseline", action="store_true", help="lower or drop entries (shrink only)")
    parser.add_argument("--allow-additions", action="store_true",
                        help="with --update-baseline: also raise or add entries (shows in review)")
    args = parser.parse_args(argv)
    if args.allow_additions and not args.update_baseline:
        parser.error("--allow-additions only applies with --update-baseline")
    return run(args.root.resolve(), init=args.init, update=args.update_baseline,
               allow_additions=args.allow_additions)


if __name__ == "__main__":
    sys.exit(main())
