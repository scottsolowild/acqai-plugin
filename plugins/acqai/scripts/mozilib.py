"""The Mozi (ACQ AI) send seam: ask Hormozi's business AI one question and
read its answer, through a paced, consent-gated, personal-use channel.

Two apps are supported (one learned route at a time):

  Portal (default)  https://portal.acquisition.com  UI /advisor
                    Aegis cookies; POST /api/sandbar/chat then chat-stream
  Legacy            https://ai.acquisition.com      UI /chat
                    Clerk __session (~60s); POST /api/chat (AI SDK body)

Set MOZI_BASE to pick which login opens. Re-run login (and send one message,
or discover --from-curl) after switching so endpoint.json matches that app.

Mozi offers no public API and no MCP (confirmed in the ACQ community). The
reachable path is each app's own internal HTTP endpoint, called with a
captured browser session, the way Hamel Husain's reverse-eng-site-skill and
skoollib both work.

AUTH IS THE WHOLE COOKIE HEADER (MOZI_TOKEN). Portal:
`__Secure-aegis-external.session_token` + `session_data`. Legacy: Clerk
`__session`. Prefer the browser transport: it refreshes the session itself.

WHY THIS IS FENCED HARDER THAN THE READERS. Every other channel only reads.
This one writes into a paid third-party account through an undocumented route,
which can break Mozi's terms of service (the community's own HTTP-tips post
flags exactly this). So it is personal-use only, one request at a time, heavily
paced, and it never sends without a per-run consent flag. It touches Mozi's own
chat only, never the Skool community (that stays skoollib's job).

Secrets, both the member's own browser session, never committed:
  MOZI_TOKEN   the full Cookie header from a logged-in request
  MOZI_BASE    app origin (default https://portal.acquisition.com; legacy
               https://ai.acquisition.com)
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
PORTAL_BASE = "https://portal.acquisition.com"
LEGACY_BASE = "https://ai.acquisition.com"
DEFAULT_BASE = PORTAL_BASE
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0.0.0 Safari/537.36")
# Portal Aegis session cookie (HTTP --http path). Browser transport preferred.
_AEGIS_TOKEN = "__Secure-aegis-external.session_token"

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


def is_portal(origin: str | None = None) -> bool:
    """True when the origin (or MOZI_BASE) is the ACQ portal, not legacy AI."""
    host = (origin or base()).lower()
    return "portal.acquisition.com" in host


def page_path(origin: str | None = None) -> str:
    """Chat UI path: /advisor on portal, /chat on legacy."""
    return "/advisor" if is_portal(origin) else "/chat"


def chat_page_url(*, chat_id: str | None = None,
                  company_id: str | None = None,
                  origin: str | None = None) -> str:
    """The signed-in chat page, with optional portal query ids."""
    root = (origin or base()).rstrip("/")
    url = root + page_path(root)
    q: list[str] = []
    if chat_id:
        q.append("chatId=" + chat_id)
    if company_id:
        q.append("companyId=" + company_id)
    return url + (("?" + "&".join(q)) if q else "")


def send_allowed() -> bool:
    """A send needs explicit per-run consent, since it writes to a third party."""
    return os.environ.get("MOZI_SEND_OK", "").strip() in {"1", "true", "yes"}


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(os.environ.get(name, ""))))
    except (TypeError, ValueError):
        return default


def has_session_cookie(header: str | None = None) -> bool:
    """True when the Cookie header carries portal Aegis or legacy Clerk auth."""
    h = header if header is not None else cookie_header()
    if not h:
        return False
    if _AEGIS_TOKEN in h or "aegis-external.session_token=" in h:
        return True
    return bool(re.search(r"(?:^|;\s*)__session(?:_[A-Za-z0-9]+)?=", h))


# --- token freshness: decode the __session JWT exp, warn when stale -----------
def session_expiry() -> int | None:
    """Seconds until the captured Clerk __session JWT expires (negative if
    already expired), or None when absent / unreadable / Aegis-only. Clerk
    tokens live ~60s; Aegis has no readable exp here, so the browser path is
    the safe default on portal."""
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
             timeout: int = 90,
             referer: str | None = None) -> tuple[int, bytes]:
    header = cookie_header()
    if not header:
        raise MoziError(
            "MOZI_TOKEN not set (the whole Cookie header from a logged-in "
            f"{base()} request, exported in the shell or set in .env)")
    if not has_session_cookie(header):
        raise MoziError(
            "MOZI_TOKEN has no Aegis or Clerk session cookie "
            f"({_AEGIS_TOKEN} or __session)")
    headers = {"User-Agent": USER_AGENT, "Cookie": header,
               "Origin": base(),
               "Referer": referer or chat_page_url(),
               "Accept": "text/event-stream, application/json, text/plain"}
    # Legacy /api/chat checks x-ui-nonce mirroring acq_ui_nonce; portal does not.
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
        # Legacy AI SDK useChat body: one `message` object. Set its text and
        # give the message a fresh id + timestamp. The chat `id` is the
        # conversation: reuse chat_id to continue, or mint a new one per ask.
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
        # Portal sandbar chat-stream: one user message in `messages`, ids in
        # toolContext.chatId / newMessageId / messageId, plus requestId.
        field = slot.split(".", 1)[1]
        msg = body["messages"][-1]
        msg[field] = question
        new_id = str(uuid.uuid4())
        if isinstance(msg, dict):
            msg["id"] = new_id
        if "requestId" in body:
            body["requestId"] = str(uuid.uuid4())
        if "clientLocalTime" in body:
            body["clientLocalTime"] = _now_iso()
        tc = body.get("toolContext")
        if isinstance(tc, dict):
            cid = chat_id or tc.get("chatId") or str(uuid.uuid4())
            tc["chatId"] = cid
            tc["newMessageId"] = new_id
            tc["messageId"] = str(uuid.uuid4())
        return body
    body[slot] = question
    return body


def _chat_id_of(body: object) -> str | None:
    if not isinstance(body, dict):
        return None
    tc = body.get("toolContext")
    if isinstance(tc, dict) and tc.get("chatId"):
        return str(tc["chatId"])
    cid = body.get("id")
    return str(cid) if cid else None


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
    return body, _chat_id_of(body)


def is_sandbar(cfg: dict | None = None) -> bool:
    """True when the learned route is portal sandbar chat-stream."""
    cfg = cfg if cfg is not None else (endpoint() or {})
    url = (cfg.get("url") or "").lower()
    return "sandbar" in url and "chat-stream" in url


def create_url(cfg: dict | None = None) -> str | None:
    """POST URL that creates a sandbar chat, or None on legacy."""
    cfg = cfg if cfg is not None else (endpoint() or {})
    if cfg.get("create_url"):
        return str(cfg["create_url"])
    url = (cfg.get("url") or "").rstrip("/")
    if url.endswith("chat-stream"):
        return url[: -len("chat-stream")] + "chat"
    return None


def company_id_from_endpoint(cfg: dict | None = None) -> str | None:
    cfg = cfg if cfg is not None else (endpoint() or {})
    body = cfg.get("body_template")
    if not isinstance(body, dict):
        return None
    tc = body.get("toolContext")
    if isinstance(tc, dict):
        for key in ("clientServiceCompanyId", "companyId"):
            val = tc.get(key)
            if val:
                return str(val)
    return None


def _title_from(question: str) -> str:
    line = (question or "").strip().splitlines()[0] if question else ""
    line = re.sub(r"\s+", " ", line).strip()
    return (line[:80] if line else "Chat")


def create_chat_body(title: str, cfg: dict | None = None) -> dict:
    """JSON body for POST /api/sandbar/chat."""
    cfg = cfg if cfg is not None else (endpoint() or {})
    body: dict = {"title": _title_from(title)}
    company = company_id_from_endpoint(cfg)
    if company:
        body["clientServiceCompanyId"] = company
    return body


def parse_create_chat_id(raw: bytes) -> str:
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError as err:
        raise MoziError(f"create chat returned non-JSON: {raw[:200]!r}") from err
    if not isinstance(data, dict):
        raise MoziError(f"create chat returned a non-object: {raw[:200]!r}")
    cid = data.get("id") or data.get("chatId")
    if not cid:
        raise MoziError(f"create chat returned no id: {raw[:200]!r}")
    return str(cid)


def create_chat(title: str, *, timeout: int = 60) -> str:
    """HTTP: create a portal sandbar chat and return its id."""
    cfg = endpoint() or {}
    url = create_url(cfg)
    if not url:
        raise MoziError("no sandbar create URL (learned route is not chat-stream)")
    body = create_chat_body(title, cfg)
    _status, raw = _request("POST", url, body=json.dumps(body).encode("utf-8"),
                            timeout=timeout)
    return parse_create_chat_id(raw)


def _with_create_url(cfg: dict) -> dict:
    """Attach create_url when learning a sandbar stream route."""
    out = dict(cfg)
    if not out.get("create_url"):
        derived = create_url(out)
        if derived:
            out["create_url"] = derived
    return out


def parse_curl(text: str) -> dict:
    """Learn the chat endpoint from a Copy-as-cURL blob: url, method, JSON body
    template, and where the message slots in. The cookie is read but never
    stored (it's the live secret; it belongs in MOZI_TOKEN)."""
    # URL: the first http(s) token, quoted or bare (--url FORM included).
    m = re.search(r"""curl\s+(?:-[A-Za-z-]+\s+)*['"]?(https?://[^'"\s]+)""", text)
    if not m:
        m = re.search(r"""--url\s+['"]?(https?://[^'"\s]+)""", text)
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
    stream = ("text/event-stream" in text or "/api/chat" in url
              or "chat-stream" in url)
    return _with_create_url({
        "url": url, "method": method, "body_template": body_template,
        "message_slot": message_slot, "stream": stream,
    })


def learn_from_request(url: str, method: str, post_data: "str | None") -> "dict | None":
    """The route config from one request the app made itself: the POST that
    carries a chat message. login listens for it, so a member who sends one
    message in the window has taught the transport the route with no cURL to
    copy. A GET, another path, or a body with no message slot is None.

    Accepts legacy /api/chat and portal /api/sandbar/chat-stream. Skips
    /api/sandbar/chat (create-only; no message slot)."""
    if (method or "").upper() != "POST" or not post_data:
        return None
    clean = (url or "").split("?", 1)[0]
    path = clean.lower()
    if "/api/" not in path:
        return None
    leaf = path.rsplit("/", 1)[-1]
    # chat-stream (portal) or chat (legacy /api/chat). Not chat-list, etc.
    if leaf not in ("chat", "chat-stream") and "chat-stream" not in leaf:
        return None
    if leaf == "chat" and "/sandbar/" in path:
        return None  # create-or-get, no user message
    try:
        body = json.loads(post_data)
    except ValueError:
        return None
    slot = _find_message_slot(body)
    if not slot:
        return None
    return _with_create_url({
        "url": clean, "method": "POST", "body_template": body,
        "message_slot": slot, "stream": True,
    })


def save_endpoint(cfg: dict) -> Path:
    ENDPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    cfg = _with_create_url(cfg)
    safe = {k: cfg.get(k) for k in
            ("url", "method", "body_template", "message_slot", "stream",
             "answer_path", "create_url")}
    ENDPOINT_FILE.write_text(json.dumps(safe, indent=2) + "\n", encoding="utf-8")
    return ENDPOINT_FILE


# --- response parsing: JSON, SSE, NDJSON, or Vercel AI SDK data stream --------
def extract_answer(raw: bytes, cfg: dict) -> str:
    text = raw.decode("utf-8", errors="replace").strip()
    # Vercel AI SDK data-stream lines: 0:"chunk"  (legacy text parts)
    ai_parts = re.findall(r'^0:"((?:[^"\\]|\\.)*)"', text, re.M)
    if ai_parts:
        return "".join(json.loads(f'"{p}"') for p in ai_parts).strip()
    # Portal sandbar / SSE: accumulate delta fields from JSON lines.
    deltas: list[str] = []
    chunks: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if line in ("", "[DONE]"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        d = obj.get("delta")
        if isinstance(d, str) and d:
            deltas.append(d)
            continue
        dug = _dig_text(obj)
        if dug:
            chunks.append(dug)
    if deltas:
        return "".join(deltas).strip()
    if chunks:
        return "".join(chunks).strip()
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
    --new). Portal sandbar creates a chat first when none is saved. Refuses
    without the per-run consent flag or a discovered endpoint, and never
    guesses a route."""
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
    created = False
    if not cid and is_sandbar(cfg):
        print("acqai: creating portal chat…", file=sys.stderr, flush=True)
        cid = create_chat(question, timeout=min(60, timeout))
        created = True
    payload, used = build_body(question, cid)
    if created and used:
        print(f"acqai: new portal chat {used[:8]}…",
              file=sys.stderr, flush=True)
    elif cid:
        print(f"acqai: continuing Mozi chat {cid[:8]}…",
              file=sys.stderr, flush=True)
    else:
        print("acqai: new Mozi chat…", file=sys.stderr, flush=True)
    company = company_id_from_endpoint(cfg)
    referer = chat_page_url(chat_id=used or cid, company_id=company)
    print("acqai: posting to Mozi…", file=sys.stderr, flush=True)
    _status, raw = _request(cfg.get("method", "POST"), cfg["url"],
                            body=json.dumps(payload).encode("utf-8"),
                            timeout=timeout, referer=referer)
    print("acqai: Mozi answered.", file=sys.stderr, flush=True)
    save_chat_id(used)
    return extract_answer(raw, cfg), used
