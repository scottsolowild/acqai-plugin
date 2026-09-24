"""Playwright transport for Mozi (ACQ AI): drive the member's own logged-in
browser and send via an in-page fetch to the discovered chat route.

Supports both apps (MOZI_BASE picks which login opens):
  Portal  portal.acquisition.com/advisor  Aegis; sandbar chat + chat-stream
  Legacy  ai.acquisition.com/chat         Clerk; /api/chat

Why this beats the raw HTTP seam: the browser holds the live session and
refreshes it itself, so there is nothing to capture and no clock to race.
The send runs as a same-origin fetch inside the page, so its cookies are the
browser's own. One login persists in a local profile (both hosts can share it;
cookies are domain-scoped).

  login            headed browser to sign in once (persists the session
                   in the profile and as decrypted storage-state JSON); the
                   member's first message there teaches the chat route;
                   email OTP on the sign-in form (Google is skipped when their
                   OAuth client is deleted); clicks MOZI_COMPANY on Clerk's
                   org picker or the portal's workspace picker, or leaves the
                   click to the member; Enter saves at any time
  require_session  headless chat-page check on the same Chromium binary as
                   login (not chrome-headless-shell); ask uses this before
                   Claude; also clicks the company when Clerk lands on /choose
                   or the portal asks which workspace
  send(question)   launch, wait for the chat box, in-page fetch, return the
                   answer; keeps a chat id so a dialogue can continue in the
                   same conversation

Runs on the member's own machine, where the browser has normal network. The
setup step (./setup.sh in the notes repo, `setup` in the plugin) installs
Playwright and its Chromium into a dedicated venv, and the wrapper uses that
venv's python. The fence is unchanged: a send still needs MOZI_SEND_OK; the
browser removes the token pain, not the consent. The state dir, the command
name in messages, and the company come from the environment (mozilib.STATE_DIR,
mozilib.CMD, MOZI_COMPANY), so this file ships as a public give unchanged.
"""
from __future__ import annotations

import json
import os
import re
import select
import sys
import time
from pathlib import Path

import mozilib

PROFILE_DIR = mozilib.STATE_DIR / "browser-profile"
# Decrypted Clerk cookies from the headed login. Headless chrome-headless-shell
# cannot read the persistent-profile cookie DB that headed Chromium wrote, so
# login also dumps this JSON (gitignored with review/mozi/) and headless loads it.
STATE_FILE = PROFILE_DIR.parent / "storage-state.json"
# Clerk's /sign-in/choose lists every org the member belongs to. Mozi chats
# are scoped to one; the wrong pick (or "All Companies" after a later UI
# change) answers against the wrong context. MOZI_COMPANY names the one to
# click (the notes wrapper exports it; the plugin saves it at setup). With no
# name, the member clicks it in the window, and the headless paths say so.
DEFAULT_COMPANY = ""
_company_hint_shown = False
_company_missed: set[str] = set()

# Playwright's headless=True picks chrome-headless-shell, a different binary
# than headed Chromium. Clerk cookies written during login live in that
# headed profile and the shell does not present them, so /chat bounces to
# sign-in. Keep headless=False so we get the Chromium executable, then pass
# --headless=new so no window opens on a check or send.
_HEADLESS_ARGS = (
    "--headless=new",
    "--disable-extensions",
    "--disable-component-extensions-with-background-pages",
)


def company_name() -> str:
    """MOZI_COMPANY, then the plugin's install option, then its saved config,
    then DEFAULT_COMPANY (empty: this file ships as a public give, so no one's
    company is written into it)."""
    for val in (os.environ.get("MOZI_COMPANY"),
                os.environ.get("CLAUDE_PLUGIN_OPTION_MOZI_COMPANY"),
                mozilib.config().get("company")):
        if isinstance(val, str) and val.strip():
            return val.strip()
    return DEFAULT_COMPANY

