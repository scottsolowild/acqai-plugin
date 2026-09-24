"""The portal's workspace gate, and the reason a refused send gives.

A portal session with no workspace picked draws "Choose a workspace" at
/advisor, and every /api/sandbar call answers 403 ("ACQ AI and Command Center
access is not active.") until one is picked. The URL stays /advisor, so the
URL checks read that page as signed in, and every send from 2026-09-24 10:49
on posted from it and got the 403. The transport now picks MOZI_COMPANY there
before it posts anything, and a refusal carries the server's own reason.

This file is shared byte for byte with the acqai plugin repo's tests/, so
it imports only the transport pair.
"""
from __future__ import annotations

import io
import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mozilib  # noqa: E402
import mozi_browser  # noqa: E402

PORTAL = "https://portal.acquisition.com"
CREATE = PORTAL + "/api/sandbar/chat"
STREAM = PORTAL + "/api/sandbar/chat-stream"
NOT_ACTIVE = "ACQ AI and Command Center access is not active."
REFUSED = json.dumps({"error": "FORBIDDEN", "message": NOT_ACTIVE,
                      "statusCode": 403})
CFG = {
    "url": STREAM,
    "method": "POST",
    "create_url": CREATE,
    "message_slot": "messages[-1].content",
    "stream": True,
    "body_template": {
        "advisorAgentId": "general",
        "messages": [{"id": "m1", "role": "user", "content": "test"}],
        "requestId": "r1",
        "toolContext": {"userId": "u1", "chatId": "c0", "messageId": "m0",
                        "newMessageId": "m1",
                        "clientServiceCompanyId": "acq.clients.company.x"},
        "clientLocalTime": "2026-09-24T15:36:59.910Z",
        "clientTimeZone": "America/Chicago",
    },
}
NEW_CHAT = "11111111-1111-4111-8111-111111111111"


class Loc:
    """A Playwright locator stand-in, visible while `shown()` is true."""

    def __init__(self, shown=lambda: False, on_click=None, text=None):
        self._shown = shown
        self._on_click = on_click
        self.text = text
        self.first = self

    def count(self):
        return 1 if self._shown() else 0

    def is_visible(self):
        return bool(self._shown())

    def click(self):
        if self._on_click:
            self._on_click()


class Buttons:
    """`locator("button[data-aegis-org-id]")`: one button per workspace
    until one is picked, then none."""

    def __init__(self, page):
        self.page = page
        self.first = self

    def count(self):
        return 0 if self.page.picked else len(self.page.workspaces)

    def is_visible(self):
        return self.count() > 0

    def filter(self, has=None, **_k):
        pattern = getattr(has, "text", None)
        for name in self.page.workspaces:
            if hasattr(pattern, "search") and pattern.search(name):
                return Loc(lambda: not self.page.picked,
                           on_click=lambda n=name: self.page.pick(n))
        return Loc()


class PortalPage:
    """/advisor on the portal: the workspace picker until a workspace is
    clicked, then the chat box. Records each in-page fetch with the workspace
    that was picked when it ran."""

    def __init__(self, picked=None,
                 workspaces=("Beta Co", "Acme Co")):
        self.url = PORTAL + "/advisor"
        self.frames = []
        self.picked = picked
        self.workspaces = workspaces
        self.fetches: list[tuple[str, str | None]] = []
        self.replies = {
            CREATE: (200, json.dumps({"id": NEW_CHAT})),
            STREAM: (200, 'data: {"type":"text-delta","delta":"The answer"}'),
        }

    def pick(self, name):
        self.picked = name

    def locator(self, selector):
        if selector == mozi_browser._WORKSPACE_BTN:
            return Buttons(self)
        if selector == "textarea":
            return Loc(lambda: self.picked is not None)
        return Loc()

    def get_by_text(self, text, **_k):
        return Loc(text=text)

    def get_by_role(self, *_a, **_k):
        return Loc()

    def goto(self, url, **_k):
        self.url = url

    def set_default_timeout(self, _ms):
        pass

    def evaluate(self, _js, arg):
        self.fetches.append((arg["url"], self.picked))
        status, text = self.replies[arg["url"]]
        return {"status": status, "text": text}


