# acqai

ACQ AI, in Claude Code.

You are in a Claude Code conversation about your business. This plugin lets
that conversation include ACQ AI. Claude reads the docs you point it at, writes
the question, shows it to you, sends it to ACQ AI through your own signed-in
browser once you say yes, reads the answer, asks a follow-up or two with what
your docs add, and hands you the result with the edits it implies. Your docs are the context, so
there is nothing to paste and no context document to keep current.

This is its own GitHub marketplace
([scottsolowild/acqai-plugin](https://github.com/scottsolowild/acqai-plugin)),
not Anthropic's plugin catalog. You will not find it by searching Claude's
official list.

## Install (pick one)

### Option A: Terminal

```
claude plugin marketplace add scottsolowild/acqai-plugin
claude plugin install acqai@acqai-plugin
```

If you belong to more than one company on ACQ AI:

```
claude plugin install acqai@acqai-plugin --config mozi_company="Your Company"
```

### Option B: Claude Code Desktop (local session)

Cloud sessions do not load this plugin. Use a **local** Code session on your
machine.

1. Open Claude Code Desktop → a local project (a folder of docs is enough).
2. **+ → Plugins → Add marketplace**.
3. URL: `scottsolowild/acqai-plugin` → **Sync**.
4. Install **acqai** from that marketplace. Confirm it shows enabled under
   **+ → Plugins**.

If Sync shows only **Failed to add marketplace**, use Option A instead. Do
not type `/plugin …` in the Desktop composer; that command is not available
there. If you already ran Option A, skip Add marketplace. The plugin is
already under **+ → Plugins**. Quit Desktop and reopen if it is missing, so
it reloads `~/.claude/settings.json`.

## After install (once, a few minutes)

1. **Say "set up ACQ AI"** in a Claude Code chat (Desktop local, or
   `claude` in a terminal). Claude installs the browser it drives (a private
   copy in `~/.config/acqai`) and opens a sign-in window at
   portal.acquisition.com/advisor.
2. **Sign in** in that window. Email code, as usual. If a company list
   shows, click yours. Send one short message there ("hi" is enough). The
   plugin learns the chat route from that message and closes the window.
3. **Try it.** Say `ask ACQ AI what it would change first about my offer`
   and point it at a doc.

From a terminal, the same chat looks like:

```
claude
```

Then type the ask. Or one shot:

```
claude -p "ask ACQ AI what it would change first about my offer"
```

## Use it

Say what you want in plain English:

- "ask ACQ AI to price this offer" (with the offer doc open or named)
- "run my landing page past ACQ AI and tell me what it would change"
- "what would ACQ AI say about this funnel? here are the numbers"
- "ask a follow-up: how does that change at my price?"
- "sort that reply: what do we adopt, what waits, what do we drop"

Claude writes the question from your docs and shows it before anything is
sent. Answers are kept in `~/.config/acqai/answers/`, one file per question,
so nothing is lost when the chat scrolls away.

## What you need

- A paid ACQ AI (Mozi) account you can sign into in a browser.
- Claude Code (terminal CLI and/or the Desktop app on a local session).
- Python 3.9 or newer. A Mac already has it.

Cursor is not required. A repo is not required. A folder of docs is enough,
and no folder at all still works: Claude sends your question on its own.

## What it does

- **Writes the question from your docs.** The situation, the numbers, the decision, and the docs that matter, pasted in whole, with a standing ask that ACQ AI use your terms and say what each recommendation rests on.
- **Sends it through your own browser.** Playwright drives a private Chromium that you signed into once. The session stays on your machine.
- **Refines with questions.** Claude reads the answer and asks a follow-up or two in the same ACQ AI conversation, each carrying a number or a result from your docs that ACQ AI did not have yet. Each answer builds on the last, so "go deeper on point two" works too. A new topic starts a new conversation.
- **Brings the mechanics home.** Claude labels what ACQ AI said and what it added, then sorts the answer: adopt (an edit, drafted in your voice), later, or drop.

## The rules

- **Your yes, every send.** The script asks before it sends, and Claude asks you before it runs the script. There is no batch mode.
- **One question at a time.** The script paces itself.
- **Your account, your use.** It does what you would do by hand, in your own browser, for yourself.
- **Chat only.** It never posts to the community.
- **Private things stay home.** Client names, call transcripts, anything you call private. Claude leaves them out, and you see the question before it goes.

## Questions

**Is Cursor required?** No. It runs from Claude Code in a terminal, or in
Claude Code's desktop app on a **local** session. Cloud sessions do not load
this plugin. Cursor is one editor that can host Claude Code, and it is not
needed.

**Do I need to know what a repo is?** No. Point it at a folder of docs, or at
one doc, or at nothing.

**Is there an ACQ AI API?** No, and none has been announced. So the plugin uses
your own signed-in browser, on your own computer, the way you would by hand.
That is also why it runs on your machine and not in a cloud session.

**Is this an official ACQ tool?** No. One member built it and shared it. It
uses your account through your browser. If ACQ asks you to stop, stop.

**What does it read?** The docs you point it at, and the question you
approved. Nothing else leaves your machine, and nothing goes anywhere but ACQ
AI.

**Where is my login kept?** In `~/.config/acqai/browser-profile`, on your
computer. Delete that folder to sign out.

**Will it work on Windows or Linux?** It was built and is used on a Mac.
Playwright runs on all three, so the pieces are there, and nobody has run it
on the other two yet. If you do, say how it went.

**The sign-in window showed a list of companies.** Click yours. To have it
clicked for you next time: `acqai.py setup --company "Your Company"`.

**It says the route is not learned.** Run `login` again and send one message
in the window. If that still does not take: in Chrome, open
portal.acquisition.com/advisor, open DevTools → Network, send any message,
right-click the `chat-stream` request (Create is `…/sandbar/chat`; the stream
is what carries the answer), Copy → Copy as cURL, save it to a file, and run
`acqai.py discover --from-curl that-file`. The cookie in it is read and thrown
away. Only the route is kept.

**I still use the older ai.acquisition.com app.** Run `acqai.py login-legacy`
(or `./acqai.sh login-legacy` in the notes repo). Send one message in that
window so the learned route matches. Later sends follow that route's host.
`login` alone returns you to portal.

## Run it by hand (optional)

The skill runs the bundled script for you. To run it yourself, from a checkout
of this repo:

```
python3 plugins/acqai/scripts/acqai.py setup --company "Your Company"
python3 plugins/acqai/scripts/acqai.py login
python3 plugins/acqai/scripts/acqai.py probe
python3 plugins/acqai/scripts/acqai.py send "What would you change first about this offer?"
python3 plugins/acqai/scripts/acqai.py send --file question.md -y
python3 plugins/acqai/scripts/acqai.py send "Go deeper on point two."
python3 plugins/acqai/scripts/acqai.py answers
```

`send` asks y/N before it sends. `-y` answers it. `--dry-run` shows what would
go out and sends nothing. `--new` starts a fresh conversation.

Config, all optional:

| Variable | Default | Meaning |
| --- | --- | --- |
| `ACQAI_STATE_DIR` | `~/.config/acqai` | The folder that holds the login, the route, the chat id, the venv, and the answers. |
| `MOZI_COMPANY` | (saved by `setup --company`) | The company to click on the sign-in list. |
| `MOZI_BASE` | (learned route, else portal) | Override the app origin. Prefer `login` / `login-legacy`. Re-run login after switching. |
| `MOZI_MIN_DELAY`, `MOZI_MAX_DELAY` | `12`, `30` | Seconds between sends, a random gap in that range. |
| `MOZI_SEND_OK` | (unset) | `1` gives the yes for the whole shell. `-y` gives it for one send, which is the better habit. |
| `MOZI_TOKEN` | (unset) | The `--http` path only: the whole Cookie header from a signed-in request. Prefer the browser path; a captured legacy Clerk cookie dies in about a minute. |
| `ACQAI_LOGIN_WAIT` | `600` | How long `login` waits for you, in seconds. |

## Changelog

Version history and the versioning contract are in [CHANGELOG.md](CHANGELOG.md).
Released versions are git tags `acqai--vX.Y.Z` (Claude) and `vX.Y.Z`. A version
bump on `main` is tagged and published as a GitHub Release automatically.
`./release.sh --yes --push` does the same by hand.

## License

MIT. Take it, change it, ship your own.