_FETCH_JS = """
async ({url, body}) => {
  const nonce = (document.cookie.match(/acq_ui_nonce=([^;]+)/) || [])[1] || '';
  const headers = {'content-type': 'application/json',
                   'accept': 'text/event-stream, application/json, text/plain'};
  if (nonce) headers['x-ui-nonce'] = nonce;
  const r = await fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    credentials: 'include',
  });
  return {status: r.status, text: await r.text()};
}
"""


class BrowserNotReady(mozilib.MoziError):
    """Playwright missing, or the profile is not logged in."""


def _op_quiet_env() -> dict[str, str]:
    """Keep Chromium/Python off 1Password.app (macOS TCC Apple Events)."""
    env = os.environ.copy()
    env.setdefault("OP_CACHE", "false")
    env.setdefault("OP_BIOMETRIC_UNLOCK_ENABLED", "false")
    env.setdefault("OP_LOAD_DESKTOP_APP_SETTINGS", "false")
    return env


def _save_storage_state(ctx) -> None:
    """Write decrypted cookies so a later headless run can inject them."""
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=str(STATE_FILE))
    except Exception:
        return


def _apply_storage_state(ctx) -> None:
    """Load cookies saved at login into this context. No-op when none exist."""
    if not STATE_FILE.is_file():
        return
    try:
        ctx.add_cookies(_state_cookies())
    except Exception:
        return


def _state_cookies() -> list[dict]:
    try:
        data = json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return []
    cookies = data.get("cookies") if isinstance(data, dict) else None
    return cookies if isinstance(cookies, list) else []


def _cookie_list(ctx) -> list[dict]:
    try:
        cookies = list(ctx.cookies() or [])
    except Exception:
        cookies = []
    if cookies:
        return [c for c in cookies if isinstance(c, dict)]
    return [c for c in _state_cookies() if isinstance(c, dict)]


def _auth_signed_in(ctx) -> bool:
    """True when portal Aegis or legacy Clerk has a live session cookie.

    Legacy Clerk: `__session` alone is not enough (it can flash during Google
    auth while `__client_uat` stays `0`). A non-zero `__client_uat` is the
    durable Clerk signal. Portal: `__Secure-aegis-external.session_token`.
    """
    for cookie in _cookie_list(ctx):
        name = str(cookie.get("name") or "")
        val = str(cookie.get("value") or "").strip()
        if not val:
            continue
        if name == mozilib._AEGIS_TOKEN or name.endswith(
                "aegis-external.session_token"):
            return True
        if name.startswith("__client_uat") and val != "0":
            return True
    return False


# Back-compat name used in older call sites / tests.
_clerk_signed_in = _auth_signed_in


def _close(ctx, *, save: bool = False) -> None:
    if save:
        _save_storage_state(ctx)
    ctx.close()


def _context(playwright, *, headless: bool):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    kwargs = {
        # Always the Chromium executable, never chrome-headless-shell.
        "headless": False,
        "user_agent": mozilib.USER_AGENT,
        "env": _op_quiet_env(),
    }
    # Headless sends/checks do not need 1Password's Chromium helper. Loading it
    # is what named python3.14 in "would like to access data from other apps".
    # Headed login keeps extensions so a one-time Google fill still works.
    if headless:
        kwargs["args"] = list(_HEADLESS_ARGS)
    exe = os.environ.get("MOZI_CHROMIUM")  # optional override; default finds it
    if exe:
        kwargs["executable_path"] = exe
    try:
        ctx = playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR), **kwargs)
    except Exception as err:
        msg = str(err).lower()
        if "already in use" in msg or "existing browser session" in msg:
            raise BrowserNotReady(
                "the Mozi browser profile is already open; close that "
                f"Chromium window, then run {mozilib.CMD} login again"
            ) from err
        raise
    if headless:
        _apply_storage_state(ctx)
    return ctx


def _import_playwright():
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError as err:
        raise BrowserNotReady(
            f"Playwright is not installed. Run {mozilib.SETUP_CMD} (it installs "
            "it into a dedicated venv), or run this under that venv's python.") from err


