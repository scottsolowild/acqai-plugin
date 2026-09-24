"""Releases come from the commits: CI stamps the version after the merge.

A commit's Conventional Commit type decides the bump, the message never names
a version, and the order commits merge in does not change the version they
add up to.
"""
import contextlib
import importlib.util
import io
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release", ROOT / "tools" / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

PLUGIN = '{\n  "name": "acqai",\n  "version": "0.4.0",\n  "description": "x"\n}\n'
LOG = "# Changelog\n\nHow releases work.\n\n## [0.4.0] - 2026-09-24\n\n### Added\n- Earlier work.\n"


class Parsing(unittest.TestCase):
    def test_types_scopes_and_breaks(self):
        p = release.parse("feat(answers): one file per conversation")
        self.assertEqual((p["type"], p["scope"], p["breaking"]), ("feat", "answers", False))
        self.assertTrue(release.parse("fix!: drop the old flag")["breaking"])
        self.assertTrue(release.parse("feat: x", "Body.\n\nBREAKING CHANGE: y")["breaking"])
        self.assertIsNone(release.parse("Fix the login wait"))

    def test_a_message_names_no_version(self):
        self.assertEqual(release.message_faults("feat: record what came of it"), [])
        self.assertEqual(release.message_faults(release.STAMP_SUBJECT), [])
        self.assertEqual(len(release.message_faults("Clean portal answers (0.3.1)")), 2)
        self.assertTrue(release.message_faults("fix: ship it as v0.4.1"))
        self.assertEqual(release.message_faults(
            "fix: wait for the chat page", "Seen on 127.0.0.1 with Python 3.9."), [])

    def test_the_largest_bump_wins(self):
        parse = release.parse
        self.assertEqual(release.bump_of([parse("fix: a"), parse("feat: b")]), "minor")
        self.assertEqual(release.bump_of([parse("fix: a"), parse("docs: b")]), "patch")
        self.assertIsNone(release.bump_of([parse("docs: a"), None]))
        self.assertEqual(release.next_version((0, 4, 0), "major"), (0, 5, 0))
        self.assertEqual(release.next_version((1, 2, 3), "major"), (2, 0, 0))
        self.assertEqual(release.next_version((0, 4, 0), "patch"), (0, 4, 1))


class Stamping(unittest.TestCase):
    """A throwaway repo shaped like this one, released from v0.4.0."""

    def setUp(self):
        self.repo = pathlib.Path(tempfile.mkdtemp(prefix="acqai_release_"))
        (self.repo / "plugins/acqai/.claude-plugin").mkdir(parents=True)
        (self.repo / "plugins/acqai/.claude-plugin/plugin.json").write_text(PLUGIN)
        (self.repo / "CHANGELOG.md").write_text(LOG)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: start")
        self.git("tag", "-a", "v0.4.0", "-m", "acqai 0.4.0")
        self.saved = (release.ROOT, release.PLUGIN_JSON, release.CHANGELOG)
        release.ROOT = self.repo
        release.PLUGIN_JSON = self.repo / "plugins/acqai/.claude-plugin/plugin.json"
        release.CHANGELOG = self.repo / "CHANGELOG.md"

    def tearDown(self):
        release.ROOT, release.PLUGIN_JSON, release.CHANGELOG = self.saved
        shutil.rmtree(self.repo, ignore_errors=True)

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True,
                              capture_output=True, text=True).stdout

    def commit(self, message, name):
        (self.repo / name).write_text(message)
        self.git("add", name)
        self.git("commit", "-q", "-m", message)

    def run_release(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = release.main(list(args))
        return code, out.getvalue()

    def test_a_dry_run_names_the_version_and_changes_nothing(self):
        self.commit("feat: one file per conversation", "a")
        code, out = self.run_release()
        self.assertEqual(code, 0)
        self.assertIn("a minor since v0.4.0, so 0.5.0", out)
        self.assertIn('"version": "0.4.0"', release.PLUGIN_JSON.read_text())
        self.assertNotIn("v0.5.0", self.git("tag"))

    def test_yes_stamps_commits_and_tags_both_names(self):
        self.commit("feat: one file per conversation\n\nA follow-up lands in its file.", "a")
        self.commit("docs: say so in the readme", "b")
        self.commit("Fix without a type", "c")
        code, out = self.run_release("--yes")
        self.assertEqual(code, 0, out)
        self.assertIn('"version": "0.5.0"', release.PLUGIN_JSON.read_text())
        log = release.CHANGELOG.read_text()
        self.assertLess(log.index("## [0.5.0]"), log.index("## [0.4.0]"))
        self.assertIn("### Added\n- One file per conversation (", log)
        self.assertIn("  A follow-up lands in its file.", log)
        self.assertNotIn("readme", log.split("## [0.4.0]")[0])
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), release.STAMP_SUBJECT)
        tags = self.git("tag", "--points-at", "HEAD").split()
        self.assertEqual(sorted(tags), ["acqai--v0.5.0", "v0.5.0"])
        self.assertIn("is not a Conventional Commit", out)
        code, out = self.run_release("--yes")
        self.assertIn("nothing to release since v0.5.0", out)
        code, notes = self.run_release("notes", "0.5.0")
        self.assertIn("One file per conversation", notes)

    def test_the_merge_order_does_not_change_the_version(self):
        base = self.git("rev-parse", "HEAD").strip()
        self.commit("fix: wait for the page", "a")
        self.commit("feat: record the outcome", "b")
        first = self.run_release()[1]
        self.git("reset", "-q", "--hard", base)
        self.commit("feat: record the outcome", "b")
        self.commit("fix: wait for the page", "a")
        second = self.run_release()[1]
        self.assertIn("so 0.5.0", first)
        self.assertIn("so 0.5.0", second)

    def test_lint_fails_on_a_versioned_or_untyped_message(self):
        self.commit("Carry the question across the hop (0.3.2)", "a")
        code, out = self.run_release("lint")
        self.assertEqual(code, 1)
        self.assertIn("names a version (0.3.2)", out)
        self.assertEqual(self.run_release("lint", "--warn")[0], 0)
        msg = self.repo / "MSG"
        msg.write_text("feat: record the outcome\n\n# a comment git strips\n")
        self.assertEqual(self.run_release("lint", "--message-file", str(msg))[0], 0)
        msg.write_text("Record the outcome\n")
        self.assertEqual(self.run_release("lint", "--message-file", str(msg))[0], 1)


if __name__ == "__main__":
    unittest.main()
