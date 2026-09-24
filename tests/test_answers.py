"""The answer files: one per conversation with ACQ AI, read as a digest first.

The top says what was asked, the best answer, and what changed. Each message
sits under who sent it, with its own headings a level down, and a follow-up in
the same chat lands in the same file. The per-send files of 0.3 and earlier
still read, and answers --regroup merges them by chat.
"""
import contextlib
import importlib.util
import io
import os
import pathlib
import shutil
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugins" / "acqai" / "scripts" / "acqai.py"
os.environ["ACQAI_STATE_DIR"] = tempfile.mkdtemp(prefix="acqai_state_")
spec = importlib.util.spec_from_file_location("acqai", SCRIPT)
acqai = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acqai)

PASTE = ("# Before you answer\n\nYou advise a one-person practice.\n\n"
         "## The question\n\nWhat would you change first about my offer?\n\n"
         "## The docs\n\nWhy would anyone ask this?\n")
REPLY = ("## 1. First change\n\nMake payment close the decision.\n\n"
         "```\n# a comment, not a heading\n```\n")
OLD = ("# ACQ AI · 2026-09-24 11:22\n\nchat: {chat} · transport: browser\n\n"
       "## Question\n\n{q}\n\n## Answer\n\n## 1. The read\n\n{a}\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="acqai_answers_"))
        self.saved = acqai.ANSWERS
        acqai.ANSWERS = self.dir

    def tearDown(self):
        acqai.ANSWERS = self.saved
        shutil.rmtree(self.dir, ignore_errors=True)

    def send(self, message, answer=REPLY, chat="chat-1", **kw):
        return acqai._file_exchange(message, answer, chat, "browser",
                                    ["send", "--file", "q.md", "-y"], **kw)


class TheDigest(Base):
    def test_a_first_send_opens_on_the_question_then_the_digest(self):
        text = self.send(PASTE).read_text(encoding="utf-8")
        self.assertTrue(text.startswith(
            "# What would you change first about my offer?\n\n*ACQ AI · "), text[:80])
        order = [text.index(h) for h in (
            "## Answer\n\n_(none yet)_", "## What changed\n\n_(none yet)_",
            "## Claude ➡️ ACQ", "## ACQ ➡️ Claude", "## Which commands ran",
            "## Timeline")]
        self.assertEqual(order, sorted(order))
        self.assertIn("answered in chat chat-1", text)
        self.assertIn("acqai.py send --file q.md -y", text)

    def test_a_message_nests_under_its_sender(self):
        text = self.send(PASTE).read_text(encoding="utf-8")
        self.assertIn("## Claude ➡️ ACQ\n\n### Before you answer", text)
        self.assertIn("#### The question", text)
        self.assertIn("## ACQ ➡️ Claude\n\n### 1. First change", text)
        self.assertIn("```\n# a comment, not a heading\n```", text)


class TheConversation(Base):
    def test_a_follow_up_lands_in_the_same_file_with_its_reason(self):
        first = self.send(PASTE)
        second = self.send("Is that still first at my price?", answer="Yes.",
                           why="The price is already set.")
        self.assertEqual(first, second)
        text = second.read_text(encoding="utf-8")
        self.assertEqual(text.count("## Claude ➡️ ACQ"), 2)
        self.assertIn("## Claude ➡️ ACQ\n\n> Why: The price is already set.\n\n"
                      "Is that still first at my price?", text)
        self.assertIn(" · 2 exchanges · ", text)
        self.assertEqual(len(list(self.dir.glob("*.md"))), 1)

    def test_new_starts_its_own_file(self):
        self.send(PASTE)
        self.send("A different topic?", chat="chat-2", new=True)
        self.assertEqual(len(list(self.dir.glob("*.md"))), 2)

    def test_a_render_reads_back_the_same(self):
        text = self.send(PASTE).read_text(encoding="utf-8")
        self.assertEqual(acqai._render(acqai._parse(text)), text)


class TheRecord(Base):
    def test_outcome_writes_the_top_and_keeps_the_history(self):
        path = self.send(PASTE)
        with contextlib.redirect_stderr(io.StringIO()):
            code = acqai.cmd_outcome([path.name, "--title", "What goes first?",
                                     "--answer", "- Make payment close the decision.",
                                     "--adopt", "Put the link in the room.",
                                     "--later", "Count named-to-paid at the close."])
        self.assertEqual(code, 0)
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# What goes first?\n"))
        self.assertIn("## Answer\n\n- Make payment close the decision.", text)
        self.assertIn("## What changed\n\n- adopt: Put the link in the room.\n"
                      "- later: Count named-to-paid at the close.", text)
        self.assertIn("### 1. First change", text)
        self.assertEqual(acqai._render(acqai._parse(text)), text)

    def test_continue_appends_to_the_file_it_names_in_the_layout(self):
        old = self.dir / "2026-09-24-112256-first.md"
        old.write_text(OLD.format(chat="c-1", q="First question?", a="One."), encoding="utf-8")
        got = self.send("And then?", answer="Two.", chat="c-1", into=old)
        self.assertEqual(got, old)
        text = old.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# First question?\n"))
        self.assertEqual(text.count("## ACQ ➡️ Claude"), 2)


class TheOldFiles(Base):
    def test_a_record_from_the_end_of_an_old_file_moves_to_the_top(self):
        path = self.dir / "2026-09-24-112256-first.md"
        path.write_text(OLD.format(chat="c-1", q="First question?", a="One.")
                        + "\n<!-- acqai: what came of it -->\n## What came of it\n\n"
                        "- adopt: Kept it.\n", encoding="utf-8")
        self.assertEqual(acqai._outcome_of(path.read_text(encoding="utf-8")),
                         [("adopt", "Kept it.")])
        with contextlib.redirect_stderr(io.StringIO()):
            acqai.cmd_outcome([path.name, "--later", "Try it later."])
        text = path.read_text(encoding="utf-8")
        self.assertIn("## What changed\n\n- later: Try it later.", text)
        self.assertNotIn("What came of it", text)

    def old(self, name, chat, q, a):
        (self.dir / name).write_text(OLD.format(chat=chat, q=q, a=a), encoding="utf-8")

    def test_answers_lists_old_and_new_side_by_side(self):
        self.old("2026-09-24-112256-what-makes-a-good-referral.md", "c-1",
                 "What makes a good referral ask?", "One name.")
        self.send(PASTE)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            acqai.cmd_answers([])
        self.assertIn("What makes a good referral ask?", out.getvalue())
        self.assertIn("What would you change first about my offer?", out.getvalue())
        self.assertIn("answers --regroup", out.getvalue())

    def test_regroup_merges_a_chat_and_keeps_the_originals(self):
        self.old("2026-09-24-112256-first.md", "c-1", "First question?", "One.")
        self.old("2026-09-24-113152-second.md", "c-1", "Second question?", "Two.")
        self.old("2026-09-24-115018-other.md", "c-2", "Other question?", "Three.")
        with contextlib.redirect_stdout(io.StringIO()):
            acqai.cmd_answers(["--regroup", "--dry-run"])
        self.assertEqual(len(list(self.dir.glob("*.md"))), 3)
        with contextlib.redirect_stdout(io.StringIO()):
            acqai.cmd_answers(["--regroup"])
        files = sorted(self.dir.glob("*.md"))
        self.assertEqual(len(files), 2)
        merged = acqai._parse(files[0].read_text(encoding="utf-8"))
        self.assertEqual([e["q"] for e in merged["exchanges"]],
                         ["First question?", "Second question?"])
        self.assertIn("### 1. The read", files[0].read_text(encoding="utf-8"))
        self.assertEqual(len(list((self.dir / "regrouped").glob("*.md"))), 3)


class TheTitle(unittest.TestCase):
    def test_the_question_section_names_the_file(self):
        self.assertEqual(acqai._one_line(PASTE),
                         "What would you change first about my offer?")

    def test_a_long_question_is_clipped_at_a_word(self):
        got = acqai._one_line("Would " + "a long clause " * 20 + "work?", limit=50)
        self.assertLessEqual(len(got), 51)
        self.assertTrue(got.endswith("…"))


if __name__ == "__main__":
    unittest.main()
