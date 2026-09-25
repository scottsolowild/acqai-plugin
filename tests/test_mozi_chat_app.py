"""A send never posts a chat to an app that did not make it.

On 2026-09-24 two sends printed "continuing Mozi chat 18711427…" and posted
that id to the portal's chat-stream. The id came from the 9/23 runs on the
older app, and the portal does not know it: a GET of its messages there
answers 404. Nothing recorded which app made a chat, so the transport could
not tell. The saved chat now names its app as <id>@<host>, in the chat-id
file and in MOZI_CHAT_ID, and a send whose route is on another host stops
before its first request. The stop says that --new starts a fresh thread
and which login goes back to the chat's app, and a login on the other app
learns the route again, so that login is a real way back.

This file is shared byte for byte with the acqai plugin repo's tests/, so
it imports only the transport pair.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mozilib  # noqa: E402
import mozi_browser  # noqa: E402

PORTAL = "https://portal.acquisition.com"
STREAM = PORTAL + "/api/sandbar/chat-stream"
CREATE = PORTAL + "/api/sandbar/chat"
LEGACY_URL = "https://ai.acquisition.com/api/chat"
PORTAL_CFG = {
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
    },
}
LEGACY_CFG = {
    "url": LEGACY_URL,
    "method": "POST",
    "message_slot": "message.aisdk",
    "stream": True,
    "body_template": {
        "id": "chat-in-the-capture",
        "message": {"id": "m1", "role": "user", "content": "hi",
                    "parts": [{"type": "text", "text": "hi"}]},
    },
}
# The chat the 9/24 sends carried, and the portal chat a later send makes.
OLD_ID = "18711427-be74-4c37-bb00-78cec2236e64"
OLD_CHAT = OLD_ID + "@ai.acquisition.com"
PORTAL_ID = "22222222-2222-4222-8222-222222222222"
PORTAL_CHAT = PORTAL_ID + "@portal.acquisition.com"
NEW_ID = "11111111-1111-4111-8111-111111111111"
LEGACY_ANSWER = b'0:"The answer"\n'
PORTAL_ANSWER = ('data: {"type":"text-delta","delta":"The answer"}\n\n'
                 'data: {"type":"finish","finishReason":"stop"}')


class ChatCase(unittest.TestCase):
    """A temp chat-id file, a clean env, and one learned route per test."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        env = {k: v for k, v in os.environ.items()
               if k not in (mozilib.CHAT_ID_ENV, "MOZI_BASE", "MOZI_SEND_OK",
                            "MOZI_TOKEN", "ACQAI_CMD")}
        self.err = io.StringIO()
        self.cfg = PORTAL_CFG
        for patch in (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(mozilib, "CHAT_ID_FILE", self.tmp / "chat-id"),
                mock.patch.object(mozilib, "CMD", "acqai"),
                mock.patch.object(mozilib, "endpoint",
                                  side_effect=lambda: self.cfg),
                mock.patch.object(mozilib, "session_expiry", return_value=None),
                mock.patch.object(mozilib, "load_dotenv"),
                mock.patch.object(mozilib, "_pace"),
                mock.patch("sys.stderr", self.err)):
            patch.start()
            self.addCleanup(patch.stop)

    def saved(self) -> str | None:
        path = mozilib.CHAT_ID_FILE
        return path.read_text(encoding="utf-8") if path.is_file() else None


class TheChatNamesItsApp(ChatCase):
    def test_a_chat_carries_the_host_of_the_route_it_was_made_on(self):
        ref = mozilib.chat_ref(OLD_ID, "https://AI.acquisition.com:443/api/chat")
        self.assertEqual(ref, OLD_CHAT)
        self.assertEqual(mozilib.split_chat(ref), (OLD_ID, "ai.acquisition.com"))

    def test_an_id_with_no_app_splits_to_no_host(self):
        self.assertEqual(mozilib.split_chat(OLD_ID), (OLD_ID, None))
        self.assertEqual(mozilib.split_chat(""), ("", None))
        self.assertEqual(mozilib.split_chat(None), ("", None))
        self.assertIsNone(mozilib.chat_ref("", STREAM))
        self.assertEqual(mozilib.chat_ref(OLD_ID, None), OLD_ID)

    def test_the_file_and_the_env_hold_the_chat_with_its_app(self):
        mozilib.save_chat_id(mozilib.chat_ref(PORTAL_ID, STREAM))
        self.assertEqual(self.saved(), PORTAL_CHAT + "\n")
        self.assertEqual(os.environ[mozilib.CHAT_ID_ENV], PORTAL_CHAT)
        self.assertEqual(mozilib.load_chat_id(), PORTAL_CHAT)


