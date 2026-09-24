"""The Mozi (ACQ AI, ai.acquisition.com) send seam: ask Hormozi's business AI
one question and read its answer, through a paced, consent-gated, personal-use
channel.

Mozi offers no public API and no MCP (confirmed in the ACQ community). The app
is Next.js on Vercel behind Clerk v5 auth, and the chat route lives inside its
authed segment, so the reachable path is its own internal HTTP endpoint,
called with a captured browser session, the way Hamel Husain's
reverse-eng-site-skill and skoollib both work.

AUTH IS CLERK, SO THE WHOLE COOKIE HEADER IS THE TOKEN. Clerk's bearer is the
`__session` cookie, a JWT that expires ~60 seconds after it is minted and is
refreshed constantly by the browser. A single cookie value goes stale within a
minute, and Clerk prefers a publishable-key-suffixed twin plus `_client_uat`
alongside it. So MOZI_TOKEN is the entire `Cookie:` header copied from a
logged-in request, sent verbatim, and this is an interactive tool: capture it
right before an ask, not for a schedule.

WHY THIS IS FENCED HARDER THAN THE READERS. Every other channel only reads.
This one writes into a paid third-party account through an undocumented route,
which can break Mozi's terms of service (the community's own HTTP-tips post
flags exactly this). So it is personal-use only, one request at a time, heavily
paced, and it never sends without a per-run consent flag. It touches Mozi's own
chat only, never the Skool community (that stays skoollib's job).

Secrets, both the member's own browser session, never committed:
  MOZI_TOKEN   the full Cookie header from a logged-in ai.acquisition.com request
  MOZI_BASE    the app origin (default https://ai.acquisition.com)
The chat route and payload shape, learned by `discover` from a Copy-as-cURL of
a real send, land in endpoint.json under STATE_DIR (review/mozi/ here,
gitignored; the plugin's own folder elsewhere). No secret is ever
written there; the cookie stays in the environment.
"""
from __future__ import annotations

import base64
import datetime
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

try:
    from load_env import load_dotenv  # the notes repo's .env reader
except ImportError:  # the plugin ships this file alone; the shell carries the env
    def load_dotenv() -> None:
        return None

ROOT = Path(__file__).resolve().parent.parent
# The learned route, the chat id, and the browser profile. In the notes repo
# they sit under review/mozi/ (gitignored). The plugin sets ACQAI_STATE_DIR to
# a folder in the member's home, so a plugin update never takes a login with it.
STATE_DIR = Path(os.environ.get("ACQAI_STATE_DIR", "").strip()
                 or (ROOT / "review" / "mozi")).expanduser()
ENDPOINT_FILE = STATE_DIR / "endpoint.json"
CHAT_ID_FILE = STATE_DIR / "chat-id"
CONFIG_FILE = STATE_DIR / "config.json"  # the plugin's saved company; notes reads env
CHAT_ID_ENV = "MOZI_CHAT_ID"  # overrides the file for one process / nested send
# How a message names the command to run next: ./acqai.sh in the notes repo,
# the plugin's own script elsewhere. Both transport files read these, so a
# public copy of the pair never tells a stranger to run ./acqai.sh.
CMD = os.environ.get("ACQAI_CMD", "").strip() or "./acqai.sh"
SETUP_CMD = os.environ.get("ACQAI_SETUP_CMD", "").strip() or "./setup.sh"
DEFAULT_BASE = "https://ai.acquisition.com"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0.0.0 Safari/537.36")

_last_request = 0.0


class MoziError(RuntimeError):
    """Setup or parse trouble: missing token, no endpoint discovered yet."""


class MoziBlocked(MoziError):
    """The service said stop (401/403/429) or the session is logged out."""


class MoziConsent(MoziError):
    """A send was attempted without the per-run MOZI_SEND_OK consent flag."""


def load_chat_id() -> str | None:
    """The active Mozi conversation id, if any. Env wins so a nested send
    inside an ask can inherit without racing the file; otherwise the file
    under STATE_DIR carries the thread across separate runs."""
    env = os.environ.get(CHAT_ID_ENV, "").strip()
    if env:
        return env
    try:
        if CHAT_ID_FILE.is_file():
            return CHAT_ID_FILE.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None
    return None


