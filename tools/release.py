#!/usr/bin/env python3
"""Stamp a release from the commits merged since the last one.

Commit messages follow Conventional Commits and carry no version numbers. After
every merge to main, CI reads the commits since the last release and stamps the
next version: a fix is a patch, a feature is a minor, and a breaking change is a
major (a minor while the version is 0.x). So the order things merge in does not
matter, and nobody writes a version by hand.

  release.sh                   what the next release would be; changes nothing
  release.sh --yes             stamp plugin.json and CHANGELOG.md, commit, tag
  release.sh --yes --push      the same, then push the commit and both tags
  release.sh lint [RANGE]      check the commit messages in RANGE (default: the
                               commits since the last release); --warn reports
                               without failing
  release.sh lint --message-file FILE
                               check one message (the commit-msg hook)
  release.sh notes VERSION     the CHANGELOG section for VERSION

Exit codes: 0 done (or nothing to release), 1 a check failed or git refused, 2
usage.
"""
from __future__ import annotations

import datetime
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN_JSON = ROOT / "plugins" / "acqai" / ".claude-plugin" / "plugin.json"
CHANGELOG = ROOT / "CHANGELOG.md"
NAME = "acqai"
# The commit CI makes to stamp a release. It names no version: the tag does.
STAMP_SUBJECT = "chore(release): stamp the version and changelog"

TYPES = ("feat", "fix", "perf", "refactor", "docs", "test", "ci", "build",
         "chore", "style", "revert")
HEADER = re.compile(
    r"^(?P<type>" + "|".join(TYPES) + r")(?:\((?P<scope>[^()\s]+)\))?"
    r"(?P<bang>!)?: (?P<subject>\S.*)$")
BREAKING_FOOTER = re.compile(r"^BREAKING[ -]CHANGE: ", re.M)
# A version number anywhere in a message: 1.2.3, v1.2.3, 1.2.3-rc.1.
VERSION_IN_TEXT = re.compile(r"(?<![\w.])v?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?![\w.])")
SEMVER_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
TRAILER = re.compile(r"^[A-Za-z-]+: \S")


def git(*args: str, check: bool = True) -> str:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if check and out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout


# --- reading commits ------------------------------------------------------------
def last_release() -> "tuple[tuple[int, int, int], str | None]":
    """The newest vX.Y.Z tag HEAD contains, as numbers and its name."""
    best, name = (0, 0, 0), None
    for tag in git("tag", "--merged", "HEAD", "--list", "v*").split():
        m = SEMVER_TAG.match(tag)
        if m:
            version = tuple(int(n) for n in m.groups())
            if version > best or name is None:
                best, name = version, tag
    return best, name


def commits(rev_range: str) -> list:
    """(sha, subject, body) for each commit in the range, oldest first. Merges
    and CI's own stamp commits are left out."""
    raw = git("log", "--reverse", "--no-merges", "--format=%H%x1f%s%x1f%b%x1e",
              rev_range)
    out = []
    for record in raw.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, subject, body = (record.split("\x1f") + ["", ""])[:3]
        if subject.strip() == STAMP_SUBJECT:
            continue
        out.append((sha.strip(), subject.strip(), body.strip()))
    return out


def parse(subject: str, body: str = "") -> "dict | None":
    """The commit's type, scope, subject, and whether it breaks, or None when
    the header is not a Conventional Commit."""
    m = HEADER.match(subject.strip())
    if not m:
        return None
    return {"type": m.group("type"), "scope": m.group("scope") or "",
            "subject": m.group("subject").strip(),
            "breaking": bool(m.group("bang")) or bool(BREAKING_FOOTER.search(body or ""))}


def bump_of(parsed: list) -> "str | None":
    """The largest bump the commits ask for: major, minor, patch, or None."""
    kinds = set()
    for p in parsed:
        if p is None:
            continue
        if p["breaking"]:
            kinds.add("major")
        elif p["type"] == "feat":
            kinds.add("minor")
        elif p["type"] in ("fix", "perf"):
            kinds.add("patch")
    for kind in ("major", "minor", "patch"):
        if kind in kinds:
            return kind
    return None


def next_version(current: tuple, bump: str) -> tuple:
    major, minor, patch = current
    if bump == "major" and major == 0:
        bump = "minor"  # 0.x: a breaking change moves the minor; 1.0 is chosen
    if bump == "major":
        return (major + 1, 0, 0)
    if bump == "minor":
        return (major, minor + 1, 0)
    return (major, minor, patch + 1)


def _range_since(tag: "str | None") -> str:
    return f"{tag}..HEAD" if tag else "HEAD"


# --- checking messages ----------------------------------------------------------
def message_faults(subject: str, body: str = "") -> list:
    """Why a commit message breaks the convention, or []."""
    faults = []
    if subject.strip() == STAMP_SUBJECT:
        return faults
    if parse(subject, body) is None:
        faults.append("the header is not type(scope)?!?: subject, with type one of "
                      + ", ".join(TYPES))
    for text in (subject, body):
        hit = VERSION_IN_TEXT.search(text or "")
        if hit:
            faults.append(f"it names a version ({hit.group(0)}); CI stamps versions")
            break
    return faults


def _message_file(path: str) -> "tuple[str, str]":
    text = pathlib.Path(path).read_text(encoding="utf-8")
    lines = [l for l in text.splitlines() if not l.startswith("#")]
    subject = lines[0].strip() if lines else ""
    return subject, "\n".join(lines[1:]).strip()