def _scopes(page):
    """The page plus any iframes. Clerk's org picker often lives in a frame
    on accounts.acquisition.com while the top URL stays /sign-in."""
    out = [page]
    frames = getattr(page, "frames", None)
    if isinstance(frames, (list, tuple)):
        for frame in frames:
            if frame is not None and frame not in out:
                out.append(frame)
    return out


def _urls(page) -> list[str]:
    found = []
    for scope in _scopes(page):
        try:
            url = scope.url or ""
        except Exception:
            url = ""
        if url:
            found.append(url)
    return found or [getattr(page, "url", "") or ""]


def _auth_url(url: str) -> bool:
    low = (url or "").lower()
    return "sign-in" in low or "accounts.acquisition.com" in low


def _chat_url(url: str) -> bool:
    low = (url or "").lower()
    if "sign-in" in low:
        return False
    if "portal.acquisition.com" in low and "/advisor" in low:
        return True
    if "ai.acquisition.com" in low and "/chat" in low:
        return True
    return False


# Clerk/ACQ interstitial between Google and /chat. A /chat URL with this copy
# still up is not a session: login used to save and close on it.
_UPGRADE_RX = re.compile(
    r"we['’]?re upgrading how you sign in|upgrading how you sign in",
    re.I,
)
_UPGRADE_CONTINUE_RX = (
    re.compile(r"^\s*continue\s*$", re.I),
    re.compile(r"^\s*got it\s*$", re.I),
    re.compile(r"^\s*next\s*$", re.I),
    re.compile(r"continue to (the )?new sign[- ]in", re.I),
)
# Clerk's sign-in form has a Continue next to Google. A Continue click
# there starts Google OAuth. Acquisition.com's Google client currently
# 401s as deleted_client (client 1027589831402-4d1jd5st0r9rsrvqi08qvltj7mqk4nl5).
_GOOGLE_BTN_RX = re.compile(r"^\s*(continue with )?google\s*$", re.I)
_GOOGLE_DEAD_RX = re.compile(
    r"the oauth client was deleted|deleted_client", re.I)
_EMAIL_BOX_RX = re.compile(r"email", re.I)


def _text_shown(page, pattern) -> bool:
    rx = pattern if hasattr(pattern, "search") else re.compile(pattern, re.I)
    for scope in _scopes(page):
        try:
            loc = scope.get_by_text(rx)
            n = loc.count()
            if not isinstance(n, int) or n < 1:
                continue
            try:
                if loc.first.is_visible() is True:
                    return True
            except Exception:
                return True
        except Exception:
            continue
    return False


def _upgrade_shown(page) -> bool:
    return _text_shown(page, _UPGRADE_RX)


def _google_button_shown(page) -> bool:
    """True when Clerk's Google button is on the identifier form."""
    for scope in _scopes(page):
        try:
            loc = scope.get_by_role("button", name=_GOOGLE_BTN_RX)
        except Exception:
            continue
        if _clickable(loc):
            return True
    return False


def _google_oauth_failed(page) -> bool:
    """True when Google rejected Clerk's OAuth client (deleted_client)."""
    for url in _urls(page):
        low = (url or "").lower()
        if "accounts.google.com" not in low:
            continue
        if "/signin/oauth/error" in low or "deleted_client" in low:
            return True
    return _text_shown(page, _GOOGLE_DEAD_RX)


def _google_oauth_failed_anywhere(ctx, page) -> bool:
    pages = []
    try:
        pages = [p for p in (ctx.pages or []) if p is not None]
    except Exception:
        pages = []
    if page is not None and page not in pages:
        pages = [page] + pages
    return any(_google_oauth_failed(p) for p in pages)


def _abort_google_oauth(ctx) -> None:
    """Stop further Google OAuth/One Tap navigations for this login."""
    def abort_google(route):
        try:
            route.abort()
        except Exception:
            try:
                route.continue_()
            except Exception:
                pass
    try:
        ctx.route("https://accounts.google.com/**", abort_google)
    except Exception:
        return


