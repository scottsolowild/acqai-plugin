#!/usr/bin/env bash
# Tag the version in plugin.json as an annotated git tag vX.Y.Z.
#
#   ./release.sh           # dry-run: name the tag, write nothing
#   ./release.sh --yes     # create the tag on HEAD
#   ./release.sh -h        # this help
#
# A release is three things in the same commit, then this tag on that commit:
# plugin.json's version, the matching ## [X.Y.Z] at the top of CHANGELOG.md,
# and git tag vX.Y.Z. Push the tag after: git push origin vX.Y.Z
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

YES=0
case "${1:-}" in
  -h|--help|\?) awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "$0"; exit 0 ;;
  --yes|-y) YES=1 ;;
  "") ;;
  *) echo "release: unknown arg: $1 (try -h)" >&2; exit 2 ;;
esac

version="$(python3 - <<'PY'
import json
print(json.load(open("plugins/acqai/.claude-plugin/plugin.json"))["version"])
PY
)"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-].+)?$ ]]; then
  echo "release: plugin.json version is not SemVer: $version" >&2
  exit 1
fi

newest="$(python3 - <<'PY'
import re
text = open("CHANGELOG.md").read()
m = re.search(r"^## \[([^\]]+)\]", text, re.M)
print(m.group(1) if m else "")
PY
)"
if [[ "$newest" != "$version" ]]; then
  echo "release: plugin.json is $version, CHANGELOG.md's first heading is ${newest:-none}. Bump both first." >&2
  exit 1
fi

tag="v${version}"
if git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
  at="$(git rev-list -n1 "$tag")"
  head="$(git rev-parse HEAD)"
  if [[ "$at" == "$head" ]]; then
    echo "release: $tag already points at HEAD"
    exit 0
  fi
  echo "release: $tag already exists at ${at:0:7} (HEAD is ${head:0:7})" >&2
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "release: working tree is dirty; commit the version bump first" >&2
  exit 1
fi

msg="acqai ${version}"
if [[ "$YES" -eq 0 ]]; then
  echo "DRY RUN, would tag HEAD as $tag"
  echo "  $(git rev-parse --short HEAD)  $(git log -1 --format=%s)"
  echo "  message: $msg"
  echo
  echo "Nothing changed. Re-run with --yes to create the tag."
  exit 0
fi

git tag -a "$tag" -m "$msg"
echo "tagged $tag → $(git rev-parse --short HEAD)"
echo "push with: git push origin $tag"
