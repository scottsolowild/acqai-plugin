# Changelog

All notable changes to acqai are recorded here. Versions follow SemVer, and
the contract those versions promise is three things: the natural-language
skill behavior, the documented script commands and flags, and where the script
keeps its state (`~/.config/acqai`). A **major** bump means one of those
changed in a breaking way, a **minor** bump adds capability, and a **patch**
fixes without touching the contract. Pre-1.0, the contract is not frozen yet,
so expect the shape to move.

CI writes each version. After every push to `main`, it reads the commits
merged since the last release: a `fix` is a patch, a `feat` is a minor, and a
breaking change is a major, or a minor while the version is 0.x. It stamps the
version into `plugin.json` and a new section here, commits both, tags that
commit `acqai--vX.Y.Z` (Claude Code's release convention) and `vX.Y.Z` (plain
SemVer), and opens a GitHub Release. That is the publish for this marketplace:
members who added `scottsolowild/acqai-plugin` pick up the new version on
their next marketplace update. Anthropic's community catalog is a separate one-time
submit ([platform.claude.com/plugins/submit](https://platform.claude.com/plugins/submit));
after approval their CI bumps the pin when this repo moves. `./release.sh`
shows what the next release would be.

## [0.8.0] - 2026-09-25

### Added
- /acq -y writes Adopt edits with no further ask (062694c)
  The flag covers the sends and the file changes. When the loop ends, write every Adopt change (new offer or page when the task calls for one), record the outcome, and stop. Step-by-step /acq still waits before editing.

## [0.7.1] - 2026-09-25

### Fixed
- /acq -y is the yes, do not ask again in chat (34c2b03)
  The flag on the command is consent for the first send and follow-ups. Show the question for the record, then send without waiting for Reply yes.

## [0.7.0] - 2026-09-25

### Added
- /acq -y runs the full loop on one yes (1d690b1)
  Step-by-step /acq still asks before each send. /acq -y (or --yes) shows the first question once, waits for one yes, then sends that question and up to two follow-ups with -y on each send, without asking again between them.

## [0.6.1] - 2026-09-25

### Fixed
- End a portal reply where the portal's own chat page ends it (c24c3e6)
  send now reads a portal reply the way ACQ AI's own chat page does. A reply that stops on an error, or that ends before it finished, fails with the part it got kept on file, even when the error comes before any reply text. A reply that arrives whole in its closing event, with nothing streamed before it, comes through instead of failing as empty.

## [0.6.0] - 2026-09-25

### Added
- /acq alone gets ready, and every ask runs the same check first (7a15df7)
  `/acq` with no task is now the readiness run. A new `ready` command checks what a send needs, in order, and fixes what it can: it runs `setup` when Playwright is missing, opens the sign-in window when no login is saved or the saved session has expired (a headless check first), then confirms the route and the chat the next send would continue, and ends with the state of things and `ready`. An ask runs the same check before the question is shaped. `ready --dry-run` names the steps and runs nothing, browser included.

## [0.5.2] - 2026-09-24

### Fixed
- Fail a cut-short answer on the older app's stream too (fea1eb3)
  When the older ACQ AI app (login-legacy) stops a reply partway with an error, send now exits 1 and files the part it got, marked where the reply stopped and why, the way a portal reply that stops partway already does. When that app sends an error before any reply, send exits 1 and names the error, where it had filed the raw stream as the answer.

## [0.5.1] - 2026-09-24

### Fixed
- Fail a send whose reply stops partway, and keep the part on file (4fb78f5)
  When ACQ AI's reply stops partway with an error, send now exits 1 and files the part it got in the conversation's answer file, with a closing line that says where the reply stopped and why. It had kept the part as the whole answer and exited 0. The conversation holds your question, so send --continue on that file can ask again there.

## [0.5.0] - 2026-09-24

### Added
- Keep one answer file per conversation, read as a digest first (fdbbd72)
  A follow-up in the same chat lands in the file its question started, so a conversation reads top to bottom in one place. The file opens on the question in one line, the best answer, and what changed. Each message follows under who sent it, Claude ➡️ ACQ or ACQ ➡️ Claude, with its own headings a level below, then the commands that ran and a timeline.

## [0.4.0] - 2026-09-24

### Added
- `send --continue ANSWER` goes back to the conversation an answer on file used, named by its file name, a prefix of it, or its path. That chat was saved with its app, so a chat from the other app still stops before it goes out.
- A private-names list, `private-names.txt` in the state folder, kept with `names add` and `names remove`. When a question includes one of the names, the send stops before it asks for your yes, and the dry run shows it as a NOT line.
- `outcome ANSWER --adopt "…" --later "…" --drop "…"` records what came of an answer at the end of its file, and `outcome ANSWER` reads it back. `answers` shows each answer's chat and that record.
- `send --paste` takes the question from the clipboard, after an optional note.
- The unit tests run in CI: the transport's tests, shared byte for byte with the notes repo, and the script's own.

### Changed
- `send --dry-run` exits 1 when a line says NOT ready, so a caller can check before it asks for a yes. It still exits 0 when every check passes.

## [0.3.2] - 2026-09-24

### Fixed
- `send --file -` reaches the venv with the question intact. The question was read from stdin before the hop into the private venv, and the child re-read an empty stdin and stopped with "--file - is empty; nothing to send". The text now goes to the child as its stdin.

## [0.3.1] - 2026-09-24

### Changed
- The question asks ACQ AI, when a follow-up brings in more of the situation, to build on its last answer and say what the new detail changes.

### Fixed
- An answer from the portal carries only the reply. The portal's stream tags its reasoning the way it tags the reply, and a paragraph of reasoning came back glued to the front of the answer.
- A saved conversation goes only to the app that started it. A chat from the older app was sent to the portal, which has never seen it, and the send failed. Now a send on the other app stops before it goes out, and it says that `--new` starts a fresh conversation there.
- `login`'s sign-in loop waits with `page.wait_for_timeout` too, so the route listener runs during its pauses.

## [0.3.0] - 2026-09-24

### Changed
- After an answer, Claude refines it with a follow-up question or two in the same conversation, each bringing in a number or a result from your docs that ACQ AI did not have yet. It pushed back on the answer before. Only the skill, the `/acq` command, the README, and the plugin description changed for it.

### Fixed
- A send on the portal waits for a chat page that can take a message. A session with no workspace picked drew "Choose a workspace" at /advisor, and every send from it got a 403 ("ACQ AI and Command Center access is not active."). The send now picks the company set with `setup --company` on that list, and when the company is not on it, it stops before sending and names the setting. A refused send reports the server's reason.
- `login` could sit on "Send one short message in the window" for its full five minutes after the member had sent one. The wait between checks was `time.sleep`, and Playwright's sync API delivers the request that teaches the chat route only while a Playwright call runs. It now waits with `page.wait_for_timeout`, so the first message is seen within a second.

## [0.2.2] - 2026-09-24

### Fixed
- `send`, `login`, `login-legacy`, and `probe --session` move into the private venv again when `python3` is the Python the venv was built from, which is the default with Homebrew Python on a Mac. They had stayed in the system Python and stopped with "Playwright is not installed", which running `setup` could not fix.
- `send --dry-run --new` keeps the saved conversation, and its `chat:` line says "new conversation". Only a send that goes ahead starts the new one; a no at the y/N prompt keeps the old one too.

## [0.2.1] - 2026-09-24

### Added
- `login-legacy`: sign into the older ai.acquisition.com/chat app. `login` stays on portal. After a route is learned, sends follow that endpoint's host without keeping `MOZI_BASE` set.

## [0.2.0] - 2026-09-24

### Changed
- Default app is portal.acquisition.com/advisor (Aegis; sandbar `chat` then `chat-stream`). Use `login-legacy` for the older /chat app. Re-run login after switching so the learned route matches.

### Added
- Dual transport: portal and legacy in the same script, picked by `MOZI_BASE` and the shape of the learned route.

## [0.1.0] - 2026-09-23

### Fixed
- Install leads with two paths: terminal (`claude plugin marketplace add` / `install`), or Claude Code Desktop on a local session. `/plugin` in the Desktop composer is not available; if Add marketplace fails, use the terminal.

### Added
- First release: the `acq` skill and the `/acq` command.
- `setup`: a private venv with Playwright and its Chromium, and the company to pick on ACQ AI's sign-in list.
- `login`: sign into ACQ AI once in a browser window. The first message you send there teaches the script the chat route, so there is no request to copy out of the browser.
- `send`: one question, one answer, through your own signed-in browser. It asks for your yes first, keeps the conversation across sends, and files each answer under `~/.config/acqai/answers/`.
- `probe`, `discover --from-curl`, and `answers`.
