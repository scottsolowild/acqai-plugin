# Changelog

All notable changes to acqai are recorded here. Versions follow SemVer, and
the contract those versions promise is three things: the natural-language
skill behavior, the documented script commands and flags, and where the script
keeps its state (`~/.config/acqai`). A **major** bump means one of those
changed in a breaking way, a **minor** bump adds capability, and a **patch**
fixes without touching the contract. Pre-1.0, the contract is not frozen yet,
so expect the shape to move.

Each released version is two git tags on the commit that bumps `plugin.json`
and this file together: `acqai--vX.Y.Z` (Claude Code's release convention)
and `vX.Y.Z` (plain SemVer). A push to `main` that bumps the version tags
both and opens a GitHub Release. That is the publish for this marketplace:
members who added `scottsolowild/acqai-plugin` pick up the bump on their next
marketplace update. Anthropic's community catalog is a separate one-time
submit ([platform.claude.com/plugins/submit](https://platform.claude.com/plugins/submit));
after approval their CI bumps the pin when this repo moves. By hand:
`./release.sh --yes --push`.

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
