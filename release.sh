#!/usr/bin/env bash
# Stamp and tag a release from the Conventional Commits merged since the last.
#
#   ./release.sh                  dry run: the next version and its changelog
#   ./release.sh --yes            stamp plugin.json and CHANGELOG.md, commit, tag
#   ./release.sh --yes --push     the same, then push the commit and both tags
#   ./release.sh lint [RANGE]     check commit messages (default: since the last
#                                 release); --warn reports without failing
#   ./release.sh notes VERSION    the CHANGELOG section for VERSION
#   ./release.sh -h               this help
#
# CI runs ./release.sh --yes --push after each push to main, so no commit and
# no person writes a version. The logic lives in tools/release.py.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "${1:-}" in
  -h|--help|\?) awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "$0"; exit 0 ;;
esac
exec python3 "$ROOT/tools/release.py" "$@"