def save_chat_id(chat_id: str | None) -> None:
    """Remember the conversation so the next send continues it."""
    cid = (chat_id or "").strip()
    if not cid:
        return
    os.environ[CHAT_ID_ENV] = cid
    try:
        CHAT_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
        CHAT_ID_FILE.write_text(cid + "\n", encoding="utf-8")
    except OSError as err:
        print(f"acqai: chat-id save skipped ({err})", file=sys.stderr, flush=True)


def clear_chat_id() -> None:
    """Start a fresh Mozi conversation on the next send (`--new`)."""
    os.environ.pop(CHAT_ID_ENV, None)
    try:
        if CHAT_ID_FILE.is_file():
            CHAT_ID_FILE.unlink()
    except OSError as err:
        print(f"acqai: chat-id clear skipped ({err})", file=sys.stderr, flush=True)


def config() -> dict:
    """The plugin's saved settings (the company, for now). Empty in the notes
    repo, where the wrapper exports MOZI_COMPANY instead."""
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(**fields) -> Path:
    """Merge the given settings into the config file and return its path."""
    data = config()
    data.update({k: v for k, v in fields.items() if v is not None})
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return CONFIG_FILE


def cookie_header() -> str:
    """The whole Cookie header (MOZI_TOKEN), verbatim."""
    try:
        load_dotenv()
    except Exception:
        pass
    return os.environ.get("MOZI_TOKEN", "").strip()


def base() -> str:
    return (os.environ.get("MOZI_BASE", "").strip() or DEFAULT_BASE).rstrip("/")


def send_allowed() -> bool:
    """A send needs explicit per-run consent, since it writes to a third party."""
    return os.environ.get("MOZI_SEND_OK", "").strip() in {"1", "true", "yes"}


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(os.environ.get(name, ""))))
    except (TypeError, ValueError):
        return default


# --- token freshness: decode the __session JWT exp, warn when stale -----------
def session_expiry() -> int | None:
    """Seconds until the captured __session JWT expires (negative if already
    expired), or None if no __session is present or it can't be read. Clerk
    session tokens live ~60s, so this catches a stale paste before a send."""
    header = cookie_header()
    m = re.search(r"__session(?:_[A-Za-z0-9]+)?=([^;]+)", header)
    if not m:
        return None
    parts = m.group(1).split(".")
    if len(parts) < 2:
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return int(claims["exp"]) - int(time.time())
    except Exception:
        return None


def _pace() -> None:
    """One request at a time, a long jittered gap. A send channel to a paid
    service earns a slower floor than the reader (default 12-30s)."""
    global _last_request
    lo = _int_env("MOZI_MIN_DELAY", 12, 8, 120)
    hi = max(lo, _int_env("MOZI_MAX_DELAY", 30, 8, 300))
    if _last_request:
        wait = random.uniform(lo, hi) - (time.monotonic() - _last_request)
        if wait > 0:
            print(f"acqai: pacing {wait:.0f}s before Mozi send…",
                  file=sys.stderr, flush=True)
            time.sleep(wait)
    _last_request = time.monotonic()


def _request(method: str, url: str, *, body: bytes | None = None,
             content_type: str = "application/json",
             timeout: int = 90) -> tuple[int, bytes]:
    header = cookie_header()
    if not header:
        raise MoziError("MOZI_TOKEN not set (the whole Cookie header from a "
                        "logged-in ai.acquisition.com request, exported in "
                        "the shell or set in .env)")
    headers = {"User-Agent": USER_AGENT, "Cookie": header,
               "Origin": base(), "Referer": base() + "/chat",
               "Accept": "text/event-stream, application/json, text/plain"}
    # ai.acquisition.com's /api/chat checks an x-ui-nonce header that mirrors
    # the acq_ui_nonce cookie; derive it so no stale nonce is stored.
    nonce = re.search(r"acq_ui_nonce=([^;]+)", header)
    if nonce:
        headers["x-ui-nonce"] = nonce.group(1)
    if body is not None:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    _pace()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        if err.code in (401, 403, 429):
            raise MoziBlocked(f"{err.code} from mozi on {url}") from err
        raise MoziError(f"{err.code} on {url}") from err
    except Exception as err:
        raise MoziError(f"{err} on {url}") from err


