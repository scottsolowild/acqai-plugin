# acqai-plugin

## Commits and releases

Commit messages are Conventional Commits, and they never name a version. CI stamps the version after each push to `main`, from the commits merged since the last release, so the order things merge in does not change the result.

- `fix:` releases a patch, and `feat:` a minor. A `!` after the type (`feat!:`) or a `BREAKING CHANGE:` footer releases a major, or a minor while the version is 0.x.
- `docs`, `test`, `ci`, `build`, `refactor`, `style`, `chore`, and `revert` release nothing on their own.
- Leave `plugin.json`'s version and `CHANGELOG.md`'s version sections alone. CI writes both in one commit, tags it `vX.Y.Z` and `acqai--vX.Y.Z`, and opens the GitHub Release. A commit's subject becomes its CHANGELOG line, and the first paragraph of its body the note under it, so write both for a member reading the release.
- `./release.sh` shows the next release without changing anything, and `./release.sh lint` checks the messages since the last release. The commit-msg hook runs that check on each commit once a clone turns it on with `git config core.hooksPath .githooks`.

## Pull requests

A PR description runs why, what, how, now. It opens with a line or two on why the change matters to a member, the failure it prevents or what it adds, before any file or mechanism. Then it says what changed, and how it works and how it was checked. It ends on what the reviewer does next, such as a step after the merge or a decision left open. The commit message carries the same why, what, and how, so the first paragraph of its body, the note a release prints, gives a member the why too.
