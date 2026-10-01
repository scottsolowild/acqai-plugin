#!/usr/bin/env python3
"""The acqai commands as MCP tools, so Cowork runs them on the member's own
computer.

  acqai_mcp.py              serve the tools over stdio (the plugin's .mcp.json
                            starts it, in Claude Code and in Cowork)
  acqai_mcp.py -h           this text

Cowork runs shell commands in a Linux VM on the member's computer. The VM has
no screen for the sign-in window, a home folder of its own, and filtered
network, so acqai.py run from its shell cannot reach ACQ AI. A plugin's local
MCP server runs on the computer itself, outside the VM, with the member's
screen, network, and ~/.config/acqai. Claude Code loads the same server, so
both apps run one path.

Each tool runs one acqai.py command and hands back what it printed, with the
exit code on the last line. The logic stays in acqai.py, and this file maps a
tool to a command and nothing more.

Claude Desktop cancels a local MCP call at about 60 seconds, and no setting
raises that. So a call returns inside CAP_S. ready, login, and a live send can
take longer: setup installs Playwright, the sign-in window waits for the
member, and an answer can take two minutes. Each of those starts a job and
returns when it ends or at the cap, whichever comes first, and the wait tool
picks the job up again. A job runs in a session of its own, so a send under
way when the chat closes still files its answer. One job runs at a time,
because each one opens the same browser profile. A job lives in
<state>/jobs/<id>/ and is cleared a day after it ends, and the question a send
carries is deleted from there once the send ends (the answer's file keeps it).

Standard library only, like acqai.py, so the plugin asks for Python 3.9 or
newer and nothing else.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import secrets
import shutil
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = pathlib.Path(__file__).resolve()
ACQAI = HERE / "acqai.py"
PLUGIN_JSON = HERE.parent / ".claude-plugin" / "plugin.json"
STATE_DIR = pathlib.Path(
    os.environ.get("ACQAI_STATE_DIR", "").strip() or "~/.config/acqai").expanduser()
# Claude Desktop cancels a local MCP call at about 60 seconds, so every call
# returns well inside that.
CAP_S = 45
# A finished job is cleared this long after it ends.
KEEP_S = 24 * 3600
# The most text one result holds. A send's answer fits many times over. A
# longer log keeps its end, and a longer answer file keeps its top, where the
# digest is.
CLIP = 50_000
# The protocol versions this server speaks, newest first. A client asking for
# another gets the newest.
PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
# The jobs this process started, so their exit is read here and none of them
# is left a zombie that still looks alive.
_PROCS: dict = {}


class ToolError(Exception):
    """A call this server refuses before any command runs."""


def _version() -> str:
    try:
        return str(json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError):
        return "0"


INSTRUCTIONS = (
    "These tools run the acqai commands on the person's own computer, where "
    "their browser holds the ACQ AI login. The acq skill says when to call "
    "each one. Call ready first on every /acq run. A live send needs yes: "
    "true, which is the person's yes for that send. ready, login, and a live "
    "send return within 45 seconds. When a result ends on 'still running', "
    "call wait with its job until the result ends on an exit code.")

_STR = {"type": "string"}
_BOOL = {"type": "boolean"}
_LINES = {"type": "array", "items": {"type": "string"}}

TOOLS = [
    {
        "name": "ready",
        "title": "Get ready for ACQ AI",
        "description": (
            "Check what a send needs and fix what can be fixed: install "
            "Playwright when it is missing, open the sign-in window on the "
            "person's screen when the login is out, then check the route and "
            "the chat the next send continues. Call it first on every /acq "
            "run. A first run can take minutes, so when the result says the "
            "job is still running, call wait with that job. dry_run names the "
            "steps and runs nothing."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "company": dict(_STR, description=(
                    "The company to click on ACQ AI's sign-in list, when the "
                    "person belongs to more than one. It is saved for later "
                    "runs.")),
                "dry_run": dict(_BOOL, description="Name the steps and run nothing."),
            },
        },
        "annotations": {"openWorldHint": True},
    },
    {
        "name": "login",
        "title": "Sign into ACQ AI",
        "description": (
            "Open the sign-in window on the person's screen, at "
            "portal.acquisition.com/advisor (legacy: the older "
            "ai.acquisition.com/chat). The person signs in with the email "
            "code, clicks their company if a list shows, and sends one short "
            "message there, which teaches the chat route. The window closes "
            "once it has both. The call returns while the window waits, so "
            "call wait with the job until it ends."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "legacy": dict(_BOOL, description="Sign into the older ai.acquisition.com app."),
                "company": dict(_STR, description="The company to click on the sign-in list."),
            },
        },
        "annotations": {"openWorldHint": True},
    },
    {
        "name": "send",
        "title": "Ask ACQ AI",
        "description": (
            "Send one question to ACQ AI through the person's signed-in "
            "browser and return the answer. Run it with dry_run: true first. "
            "The dry run shows what would go out, and it exits 1 on a NOT "
            "ready line, such as a private name or a chat on the other app. "
            "A live send needs yes: true, the person's yes for this send, "
            "given in chat or by /acq -y. A send paces itself and can take "
            "two minutes, so when the result says the job is still running, "
            "call wait with that job."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": dict(_STR, description=(
                    "The whole question, with the docs that ride with it "
                    "pasted in.")),
                "yes": dict(_BOOL, description=(
                    "The person said yes to this send. Required for a live "
                    "send.")),
                "dry_run": dict(_BOOL, description="Show what would go out and send nothing."),
                "new": dict(_BOOL, description="Start a new conversation, for a new topic."),
                "continue": dict(_STR, description=(
                    "Go on in the conversation an answer on file used: its "
                    "file name or a prefix of it, from the answers tool.")),
                "why": dict(_STR, description=(
                    "A follow-up's reason, in one line. The answer's file "
                    "keeps it above the message.")),
            },
            "required": ["question"],
        },
        "annotations": {"openWorldHint": True},
    },
    {
        "name": "wait",
        "title": "Wait on a job",
        "description": (
            "Wait on a job that ready, login, or send started, for up to 45 "
            "seconds, and return what it printed. Call it again while the "
            "result says the job is still running."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "job": dict(_STR, description="The job id. Leave it out for the newest job."),
                "seconds": {"type": "integer", "minimum": 1, "maximum": CAP_S,
                            "description": f"How long to wait, at most {CAP_S}."},
            },
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "answers",
        "title": "List answers on file",
        "description": (
            "List the conversations on file, newest first, with each one's "
            "chat and what came of it."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "minimum": 1,
                          "description": "How many to list (default 10)."},
            },
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "answer",
        "title": "Read one answer",
        "description": (
            "Return one answer's whole file: the question, the best answer, "
            "what changed, and every message in the conversation. Name it by "
            "its file name or a prefix of it, from the answers tool."),
        "inputSchema": {
            "type": "object",
            "properties": {"name": _STR},
            "required": ["name"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "outcome",
        "title": "Record what came of an answer",
        "description": (
            "Read or record what came of an answer, at the top of its file. "
            "With no record fields, it reads. A record replaces the one "
            "before it. adopt is a change made, suggest a change drafted that "
            "waits on the person's yes, later an idea kept, and drop what "
            "was turned down, one line each."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "answer": dict(_STR, description="The answer's file name or a prefix of it."),
                "title": dict(_STR, description="The question, in one line."),
                "best_answer": dict(_STR, description="The best answer, in a few bullets."),
                "adopt": _LINES,
                "suggest": _LINES,
                "later": _LINES,
                "drop": _LINES,
                "dry_run": dict(_BOOL, description="Show the top it would write, and write nothing."),
            },
            "required": ["answer"],
        },
    },
    {
        "name": "names",
        "title": "Private names",
        "description": (
            "List the private names, or add or remove them. A send that "
            "carries one of them stops before it goes out."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "add", "remove"],
                           "description": "list (the default), add, or remove."},
                "names": _LINES,
                "dry_run": dict(_BOOL, description="Show the change and write nothing."),
            },
        },
    },
]


# --- arguments ----------------------------------------------------------------
_KINDS = {str: "text", bool: "true or false", int: "whole number", list: "list"}
# An answer is named by its file name in the answers folder, or a prefix of
# it. A path would let a tool read or rewrite any file on the computer, which
# Cowork keeps out of Claude's reach, so a name with a path in it is refused.
_ANSWER_NAME = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]*$")


def _get(args: dict, key: str, kind, *, required: bool = False):
    value = args.get(key)
    if value is None:
        if required:
            raise ToolError(f"{key} is required")
        return None
    if (kind is int and isinstance(value, bool)) or not isinstance(value, kind):
        raise ToolError(f"{key} must be a {_KINDS[kind]}")
    return value


def _text(args: dict, key: str, *, required: bool = False) -> "str | None":
    value = _get(args, key, str, required=required)
    if required and not (value or "").strip():
        raise ToolError(f"{key} is empty")
    return value


def _answer(args: dict, key: str, *, required: bool = False) -> "str | None":
    value = _text(args, key, required=required)
    if value is not None and not _ANSWER_NAME.match(value):
        raise ToolError(f"{key} names an answer by its file name or a prefix "
                        "of it, from the answers tool, and takes no path")
    return value


def _lines(args: dict, key: str) -> list:
    value = _get(args, key, list) or []
    if not all(isinstance(v, str) for v in value):
        raise ToolError(f"{key} must be a list of lines")
    return value


# --- running acqai.py -----------------------------------------------------------
def _env() -> dict:
    """The child's environment. Its messages name the tools ("acqai login"),
    since the member here has a tool to call rather than a path to type, and
    setup is the first step ready runs."""
    env = dict(os.environ)
    env["ACQAI_STATE_DIR"] = str(STATE_DIR)
    env.setdefault("ACQAI_CMD", "acqai")
    env.setdefault("ACQAI_SETUP_CMD", "acqai ready")
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _cwd() -> str:
    """The answers folder, where every command runs. acqai.py reads a bare
    answer name as a file here before it looks it up, so a name can only
    ever find an answer."""
    answers = STATE_DIR / "answers"
    answers.mkdir(parents=True, exist_ok=True)
    return str(answers)


def _clip(text: str, keep: str) -> str:
    if len(text) <= CLIP:
        return text
    if keep == "head":
        return text[:CLIP] + "\n…(the rest is cut here)"
    return "(the start is cut here)…\n" + text[-CLIP:]


def _run_now(argv: list, stdin_text: "str | None" = None) -> tuple:
    """A command that ends in seconds: run it, and hand back its output and
    whether it failed. Its own session keeps it off any terminal, so nothing
    can stop to ask at one."""
    try:
        out = subprocess.run(
            [sys.executable, str(ACQAI), *argv], input=stdin_text,
            stdin=None if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", env=_env(), cwd=_cwd(),
            timeout=CAP_S, start_new_session=True)
    except subprocess.TimeoutExpired:
        return f"acqai {argv[0]} did not end within {CAP_S} seconds.", True
    keep = "head" if argv[0] == "show" else "tail"
    body = _clip((out.stdout or "").rstrip(), keep)
    return f"{body}\n\n[acqai {argv[0]}: exit {out.returncode}]".lstrip(), out.returncode != 0


# --- jobs -----------------------------------------------------------------------
def _jobs() -> pathlib.Path:
    return STATE_DIR / "jobs"


def _exit_code(d: pathlib.Path) -> "int | None":
    try:
        return int((d / "exit").read_text().strip())
    except (OSError, ValueError):
        return None


def _alive(job: str, d: pathlib.Path) -> bool:
    proc = _PROCS.get(job)
    if proc is not None:
        return proc.poll() is None
    if os.name == "nt":
        # os.kill ends a process on Windows rather than probing it.
        return False
    try:
        os.kill(int((d / "pid").read_text().strip()), 0)
    except (OSError, ValueError):
        return False
    return True


def _all_jobs() -> list:
    """Every job on file, newest first."""
    root = _jobs()
    if not root.is_dir():
        return []
    return sorted((p for p in root.iterdir() if p.is_dir()), reverse=True)


def _running() -> "str | None":
    for d in _all_jobs():
        if _exit_code(d) is None and _alive(d.name, d):
            return d.name
    return None


def _prune() -> None:
    """Clear the jobs that ended more than KEEP_S ago."""
    now = time.time()
    for d in _all_jobs():
        if _exit_code(d) is None and _alive(d.name, d):
            continue
        try:
            age = now - d.stat().st_mtime
            if (d / "exit").exists():
                age = now - (d / "exit").stat().st_mtime
        except OSError:
            continue
        if age > KEEP_S:
            shutil.rmtree(d, ignore_errors=True)
            _PROCS.pop(d.name, None)


def _start(argv: list, stdin_text: "str | None" = None) -> str:
    """Start a job in a session of its own, and return its id."""
    _prune()
    busy = _running()
    if busy:
        raise ToolError(
            f"job {busy} is still running, and one job runs at a time. "
            f'Call wait with job "{busy}" first.')
    job = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    d = _jobs() / job
    d.mkdir(parents=True, mode=0o700)
    (d / "command.json").write_text(
        json.dumps([sys.executable, str(ACQAI), *argv]), encoding="utf-8")
    if stdin_text is not None:
        fd = os.open(d / "stdin.txt", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(stdin_text)
    proc = subprocess.Popen(
        [sys.executable, str(SCRIPT), "run-job", str(d)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, env=_env(), start_new_session=True)
    _PROCS[job] = proc
    (d / "pid").write_text(str(proc.pid), encoding="utf-8")
    return job


def _wait(job: str, seconds: int) -> tuple:
    """Wait on a job for up to seconds, and hand back what it printed and
    whether it failed."""
    d = _jobs() / job
    if not d.is_dir():
        raise ToolError(f"no job {job!r} on file")
    deadline = time.monotonic() + seconds
    code = _exit_code(d)
    while code is None and _alive(job, d) and time.monotonic() < deadline:
        time.sleep(0.25)
        code = _exit_code(d)
    try:
        log = (d / "out.log").read_text(encoding="utf-8", errors="replace").rstrip()
    except OSError:
        log = ""
    log = _clip(log, "tail")
    if code is not None:
        return f"{log}\n\n[job {job}: exit {code}]".lstrip(), code != 0
    if _alive(job, d):
        return (f"{log}\n\n[job {job} is still running. Call wait with "
                f'job "{job}" to keep waiting.]').lstrip(), False
    return f"{log}\n\n[job {job} stopped without an exit code]".lstrip(), True


def _start_and_wait(argv: list, stdin_text: "str | None" = None) -> tuple:
    return _wait(_start(argv, stdin_text), CAP_S)


def run_job(d: pathlib.Path) -> int:
    """The job itself, in its own session: run the command on file, keep
    what it prints in out.log, then write its exit code. The question a send
    carried is deleted once the send ends."""
    command = json.loads((d / "command.json").read_text(encoding="utf-8"))
    question = d / "stdin.txt"
    with open(d / "out.log", "wb") as out:
        stdin = open(question, "rb") if question.exists() else subprocess.DEVNULL
        try:
            code = subprocess.call(command, stdin=stdin, stdout=out,
                                   stderr=subprocess.STDOUT, env=_env(),
                                   cwd=_cwd())
        except OSError as err:
            out.write(f"acqai: {err}\n".encode("utf-8"))
            code = 1
        finally:
            if stdin is not subprocess.DEVNULL:
                stdin.close()
    try:
        question.unlink()
    except OSError:
        pass
    tmp = d / "exit.tmp"
    tmp.write_text(str(code), encoding="utf-8")
    tmp.replace(d / "exit")
    return 0


# --- the tools ------------------------------------------------------------------
def tool_ready(args: dict) -> tuple:
    company = _text(args, "company")
    argv = ["ready"] + (["--company", company] if company else [])
    if _get(args, "dry_run", bool):
        return _run_now(argv + ["--dry-run"])
    return _start_and_wait(argv)


def tool_login(args: dict) -> tuple:
    company = _text(args, "company")
    argv = ["login-legacy" if _get(args, "legacy", bool) else "login"]
    return _start_and_wait(argv + (["--company", company] if company else []))


def tool_send(args: dict) -> tuple:
    question = _text(args, "question", required=True)
    argv = ["send", "--file", "-"]
    cont = _answer(args, "continue")
    why = _text(args, "why")
    if cont:
        argv += ["--continue", cont]
    if why:
        argv += ["--why", why]
    if _get(args, "new", bool):
        argv.append("--new")
    if _get(args, "dry_run", bool):
        return _run_now(argv + ["--dry-run"], stdin_text=question)
    if not _get(args, "yes", bool):
        raise ToolError(
            "a live send needs the person's yes. Show them the question, and "
            "once they say yes (or ran /acq -y), call send again with yes: true.")
    return _start_and_wait(argv + ["-y"], stdin_text=question)


def tool_wait(args: dict) -> tuple:
    job = _text(args, "job")
    seconds = _get(args, "seconds", int) or CAP_S
    if not job:
        jobs = _all_jobs()
        if not jobs:
            raise ToolError("no job on file yet")
        job = jobs[0].name
    return _wait(job, max(1, min(CAP_S, seconds)))


def tool_answers(args: dict) -> tuple:
    count = _get(args, "count", int)
    return _run_now(["answers"] + ([str(count)] if count else []))


def tool_answer(args: dict) -> tuple:
    return _run_now(["show", _answer(args, "name", required=True)])


def tool_outcome(args: dict) -> tuple:
    argv = ["outcome", _answer(args, "answer", required=True)]
    title = _text(args, "title")
    best = _text(args, "best_answer")
    if title is not None:
        argv += ["--title", title]
    if best is not None:
        argv += ["--answer", best]
    for kind in ("adopt", "suggest", "later", "drop"):
        for line in _lines(args, kind):
            argv += [f"--{kind}", line]
    if _get(args, "dry_run", bool):
        argv.append("--dry-run")
    return _run_now(argv)


def tool_names(args: dict) -> tuple:
    action = _text(args, "action") or "list"
    names = _lines(args, "names")
    if action == "list":
        return _run_now(["names"])
    if action not in ("add", "remove"):
        raise ToolError("action is list, add, or remove")
    if not names:
        raise ToolError(f"names {action} needs at least one name")
    return _run_now(["names", action, *names]
                    + (["--dry-run"] if _get(args, "dry_run", bool) else []))


HANDLERS = {
    "ready": tool_ready,
    "login": tool_login,
    "send": tool_send,
    "wait": tool_wait,
    "answers": tool_answers,
    "answer": tool_answer,
    "outcome": tool_outcome,
    "names": tool_names,
}


# --- the protocol ---------------------------------------------------------------
def _error(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def handle(msg) -> "dict | None":
    """One JSON-RPC message in, the reply out, or None for a notification."""
    if not isinstance(msg, dict) or not isinstance(msg.get("method"), str):
        if isinstance(msg, dict) and "method" not in msg:
            return None  # a reply to a request this server never makes
        return _error(None, -32600, "Invalid Request")
    method, mid = msg["method"], msg.get("id")
    if "id" not in msg:
        return None
    params = msg.get("params") or {}
    if method == "initialize":
        asked = params.get("protocolVersion")
        result = {
            "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "acqai", "title": "ACQ AI", "version": _version()},
            "instructions": INSTRUCTIONS,
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        name = params.get("name")
        fn = HANDLERS.get(name)
        if fn is None:
            return _error(mid, -32602, f"Unknown tool: {name}")
        args = params.get("arguments") or {}
        try:
            if not isinstance(args, dict):
                raise ToolError("arguments must be an object")
            text, failed = fn(args)
        except ToolError as err:
            text, failed = f"acqai: {err}", True
        except Exception as err:  # a tool fails, and the server stays up
            text, failed = f"acqai: {type(err).__name__}: {err}", True
        result = {"content": [{"type": "text", "text": text}], "isError": failed}
    else:
        return _error(mid, -32601, f"Method not found: {method}")
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve() -> int:
    """Read one JSON-RPC message per line from stdin and write each reply as
    one line to stdout. stdout carries the protocol and nothing else."""
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    while True:
        line = sys.stdin.readline()
        if not line:
            return 0
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = _error(None, -32700, "Parse error")
        else:
            try:
                reply = handle(msg)
            except Exception as err:  # a malformed message, and the server stays up
                mid = msg.get("id") if isinstance(msg, dict) else None
                reply = _error(mid, -32603, f"Internal error: {err}")
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


def main(argv: list) -> int:
    if argv and argv[0] in ("-h", "--help", "?"):
        print(__doc__.strip())
        return 0
    if len(argv) == 2 and argv[0] == "run-job":
        return run_job(pathlib.Path(argv[1]))
    if argv:
        print("acqai_mcp.py takes no arguments; -h says what it does",
              file=sys.stderr)
        return 2
    return serve()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
