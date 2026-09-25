"""The script's own commands: send's checks, names, outcome, and answers.

Most of these run the script the way a member does, in a subprocess with a
private state folder, so nothing touches ~/.config/acqai and nothing reaches
ACQ AI: every send here is a dry run, or stops before the transport. Two
live sends run in-process instead: the one that --continue hands its chat
to, with the transport replaced, and the ones that hop into the venv, with
the hop replaced.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "acqai" / "scripts"
SCRIPT = SCRIPTS / "acqai.py"
# acqai.py reads its state folder when it is imported, so point it at a
# scratch one first. Each test below still moves the paths it uses.
_SCRATCH = tempfile.TemporaryDirectory()
os.environ.setdefault("ACQAI_STATE_DIR", _SCRATCH.name)
sys.path.insert(0, str(SCRIPTS))

import acqai  # noqa: E402
import mozi_browser  # noqa: E402
import mozilib  # noqa: E402

PORTAL_ROUTE = {
    "url": "https://portal.acquisition.com/api/sandbar/chat-stream",
    "method": "POST",
    "message_slot": "messages[-1].content",
    "stream": True,
    "body_template": {"messages": [{"id": "m1", "role": "user", "content": "x"}],
                      "toolContext": {"chatId": "c0"}},
}
OLD_CHAT = "18711427-be74-4c37-bb00-78cec2236e64@ai.acquisition.com"
PORTAL_CHAT = "22222222-2222-4222-8222-222222222222@portal.acquisition.com"
ANSWER = "2026-09-23-220939-which-lane-first.md"


def answer_body(chat: str = PORTAL_CHAT, answer: str = "The bridge.") -> str:
    """An answer file in the shape send writes."""
    return (f"# ACQ AI · 2026-09-23 22:09\n\nchat: {chat} · transport: browser\n\n"
            f"## Question\n\nWhich lane first?\n\n## Answer\n\n{answer}\n")


class CliCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name)
        self.answers = self.state / "answers"
        self.answers.mkdir()
        self.bin = self.state / "bin"
        self.bin.mkdir()

    def route(self, cfg=PORTAL_ROUTE):
        (self.state / "endpoint.json").write_text(json.dumps(cfg), encoding="utf-8")

    def answer(self, name: str = ANSWER, **kw) -> Path:
        path = self.answers / name
        path.write_text(answer_body(**kw), encoding="utf-8")
        return path

    def run_cli(self, *args, path: str | None = None) -> subprocess.CompletedProcess:
        env = {"PATH": path if path is not None else os.environ.get("PATH", ""),
               "HOME": str(self.state), "ACQAI_STATE_DIR": str(self.state)}
        return subprocess.run([sys.executable, str(SCRIPT), *args], env=env,
                              stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=60)


class Names(CliCase):
    def test_add_list_and_remove(self):
        self.assertIn("no private names yet", self.run_cli("names").stdout)
        r = self.run_cli("names", "add", "Jane Doe", "Acme Holdings")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.run_cli("names").stdout.splitlines(),
                         ["Jane Doe", "Acme Holdings"])
        r = self.run_cli("names", "remove", "acme holdings")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.run_cli("names").stdout.splitlines(), ["Jane Doe"])

    def test_a_name_already_there_is_not_added_twice(self):
        self.run_cli("names", "add", "Jane Doe", "JANE DOE")
        r = self.run_cli("names", "add", "jane doe")
        self.assertIn("nothing to add", r.stderr)
        self.assertEqual(self.run_cli("names").stdout.splitlines(), ["Jane Doe"])

    def test_comments_the_member_wrote_stay(self):
        (self.state / "private-names.txt").write_text(
            "# clients\nJane Doe\n", encoding="utf-8")
        self.run_cli("names", "add", "Sam Roe")
        self.assertEqual((self.state / "private-names.txt").read_text(),
                         "# clients\nJane Doe\nSam Roe\n")
        self.assertEqual(self.run_cli("names").stdout.splitlines(),
                         ["Jane Doe", "Sam Roe"])

    def test_a_dry_run_writes_nothing(self):
        r = self.run_cli("names", "add", "Jane Doe", "--dry-run")
        self.assertIn("would add: Jane Doe", r.stdout)
        self.assertFalse((self.state / "private-names.txt").exists())

    def test_usage(self):
        for args in (("names", "add"), ("names", "rename", "x")):
            with self.subTest(args=args):
                r = self.run_cli(*args)
                self.assertEqual(r.returncode, 2)
                self.assertIn("names takes:", r.stderr)


class SendChecks(CliCase):
    def setUp(self):
        super().setUp()
        self.route()
        self.run_cli("names", "add", "Jane Doe")

    def test_a_clean_dry_run_exits_zero(self):
        r = self.run_cli("send", "What should I charge?", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("NOT ready", r.stdout)
        self.assertTrue(r.stdout.rstrip().endswith("dry run: nothing sent"))

    def test_a_private_name_is_a_not_line_in_the_dry_run(self):
        r = self.run_cli("send", "Should JANE DOE get the pilot?", "--dry-run")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("private:   NOT ready: the question names Jane Doe", r.stdout)
        self.assertIn("not ready: fix each NOT line", r.stdout)
        self.assertIn("dry run: nothing sent", r.stdout)

    def test_a_name_inside_a_longer_word_is_not_a_hit(self):
        self.run_cli("names", "add", "Ann")
        r = self.run_cli("send", "Plan the annual review.", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout)
        r = self.run_cli("send", "Plan the annual review with Ann.", "--dry-run")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("names Ann", r.stdout)

    def test_a_live_send_stops_on_a_private_name_before_it_asks(self):
        # No -y and no terminal: past the names check this would be exit 3.
        r = self.run_cli("send", "Should Jane Doe get the pilot?")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("names Jane Doe", r.stderr)
        self.assertIn("Nothing was sent.", r.stderr)


class Continue(CliCase):
    def setUp(self):
        super().setUp()
        self.route()

    def test_the_dry_run_names_the_chat_and_the_answer_it_came_from(self):
        self.answer()
        r = self.run_cli("send", "And the second point?", "--continue",
                         "2026-09-23-2209", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"chat:      continue 22222222… from {ANSWER}", r.stdout)

    def test_a_chat_from_the_other_app_is_a_not_line(self):
        self.answer(chat=OLD_CHAT)
        r = self.run_cli("send", "And the second point?", "--continue", ANSWER,
                         "--dry-run")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("NOT ready: Mozi chat 18711427… is from the older app", r.stdout)
        self.assertIn("acqai.py login-legacy continues this chat", r.stdout)

    def test_the_answer_can_be_named_by_its_path(self):
        path = self.answer()
        r = self.run_cli("send", "And?", "--continue", str(path), "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"chat:      continue 22222222… from {ANSWER}", r.stdout)

    def test_usage(self):
        self.answer()
        self.answer(name="2026-09-23-230000-other.md", chat="new")
        cases = [
            ("send", "x", "--continue", ANSWER, "--new"),
            ("send", "x", "--continue", "2026-09-23", "--dry-run"),
            ("send", "x", "--continue", "2026-09-24", "--dry-run"),
            ("send", "x", "--continue", "2026-09-23-230000", "--dry-run"),
            ("send", "x", "--continue"),
        ]
        for args in cases:
            with self.subTest(args=args):
                self.assertEqual(self.run_cli(*args).returncode, 2)

    def test_the_live_send_hands_the_chat_to_the_transport(self):
        path = self.answer()
        seen = {}

        def ask(message, *, chat_id=None, timeout=120):
            seen["message"], seen["chat_id"] = message, chat_id
            return "The answer", chat_id

        out = io.StringIO()
        # -y opens the send gate in this process's environment, and
        # patch.dict puts the environment back for the tests after this one.
        with mock.patch.dict(os.environ), \
                mock.patch.object(acqai, "ANSWERS", self.answers), \
                mock.patch.object(mozilib, "ask", side_effect=ask), \
                contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            code = acqai.main(["send", "And the second point?", "--http", "-y",
                               "--continue", path.name])
        self.assertEqual(code, 0)
        self.assertEqual(seen["chat_id"], PORTAL_CHAT)
        self.assertIn("The answer", out.getvalue())
        filed = path.read_text(encoding="utf-8")
        self.assertIn(f"answered in chat {PORTAL_CHAT}", filed)
        self.assertEqual(filed.count("## ACQ ➡️ Claude"), 2, "the follow-up joins its file")


class Paste(CliCase):
    def clipboard(self, text: str):
        tool = self.bin / "pbpaste"
        tool.write_text(f"#!/bin/sh\nprintf '%s' '{text}'\n", encoding="utf-8")
        tool.chmod(0o755)

    def test_the_question_comes_from_the_clipboard_after_the_note(self):
        self.route()
        self.clipboard("What should I charge?")
        r = self.run_cli("send", "Context first.", "--paste", "--dry-run",
                         path=str(self.bin))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("question:  Context first.\nWhat should I charge?", r.stdout)

    def test_the_names_check_reads_the_pasted_text(self):
        self.route()
        self.run_cli("names", "add", "Jane Doe")
        self.clipboard("Should Jane Doe get the pilot?")
        r = self.run_cli("send", "--paste", "--dry-run", path=str(self.bin))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("names Jane Doe", r.stdout)

    def test_an_empty_clipboard_or_no_tool_or_a_file_too_is_usage(self):
        empty = self.bin / "empty"
        empty.mkdir()
        self.assertEqual(self.run_cli("send", "--paste", "--dry-run",
                                      path=str(empty)).returncode, 2)
        self.clipboard("   ")
        self.assertEqual(self.run_cli("send", "--paste", "--dry-run",
                                      path=str(self.bin)).returncode, 2)
        self.clipboard("A question")
        self.assertEqual(self.run_cli("send", "--paste", "--file", "q.md",
                                      "--dry-run", path=str(self.bin)).returncode, 2)

    def test_the_venv_child_reads_the_question_read_here_from_its_stdin(self):
        # A child that read the clipboard again could find other text, and
        # stdin is spent. So the child gets the question this process read,
        # through `--file -`. No child runs here, and nothing reaches ACQ AI.
        path = self.answer()
        pasted = "What should I charge?"
        cases = [
            (["send", "Context first.", "--paste", "-y"], "",
             ["send", "--file", "-"], "Context first.\n" + pasted),
            (["send", "--paste", "-y", "--continue", path.name], "",
             ["send", "--file", "-", "--continue", path.name], pasted),
            (["send", "--paste", "--new", "-y"], "",
             ["send", "--file", "-", "--new"], pasted),
            (["send", "--file", "-", "-y"], "A question from stdin",
             ["send", "--file", "-", "-y"], "A question from stdin"),
        ]
        for argv, stdin, child, text in cases:
            with self.subTest(argv=argv):
                seen = {}

                def hop(args, stdin_text=None):
                    seen["args"], seen["stdin"] = args, stdin_text
                    return 0

                with mock.patch.dict(os.environ), \
                        mock.patch.object(acqai, "ANSWERS", self.answers), \
                        mock.patch.object(acqai, "NAMES_FILE", self.state / "private-names.txt"), \
                        mock.patch.object(acqai, "_clipboard", return_value=pasted), \
                        mock.patch.object(acqai, "_under_venv", side_effect=hop), \
                        mock.patch.object(mozilib, "clear_chat_id"), \
                        mock.patch.object(sys, "stdin", io.StringIO(stdin)), \
                        contextlib.redirect_stderr(io.StringIO()):
                    code = acqai.main(argv)
                self.assertEqual(code, 0)
                self.assertEqual(seen["args"], child)
                self.assertEqual(seen["stdin"], text)


class Outcome(CliCase):
    def test_record_read_back_and_rerun(self):
        path = self.answer()
        self.assertIn("No record", self.run_cli("outcome", ANSWER).stdout)
        args = ("outcome", ANSWER, "--adopt", "Put the bridge conversation first.",
                "--drop", "The webinar; no audience yet.",
                "--adopt", "Name the pilot's one acceptance test.")
        r = self.run_cli(*args)
        self.assertEqual(r.returncode, 0, r.stderr)
        first = path.read_text(encoding="utf-8")
        self.assertIn("## What changed", first)
        r = self.run_cli(*args)
        self.assertIn("already holds that record", r.stderr)
        self.assertEqual(path.read_text(encoding="utf-8"), first)
        self.assertEqual(self.run_cli("outcome", ANSWER).stdout.splitlines()[1:], [
            "- adopt: Put the bridge conversation first.",
            "- drop: The webinar; no audience yet.",
            "- adopt: Name the pilot's one acceptance test."])

    def test_a_new_record_replaces_the_old_one(self):
        path = self.answer()
        self.run_cli("outcome", ANSWER, "--adopt", "First try.")
        self.run_cli("outcome", ANSWER, "--later", "Second thought.")
        body = path.read_text(encoding="utf-8")
        self.assertNotIn("First try.", body)
        self.assertEqual(body.count("## What changed"), 1)

    def test_a_heading_inside_the_answer_is_not_the_record(self):
        path = self.answer(answer="## What came of it\n\n- adopt: ACQ AI's own list")
        self.assertIn("No record", self.run_cli("outcome", ANSWER).stdout)
        self.run_cli("outcome", ANSWER, "--drop", "Kept nothing.")
        body = path.read_text(encoding="utf-8")
        self.assertIn("- adopt: ACQ AI's own list", body, "the answer stays whole")
        self.assertEqual(self.run_cli("outcome", ANSWER).stdout.splitlines()[1:],
                         ["- drop: Kept nothing."])

    def test_a_dry_run_writes_nothing(self):
        path = self.answer()
        before = path.read_text(encoding="utf-8")
        r = self.run_cli("outcome", ANSWER, "--later", "Pilot pricing.", "--dry-run")
        self.assertIn("- later: Pilot pricing.", r.stdout)
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_usage(self):
        self.answer()
        cases = [
            (("outcome",), "outcome takes one answer"),
            (("outcome", ANSWER, "--adopt"), "--adopt needs one line"),
            (("outcome", "nothing-like-it"), "no single answer on file matches"),
        ]
        for args, said in cases:
            with self.subTest(args=args):
                r = self.run_cli(*args)
                self.assertEqual(r.returncode, 2)
                self.assertIn(said, r.stderr)


class Answers(CliCase):
    def test_each_answer_shows_its_chat_and_what_came_of_it(self):
        self.answer()
        self.answer(name="2026-09-23-230000-other.md", chat=OLD_CHAT)
        self.answer(name="2026-09-23-231000-before.md",
                    chat="18711427-be74-4c37-bb00-78cec2236e64")
        self.run_cli("outcome", ANSWER, "--adopt", "One.", "--drop", "Two.")
        out = self.run_cli("answers").stdout
        self.assertIn("chat 22222222… on the portal · adopt 1, drop 1", out)
        self.assertIn("chat 18711427… on the older app", out)
        self.assertIn("chat 18711427… with no app on record", out)


class CutShort(CliCase):
    """Part of a reply, then an error: the send fails, and the part that
    came joins the conversation's file, marked where it stopped."""

    def test_the_part_is_filed_with_its_chat_and_the_send_exits_1(self):
        def ask(message, *, chat_id=None, timeout=120):
            # The transport saves the chat before it reads the answer, so
            # the chat holds the question and the part.
            mozilib.save_chat_id(PORTAL_CHAT)
            raise mozilib.MoziCutShort("The model is overloaded.",
                                       "Price it at $4,500 and")

        out, err = io.StringIO(), io.StringIO()
        # -y opens the send gate in this process's environment, and
        # patch.dict puts the environment back for the tests after this one.
        with mock.patch.dict(os.environ), \
                mock.patch.object(acqai, "ANSWERS", self.answers), \
                mock.patch.object(mozilib, "CHAT_ID_FILE", self.state / "chat-id"), \
                mock.patch.object(mozilib, "ask", side_effect=ask), \
                contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = acqai.main(["send", "What should I charge?", "--http", "-y"])
        self.assertEqual(code, 1)
        self.assertIn("Price it at $4,500 and", out.getvalue())
        self.assertIn("The model is overloaded.", err.getvalue())
        filed = sorted(self.answers.glob("*.md"))
        self.assertEqual(len(filed), 1, filed)
        kept = filed[0].read_text(encoding="utf-8")
        self.assertIn(f"answered in chat {PORTAL_CHAT}", kept)
        reply = kept.split("## ACQ ➡️ Claude", 1)[1]
        self.assertIn("Price it at $4,500 and", reply)
        self.assertIn("Cut short here", reply)