class TheStop(ChatCase):
    def test_a_chat_from_the_older_app_stops_on_the_portal(self):
        why = mozilib.chat_mismatch(OLD_CHAT, PORTAL_CFG)
        self.assertIn("Mozi chat 18711427… is from the older app "
                      "(ai.acquisition.com)", why)
        self.assertIn("the route posts to the portal (portal.acquisition.com)",
                      why)
        self.assertIn("nothing goes to Mozi", why)
        self.assertIn("--new starts a fresh thread on the portal", why)
        self.assertIn("acqai login-legacy continues this chat on the "
                      "older app", why)

    def test_a_portal_chat_stops_on_the_older_app(self):
        why = mozilib.chat_mismatch(PORTAL_CHAT, LEGACY_CFG)
        self.assertIn("is from the portal", why)
        self.assertIn("--new starts a fresh thread on the older app", why)
        self.assertIn("acqai login continues this chat on the portal",
                      why)

    def test_an_id_that_names_no_app_stops_on_either_app(self):
        for cfg in (PORTAL_CFG, LEGACY_CFG):
            why = mozilib.chat_mismatch(OLD_ID, cfg)
            self.assertIn("names no app, so nothing goes to Mozi", why)
            self.assertIn("<id>@ai.acquisition.com", why)
            self.assertIn("--new starts a fresh thread", why)

    def test_a_chat_on_the_routes_own_app_goes_on(self):
        self.assertEqual(mozilib.chat_mismatch(PORTAL_CHAT, PORTAL_CFG), "")
        self.assertEqual(mozilib.chat_for_route(PORTAL_CHAT, PORTAL_CFG),
                         PORTAL_ID)
        self.assertEqual(mozilib.chat_for_route(OLD_CHAT, LEGACY_CFG), OLD_ID)

    def test_no_chat_or_no_route_is_not_a_stop(self):
        self.assertEqual(mozilib.chat_mismatch(None, PORTAL_CFG), "")
        self.assertEqual(mozilib.chat_mismatch("", PORTAL_CFG), "")
        self.assertEqual(mozilib.chat_mismatch(OLD_CHAT, {}), "")
        self.assertIsNone(mozilib.chat_for_route("", PORTAL_CFG))

    def test_the_stop_names_the_command_the_env_gives(self):
        with mock.patch.object(mozilib, "CMD", "python3 /x/acqai.py"):
            why = mozilib.chat_mismatch(OLD_CHAT, PORTAL_CFG)
        self.assertIn("python3 /x/acqai.py login-legacy", why)


class _Loc:
    """A Playwright locator stand-in."""

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


class ChatPage:
    """/advisor past the workspace pick, chat box up. Records each fetch."""

    def __init__(self):
        self.url = PORTAL + "/advisor"
        self.frames = []
        self.fetches: list[dict] = []

    def locator(self, selector):
        return _Loc(selector == "textarea")

    def get_by_text(self, *_a, **_k):
        return _Loc(False)

    def get_by_role(self, *_a, **_k):
        return _Loc(False)

    def goto(self, url, **_k):
        self.url = url

    def set_default_timeout(self, _ms):
        pass

    def evaluate(self, _js, arg):
        self.fetches.append(arg)
        if arg["url"] == CREATE:
            return {"status": 200, "text": json.dumps({"id": NEW_ID})}
        return {"status": 200, "text": PORTAL_ANSWER}


