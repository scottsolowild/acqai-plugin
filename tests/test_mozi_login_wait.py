"""login waits with a Playwright call, so the page can move while it waits.

Playwright's sync API runs the page's events, the request that teaches the
chat route among them, only while a Playwright call is running. login's loop
and _wait_for_route paused with time.sleep, so a member who signed in and
sent the teaching message could wait out the full deadline with nothing
seen. Both pause with page.wait_for_timeout now. The fake page here moves
only inside that call, the way the real one does, so a time.sleep pause
never sees it move.

This file is shared byte for byte with the acqai plugin repo's tests/, so
it imports only the transport pair.
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mozilib  # noqa: E402
import mozi_browser  # noqa: E402

PORTAL = "https://portal.acquisition.com"
ROUTE = {"url": PORTAL + "/api/sandbar/chat-stream"}


class _Loc:
    def __init__(self, shown: bool):
        self.shown = shown
        self.first = self

    def count(self):
        return 1 if self.shown else 0

    def is_visible(self):
        return self.shown

    def filter(self, **_k):
        return _Loc(False)

    def click(self):
        pass


class SlowPage:
    """/advisor, signed in, with the chat box still to draw. It draws, and
    the teaching message's route arrives, only during wait_for_timeout."""

    def __init__(self, on_wait=None):
        self.url = PORTAL + "/advisor"
        self.frames = []
        self.waits = 0
        self.on_wait = on_wait

    def locator(self, selector):
        return _Loc(selector == "textarea" and self.waits > 0)

    def get_by_text(self, *_a, **_k):
        return _Loc(False)

    def get_by_role(self, *_a, **_k):
        return _Loc(False)

    def goto(self, url, **_k):
        self.url = url

    def wait_for_timeout(self, _ms):
        self.waits += 1
        if self.on_wait:
            self.on_wait()


class Clock:
    """time.monotonic and time.sleep on a fake clock: a sleep moves time and
    nothing else, the way it holds the real page still."""

    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


class WaitCase(unittest.TestCase):
    def setUp(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("MOZI_BASE", "MOZI_COMPANY",
                            "CLAUDE_PLUGIN_OPTION_MOZI_COMPANY")}
        self.clock = Clock()
        self.route = None
        self.out = io.StringIO()
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        for patch in (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(mozilib, "endpoint",
                                  side_effect=lambda: self.route),
                mock.patch.object(mozilib, "config", return_value={}),
                mock.patch.object(mozi_browser, "_save_storage_state"),
                mock.patch.object(mozi_browser, "_enter_pressed",
                                  return_value=False),
                mock.patch.object(mozi_browser.time, "monotonic",
                                  side_effect=self.clock.monotonic),
                mock.patch.object(mozi_browser.time, "sleep",
                                  side_effect=self.clock.sleep),
                mock.patch("builtins.input", side_effect=EOFError),
                contextlib.redirect_stdout(self.out),
                contextlib.redirect_stderr(self.out)):
            stack.enter_context(patch)

    def learn(self):
        self.route = ROUTE


class WaitForRoute(WaitCase):
    def test_a_message_sent_during_the_pause_is_seen(self):
        page = SlowPage(on_wait=self.learn)
        mozi_browser._wait_for_route(mock.Mock(pages=[page]), page, wait_s=300)
        self.assertEqual(page.waits, 1)
        self.assertNotIn("no message seen", self.out.getvalue())
        self.assertLess(self.clock.t, 300)

    def test_a_closed_window_ends_the_wait(self):
        page = SlowPage()
        page.wait_for_timeout = mock.Mock(side_effect=RuntimeError("closed"))
        mozi_browser._wait_for_route(mock.Mock(pages=[]), page, wait_s=300)
        self.assertIn("no message seen", self.out.getvalue())
        self.assertLess(self.clock.t, 300, "a closed window waited out the deadline")


class LoginLoop(WaitCase):
    def test_the_pause_lets_the_page_finish_signing_in(self):
        self.route = ROUTE
        page = SlowPage()
        ctx = mock.Mock(pages=[page])
        ctx.cookies = mock.Mock(return_value=[])
        with mock.patch.object(mozi_browser, "_import_playwright",
                               return_value=lambda: mock.MagicMock()), \
                mock.patch.object(mozi_browser, "_context", return_value=ctx):
            mozi_browser.login(wait_s=60)
        self.assertGreaterEqual(page.waits, 1)
        self.assertIn("logged in, session saved.", self.out.getvalue())


if __name__ == "__main__":
    unittest.main()
