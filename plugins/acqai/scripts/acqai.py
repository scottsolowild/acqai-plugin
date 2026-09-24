#!/usr/bin/env python3
"""ACQ AI (Mozi) from a script: sign in once, then ask it questions with your
own docs as the context, one paced question at a time, each one on your yes.

  acqai.py setup [--company NAME]     install Playwright and its Chromium into a
                                      private venv, and save the company to pick
                                      on ACQ AI's sign-in list
  acqai.py login [--company NAME]     open a browser and sign into ACQ AI once
                                      (portal.acquisition.com/advisor); one
                                      message sent there teaches the route
  acqai.py login-legacy [--company N] same, for the older ai.acquisition.com/chat
  acqai.py probe [--session]          what is set up: venv, route, chat, consent
                                      (--session also opens the profile headless
                                      and says whether it is still signed in)
  acqai.py send "question" [-y]       one question, one answer (asks y/N first)
  acqai.py send --file PATH [-y]      the question from a file (- for stdin)
  acqai.py send ... --dry-run         show what would go out, send nothing
  acqai.py send ... --new             start a fresh conversation
  acqai.py send ... --http            replay MOZI_TOKEN instead of the browser
  acqai.py discover --from-curl FILE  learn the route by hand from a Copy-as-cURL
                                      (- for stdin), when login did not catch it
  acqai.py answers [N]                the last N answers on file (default 10)

Everything it keeps lives in ~/.config/acqai (override with ACQAI_STATE_DIR):
the browser profile with your login, the learned route, the current chat id,
the venv, and answers/ with one file per send. It reaches ACQ AI and nothing
else, and it never sends without your yes: y at the prompt, -y on the
command, or MOZI_SEND_OK=1 in the environment.

Exit codes: 0 done, 1 the send or the browser failed (the message says what to
do), 2 usage, 3 no yes was given and none could be asked for.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = pathlib.Path(__file__).resolve()
STATE_DIR = pathlib.Path(
    os.environ.get("ACQAI_STATE_DIR", "").strip() or "~/.config/acqai").expanduser()
VENV = STATE_DIR / "venv"
ANSWERS = STATE_DIR / "answers"
SELF = "python3 " + shlex.quote(str(SCRIPT))

# The transport pair reads these when it is imported, so they go first.
os.environ["ACQAI_STATE_DIR"] = str(STATE_DIR)
os.environ.setdefault("ACQAI_CMD", SELF)
os.environ.setdefault("ACQAI_SETUP_CMD", SELF + " setup")
sys.path.insert(0, str(HERE))
import mozilib  # noqa: E402

CONSENT_PROMPT = "This reaches your paid ACQ AI account. Continue? [y/N] "


# --- the venv that holds Playwright ------------------------------------------
def _venv_python() -> pathlib.Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def _playwright_here() -> bool:
    return importlib.util.find_spec("playwright") is not None


def _venv_has_playwright() -> bool:
    py = _venv_python()
    if not py.is_file():
        return False
    try:
        return subprocess.run(
            [str(py), "-c", "import playwright"],
            capture_output=True, text=True).returncode == 0
    except OSError:
        return False


def _under_venv(argv: list, stdin_text: "str | None" = None) -> "int | None":
    """A command that drives the browser runs under the venv setup made. When
    this interpreter lacks Playwright and the venv exists, run there and hand
    back its exit code. None means stay here. stdin_text, when given, becomes
    the child's stdin: text this process already read from its own, which the
    child, running the same argv, would otherwise find empty."""
    py = _venv_python()
    # ACQAI_IN_VENV marks the child started below, so a venv python that still
    # fails the prefix check (its pyvenv.cfg gone, say) stops after one hop
    # instead of starting itself again forever.
    if _playwright_here() or not py.is_file() or os.environ.get("ACQAI_IN_VENV"):
        return None
    # Inside the venv, sys.prefix is the venv. The resolved executables can't
    # tell: venv/bin/python is a symlink chain to the python3 that built it, so
    # with Homebrew Python both sides resolve to the same binary.
    try:
        if pathlib.Path(sys.prefix).resolve() == VENV.resolve():
            return None
    except OSError:
        return None
    env = dict(os.environ, ACQAI_IN_VENV="1")
    cmd = [str(py), str(SCRIPT), *argv]
    if stdin_text is None:
        return subprocess.call(cmd, env=env)
    return subprocess.run(cmd, env=env, input=stdin_text, text=True).returncode


def _pick_python() -> "str | None":
    """A Python 3.9 or newer for the venv: Homebrew, then the system one, then
    this interpreter, then anything on PATH."""
    seen = []
    for cand in ("/opt/homebrew/bin/python3", "/usr/local/bin/python3",
                 "/usr/bin/python3", sys.executable, "python3"):
        if not cand or cand in seen:
            continue
        seen.append(cand)
        try:
            out = subprocess.run(
                [cand, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
                capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if out.returncode != 0:
            continue
        parts = out.stdout.split()
        if len(parts) == 2 and parts[0] == "3" and int(parts[1]) >= 9:
            return cand
    return None


def _say(msg: str) -> None:
    print(f"acqai: {msg}", file=sys.stderr, flush=True)


# --- arguments ----------------------------------------------------------------
def _flag_value(rest: list, flag: str) -> "str | None":
    """Pull `--flag VALUE` out of rest, in place. None when absent."""
    if flag not in rest:
        return None
    i = rest.index(flag)
    if i + 1 >= len(rest):
        raise ValueError(f"{flag} needs a value")
    value = rest[i + 1]
    del rest[i:i + 2]
    return value


def _file_arg(rest: list) -> "str | None":
    """The PATH after --file, or None when the flag is absent. `-` is stdin."""
    if "--file" not in rest:
        return None
    i = rest.index("--file")
    if i + 1 >= len(rest):
        raise ValueError("--file needs a path (- for stdin)")
    return rest[i + 1]


def _message_from(rest: list) -> "str | None":
    """The question, from a positional argument or --file PATH (- for stdin).
    A long paste of docs is more than an argv can carry, so the file is the way
    a grounded question reaches the transport. An empty file is an error rather
    than an empty send, because a send spends your one yes."""
    path = _file_arg(rest)
    if path is not None:
        text = sys.stdin.read() if path == "-" else pathlib.Path(path).read_text(encoding="utf-8")
        if not text.strip():
            raise ValueError(f"--file {path} is empty; nothing to send")
        return text
    return rest[0] if rest else None


# --- consent ------------------------------------------------------------------
def _consent(yes: bool) -> "int | None":
    """Open the send gate for this process. None means go on; an int is the
    exit code to stop with (0 for a no at the prompt, 3 when nobody could be
    asked)."""
    if yes or mozilib.send_allowed():
        os.environ["MOZI_SEND_OK"] = "1"
        return None
    reply = ""
    try:
        if sys.stdin.isatty():
            reply = input(CONSENT_PROMPT)
        else:
            with open("/dev/tty") as tty:
                print(CONSENT_PROMPT, end="", file=sys.stderr, flush=True)
                reply = tty.readline()
    except (OSError, EOFError):
        _say("a send needs your yes: pass -y, or set MOZI_SEND_OK=1.")
        return 3
    if reply.strip().lower() in ("y", "yes"):
        os.environ["MOZI_SEND_OK"] = "1"
        return None
    print("cancelled.", file=sys.stderr)
    return 0


# --- the answers on file ------------------------------------------------------
def _slug(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:max_len].rstrip("-") or "question"


def _log_answer(question: str, answer: str, chat_id: "str | None", transport: str) -> pathlib.Path:
    ANSWERS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d-%H%M%S")
    path = ANSWERS / f"{stamp}-{_slug(question.strip().splitlines()[0] if question.strip() else '')}.md"
    lines = [
        f"# ACQ AI · {time.strftime('%Y-%m-%d %H:%M')}",
        "",
        f"chat: {chat_id or 'new'} · transport: {transport}",
        "",
        "## Question",
        "",
        question.rstrip(),
        "",
        "## Answer",
        "",
        answer.rstrip(),
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# --- commands -----------------------------------------------------------------
def cmd_setup(rest: list) -> int:
    company = _flag_value(rest, "--company")
    if company:
        mozilib.save_config(company=company)
        _say(f"company saved: {company!r}")
    py = _venv_python()
    if not py.is_file():
        base = _pick_python()
        if not base:
            _say("need Python 3.9 or newer for Playwright (on a Mac: brew install python), then run setup again.")
            return 1
        _say(f"creating a private venv at {VENV} (with {base})…")
        if subprocess.call([base, "-m", "venv", str(VENV)]) != 0:
            _say("python -m venv failed; see above.")
            return 1
    if not _venv_has_playwright():
        _say("installing Playwright (a minute or two)…")
        if subprocess.call([str(py), "-m", "pip", "install", "--quiet",
                            "--disable-pip-version-check", "playwright"]) != 0:
            _say("pip install playwright failed; see above.")
            return 1
    _say("checking Playwright's Chromium (a download the first time)…")
    if subprocess.call([str(py), "-m", "playwright", "install", "chromium"]) != 0:
        _say("playwright install chromium failed; see above.")
        return 1
    _say(f"ready. Next: {SELF} login")
    return 0


def cmd_login(rest: list, *, origin: str | None = None) -> int:
    company = _flag_value(rest, "--company")
    if company:
        mozilib.save_config(company=company)
    os.environ["MOZI_BASE"] = (origin or mozilib.PORTAL_BASE).rstrip("/")
    # The saved conversation stays. It names its app, so login-legacy goes
    # back to a conversation from the older app, and a send on the other app
    # stops and says so.
    argv = ["login"] + (["--company", company] if company else [])
    if origin == mozilib.LEGACY_BASE:
        # Re-enter under the venv as login-legacy so the child keeps the base.
        argv = ["login-legacy"] + argv[1:]
    code = _under_venv(argv)
    if code is not None:
        return code
    try:
        import mozi_browser
        wait_s = int(os.environ.get("ACQAI_LOGIN_WAIT", "600") or 600)
        mozi_browser.login(company=company or None, wait_s=wait_s)
    except mozilib.MoziError as err:
        _say(str(err))
        return 1
    except ValueError:
        _say("ACQAI_LOGIN_WAIT must be a number of seconds")
        return 2
    return 0


def cmd_login_legacy(rest: list) -> int:
    return cmd_login(rest, origin=mozilib.LEGACY_BASE)


def cmd_probe(rest: list) -> int:
    session = "--session" in rest
    if session:
        code = _under_venv(["probe", "--session"])
        if code is not None:
            return code
    cfg = mozilib.endpoint() or {}
    cid = mozilib.load_chat_id()
    if _playwright_here():
        pw = "installed here"
    elif _venv_has_playwright():
        pw = f"in the venv ({VENV})"
    else:
        pw = f"NOT installed: run {SELF} setup"
    company = None
    try:
        import mozi_browser
        company = mozi_browser.company_name()
        state_file = mozi_browser.STATE_FILE
    except Exception:
        state_file = mozilib.STATE_DIR / "storage-state.json"
    signed = ("profile saved" if state_file.is_file()
              else f"not yet: run {SELF} login")
    count = len(list(ANSWERS.glob("*.md"))) if ANSWERS.is_dir() else 0
    rows = [
        f"state:      {mozilib.STATE_DIR}",
        f"playwright: {pw}",
        f"login:      {signed}",
        f"company:    {company or 'none set (click it in the window, or setup --company NAME)'}",
        f"route:      {cfg.get('url') or 'not learned yet: send one message in the login window'}",
        f"chat:       {cid if cid else 'none (the next send starts a new conversation)'}",
        f"send gate:  MOZI_SEND_OK={'set' if mozilib.send_allowed() else 'not set (the send asks y/N)'}",
        f"answers:    {count} on file in {ANSWERS}",
    ]
    why = mozilib.chat_mismatch(cid, cfg)
    if why:
        rows.insert(6, f"next send:  NOT ready: {why}")
    if session:
        try:
            import mozi_browser
            mozi_browser.require_session()
            rows.append("session:    signed in")
        except mozilib.MoziError as err:
            rows.append(f"session:    NOT ready: {err}")
    print("\n".join(rows))
    return 0


def cmd_send(rest: list, argv: list) -> int:
    dry = "--dry-run" in rest
    http = "--http" in rest
    new = "--new" in rest
    yes = any(a in ("-y", "--yes") for a in rest)
    rest = [a for a in rest if a not in ("--dry-run", "--http", "--new", "-y", "--yes")]
    try:
        message = _message_from(rest)
    except (OSError, ValueError) as err:
        _say(str(err))
        return 2
    if message is None:
        _say('send needs a question: send "..." or send --file PATH (- for stdin)')
        return 2
    if dry:
        cfg = mozilib.endpoint() or {}
        cid = None if new else mozilib.load_chat_id()
        why = mozilib.chat_mismatch(cid, cfg)
        chat = (f"NOT ready: {why}" if why
                else f"continue {cid[:8]}…" if cid else "new conversation")
        shown = message.strip()
        if len(shown) > 400:
            shown = shown[:399].rstrip() + "…"
        print("\n".join([
            "Would send one question to ACQ AI:",
            f"  transport: {'raw HTTP (MOZI_TOKEN)' if http else 'your browser (Playwright)'}",
            f"  to:        {cfg.get('url') or '(route not learned yet)'}",
            f"  chat:      {chat}",
            f"  consent:   {'given' if (yes or mozilib.send_allowed()) else 'the live run asks y/N first (-y answers it)'}",
            f"  length:    {len(message)} characters",
            f"  question:  {shown}",
            "dry run: nothing sent",
        ]))
        return 0
    stop = _consent(yes)
    if stop is not None:
        return stop
    # Only a send that goes ahead drops the saved conversation: a dry run or a
    # no at the prompt keeps it.
    if new:
        mozilib.clear_chat_id()
    if not http:
        # `--file -` spent stdin in _message_from. The child under the venv
        # runs the same argv, so the text goes to it as its stdin, and its
        # own `--file -` reads the same question.
        stdin_text = message if _file_arg(rest) == "-" else None
        code = _under_venv(argv, stdin_text=stdin_text)
        if code is not None:
            return code
    transport = "HTTP" if http else "browser"
    try:
        _say(f"sending via {transport} ({len(message)} characters)…")
        if http:
            answer, cid = mozilib.ask(message)
        else:
            import mozi_browser
            answer, cid = mozi_browser.send(message)
    except mozilib.MoziConsent as err:
        _say(str(err))
        return 3
    except mozilib.MoziBlocked as err:
        _say(f"stopped ({err}).")
        return 1
    except mozilib.MoziError as err:
        _say(str(err))
        return 1
    print(answer)
    path = _log_answer(message, answer, cid, transport)
    _say(f"answer on file: {path}")
    return 0


def cmd_discover(rest: list) -> int:
    if len(rest) < 2 or rest[0] != "--from-curl":
        _say("discover needs --from-curl <file|-> (a Copy-as-cURL of one real send)")
        return 2
    src = rest[1]
    text = sys.stdin.read() if src == "-" else pathlib.Path(src).read_text(encoding="utf-8")
    try:
        cfg = mozilib.parse_curl(text)
    except mozilib.MoziError as err:
        _say(str(err))
        return 1
    path = mozilib.save_endpoint(cfg)
    print(f"learned: {cfg['method']} {cfg['url']}")
    print(f"  message slot: {cfg.get('message_slot') or '(none detected: set message_slot by hand)'}")
    print(f"  saved to:     {path}")
    return 0


def cmd_answers(rest: list) -> int:
    try:
        n = int(rest[0]) if rest else 10
    except ValueError:
        _say("answers takes a number")
        return 2
    files = sorted(ANSWERS.glob("*.md"), reverse=True) if ANSWERS.is_dir() else []
    if not files:
        print(f"no answers on file yet ({ANSWERS})")
        return 0
    for path in files[:n]:
        first = ""
        try:
            body = path.read_text(encoding="utf-8")
            m = re.search(r"^## Question\n\n(.+)$", body, re.M)
            first = (m.group(1).strip() if m else "")[:90]
        except OSError:
            pass
        print(f"{path.name}  {first}")
    return 0


def main(argv: list) -> int:
    if not argv or argv[0] in ("-h", "--help", "?"):
        print(__doc__.strip())
        return 0 if argv else 2
    mode, rest = argv[0], list(argv[1:])
    try:
        if mode == "setup":
            return cmd_setup(rest)
        if mode == "login":
            return cmd_login(rest)
        if mode == "login-legacy":
            return cmd_login_legacy(rest)
        if mode == "probe":
            return cmd_probe(rest)
        if mode == "send":
            return cmd_send(rest, argv)
        if mode == "discover":
            return cmd_discover(rest)
        if mode == "answers":
            return cmd_answers(rest)
    except ValueError as err:
        _say(str(err))
        return 2
    except OSError as err:
        _say(str(err))
        return 1
    _say(f"unknown command {mode!r}; try --help")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
