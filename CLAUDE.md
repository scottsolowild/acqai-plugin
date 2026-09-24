# acqai-plugin

## Commits and releases

Commit messages are Conventional Commits, and they never name a version. CI stamps the version after each push to `main`, from the commits merged since the last release, so the order things merge in does not change the result.

- `fix:` releases a patch, and `feat:` a minor. A `!` after the type (`feat!:`) or a `BREAKING CHANGE:` footer releases a major, or a minor while the version is 0.x.
- `docs`, `test`, `ci`, `build`, `refactor`, `style`, `chore`, and `revert` release nothing on their own.
- Leave `plugin.json`'s version and `CHANGELOG.md`'s version sections alone. CI writes both in one commit, tags it `vX.Y.Z` and `acqai--vX.Y.Z`, and opens the GitHub Release. A commit's subject becomes its CHANGELOG line, and the first paragraph of its body the note under it, so write both for a member reading the release.
- `./release.sh` shows the next release without changing anything, and `./release.sh lint` checks the messages since the last release. The commit-msg hook runs that check on each commit once a clone turns it on with `git config core.hooksPath .githooks`.