def focus_email_box(page) -> bool:
    """Click Clerk's email field so OTP is the path, not Google."""
    for scope in _scopes(page):
        try:
            loc = scope.get_by_role("textbox", name=_EMAIL_BOX_RX)
        except Exception:
            continue
        if not _clickable(loc):
            continue
        try:
            loc.first.click()
            return True
        except Exception:
            continue
    return False


def recover_google_oauth(page, ctx) -> bool:
    """Leave a deleted-client Google error and return to email OTP.

    True when a Google OAuth error was found and handled. Installs a
    route abort so One Tap cannot bounce back to the same error.
    """
    if not _google_oauth_failed_anywhere(ctx, page):
        return False
    print("acqai: Google sign-in failed (their OAuth client was deleted). "
          "Use the email box (OTP).",
          file=sys.stderr, flush=True)
    _abort_google_oauth(ctx)
    try:
        page.goto(mozilib.chat_page_url(), wait_until="domcontentloaded")
    except Exception:
        pass
    focus_email_box(page)
    return True


def dismiss_upgrade(page) -> bool:
    """Click Continue on the sign-in upgrade interstitial. Not Google."""
    if not _upgrade_shown(page):
        return False
    if _google_button_shown(page):
        return False
    print("acqai: sign-in upgrade page; clicking through…",
          file=sys.stderr, flush=True)
    for scope in _scopes(page):
        for role in ("button", "link"):
            for rx in _UPGRADE_CONTINUE_RX:
                try:
                    loc = scope.get_by_role(role, name=rx)
                    if not _clickable(loc):
                        continue
                    loc.first.click()
                    return True
                except Exception:
                    continue
    return False


# The portal's workspace gate. A session with no workspace picked draws
# "Choose a workspace" at /advisor, one button per workspace, and every
# /api/sandbar call answers 403 ("ACQ AI and Command Center access is not
# active.") until one is picked. The URL stays /advisor the whole time.
_WORKSPACE_BTN = "button[data-aegis-org-id]"


def _workspace_picker_shown(page) -> bool:
    """True when the portal asks which workspace to use."""
    for scope in _scopes(page):
        try:
            loc = scope.locator(_WORKSPACE_BTN)
        except Exception:
            continue
        if _clickable(loc):
            return True
    return False


def _portal_page(page) -> bool:
    return any("portal.acquisition.com" in (u or "").lower()
               for u in _urls(page))


def _logged_in(page) -> bool:
    """On the chat page, past Clerk sign-in, the org or workspace picker, and
    the upgrade page.

    Clerk often keeps accounts.acquisition.com in an iframe after the top
    URL has already moved to /chat. A top-URL-only check then reports
    success, login closes, and the org was never picked. The upgrade
    interstitial can sit on /chat the same way, and the portal's workspace
    picker sits on /advisor.
    """
    if _upgrade_shown(page):
        return False
    if _workspace_picker_shown(page):
        return False
    urls = _urls(page)
    if any(_auth_url(u) for u in urls):
        return False
    return any(_chat_url(u) for u in urls)


def _chat_composer(page) -> bool:
    """True when the Mozi chat box is on the page (session is usable)."""
    for scope in _scopes(page):
        for getter in (
                lambda s: s.locator("textarea"),
                lambda s: s.get_by_role("textbox"),
        ):
            try:
                loc = getter(scope)
                n = loc.count()
                if not isinstance(n, int) or n < 1:
                    continue
                if loc.first.is_visible() is True:
                    return True
            except Exception:
                continue
    return False


def _session_ready(page, ctx) -> bool:
    """Past auth in every frame, with Clerk's handshake or a live chat box.

    On the portal only the chat box counts. Its session cookie is set while
    the workspace picker is still up, and the picker draws after the page
    loads, so a cookie check passes before anyone has picked.
    """
    if not _logged_in(page):
        return False
    if _chat_composer(page):
        return True
    return _clerk_signed_in(ctx) and not _portal_page(page)


