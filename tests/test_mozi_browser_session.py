"""The headless Mozi session check and the sign-in steps it rides on.

This file is shared byte for byte with the acqai plugin repo's tests/, so
it imports only the transport pair.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mozilib  # noqa: E402
import mozi_browser  # noqa: E402


class LoggedInUrl(unittest.TestCase):
    def test_chat_url_counts_as_logged_in(self):
        page = mock.Mock(url="https://ai.acquisition.com/chat", frames=[])
        self.assertTrue(mozi_browser._logged_in(page))

    def test_portal_advisor_counts_as_logged_in(self):
        page = mock.Mock(
            url="https://portal.acquisition.com/advisor?chatId=x", frames=[])
        self.assertTrue(mozi_browser._logged_in(page))

    def test_chat_with_sign_in_iframe_is_not_logged_in(self):
        frame = mock.Mock(
            url="https://accounts.acquisition.com/sign-in", frames=[])
        page = mock.Mock(
            url="https://ai.acquisition.com/chat", frames=[frame])
        self.assertFalse(mozi_browser._logged_in(page))

    def test_google_or_blank_is_not_logged_in(self):
        self.assertFalse(mozi_browser._logged_in(
            mock.Mock(url="https://myaccount.google.com/", frames=[])))
        self.assertFalse(mozi_browser._logged_in(
            mock.Mock(url="about:blank", frames=[])))

    def test_sign_in_and_accounts_host_do_not(self):
        self.assertFalse(mozi_browser._logged_in(
            mock.Mock(url="https://ai.acquisition.com/sign-in", frames=[])))
        self.assertFalse(mozi_browser._logged_in(
            mock.Mock(url="https://accounts.acquisition.com/sign-in/choose",
                      frames=[])))

    def test_app_home_is_not_logged_in(self):
        self.assertFalse(mozi_browser._logged_in(
            mock.Mock(url="https://ai.acquisition.com/", frames=[])))

    def test_upgrade_interstitial_on_chat_is_not_logged_in(self):
        loc = mock.Mock()
        loc.count = mock.Mock(return_value=1)
        loc.first = loc
        loc.is_visible = mock.Mock(return_value=True)
        page = mock.Mock(url="https://ai.acquisition.com/chat", frames=[])
        page.get_by_text = mock.Mock(return_value=loc)
        self.assertTrue(mozi_browser._upgrade_shown(page))
        self.assertFalse(mozi_browser._logged_in(page))

    def test_dismiss_upgrade_clicks_continue_not_google(self):
        clicked = []

        class Loc:
            def __init__(self, visible=True, n=1):
                self._n = n
                self._visible = visible
                self.first = self

            def count(self):
                return self._n

            def is_visible(self):
                return self._visible

            def click(self):
                clicked.append("continue")

        heading = Loc()
        continue_btn = Loc()
        page = mock.Mock(url="https://ai.acquisition.com/sign-in", frames=[])
        page.get_by_text = mock.Mock(return_value=heading)

        def by_role(role, name=None, **_k):
            if role == "button" and getattr(name, "pattern", "") == r"^\s*continue\s*$":
                return continue_btn
            return Loc(visible=False, n=0)

        page.get_by_role = mock.Mock(side_effect=by_role)
        self.assertTrue(mozi_browser.dismiss_upgrade(page))
        self.assertEqual(clicked, ["continue"])

    def test_dismiss_upgrade_skips_continue_when_google_is_visible(self):
        clicked = []

        class Loc:
            def __init__(self, visible=True, n=1, label=""):
                self._n = n
                self._visible = visible
                self.first = self
                self._label = label

            def count(self):
                return self._n

            def is_visible(self):
                return self._visible

            def click(self):
                clicked.append(self._label)

        heading = Loc()
        google_btn = Loc(label="google")
        continue_btn = Loc(label="continue")
        page = mock.Mock(url="https://accounts.acquisition.com/sign-in",
                         frames=[])
        page.get_by_text = mock.Mock(return_value=heading)

        def by_role(role, name=None, **_k):
            pat = getattr(name, "pattern", "")
            if role == "button" and pat == r"^\s*(continue with )?google\s*$":
                return google_btn
            if role == "button" and pat == r"^\s*continue\s*$":
                return continue_btn
            return Loc(visible=False, n=0)

        page.get_by_role = mock.Mock(side_effect=by_role)
        self.assertFalse(mozi_browser.dismiss_upgrade(page))
        self.assertEqual(clicked, [])

    def test_google_oauth_error_url_is_detected(self):
        page = mock.Mock(frames=[])
        page.url = (
            "https://accounts.google.com/signin/oauth/error"
            "?authError=Cg5kZWxldGVkX2NsaWVudBIdVGhlIE9BdXRoIGNsaWVud"
            "CB3YXMgZGVsZXRlZC4gkQM&flowName=GeneralOAuthFlow"
            "&client_id=1027589831402-4d1jd5st0r9rsrvqi08qvltj7mqk4nl5"
            ".apps.googleusercontent.com")
        self.assertTrue(mozi_browser._google_oauth_failed(page))

    def test_clerk_sign_in_is_not_a_google_oauth_failure(self):
        page = mock.Mock(
            url="https://accounts.acquisition.com/sign-in", frames=[])
        page.get_by_text = mock.Mock(return_value=mock.Mock(
            count=mock.Mock(return_value=0)))
        self.assertFalse(mozi_browser._google_oauth_failed(page))

    def test_recover_google_oauth_returns_to_chat_and_focuses_email(self):
        clicked = []

        class Loc:
            def __init__(self, visible=True, n=1):
                self._n = n
                self._visible = visible
                self.first = self

            def count(self):
                return self._n

            def is_visible(self):
                return self._visible

            def click(self):
                clicked.append("email")

        page = mock.Mock(frames=[])
        page.url = (
            "https://accounts.google.com/signin/oauth/error"
            "?authError=Cg5kZWxldGVkX2NsaWVud")
        page.goto = mock.Mock()
        page.get_by_text = mock.Mock(return_value=Loc(visible=False, n=0))
        page.get_by_role = mock.Mock(return_value=Loc())
        ctx = mock.Mock()
        ctx.pages = [page]
        ctx.route = mock.Mock()
        self.assertTrue(mozi_browser.recover_google_oauth(page, ctx))
        page.goto.assert_called_once()
        self.assertIn(mozilib.page_path(), page.goto.call_args.args[0])
        ctx.route.assert_called_once()
        self.assertEqual(clicked, ["email"])

    def test_recover_google_oauth_is_noop_on_sign_in(self):
        page = mock.Mock(
            url="https://accounts.acquisition.com/sign-in", frames=[])
        page.goto = mock.Mock()
        page.get_by_text = mock.Mock(return_value=mock.Mock(
            count=mock.Mock(return_value=0)))
        ctx = mock.Mock()
        ctx.pages = [page]
        ctx.route = mock.Mock()
        self.assertFalse(mozi_browser.recover_google_oauth(page, ctx))
        page.goto.assert_not_called()
        ctx.route.assert_not_called()

    def test_org_choose_is_detected(self):
        self.assertTrue(mozi_browser._on_org_choose(mock.Mock(
            url="https://accounts.acquisition.com/sign-in/choose",
            frames=[])))
        self.assertFalse(mozi_browser._on_org_choose(mock.Mock(
            url="https://ai.acquisition.com/chat", frames=[])))

    def test_org_choose_in_iframe_is_detected(self):
        frame = mock.Mock(
            url="https://accounts.acquisition.com/sign-in/choose",
            frames=[])
        page = mock.Mock(
            url="https://ai.acquisition.com/sign-in", frames=[frame])
        self.assertTrue(mozi_browser._on_org_choose(page))


class CompanyName(unittest.TestCase):
    def test_default_is_empty_with_no_env_and_no_config(self):
        """The pair ships as a public give, so the file carries no company.
        A wrapper that wants a default sets MOZI_COMPANY itself."""
        with mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch.object(mozi_browser.mozilib, "config", return_value={}):
            os.environ.pop("MOZI_COMPANY", None)
            os.environ.pop("CLAUDE_PLUGIN_OPTION_MOZI_COMPANY", None)
            self.assertEqual(mozi_browser.company_name(), "")

    def test_env_override(self):
        with mock.patch.dict(os.environ, {"MOZI_COMPANY": "Other Co"}):
            self.assertEqual(mozi_browser.company_name(), "Other Co")


class SelectCompany(unittest.TestCase):
    def test_clicks_exact_button_name(self):
        clicked = []

        class Loc:
            def __init__(self, visible=True, n=1):
                self._visible = visible
                self._n = n
                self.first = self

            def count(self):
                return self._n

            def is_visible(self):
                return self._visible

            def click(self):
                clicked.append("click")

        page = mock.Mock(frames=[])
        page.get_by_role = mock.Mock(return_value=Loc())
        page.get_by_text = mock.Mock(return_value=Loc(visible=False, n=0))
        self.assertTrue(mozi_browser.select_company(page, "Acme Co"))
        self.assertEqual(clicked, ["click"])
        page.get_by_role.assert_any_call(
            "button", name="Acme Co", exact=True)

    def test_clicks_company_inside_iframe(self):
        clicked = []

        class Loc:
            def __init__(self, visible=True, n=1):
                self._visible = visible
                self._n = n
                self.first = self

            def count(self):
                return self._n

            def is_visible(self):
                return self._visible

            def click(self):
                clicked.append("frame")

        empty = Loc(visible=False, n=0)
        page = mock.Mock(url="https://ai.acquisition.com/sign-in")
        page.get_by_role = mock.Mock(return_value=empty)
        page.get_by_text = mock.Mock(return_value=empty)
        frame = mock.Mock(
            url="https://accounts.acquisition.com/sign-in/choose")
        frame.get_by_role = mock.Mock(return_value=Loc())
        frame.get_by_text = mock.Mock(return_value=empty)
        page.frames = [frame]
        self.assertTrue(mozi_browser.select_company(page, "Acme Co"))
        self.assertEqual(clicked, ["frame"])

    def test_returns_false_when_nothing_matches(self):
        empty = mock.Mock()
        empty.first = empty
        empty.count = mock.Mock(return_value=0)
        empty.is_visible = mock.Mock(return_value=False)
        page = mock.Mock(frames=[])
        page.get_by_role = mock.Mock(return_value=empty)
        page.get_by_text = mock.Mock(return_value=empty)
        self.assertFalse(mozi_browser.select_company(page, "Missing Co"))


class EnsureCompany(unittest.TestCase):
    def test_noop_when_already_logged_in(self):
        page = mock.Mock(url="https://ai.acquisition.com/chat")
        with mock.patch.object(mozi_browser, "select_company") as sel:
            self.assertTrue(mozi_browser._ensure_company(page))
        sel.assert_not_called()

    def test_clicks_when_on_choose_and_waits_for_chat(self):
        page = mock.Mock()
        page.url = "https://accounts.acquisition.com/sign-in/choose"

        def after_click(*_a, **_k):
            page.url = "https://ai.acquisition.com/chat"
            return True

        with mock.patch.object(mozi_browser, "select_company",
                               side_effect=after_click), \
             mock.patch.object(mozi_browser.time, "sleep"):
            self.assertTrue(mozi_browser._ensure_company(
                page, company="Acme Co", settle_ms=100))

    def test_no_company_on_choose_leaves_the_click_to_the_member(self):
        """With nothing to click, the picker is reported once, never
        clicked blind, and the caller reads it as not past auth."""
        page = mock.Mock(frames=[])
        page.url = "https://accounts.acquisition.com/sign-in/choose"
        mozi_browser._company_hint_shown = False
        with mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch.object(mozi_browser.mozilib, "config", return_value={}), \
             mock.patch.object(mozi_browser, "select_company") as sel:
            os.environ.pop("MOZI_COMPANY", None)
            os.environ.pop("CLAUDE_PLUGIN_OPTION_MOZI_COMPANY", None)
            self.assertFalse(mozi_browser._ensure_company(page, settle_ms=100))
            self.assertFalse(mozi_browser._ensure_company(page, settle_ms=100))
        sel.assert_not_called()
        self.assertTrue(mozi_browser._company_hint_shown)


def _session_ctx(page):
    ctx = mock.Mock()
    ctx.pages = [page]
    ctx.close = mock.Mock()
    ctx.cookies = mock.Mock(return_value=[
        {"name": "__session", "value": "tok", "domain": "ai.acquisition.com"},
        {"name": "__client_uat", "value": "1788993848",
         "domain": ".acquisition.com"},
    ])
    ctx.storage_state = mock.Mock()
    ctx.add_cookies = mock.Mock()
    return ctx


class ClerkSignedIn(unittest.TestCase):
    def test_true_when_client_uat_is_a_timestamp(self):
        ctx = mock.Mock()
        ctx.cookies = mock.Mock(return_value=[
            {"name": "__client_uat", "value": "1788993848"},
        ])
        self.assertTrue(mozi_browser._clerk_signed_in(ctx))

    def test_false_when_client_uat_is_zero(self):
        ctx = mock.Mock()
        ctx.cookies = mock.Mock(return_value=[
            {"name": "__session", "value": "tok"},
            {"name": "__client_uat", "value": "0"},
        ])
        self.assertFalse(mozi_browser._clerk_signed_in(ctx))

    def test_false_when_cookies_are_missing(self):
        ctx = mock.Mock()
        ctx.cookies = mock.Mock(return_value=[])
        with mock.patch.object(mozi_browser, "_state_cookies", return_value=[]):
            self.assertFalse(mozi_browser._clerk_signed_in(ctx))

    def test_true_when_portal_aegis_token_is_present(self):
        ctx = mock.Mock()
        ctx.cookies = mock.Mock(return_value=[
            {"name": "__Secure-aegis-external.session_token", "value": "tok"},
            {"name": "__client_uat", "value": "0"},
        ])
        self.assertTrue(mozi_browser._auth_signed_in(ctx))

    def test_session_ready_needs_uat_not_just_chat_url(self):
        page = mock.Mock(url="https://ai.acquisition.com/chat", frames=[])
        page.locator = mock.Mock(side_effect=Exception("no box"))
        page.get_by_role = mock.Mock(side_effect=Exception("no box"))
        ctx = mock.Mock()
        ctx.cookies = mock.Mock(return_value=[
            {"name": "__session", "value": "tok"},
            {"name": "__client_uat", "value": "0"},
        ])
        self.assertFalse(mozi_browser._session_ready(page, ctx))

    def test_session_ready_when_chat_box_is_visible(self):
        loc = mock.Mock()
        loc.count = mock.Mock(return_value=1)
        loc.first = loc
        loc.is_visible = mock.Mock(return_value=True)
        none = mock.Mock()
        none.count = mock.Mock(return_value=0)
        none.first = none
        page = mock.Mock(url="https://ai.acquisition.com/chat", frames=[])
        # Only the chat box is on the page; the workspace-picker lookup
        # finds nothing.
        page.locator = mock.Mock(
            side_effect=lambda sel: loc if sel == "textarea" else none)
        ctx = mock.Mock()
        ctx.cookies = mock.Mock(return_value=[
            {"name": "__client_uat", "value": "0"},
        ])
        self.assertTrue(mozi_browser._session_ready(page, ctx))


class ContextLaunch(unittest.TestCase):
    def test_headless_uses_full_chromium_without_extensions(self):
        captured = {}

        class FakeChromium:
            def launch_persistent_context(self, path, **kwargs):
                captured["kwargs"] = kwargs
                return mock.Mock()

        pw = mock.Mock()
        pw.chromium = FakeChromium()
        with mock.patch.dict(os.environ, {
                "OP_CACHE": "false",
                "OP_BIOMETRIC_UNLOCK_ENABLED": "false",
                "OP_LOAD_DESKTOP_APP_SETTINGS": "false",
        }), mock.patch.object(mozi_browser, "_apply_storage_state"):
            mozi_browser._context(pw, headless=True)
        args = captured["kwargs"]["args"]
        self.assertFalse(captured["kwargs"]["headless"])
        self.assertIn("--headless=new", args)
        self.assertIn("--disable-extensions", args)
        env = captured["kwargs"]["env"]
        self.assertEqual(env["OP_CACHE"], "false")
        self.assertEqual(env["OP_BIOMETRIC_UNLOCK_ENABLED"], "false")
        self.assertEqual(env["OP_LOAD_DESKTOP_APP_SETTINGS"], "false")

    def test_headed_login_keeps_extensions(self):
        captured = {}

        class FakeChromium:
            def launch_persistent_context(self, path, **kwargs):
                captured["kwargs"] = kwargs
                return mock.Mock()

        pw = mock.Mock()
        pw.chromium = FakeChromium()
        mozi_browser._context(pw, headless=False)
        self.assertFalse(captured["kwargs"]["headless"])
        self.assertNotIn("args", captured["kwargs"])

    def test_profile_in_use_is_browser_not_ready(self):
        class FakeChromium:
            def launch_persistent_context(self, path, **kwargs):
                raise RuntimeError("Opening in existing browser session.")

        pw = mock.Mock()
        pw.chromium = FakeChromium()
        with self.assertRaises(mozi_browser.BrowserNotReady) as caught:
            mozi_browser._context(pw, headless=False)
        self.assertIn("already open", str(caught.exception))


class EnterAndPages(unittest.TestCase):
    def test_enter_ignored_when_stdin_is_not_a_tty(self):
        stdin = mock.Mock()
        stdin.isatty.return_value = False
        with mock.patch.object(mozi_browser.sys, "stdin", stdin):
            self.assertFalse(mozi_browser._enter_pressed())

    def test_enter_when_stdin_is_ready(self):
        stdin = mock.Mock()
        stdin.isatty.return_value = True
        stdin.readline = mock.Mock(return_value="\n")
        with mock.patch.object(mozi_browser.sys, "stdin", stdin), \
             mock.patch.object(mozi_browser.select, "select",
                               return_value=([stdin], [], [])):
            self.assertTrue(mozi_browser._enter_pressed())
        stdin.readline.assert_called_once()

    def test_active_page_prefers_a_logged_in_tab(self):
        signin = mock.Mock(url="https://accounts.google.com/", frames=[])
        chat = mock.Mock(url="https://ai.acquisition.com/chat", frames=[])
        ctx = mock.Mock(pages=[signin, chat])
        self.assertIs(mozi_browser._active_page(ctx, signin), chat)


class RequireSession(unittest.TestCase):
    def test_raises_when_profile_lands_on_sign_in(self):
        page = mock.Mock(frames=[])
        page.url = "https://accounts.acquisition.com/sign-in"
        page.goto = mock.Mock()
        page.set_default_timeout = mock.Mock()
        ctx = mock.Mock()
        ctx.pages = [page]
        ctx.close = mock.Mock()
        ctx.cookies = mock.Mock(return_value=[])
        ctx.storage_state = mock.Mock()
        pw = mock.MagicMock()
        clock = {"t": 0.0}

        with mock.patch.object(mozi_browser, "_import_playwright",
                               return_value=lambda: pw), \
             mock.patch.object(mozi_browser, "_context", return_value=ctx), \
             mock.patch.object(mozi_browser, "_ensure_company",
                               return_value=False), \
             mock.patch.object(mozi_browser, "_state_cookies",
                               return_value=[]), \
             mock.patch.object(mozi_browser.time, "monotonic",
                               side_effect=lambda: clock["t"]), \
             mock.patch.object(mozi_browser.time, "sleep",
                               side_effect=lambda s: clock.__setitem__(
                                   "t", clock["t"] + s)):
            with self.assertRaises(mozi_browser.BrowserNotReady) as caught:
                mozi_browser.require_session(timeout=1)
        self.assertIn("not logged in", str(caught.exception))
        self.assertIn("accounts.acquisition.com/sign-in", str(caught.exception))
        ctx.close.assert_called()

    def test_ok_when_chat_loads(self):
        page = mock.Mock(frames=[])
        page.url = "https://ai.acquisition.com/chat"
        page.goto = mock.Mock()
        page.set_default_timeout = mock.Mock()
        ctx = _session_ctx(page)
        pw = mock.MagicMock()

        with mock.patch.object(mozi_browser, "_import_playwright",
                               return_value=lambda: pw), \
             mock.patch.object(mozi_browser, "_context", return_value=ctx):
            mozi_browser.require_session(timeout=1)
        page.goto.assert_called_once()
        ctx.close.assert_called()
        ctx.storage_state.assert_called()

    def test_org_choose_is_resolved_via_company_click(self):
        page = mock.Mock(frames=[])
        page.url = "https://accounts.acquisition.com/sign-in/choose"
        page.goto = mock.Mock()
        page.set_default_timeout = mock.Mock()
        ctx = _session_ctx(page)
        pw = mock.MagicMock()

        def ensure(*_a, **_k):
            page.url = "https://ai.acquisition.com/chat"
            return True

        with mock.patch.object(mozi_browser, "_import_playwright",
                               return_value=lambda: pw), \
             mock.patch.object(mozi_browser, "_context", return_value=ctx), \
             mock.patch.object(mozi_browser, "_ensure_company",
                               side_effect=ensure) as ensure_m:
            mozi_browser.require_session(timeout=1)
        ensure_m.assert_called()
        ctx.close.assert_called()
