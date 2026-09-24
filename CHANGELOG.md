# Changelog

All notable changes to acqai are recorded here. Versions follow SemVer, and
the contract those versions promise is three things: the natural-language
skill behavior, the documented script commands and flags, and where the script
keeps its state (`~/.config/acqai`). A **major** bump means one of those
changed in a breaking way, a **minor** bump adds capability, and a **patch**
fixes without touching the contract. Pre-1.0, the contract is not frozen yet,
so expect the shape to move.

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