def _await_session(page, ctx, timeout: float) -> bool:
    """Poll until the page is a usable chat, or the timeout passes.

    Clicks through the sign-in upgrade page and the company or workspace pick
    on the way. A send posts only after this, because the portal answers 403
    to every chat call from a session with no workspace picked.
    """
    deadline = time.monotonic() + max(1, timeout)
    while time.monotonic() < deadline:
        dismiss_upgrade(page)
        _ensure_company(page)
        if _session_ready(page, ctx):
            return True
        time.sleep(0.25)
    return False


def _enter_pressed() -> bool:
    """True when Enter is waiting on stdin. Non-blocking. TTY only."""
    try:
        stdin = sys.stdin
        if not stdin.isatty():
            return False
        ready, _, _ = select.select([stdin], [], [], 0)
        if not ready:
            return False
        stdin.readline()
        return True
    except Exception:
        return False


def _active_page(ctx, fallback):
    """The open page that looks logged in, else the newest, else fallback."""
    pages = []
    try:
        pages = [p for p in (ctx.pages or []) if p is not None]
    except Exception:
        pages = []
    if fallback is not None and fallback not in pages:
        pages = [fallback] + pages
    for page in pages:
        try:
            if _logged_in(page):
                return page
        except Exception:
            continue
    return pages[-1] if pages else fallback


def _on_org_choose(page, company: str | None = None) -> bool:
    """Clerk's organization picker after email OTP (or Microsoft), or the
    portal's workspace picker.

    Looks at every frame: the picker is often an iframe, and the path is
    not always `/sign-in/choose`. Visible company text counts too.
    """
    name = (company or company_name()).strip()
    if _workspace_picker_shown(page):
        return True
    for url in _urls(page):
        low = url.lower()
        if "accounts.acquisition.com" in low and (
                "/choose" in low or "/organization" in low):
            return True
    return bool(name) and _company_shown(page, name)


def _company_locs(scope, name: str):
    locs = []
    # The portal's workspace button holds a monogram, the name, and "Select
    # →", so its accessible name is "SS Scott Solo Wild Select →" and none of
    # the role lookups below match it. Find it by the name's own span.
    try:
        locs.append(scope.locator(_WORKSPACE_BTN).filter(
            has=scope.get_by_text(
                re.compile(rf"^\s*{re.escape(name)}\s*$", re.I))))
    except Exception:
        pass
    for role in ("button", "option", "link", "menuitem"):
        try:
            locs.append(scope.get_by_role(role, name=name, exact=True))
        except Exception:
            pass
    try:
        locs.append(scope.get_by_role(
            "button", name=re.compile(rf"^\s*{re.escape(name)}\b", re.I)))
    except Exception:
        pass
    try:
        locs.append(scope.get_by_text(name, exact=True))
    except Exception:
        pass
    return locs


def _clickable(loc) -> bool:
    try:
        target = loc.first
        n = target.count()
        if not isinstance(n, int) or n < 1:
            return False
        return target.is_visible() is True
    except Exception:
        return False


def _company_shown(page, name: str) -> bool:
    for scope in _scopes(page):
        for loc in _company_locs(scope, name):
            if _clickable(loc):
                return True
    return False


def select_company(page, company: str | None = None) -> bool:
    """Click the named org on Clerk's choose page. True when a click landed.

    Searches iframes. Clerk renders each org as a button, option, or labeled
    row; we try role first, then exact text.
    """
    name = (company or company_name()).strip()
    if not name:
        return False
    for scope in _scopes(page):
        for loc in _company_locs(scope, name):
            try:
                if not _clickable(loc):
                    continue
                loc.first.click()
                return True
            except Exception:
                continue
    return False


