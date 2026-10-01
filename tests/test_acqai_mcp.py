"""The MCP server that runs the acqai commands on the member's computer.

The protocol tests run the server the way Claude Code and Cowork start it, as
a subprocess over stdio, with the real acqai.py and an empty state folder, so
every send there is a dry run or stops before the transport. The job tests
call the server in-process with a fake command in acqai.py's place, so a job
can run long, end on any exit code, and show what it was handed, and nothing
opens a browser or reaches ACQ AI.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "acqai" / "scripts"
SERVER = SCRIPTS / "acqai_mcp.py"
PLUGIN_JSON = ROOT / "plugins" / "acqai" / ".claude-plugin" / "plugin.json"
sys.path.insert(0, str(SCRIPTS))

import acqai_mcp  # noqa: E402

ANSWER = "2026-09-23-220939-which-lane-first.md"
# An answer file in the shape send writes; outcome turns it into the digest.
ANSWER_BODY = textwrap.dedent("""\
    # ACQ AI · 2026-09-23 22:09

    chat: 22222222-2222-4222-8222-222222222222@portal.acquisition.com · transport: browser

    ## Question

    Which lane first?

    ## Answer

    The bridge.
    """)

# Stands in for acqai.py in the job tests: says what it was handed, waits
# FAKE_DELAY seconds, and exits FAKE_EXIT.
FAKE = textwrap.dedent("""\
    import os, sys, time
    print("args:", " ".join(sys.argv[1:]), flush=True)
    print("stdin:", sys.stdin.read(), flush=True)
    print("cmd:", os.environ.get("ACQAI_CMD"), flush=True)
    time.sleep(float(os.environ.get("FAKE_DELAY", "0")))
    print("done", flush=True)
    sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
    """)


def call(mid: int, tool: str, /, **arguments) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "method": "tools/call",
            "params": {"name": tool, "arguments": arguments}}


INIT = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "test", "version": "0"}}}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


class OverStdio(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name)

    def serve(self, *messages) -> dict:
        """Each reply by its id. Every line on stdout has to be one."""
        lines = [m if isinstance(m, str) else json.dumps(m) for m in messages]
        env = dict(os.environ, ACQAI_STATE_DIR=str(self.state), HOME=str(self.state))
        env.pop("ACQAI_CMD", None)
        env.pop("MOZI_SEND_OK", None)
        out = subprocess.run([sys.executable, str(SERVER)], env=env,
                             input="\n".join(lines) + "\n", capture_output=True,
                             text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        replies = {}
        for line in out.stdout.splitlines():
            reply = json.loads(line)
            self.assertEqual(reply["jsonrpc"], "2.0")
            replies[reply["id"]] = reply
        return replies

    def text(self, reply: dict) -> str:
        return reply["result"]["content"][0]["text"]

    def test_handshake_and_the_tools(self):
        r = self.serve(INIT, INITIALIZED, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                       {"jsonrpc": "2.0", "id": 2, "method": "ping"})
        self.assertEqual(sorted(r), [0, 1, 2], "a notification gets no reply")
        init = r[0]["result"]
        self.assertEqual(init["protocolVersion"], "2025-06-18")
        self.assertIn("tools", init["capabilities"])
        version = json.loads(PLUGIN_JSON.read_text())["version"]
        self.assertEqual(init["serverInfo"], {"name": "acqai", "title": "ACQ AI",
                                              "version": version})
        tools = {t["name"]: t for t in r[1]["result"]["tools"]}
        self.assertEqual(set(tools), {"ready", "login", "send", "wait", "answers",
                                      "answer", "outcome", "names"})
        for name, tool in tools.items():
            with self.subTest(tool=name):
                schema = tool["inputSchema"]
                self.assertEqual(schema["type"], "object")
                self.assertLessEqual(set(schema.get("required", [])),
                                     set(schema["properties"]))
                self.assertTrue(tool["description"])
        self.assertEqual(r[2]["result"], {})

    def test_a_version_it_does_not_speak_gets_its_newest(self):
        init = dict(INIT, params=dict(INIT["params"], protocolVersion="1999-01-01"))
        r = self.serve(init)
        self.assertEqual(r[0]["result"]["protocolVersion"], acqai_mcp.PROTOCOLS[0])

    def test_protocol_errors(self):
        r = self.serve(INIT, "not json",
                       {"jsonrpc": "2.0", "id": 1, "method": "resources/list"},
                       call(2, "post_to_community"))
        self.assertEqual(r[None]["error"]["code"], -32700)
        self.assertEqual(r[1]["error"]["code"], -32601)
        self.assertEqual(r[2]["error"]["code"], -32602)

    def test_a_bad_argument_is_a_tool_error_and_the_server_stays_up(self):
        r = self.serve(INIT, call(1, "send"), call(2, "names", action="rename", names=["x"]),
                       call(3, "answers", count="ten"), call(4, "names"))
        for mid, words in ((1, "question is required"), (2, "list, add, or remove"),
                           (3, "count must be a")):
            with self.subTest(mid=mid):
                self.assertTrue(r[mid]["result"]["isError"])
                self.assertIn(words, self.text(r[mid]))
        self.assertIn("no private names yet", self.text(r[4]))

    def test_a_dry_run_send_runs_now_and_sends_nothing(self):
        r = self.serve(INIT, call(1, "send", question="Which lane first?", dry_run=True))
        self.assertFalse(r[1]["result"]["isError"])
        self.assertIn("dry run: nothing sent", self.text(r[1]))
        self.assertIn("question:  Which lane first?", self.text(r[1]))
        self.assertTrue(self.text(r[1]).endswith("[acqai send: exit 0]"))
        self.assertFalse((self.state / "jobs").exists())

    def test_a_live_send_without_yes_starts_nothing(self):
        r = self.serve(INIT, call(1, "send", question="Which lane first?"))
        self.assertTrue(r[1]["result"]["isError"])
        self.assertIn("needs the person's yes", self.text(r[1]))
        self.assertFalse((self.state / "jobs").exists())

    def test_a_private_name_stops_the_dry_run(self):
        r = self.serve(INIT, call(1, "names", action="add", names=["Jane Doe"]),
                       call(2, "send", question="What should Jane Doe pay?", dry_run=True))
        self.assertFalse(r[1]["result"]["isError"], self.text(r[1]))
        self.assertTrue(r[2]["result"]["isError"])
        self.assertIn("NOT ready", self.text(r[2]))

    def test_a_live_send_runs_as_a_job_and_names_the_tools(self):
        """No route on file: the job stops in the transport, before anything
        could go out, and the stop names the login tool."""
        r = self.serve(INIT, call(1, "send", question="Which lane first?", yes=True))
        text = self.text(r[1])
        self.assertTrue(r[1]["result"]["isError"])
        self.assertIn("no Mozi endpoint learned yet", text)
        self.assertIn("acqai login", text)
        self.assertRegex(text, r"\[job \d{8}-\d{6}-[0-9a-f]{4}: exit 1\]$")
        (job,) = (self.state / "jobs").iterdir()
        self.assertEqual((job / "exit").read_text(), "1")
        self.assertFalse((job / "stdin.txt").exists(), "the question is deleted")

    def test_a_ready_dry_run_runs_nothing(self):
        r = self.serve(INIT, call(1, "ready", dry_run=True))
        self.assertTrue(r[1]["result"]["isError"])
        self.assertIn("login: no login saved yet", self.text(r[1]))
        self.assertIn("run acqai login", self.text(r[1]))
        self.assertFalse((self.state / "venv").exists())

    def test_answer_and_outcome_read_and_write_the_file_on_this_computer(self):
        answers = self.state / "answers"
        answers.mkdir()
        (answers / ANSWER).write_text(ANSWER_BODY, encoding="utf-8")
        r = self.serve(INIT, call(1, "answers"),
                       call(2, "outcome", answer="2026-09-23-2209", adopt=["Lead with the bridge."],
                            best_answer="- The bridge first."),
                       call(3, "answer", name="2026-09-23-2209"),
                       call(4, "answer", name="2026-01-01"))
        self.assertIn(ANSWER, self.text(r[1]))
        self.assertFalse(r[2]["result"]["isError"], self.text(r[2]))
        whole = self.text(r[3])
        self.assertIn("- adopt: Lead with the bridge.", whole)
        self.assertIn("- The bridge first.", whole)
        self.assertIn("The bridge.", whole)
        self.assertTrue(r[4]["result"]["isError"])
        self.assertIn("no single answer on file matches", self.text(r[4]))

    def test_an_answer_name_reaches_no_other_file(self):
        """Cowork keeps the computer's files out of Claude's reach, so a tool
        that reads or rewrites an answer takes a name and never a path, and
        a bare name finds nothing outside the answers folder."""
        (self.state / "storage-state.json").write_text('{"cookies": "SECRET"}')
        outside = self.state / "notes.md"
        outside.write_text("keep me", encoding="utf-8")
        r = self.serve(INIT,
                       call(1, "answer", name=str(outside)),
                       call(2, "answer", name="../storage-state.json"),
                       call(3, "outcome", answer=str(outside), adopt=["x"]),
                       call(4, "send", question="q", dry_run=True, **{"continue": "~/notes.md"}),
                       call(5, "answer", name="storage-state.json"))
        for mid in (1, 2, 3, 4):
            with self.subTest(mid=mid):
                self.assertTrue(r[mid]["result"]["isError"])
                self.assertIn("takes no path", self.text(r[mid]))
        self.assertTrue(r[5]["result"]["isError"])
        self.assertNotIn("SECRET", self.text(r[5]))
        self.assertEqual(outside.read_text(), "keep me")

    def test_a_malformed_message_gets_an_error_and_the_server_stays_up(self):
        r = self.serve({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": [1]},
                       INIT, call(2, "send", question="Café, 30 % off ✓", dry_run=True))
        self.assertEqual(r[1]["error"]["code"], -32603)
        self.assertIn("question:  Café, 30 % off ✓", self.text(r[2]))


class Jobs(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name)
        fake = self.state / "fake_acqai.py"
        fake.write_text(FAKE, encoding="utf-8")
        for patch in (mock.patch.object(acqai_mcp, "STATE_DIR", self.state),
                      mock.patch.object(acqai_mcp, "ACQAI", fake),
                      mock.patch.dict(os.environ, {"FAKE_DELAY": "0", "FAKE_EXIT": "0"}),
                      mock.patch.dict(acqai_mcp._PROCS, clear=True)):
            patch.start()
            self.addCleanup(patch.stop)
        os.environ.pop("ACQAI_CMD", None)
        self.addCleanup(self.drain)

    def drain(self):
        """Let every job end before its folder is cleaned up."""
        for proc in acqai_mcp._PROCS.values():
            proc.wait(timeout=30)

    def tool(self, name: str, /, **arguments) -> dict:
        reply = acqai_mcp.handle(call(1, name, **arguments))
        return reply["result"]

    def test_a_job_hands_back_its_output_and_exit(self):
        result = self.tool("send", question="Which lane first?", yes=True, new=True)
        text = result["content"][0]["text"]
        self.assertFalse(result["isError"])
        self.assertIn("args: send --file - --new -y", text)
        self.assertIn("stdin: Which lane first?", text)
        self.assertIn("cmd: acqai", text)
        self.assertRegex(text, r"done\n\n\[job \S+: exit 0\]$")

    def test_a_nonzero_exit_is_an_error(self):
        os.environ["FAKE_EXIT"] = "1"
        result = self.tool("login", legacy=True, company="Acme")
        self.assertTrue(result["isError"])
        self.assertIn("args: login-legacy --company Acme", result["content"][0]["text"])

    def test_a_long_job_is_picked_up_by_wait_and_runs_alone(self):
        os.environ["FAKE_DELAY"] = "2"
        with mock.patch.object(acqai_mcp, "CAP_S", 0.3):
            first = self.tool("send", question="Which lane first?", yes=True)
            text = first["content"][0]["text"]
            self.assertFalse(first["isError"])
            self.assertIn("still running", text)
            job = text.rsplit('job "', 1)[1].split('"', 1)[0]
            question = self.state / "jobs" / job / "stdin.txt"
            if os.name != "nt":
                self.assertEqual(question.stat().st_mode & 0o777, 0o600)
            second = self.tool("ready")
            self.assertTrue(second["isError"])
            self.assertIn(f"job {job} is still running, and one job runs at a time",
                          second["content"][0]["text"])
        done = self.tool("wait", seconds=20)
        self.assertIn("done", done["content"][0]["text"])
        self.assertTrue(done["content"][0]["text"].endswith(f"[job {job}: exit 0]"))
        self.assertFalse(question.exists())

    def test_a_job_from_an_earlier_server_counts_while_it_runs(self):
        if os.name == "nt":
            self.skipTest("the pid probe is POSIX only")
        d = self.state / "jobs" / "20260101-000000-abcd"
        d.mkdir(parents=True)
        (d / "pid").write_text(str(os.getpid()))
        result = self.tool("ready")
        self.assertIn("job 20260101-000000-abcd is still running", result["content"][0]["text"])
        waited = self.tool("wait", job="20260101-000000-abcd", seconds=1)
        self.assertIn("is still running", waited["content"][0]["text"])
        self.assertFalse(waited["isError"])

    def test_a_job_that_died_says_so(self):
        d = self.state / "jobs" / "20260101-000000-dead"
        d.mkdir(parents=True)
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        (d / "pid").write_text(str(proc.pid))
        result = self.tool("wait", job="20260101-000000-dead", seconds=1)
        self.assertTrue(result["isError"])
        self.assertIn("stopped without an exit code", result["content"][0]["text"])

    def test_a_job_a_day_old_is_cleared_when_the_next_starts(self):
        old = self.state / "jobs" / "20260101-000000-0001"
        old.mkdir(parents=True)
        (old / "exit").write_text("0")
        day_ago = time.time() - acqai_mcp.KEEP_S - 60
        os.utime(old / "exit", (day_ago, day_ago))
        fresh = self.state / "jobs" / "20260101-000001-0002"
        fresh.mkdir()
        (fresh / "exit").write_text("0")
        self.tool("send", question="Which lane first?", yes=True)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_wait_needs_a_job_on_file(self):
        result = self.tool("wait")
        self.assertTrue(result["isError"])
        self.assertIn("no job on file yet", result["content"][0]["text"])
        result = self.tool("wait", job="nope")
        self.assertIn("no job 'nope' on file", result["content"][0]["text"])

    def test_long_output_keeps_its_end(self):
        with mock.patch.object(acqai_mcp, "CLIP", 10):
            self.assertEqual(acqai_mcp._clip("0123456789abc", "tail"),
                             "(the start is cut here)…\n3456789abc")
            self.assertEqual(acqai_mcp._clip("0123456789abc", "head"),
                             "0123456789\n…(the rest is cut here)")


if __name__ == "__main__":
    unittest.main()