class Ready(CliCase):
    """`ready` checks what a send needs, in order, and fixes what it can:
    setup, the sign-in window, then the route and the chat. Nothing here
    opens a browser: the steps that would are replaced and counted."""

    def setUp(self):
        super().setUp()
        self.profile = self.state / "storage-state.json"

    def ready(self, *args, session=None, route=PORTAL_ROUTE, chat=None,
              playwright=True):
        calls = {"setup": [], "login": [], "hop": []}

        def setup(rest):
            calls["setup"].append(list(rest))
            return 0

        def login(rest):
            calls["login"].append(list(rest))
            self.profile.write_text("{}", encoding="utf-8")
            return 0

        def hop(argv, stdin_text=None):
            calls["hop"].append(list(argv))
            return None

        check = mock.Mock(side_effect=session)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(acqai, "_playwright_here", return_value=playwright), \
                mock.patch.object(acqai, "_venv_has_playwright", return_value=False), \
                mock.patch.object(acqai, "_under_venv", side_effect=hop), \
                mock.patch.object(acqai, "cmd_setup", side_effect=setup), \
                mock.patch.object(acqai, "cmd_login", side_effect=login), \
                mock.patch.object(acqai, "ANSWERS", self.answers), \
                mock.patch.object(mozi_browser, "STATE_FILE", self.profile), \
                mock.patch.object(mozi_browser, "require_session", check), \
                mock.patch.object(mozilib, "endpoint", return_value=route), \
                mock.patch.object(mozilib, "load_chat_id", return_value=chat), \
                contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = acqai.main(["ready", *args])
        calls["checked"] = check.call_count
        return code, out.getvalue(), err.getvalue(), calls

    def test_everything_in_place_says_ready(self):
        self.profile.write_text("{}", encoding="utf-8")
        code, out, err, calls = self.ready()
        self.assertEqual(code, 0, out + err)
        self.assertIn("ready: ask with", out)
        self.assertIn("login:      profile saved", out)
        self.assertEqual(calls["checked"], 1, "the saved session is checked")
        self.assertEqual(calls["setup"], [])
        self.assertEqual(calls["login"], [])
        self.assertEqual(calls["hop"], [["ready"]])

    def test_no_login_saved_opens_the_window(self):
        code, out, err, calls = self.ready()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(calls["login"], [[]])
        self.assertEqual(calls["checked"], 0, "nothing to check before a login")
        self.assertIn("no login saved yet", err)
        self.assertIn("ready: ask with", out)

    def test_an_expired_session_opens_the_window(self):
        self.profile.write_text("{}", encoding="utf-8")
        gone = mozi_browser.BrowserNotReady("Mozi profile is on the sign-in page")
        code, out, err, calls = self.ready(session=gone)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(calls["checked"], 1)
        self.assertEqual(calls["login"], [[]])
        self.assertIn("sign-in page", err)
        self.assertIn("ready: ask with", out)

    def test_missing_playwright_runs_setup_and_carries_the_company(self):
        code, out, err, calls = self.ready("--company", "Acme", playwright=False)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(calls["setup"], [["--company", "Acme"]])
        self.assertEqual(calls["hop"], [["ready", "--company", "Acme"]])
        self.assertEqual(calls["login"], [["--company", "Acme"]])

    def test_a_route_never_learned_is_the_stop(self):
        self.profile.write_text("{}", encoding="utf-8")
        code, out, err, calls = self.ready(route=None)
        self.assertEqual(code, 1, out + err)
        self.assertIn("NOT ready. route: not learned yet", err)
        self.assertIn("discover --from-curl", err)
        self.assertNotIn("ready: ask with", out)

    def test_a_chat_from_the_other_app_is_the_stop(self):
        self.profile.write_text("{}", encoding="utf-8")
        code, out, err, calls = self.ready(chat=OLD_CHAT)
        self.assertEqual(code, 1, out + err)
        self.assertIn("NOT ready: Mozi chat 18711427… is from the older app", err)
        self.assertIn("--new starts a fresh thread", err)
        self.assertNotIn("ready: ask with", out)

    def test_a_dry_run_names_the_steps_and_runs_nothing(self):
        code, out, err, calls = self.ready("--dry-run", route=None)
        self.assertEqual(code, 1, out + err)
        self.assertIn("ready would run:", out)
        self.assertIn("login: no login saved yet", out)
        self.assertIn("route: not learned yet", out)
        self.assertIn("not ready: nothing was run (dry run)", out)
        self.assertEqual(calls["setup"] + calls["login"] + calls["hop"], [])
        self.assertEqual(calls["checked"], 0, "a dry run opens no browser")
        self.profile.write_text("{}", encoding="utf-8")
        code, out, err, calls = self.ready("--dry-run")
        self.assertEqual(code, 0, out + err)
        self.assertIn("ready: nothing to run (dry run)", out)
        self.assertEqual(calls["checked"], 0)

    def test_a_fresh_state_dry_run_names_the_login_from_the_command_line(self):
        r = self.run_cli("ready", "--dry-run")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("login: no login saved yet", r.stdout)
        self.assertFalse((self.state / "venv").exists(), "no setup ran")
        self.assertFalse(self.profile.exists(), "no login ran")

if __name__ == "__main__":
    unittest.main()