# --- endpoint config, learned from a captured cURL ----------------------------
def endpoint() -> dict | None:
    try:
        return json.loads(ENDPOINT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


_MSG_KEYS = ("message", "prompt", "text", "input", "query", "content", "question")


def _find_message_slot(body: object) -> str | None:
    """Where the user's message text sits in a captured body, as a small path
    language: "message.aisdk" for the AI SDK single-message shape (a `message`
    object with `parts`/`content`), "messages[-1].content" for a chat array, or
    "field" for a flat key. None if nothing message-shaped is found."""
    if isinstance(body, dict):
        m = body.get("message")
        if isinstance(m, dict) and ("content" in m or "parts" in m):
            return "message.aisdk"
        msgs = body.get("messages")
        if isinstance(msgs, list) and msgs and isinstance(msgs[-1], dict):
            for k in ("content", "text"):
                if k in msgs[-1]:
                    return f"messages[-1].{k}"
        for k in _MSG_KEYS:
            if isinstance(body.get(k), str):
                return k
    return None


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.") + f"{datetime.datetime.now().microsecond // 1000:03d}Z"


def _set_message(body: object, slot: str, question: str,
                 chat_id: str | None = None) -> object:
    body = json.loads(json.dumps(body))  # deep copy
    if slot == "message.aisdk":
        # AI SDK useChat body: one `message` object. Set its text and give the
        # message a fresh id + timestamp. The chat `id` is the conversation:
        # reuse chat_id to continue a dialogue, or mint a new one per ask.
        m = body["message"]
        m["content"] = question
        text_parts = [p for p in m.get("parts", [])
                      if isinstance(p, dict) and p.get("type") == "text"]
        if text_parts:
            text_parts[0]["text"] = question
            m["parts"] = [p for p in m["parts"]
                          if not (isinstance(p, dict) and p.get("type") == "text")
                          or p is text_parts[0]]
        else:
            m["parts"] = [{"type": "text", "text": question}]
        m["id"] = str(uuid.uuid4())
        if "createdAt" in m:
            m["createdAt"] = _now_iso()
        if "id" in body:
            body["id"] = chat_id or str(uuid.uuid4())
        return body
    if slot.startswith("messages[-1]."):
        field = slot.split(".", 1)[1]
        body["messages"][-1][field] = question
        return body
    body[slot] = question
    return body


def build_body(question: str, chat_id: str | None = None) -> tuple[object, str | None]:
    """The request body for one question, and the chat id it belongs to (so a
    caller can pass it back to continue the dialogue). Shared by both transports
    (raw HTTP and the browser)."""
    cfg = endpoint() or {}
    tmpl, slot = cfg.get("body_template"), cfg.get("message_slot")
    if tmpl is not None and slot:
        body = _set_message(tmpl, slot, question, chat_id=chat_id)
    elif tmpl is not None:
        raise MoziError('the captured body has no detected message slot; add '
                        f'"message_slot" to {ENDPOINT_FILE} by hand')
    else:
        body = {"message": question}
    cid = body.get("id") if isinstance(body, dict) else None
    return body, cid


def parse_curl(text: str) -> dict:
    """Learn the chat endpoint from a Copy-as-cURL blob: url, method, JSON body
    template, and where the message slots in. The cookie is read but never
    stored (it's the live secret; it belongs in MOZI_TOKEN)."""
    # URL: the first http(s) token, quoted or bare.
    m = re.search(r"""curl\s+(?:-[A-Za-z-]+\s+)*['"]?(https?://[^'"\s]+)""", text)
    if not m:
        m = re.search(r"""['"](https?://[^'"\s]+)['"]""", text)
    if not m:
        raise MoziError("no URL found in the cURL blob")
    url = m.group(1)
    method = "POST"
    xm = re.search(r"(?:-X|--request)\s+['\"]?([A-Z]+)", text)
    if xm:
        method = xm.group(1)
    bm = re.search(r"(?:--data-raw|--data-binary|--data|-d)\s+'((?:[^'\\]|\\.)*)'",
                   text, re.S)
    if not bm:
        bm = re.search(r'(?:--data-raw|--data-binary|--data|-d)\s+"((?:[^"\\]|\\.)*)"',
                       text, re.S)
    body_template = None
    message_slot = None
    if bm:
        raw = bm.group(1).encode().decode("unicode_escape") if "\\" in bm.group(1) else bm.group(1)
        try:
            body_template = json.loads(raw)
            message_slot = _find_message_slot(body_template)
        except ValueError:
            body_template = None
        if method == "POST" and not xm and body_template is None:
            method = "POST"
    stream = "text/event-stream" in text or "/api/chat" in url
    return {"url": url, "method": method, "body_template": body_template,
            "message_slot": message_slot, "stream": stream}


def learn_from_request(url: str, method: str, post_data: "str | None") -> "dict | None":
    """The route config from one request the app made itself: the POST that
    carries a chat message. login listens for it, so a member who sends one
    message in the window has taught the transport the route with no cURL to
    copy. A GET, another path, or a body with no message slot is None."""
    if (method or "").upper() != "POST" or not post_data:
        return None
    clean = (url or "").split("?", 1)[0]
    path = clean.lower()
    if "/api/" not in path or "chat" not in path.rsplit("/", 1)[-1]:
        return None
    try:
        body = json.loads(post_data)
    except ValueError:
        return None
    slot = _find_message_slot(body)
    if not slot:
        return None
    return {"url": clean, "method": "POST", "body_template": body,
            "message_slot": slot, "stream": True}


def save_endpoint(cfg: dict) -> Path:
    ENDPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    safe = {k: cfg.get(k) for k in
            ("url", "method", "body_template", "message_slot", "stream", "answer_path")}
    ENDPOINT_FILE.write_text(json.dumps(safe, indent=2) + "\n", encoding="utf-8")
    return ENDPOINT_FILE


# --- response parsing: JSON, SSE, or Vercel AI SDK data stream ----------------
def extract_answer(raw: bytes, cfg: dict) -> str:
    text = raw.decode("utf-8", errors="replace").strip()
    # Vercel AI SDK data-stream lines: 0:"chunk"  (text parts)
    ai_parts = re.findall(r'^0:"((?:[^"\\]|\\.)*)"', text, re.M)
    if ai_parts:
        return "".join(json.loads(f'"{p}"') for p in ai_parts).strip()
    # SSE: concatenate data: payloads, pulling text out of JSON chunks.
    if "data:" in text:
        out = []
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload in ("", "[DONE]"):
                continue
            try:
                obj = json.loads(payload)
                out.append(_dig_text(obj))
            except ValueError:
                out.append(payload)
        joined = "".join(p for p in out if p).strip()
        if joined:
            return joined
    # Plain JSON.
    try:
        data = json.loads(text)
    except ValueError:
        return text
    for key in cfg.get("answer_path") or []:
        if isinstance(data, dict):
            data = data.get(key, "")
    return data if isinstance(data, str) else _dig_text(data)


def _dig_text(obj: object) -> str:
    """Best-effort: pull a content/text/delta string out of a chunk object."""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        for k in ("content", "text", "delta", "message", "answer"):
            v = obj.get(k)
            if isinstance(v, str):
                return v
            if isinstance(v, dict):
                inner = _dig_text(v)
                if inner:
                    return inner
    return ""


def ask(question: str, *, chat_id: str | None = None,
        timeout: int = 120) -> tuple[str, str | None]:
    """Send one question to Mozi and return (answer, chat_id). Reuses the
    saved conversation unless chat_id is passed (or cleared via clear_chat_id /
    --new). Refuses without the per-run consent flag or a discovered endpoint,
    and never guesses a route."""
    if not send_allowed():
        raise MoziConsent(
            "a Mozi send writes to your paid account through an undocumented "
            "route; set MOZI_SEND_OK=1 for this run to allow it")
    cfg = endpoint()
    if not cfg or not cfg.get("url"):
        raise MoziError(
            f"no Mozi endpoint learned yet ({ENDPOINT_FILE}). Send one message "
            f"in the window {CMD} login opens, or capture a Copy-as-cURL of a "
            f"real send and run: {CMD} discover --from-curl <file>")
    exp = session_expiry()
    if exp is not None and exp < 5:
        raise MoziBlocked(
            f"the captured __session looks expired ({exp}s left); Clerk tokens "
            "live ~60s, so grab a fresh cookie right before the ask (or use the "
            "browser transport, which never races the token)")
    cid = chat_id if chat_id is not None else load_chat_id()
    payload, used = build_body(question, cid)
    if cid:
        print(f"acqai: continuing Mozi chat {cid[:8]}…",
              file=sys.stderr, flush=True)
    else:
        print("acqai: new Mozi chat…", file=sys.stderr, flush=True)
    print("acqai: posting to Mozi…", file=sys.stderr, flush=True)
    status, raw = _request(cfg.get("method", "POST"), cfg["url"],
                           body=json.dumps(payload).encode("utf-8"),
                           timeout=timeout)
    print("acqai: Mozi answered.", file=sys.stderr, flush=True)
    save_chat_id(used)
    return extract_answer(raw, cfg), used