def _ctx(page):
    ctx = mock.Mock()
    ctx.pages = [page]
    ctx.cookies = mock.Mock(return_value=[
        {"name": "__Secure-aegis-external.session_token", "value": "tok",
         "domain": ".acquisition.com"}])
    return ctx


class Clock:
    """time.monotonic and time.sleep on a fake clock, so a wait runs out
    at once."""

    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


class PortalCase(unittest.TestCase):
    def setUp(self):
        mozi_browser._company_hint_shown = False
        mozi_browser._company_missed.clear()
        env = {k: v for k, v in os.environ.items()
               if k not in ("MOZI_BASE", "MOZI_CHAT_ID",
                            "CLAUDE_PLUGIN_OPTION_MOZI_COMPANY")}
        env["MOZI_COMPANY"] = "Acme Co"
        self.clock = Clock()
        self.err = io.StringIO()
        for patch in (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(mozilib, "endpoint", return_value=CFG),
                mock.patch.object(mozilib, "load_chat_id", return_value=None),
                mock.patch.object(mozilib, "save_chat_id"),
                mock.patch.object(mozilib, "config", return_value={}),
                mock.patch.object(mozi_browser, "_save_storage_state"),
                mock.patch.object(mozi_browser.time, "monotonic",
                                  side_effect=self.clock.monotonic),
                mock.patch.object(mozi_browser.time, "sleep",
                                  side_effect=self.clock.sleep),
                mock.patch("sys.stderr", self.err)):
            patch.start()
            self.addCleanup(patch.stop)

    def run_send(self, page, question="What should I charge?"):
        ctx = _ctx(page)
        with mock.patch.object(mozi_browser, "_import_playwright",
                               return_value=lambda: mock.MagicMock()), \
             mock.patch.object(mozi_browser, "_context", return_value=ctx):
            return mozi_browser.send(question)


class WorkspacePicker(PortalCase):
    def test_the_picker_is_not_signed_in_though_the_url_is_advisor(self):
        page = PortalPage()
        self.assertTrue(mozi_browser._workspace_picker_shown(page))
        self.assertFalse(mozi_browser._logged_in(page))
        page.pick("Acme Co")
        self.assertFalse(mozi_browser._workspace_picker_shown(page))
        self.assertTrue(mozi_browser._logged_in(page))

    def test_the_portal_cookie_alone_is_not_a_ready_session(self):
        """The Aegis cookie is set while the picker is still to draw, so on
        the portal only the chat box proves the session is usable."""
        page = PortalPage(picked="Acme Co")
        page.locator = lambda sel: (Buttons(page) if sel ==
                                    mozi_browser._WORKSPACE_BTN else Loc())
        self.assertFalse(mozi_browser._session_ready(page, _ctx(page)))

    def test_ensure_company_clicks_the_named_workspace(self):
        page = PortalPage()
        self.assertTrue(mozi_browser._ensure_company(page))
        self.assertEqual(page.picked, "Acme Co")

    def test_the_name_matches_the_whole_name_in_any_case(self):
        page = PortalPage(workspaces=("Acme Co Labs", "Acme Co"))
        self.assertTrue(mozi_browser.select_company(page, "acme co"))
        self.assertEqual(page.picked, "Acme Co")

    def test_no_company_leaves_the_pick_to_the_member(self):
        os.environ.pop("MOZI_COMPANY")
        page = PortalPage()
        self.assertFalse(mozi_browser._ensure_company(page))
        self.assertIsNone(page.picked)
        self.assertIn("MOZI_COMPANY", self.err.getvalue())

    def test_require_session_picks_the_workspace(self):
        page = PortalPage()
        ctx = _ctx(page)
        with mock.patch.object(mozi_browser, "_import_playwright",
                               return_value=lambda: mock.MagicMock()), \
             mock.patch.object(mozi_browser, "_context", return_value=ctx):
            mozi_browser.require_session(timeout=5)
        self.assertEqual(page.picked, "Acme Co")