def _ensure_company(page, *, company: str | None = None,
                    settle_ms: int = 8000) -> bool:
    """If the page is on Clerk's org picker or the portal's workspace picker,
    click the company and wait.

    Returns True when the page is past auth (on /chat or equivalent). Safe to
    call when already logged in: then it is a no-op. Callers poll this, so a
    name that is not on the list is reported once, not on every poll.
    """
    global _company_hint_shown
    name = company or company_name()
    if _logged_in(page):
        return True
    if not _on_org_choose(page, company=name):
        return False
    if not name:
        if not _company_hint_shown:
            _company_hint_shown = True
            print("acqai: Mozi asks which company to use. Click it in the window, "
                  "or set MOZI_COMPANY (the plugin: setup --company NAME) so it "
                  "is picked for you.", file=sys.stderr, flush=True)
        return False
    if not select_company(page, name):
        if name not in _company_missed:
            _company_missed.add(name)
            print(f"acqai: {name!r} was not clickable on the company list in "
                  "this page or its frames.", file=sys.stderr, flush=True)
        return False
    print(f"acqai: selected company {name!r}…", file=sys.stderr, flush=True)
    deadline = time.monotonic() + settle_ms / 1000
    while time.monotonic() < deadline:
        if _logged_in(page):
            return True
        time.sleep(0.25)
    return _logged_in(page)