def cmd_lint(args: list) -> int:
    warn = "--warn" in args
    args = [a for a in args if a != "--warn"]
    if args[:1] == ["--message-file"]:
        if len(args) < 2:
            print("release: --message-file needs a path", file=sys.stderr)
            return 2
        subject, body = _message_file(args[1])
        faults = message_faults(subject, body)
        for fault in faults:
            print(f"commit message: {fault}", file=sys.stderr)
        if faults:
            print("  e.g. 'fix: wait for the chat page before a send' "
                  "or 'feat: record what came of an answer'", file=sys.stderr)
        return 1 if faults else 0
    rev_range = args[0] if args else _range_since(last_release()[1])
    bad = 0
    for sha, subject, body in commits(rev_range):
        for fault in message_faults(subject, body):
            bad += 1
            prefix = "::warning::" if warn and os.environ.get("GITHUB_ACTIONS") else ""
            print(f"{prefix}{sha[:7]} {subject!r}: {fault}")
    if not bad:
        print(f"commit messages ok ({rev_range})")
        return 0
    return 0 if warn else 1


# --- stamping -------------------------------------------------------------------
def _first_paragraph(body: str) -> str:
    para = (body or "").strip().split("\n\n", 1)[0].strip()
    if not para or TRAILER.match(para):
        return ""
    return " ".join(para.split())


def section(version: tuple, entries: list, today: "str | None" = None) -> str:
    """The CHANGELOG section for a release, grouped the way readers look."""
    today = today or datetime.date.today().isoformat()
    groups = {"Breaking": [], "Added": [], "Fixed": []}
    for sha, subject, body in entries:
        p = parse(subject, body)
        if p is None:
            continue
        if p["breaking"]:
            group = "Breaking"
        elif p["type"] == "feat":
            group = "Added"
        elif p["type"] in ("fix", "perf"):
            group = "Fixed"
        else:
            continue
        text = p["subject"][:1].upper() + p["subject"][1:]
        line = f"- {text} ({sha[:7]})"
        detail = _first_paragraph(body)
        if detail:
            line += f"\n  {detail}"
        groups[group].append(line)
    out = [f"## [{'.'.join(map(str, version))}] - {today}", ""]
    for group, lines in groups.items():
        if lines:
            out += [f"### {group}", *lines, ""]
    return "\n".join(out).rstrip() + "\n"


def stamp(version: tuple, entries: list) -> None:
    """Set plugin.json's version and put the new section on the CHANGELOG."""
    v = ".".join(map(str, version))
    text = PLUGIN_JSON.read_text(encoding="utf-8")
    new, n = re.subn(r'("version"\s*:\s*")[^"]*(")', rf"\g<1>{v}\g<2>", text, count=1)
    if n != 1:
        raise RuntimeError(f"no version field in {PLUGIN_JSON}")
    PLUGIN_JSON.write_text(new, encoding="utf-8")
    log = CHANGELOG.read_text(encoding="utf-8")
    m = re.search(r"^## \[", log, re.M)
    at = m.start() if m else len(log)
    CHANGELOG.write_text(log[:at] + section(version, entries) + "\n" + log[at:],
                         encoding="utf-8")


def cmd_release(args: list) -> int:
    yes, push = "--yes" in args, "--push" in args
    current, tag = last_release()
    entries = commits(_range_since(tag))
    parsed = [parse(s, b) for _, s, b in entries]
    for (sha, subject, _), p in zip(entries, parsed):
        if p is None:
            print(f"release: {sha[:7]} {subject!r} is not a Conventional Commit; "
                  "it releases nothing")
    bump = bump_of(parsed)
    since = tag or "the first commit"
    if bump is None:
        print(f"release: nothing to release since {since}")
        return 0
    version = next_version(current, bump)
    v = ".".join(map(str, version))
    tags = [f"v{v}", f"{NAME}--v{v}"]
    if not yes:
        print(f"release: a {bump} since {since}, so {v} ({', '.join(tags)})")
        print(section(version, entries).rstrip())
        print("\nNothing changed. Re-run with --yes to stamp, commit, and tag.")
        return 0
    if git("status", "--porcelain").strip():
        print("release: the working tree is dirty; commit or stash first",
              file=sys.stderr)
        return 1
    stamp(version, entries)
    git("add", "--", str(PLUGIN_JSON), str(CHANGELOG))
    git("commit", "-q", "-m", STAMP_SUBJECT)
    for name in tags:
        git("tag", "-a", name, "-m", f"{NAME} {v}")
    print(f"release: stamped {v} and tagged {', '.join(tags)}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"version={v}\n")
    if push:
        git("push", "origin", "HEAD:refs/heads/main")
        git("push", "origin", *(f"refs/tags/{t}" for t in tags))
        print("release: pushed the stamp commit and both tags")
    return 0


def cmd_notes(args: list) -> int:
    if not args:
        print("release: notes needs a version", file=sys.stderr)
        return 2
    text = CHANGELOG.read_text(encoding="utf-8")
    m = re.search(rf"^## \[{re.escape(args[0])}\][^\n]*\n(.*?)(?=^## |\Z)",
                  text, re.M | re.S)
    print((m.group(1).strip() if m else "") or f"{NAME} {args[0]}")
    return 0


def main(argv: list) -> int:
    if argv[:1] in (["-h"], ["--help"], ["?"]):
        print(__doc__.strip())
        return 0
    try:
        if argv[:1] == ["lint"]:
            return cmd_lint(argv[1:])
        if argv[:1] == ["notes"]:
            return cmd_notes(argv[1:])
        unknown = [a for a in argv if a not in ("--yes", "-y", "--push")]
        if unknown:
            print(f"release: unknown argument {unknown[0]!r} (try -h)", file=sys.stderr)
            return 2
        return cmd_release(["--yes" if a == "-y" else a for a in argv])
    except RuntimeError as err:
        print(f"release: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