class SendAfterThePick(PortalCase):
    def test_every_fetch_runs_after_the_workspace_is_picked(self):
        """The 2026-09-24 failure: the send posted from the picker page."""
        page = PortalPage()
        answer, cid = self.run_send(page)
        self.assertEqual(answer, "The answer")
        self.assertEqual(cid, NEW_CHAT + "@portal.acquisition.com")
        self.assertEqual(page.fetches, [(CREATE, "Acme Co"),
                                        (STREAM, "Acme Co")])

    def test_a_name_missing_from_the_list_stops_before_any_fetch(self):
        os.environ["MOZI_COMPANY"] = "Other Co"
        page = PortalPage()
        with self.assertRaises(mozi_browser.BrowserNotReady) as caught:
            self.run_send(page)
        self.assertIn("workspace", str(caught.exception))
        self.assertIn("MOZI_COMPANY", str(caught.exception))
        self.assertEqual(page.fetches, [])
        self.assertEqual(self.err.getvalue().count("not clickable"), 1)

    def test_a_refused_create_names_the_servers_reason(self):
        page = PortalPage(picked="Acme Co")
        page.replies[CREATE] = (403, REFUSED)
        with self.assertRaises(mozilib.MoziBlocked) as caught:
            self.run_send(page)
        msg = str(caught.exception)
        self.assertIn("403 from sandbar chat create", msg)
        self.assertIn("access is not active", msg)
        self.assertIn("login", msg)
        self.assertEqual(page.fetches, [(CREATE, "Acme Co")])

    def test_a_refused_stream_names_the_servers_reason(self):
        page = PortalPage(picked="Acme Co")
        page.replies[STREAM] = (403, REFUSED)
        with self.assertRaises(mozilib.MoziBlocked) as caught:
            self.run_send(page)
        self.assertIn("access is not active", str(caught.exception))
        mozilib.save_chat_id.assert_not_called()

    def test_a_server_error_is_not_read_as_a_block(self):
        page = PortalPage(picked="Acme Co")
        page.replies[STREAM] = (500, json.dumps({"message": "upstream down"}))
        with self.assertRaises(mozilib.MoziError) as caught:
            self.run_send(page)
        self.assertNotIsInstance(caught.exception, mozilib.MoziBlocked)
        self.assertIn("500 from chat send", str(caught.exception))
        self.assertIn("upstream down", str(caught.exception))


class Refusal(unittest.TestCase):
    def test_reads_the_message_then_the_error(self):
        self.assertEqual(mozilib.refusal(REFUSED), NOT_ACTIVE)
        self.assertEqual(mozilib.refusal(REFUSED.encode()), NOT_ACTIVE)
        self.assertEqual(mozilib.refusal('{"error": "FORBIDDEN"}'), "FORBIDDEN")

    def test_anything_else_is_empty(self):
        for raw in (None, "", b"", "<html>Forbidden</html>", "[1, 2]",
                    '{"message": "  "}', '{"statusCode": 403}'):
            self.assertEqual(mozilib.refusal(raw), "", raw)

    def test_a_long_or_multiline_reason_is_one_short_line(self):
        why = mozilib.refusal(json.dumps({"message": "a\n b " * 200}))
        self.assertNotIn("\n", why)
        self.assertLessEqual(len(why), 200)

    def test_the_http_seam_carries_the_reason(self):
        err = urllib.error.HTTPError(STREAM, 403, "Forbidden", {},
                                     io.BytesIO(REFUSED.encode()))
        self.addCleanup(err.close)
        env = {"MOZI_TOKEN": "__Secure-aegis-external.session_token=t",
               "MOZI_BASE": PORTAL}
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(mozilib, "load_dotenv"), \
             mock.patch.object(mozilib, "_pace"), \
             mock.patch.object(mozilib.urllib.request, "urlopen",
                               side_effect=err):
            with self.assertRaises(mozilib.MoziBlocked) as caught:
                mozilib._request("POST", STREAM, body=b"{}")
        self.assertIn(NOT_ACTIVE, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