def login(*, company: str | None = None, wait_s: int = 600) -> None:
    """Open a headed browser at Mozi so the member can sign in once.

    Email OTP still needs a human. Google is skipped when Clerk's OAuth
    client 401s as deleted_client. A sign-in upgrade interstitial is
    clicked through once (Continue, not the identifier-form Continue
    next to Google). Once Clerk shows the org list, this clicks
    MOZI_COMPANY when one is set and waits until /chat is ready. Press Enter at any time to save the session. The session
    persists in PROFILE_DIR and as decrypted storage-state JSON for
    later headless sends.
    """
    name = company or company_name()
    sync_playwright = _import_playwright()
    with sync_playwright() as p:
        ctx = _context(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        ctx.on("request", _learn_route)
        page.goto(mozilib.chat_page_url(), wait_until="domcontentloaded")
        pick = (f"I'll select {name!r} when the company list appears."
                if name else "If a company list appears, click yours.")
        print("A browser opened. Sign into Mozi with the email box (OTP). "
              "If a sign-in upgrade page appears, continue through it. "
              + pick, flush=True)
        if not _route_learned():
            print("Once you are in, send one short message in that window "
                  "(hi is enough). I learn the chat route from it.", flush=True)
        print("Press Enter here to save as soon as you're in.",
              flush=True)
        print(f"acqai: opened {(page.url or '').strip() or 'unknown url'}",
              file=sys.stderr, flush=True)
        forced = False
        upgrade_clicked = False
        google_recovered = False
        page = _active_page(ctx, page)
        google_recovered = recover_google_oauth(page, ctx)
        if google_recovered:
            page = _active_page(ctx, page)
        else:
            focus_email_box(page)
        if not upgrade_clicked:
            upgrade_clicked = dismiss_upgrade(page)
        _ensure_company(page, company=name)
        if _session_ready(page, ctx):
            ok = True
        else:
            deadline = time.monotonic() + max(30, wait_s)
            picked = False
            last_status = 0.0
            while time.monotonic() < deadline:
                page = _active_page(ctx, page)
                if not google_recovered:
                    google_recovered = recover_google_oauth(page, ctx)
                    if google_recovered:
                        page = _active_page(ctx, page)
                if not upgrade_clicked:
                    upgrade_clicked = dismiss_upgrade(page)
                _ensure_company(page, company=name)
                if _session_ready(page, ctx):
                    break
                if _enter_pressed():
                    forced = True
                    break
                if not picked:
                    # Click may have fired but navigation is slow; keep
                    # polling rather than re-clicking forever.
                    picked = select_company(page, name) or picked
                now = time.monotonic()
                if now - last_status >= 15:
                    here = (page.url or "").strip() or "unknown url"
                    if _upgrade_shown(page):
                        here += "; sign-in upgrade page"
                    elif _logged_in(page) and not _auth_signed_in(ctx):
                        here += "; session handshake not finished"
                    print(f"acqai: still waiting ({here}). "
                          "Press Enter here to save.",
                          file=sys.stderr, flush=True)
                    last_status = now
                time.sleep(0.5)
            ok = _session_ready(page, ctx)
            if not ok and not forced:
                print("Still waiting on Clerk. Finish the email code if "
                      "one is up, then press Enter here to save.",
                      flush=True)
                try:
                    input()
                    forced = True
                except EOFError:
                    pass
                page = _active_page(ctx, page)
                if not google_recovered:
                    google_recovered = recover_google_oauth(page, ctx)
                    if google_recovered:
                        page = _active_page(ctx, page)
                if not upgrade_clicked:
                    upgrade_clicked = dismiss_upgrade(page)
                _ensure_company(page, company=name)
                ok = _session_ready(page, ctx)
        save = bool(ok or forced)
        if ok and not _route_learned():
            _wait_for_route(ctx, page)
        if save:
            time.sleep(1)
        _close(ctx, save=save)
    print("logged in, session saved." if save else
          "still on the sign-in page; run login again once signed in.",
          flush=True)
    if not save:
        raise BrowserNotReady(
            f"login finished still on the sign-in page; run {mozilib.CMD} login")


def _route_learned() -> bool:
    return bool((mozilib.endpoint() or {}).get("url"))


def _learn_route(request) -> None:
    """Context listener during login: the page's own chat POST teaches the
    route, so the member sends one message instead of copying a cURL."""
    try:
        cfg = mozilib.learn_from_request(
            request.url, request.method, request.post_data)
    except Exception:
        return
    if not cfg:
        return
    try:
        path = mozilib.save_endpoint(cfg)
    except OSError as err:
        print(f"acqai: route seen but not saved ({err})", file=sys.stderr,
              flush=True)
        return
    print(f"acqai: learned the chat route from your message ({cfg['url']}), "
          f"saved to {path}", file=sys.stderr, flush=True)


def _wait_for_route(ctx, page, wait_s: int = 300) -> None:
    """Signed in, and no route on file yet: hold the window open until the
    member's first message teaches it, Enter is pressed, or wait_s runs out."""
    print("Signed in. Send one short message in the window so I can learn "
          "the chat route (or press Enter here to skip for now).", flush=True)
    deadline = time.monotonic() + max(10, wait_s)
    while time.monotonic() < deadline:
        if _route_learned() or _enter_pressed():
            return
        # A Playwright call, never time.sleep: the sync API hands the request
        # event to _learn_route only while one runs, so a plain sleep held the
        # member's message back until the deadline.
        try:
            page.wait_for_timeout(500)
        except Exception:
            page = _active_page(ctx, None)
            if page is None:
                break
    print(f"acqai: no message seen; run {mozilib.CMD} login again, or "
          f"{mozilib.CMD} discover --from-curl <file>.", flush=True)


def require_session(*, timeout: int = 15) -> None:
    """Headless check that the persistent profile is logged into Mozi.

    `ask` runs this before launching Claude so a dead session fails in a few
    seconds instead of after a long gather. When Clerk lands on the org
    picker with a still-valid session, this clicks MOZI_COMPANY rather than
    treating it as logged out. Raises BrowserNotReady when the profile is on
    the sign-in page, or when Playwright cannot open it (a locked profile
    from an open login window is the usual case).
    """
    sync_playwright = _import_playwright()
    print("acqai: checking Mozi session…", file=sys.stderr, flush=True)
    ok = False
    here = ""
    try:
        with sync_playwright() as p:
            ctx = _context(p, headless=True)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.set_default_timeout(timeout * 1000)
                page.goto(mozilib.chat_page_url(), wait_until="domcontentloaded")
                ok = _await_session(page, ctx, timeout)
                here = (page.url or "").strip()
            finally:
                _close(ctx, save=ok)
    except BrowserNotReady:
        raise
    except Exception as err:
        raise BrowserNotReady(
            f"could not open the Mozi browser profile ({err}); "
            f"close any open login window, or run {mozilib.CMD} login"
        ) from err
    if not ok:
        where = f" (landed on {here})" if here else ""
        google = ""
        low = (here or "").lower()
        if "accounts.google.com" in low or "deleted_client" in low:
            google = ("; Google sign-in failed (OAuth client deleted). "
                      "Use the email box")
        company = ("" if company_name() else
                   "; if Mozi asked which company, set MOZI_COMPANY")
        raise BrowserNotReady(
            f"the browser profile is not logged in; run {mozilib.CMD} login"
            + google + company + where)
    print("acqai: Mozi session ok.", file=sys.stderr, flush=True)


def _refused(status: int, what: str, text: str) -> mozilib.MoziError:
    """The error for an in-page fetch the server refused, with its reason."""
    why = mozilib.refusal(text).rstrip(".")
    msg = f"{status} from {what}" + (f": {why}" if why else "")
    if status in (401, 403, 429):
        return mozilib.MoziBlocked(
            f"{msg}; run {mozilib.CMD} login to sign in and pick the company "
            "again")
    return mozilib.MoziError(msg)


def send(question: str, *, chat_id: str | None = None,
         headless: bool = True, timeout: int = 120) -> tuple[str, str | None]:
    """Send one question through the logged-in browser, return (answer, chat_id).
    Reuses the saved conversation (or chat_id) so follow-ups stay in one thread.
    Waits for the chat box first, picking the company on the portal's
    workspace picker when one is up. On portal sandbar, creates a chat via
    in-page fetch when none is saved."""
    cfg = mozilib.endpoint()
    if not cfg or not cfg.get("url"):
        raise mozilib.MoziError(
            f"no Mozi endpoint learned yet; send one message in the window "
            f"{mozilib.CMD} login opens, or run: {mozilib.CMD} discover "
            "--from-curl <a Copy-as-cURL of a real send>")
    cid = chat_id if chat_id is not None else mozilib.load_chat_id()
    created = False
    sync_playwright = _import_playwright()
    print("acqai: opening Mozi in browser…", file=sys.stderr, flush=True)
    with sync_playwright() as p:
        ctx = _context(p, headless=headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(timeout * 1000)
        company = mozilib.company_id_from_endpoint(cfg)
        page.goto(mozilib.chat_page_url(company_id=company),
                  wait_until="domcontentloaded")
        if not _await_session(page, ctx, min(timeout, 30)):
            picker = _workspace_picker_shown(page)
            _close(ctx, save=False)
            if picker:
                raise BrowserNotReady(
                    "ACQ AI is asking which workspace to use; set MOZI_COMPANY "
                    "to the name its list shows, or run "
                    f"{mozilib.CMD} login and click it")
            raise BrowserNotReady(
                f"the browser profile is not logged in; run {mozilib.CMD} login")
        if not cid and mozilib.is_sandbar(cfg):
            create = mozilib.create_url(cfg)
            if not create:
                _close(ctx, save=False)
                raise mozilib.MoziError("sandbar route has no create URL")
            print("acqai: creating portal chat…", file=sys.stderr, flush=True)
            made = page.evaluate(
                _FETCH_JS,
                {"url": create, "body": mozilib.create_chat_body(question, cfg)})
            if made["status"] >= 400:
                _close(ctx, save=False)
                raise _refused(made["status"], "sandbar chat create",
                               made["text"])
            cid = mozilib.parse_create_chat_id(made["text"].encode("utf-8"))
            created = True
            page.goto(mozilib.chat_page_url(chat_id=cid, company_id=company),
                      wait_until="domcontentloaded")
        body, used = mozilib.build_body(question, cid)
        if created and used:
            print(f"acqai: new portal chat {used[:8]}…",
                  file=sys.stderr, flush=True)
        elif cid:
            print(f"acqai: continuing Mozi chat {cid[:8]}…",
                  file=sys.stderr, flush=True)
        else:
            print("acqai: new Mozi chat…", file=sys.stderr, flush=True)
        print("acqai: posting to Mozi…", file=sys.stderr, flush=True)
        res = page.evaluate(_FETCH_JS, {"url": cfg["url"], "body": body})
        _close(ctx, save=True)
    if res["status"] >= 400:
        raise _refused(res["status"], f"chat send ({cfg['url']})", res["text"])
    print("acqai: Mozi answered.", file=sys.stderr, flush=True)
    mozilib.save_chat_id(used)
    return mozilib.extract_answer(res["text"].encode("utf-8"), cfg), used
