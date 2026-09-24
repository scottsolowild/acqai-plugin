# Changelog

All notable changes to acqai are recorded here. Versions follow SemVer, and
the contract those versions promise is three things: the natural-language
skill behavior, the documented script commands and flags, and where the script
keeps its state (`~/.config/acqai`). A **major** bump means one of those
changed in a breaking way, a **minor** bump adds capability, and a **patch**
fixes without touching the contract. Pre-1.0, the contract is not frozen yet,
so expect the shape to move.

## [0.1.0] - 2026-09-23

### Fixed
- Install steps use `claude plugin marketplace add` / `claude plugin install`. The `/plugin` slash commands fail in the Claude Code desktop app.

### Added
- First release: the `acq` skill and the `/acq` command.
- `setup`: a private venv with Playwright and its Chromium, and the company to pick on ACQ AI's sign-in list.
- `login`: sign into ACQ AI once in a browser window. The first message you send there teaches the script the chat route, so there is no request to copy out of the browser.
- `send`: one question, one answer, through your own signed-in browser. It asks for your yes first, keeps the conversation across sends, and files each answer under `~/.config/acqai/answers/`.
- `probe`, `discover --from-curl`, and `answers`.