class BrowserSend(ChatCase):
    def setUp(self):
        super().setUp()
        patch = mock.patch.object(mozi_browser, "_save_storage_state")
        patch.start()
        self.addCleanup(patch.stop)

    def send(self, page=None):
        page = page or ChatPage()
        ctx = mock.Mock()
        ctx.pages = [page]
        with mock.patch.object(mozi_browser, "_import_playwright",
                               return_value=lambda: mock.MagicMock()), \
                mock.patch.object(mozi_browser, "_context", return_value=ctx):
            return page, mozi_browser.send("What should I charge?")

    def closed(self):
        return mock.patch.object(
            mozi_browser, "_import_playwright",
            side_effect=AssertionError("the browser opened for a send "
                                       "that had to stop"))

    def test_the_9_24_send_stops_before_the_browser_opens(self):
        os.environ[mozilib.CHAT_ID_ENV] = OLD_CHAT
        with self.closed(), self.assertRaises(mozilib.MoziChatElsewhere) as caught:
            mozi_browser.send("Two corrections, then the question")
        self.assertIn("--new starts a fresh thread on the portal",
                      str(caught.exception))
        self.assertIn("login-legacy continues this chat on the older app",
                      str(caught.exception))
        self.assertEqual(mozilib.load_chat_id(), OLD_CHAT)

    def test_the_bare_id_those_sends_carried_stops_too(self):
        os.environ[mozilib.CHAT_ID_ENV] = OLD_ID
        with self.closed(), self.assertRaises(mozilib.MoziChatElsewhere) as caught:
            mozi_browser.send("Two corrections, then the question")
        self.assertIn("names no app", str(caught.exception))

    def test_a_portal_chat_goes_on_with_the_id_the_portal_knows(self):
        mozilib.save_chat_id(PORTAL_CHAT)
        page, (answer, chat) = self.send()
        self.assertEqual(answer, "The answer")
        self.assertEqual([f["url"] for f in page.fetches], [STREAM])
        self.assertEqual(page.fetches[0]["body"]["toolContext"]["chatId"],
                         PORTAL_ID)
        self.assertEqual(chat, PORTAL_CHAT)
        self.assertEqual(self.saved(), PORTAL_CHAT + "\n")

    def test_a_new_portal_chat_is_saved_with_the_portal(self):
        page, (_answer, chat) = self.send()
        self.assertEqual([f["url"] for f in page.fetches], [CREATE, STREAM])
        self.assertEqual(chat, NEW_ID + "@portal.acquisition.com")
        self.assertEqual(self.saved(), chat + "\n")


class HttpSend(ChatCase):
    def setUp(self):
        super().setUp()
        os.environ["MOZI_SEND_OK"] = "1"
        self.posts: list[dict] = []

    def post(self, _method, url, *, body=None, referer=None, **_k):
        self.posts.append({"url": url, "body": json.loads(body),
                           "referer": referer})
        return 200, LEGACY_ANSWER

    def test_the_http_seam_stops_before_its_first_request(self):
        mozilib.save_chat_id(OLD_CHAT)
        with mock.patch.object(mozilib, "_request", side_effect=AssertionError(
                "a request went out")), \
                self.assertRaises(mozilib.MoziChatElsewhere):
            mozilib.ask("What should I charge?")
        self.assertEqual(self.saved(), OLD_CHAT + "\n")

    def test_an_older_app_chat_goes_on_there_with_its_own_id(self):
        self.cfg = LEGACY_CFG
        mozilib.save_chat_id(OLD_CHAT)
        with mock.patch.object(mozilib, "_request", side_effect=self.post):
            answer, chat = mozilib.ask("And the second point?")
        self.assertEqual(answer, "The answer")
        self.assertEqual(self.posts[0]["body"]["id"], OLD_ID)
        self.assertEqual(chat, OLD_CHAT)
        self.assertEqual(self.saved(), OLD_CHAT + "\n")

    def test_a_chat_passed_back_as_ask_returns_it_goes_on(self):
        self.cfg = LEGACY_CFG
        with mock.patch.object(mozilib, "_request", side_effect=self.post):
            _answer, chat = mozilib.ask("First question")
            mozilib.clear_chat_id()
            mozilib.ask("Second question", chat_id=chat)
        self.assertEqual(self.posts[1]["body"]["id"],
                         mozilib.split_chat(chat)[0])

    def test_a_new_chat_on_the_older_app_is_saved_with_it(self):
        self.cfg = LEGACY_CFG
        with mock.patch.object(mozilib, "_request", side_effect=self.post):
            _answer, chat = mozilib.ask("What should I charge?")
        minted = self.posts[0]["body"]["id"]
        self.assertNotEqual(minted, "chat-in-the-capture")
        self.assertEqual(chat, minted + "@ai.acquisition.com")
        self.assertEqual(self.saved(), chat + "\n")


class RouteLearned(ChatCase):
    def test_a_route_on_the_other_app_is_learned_again(self):
        """login-legacy with the portal's route on file asks for one message,
        so the route, and the chat it continues, follow the older app."""
        os.environ["MOZI_BASE"] = mozilib.LEGACY_BASE
        self.assertFalse(mozi_browser._route_learned())
        self.cfg = LEGACY_CFG
        self.assertTrue(mozi_browser._route_learned())
        os.environ["MOZI_BASE"] = mozilib.PORTAL_BASE
        self.assertFalse(mozi_browser._route_learned())
        del os.environ["MOZI_BASE"]
        self.assertTrue(mozi_browser._route_learned())
        self.cfg = None
        self.assertFalse(mozi_browser._route_learned())


if __name__ == "__main__":
    unittest.main()
