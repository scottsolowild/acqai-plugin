#!/usr/bin/env bash
# Tag the version in plugin.json for a Claude Code release.
#
#   ./release.sh              # dry-run: name the tags, write nothing
#   ./release.sh --yes        # create the tags on HEAD (no push)
#   ./release.sh --yes --push # create and push to origin
#   ./release.sh -h           # this help
#
# A release is three things in the same commit, then these tags on that commit:
#   - plugin.json's version
#   - the matching ## [X.Y.Z] at the top of CHANGELOG.md
#   - git tags acqai--vX.Y.Z (Claude's convention) and vX.Y.Z (plain SemVer)
#
# On a push to main that bumps the version, CI runs this with --yes --push and
# opens a GitHub Release. That is the publish for this marketplace: members who
# added scottsolowild/acqai-plugin pick up the bump on their next marketplace
# update. Anthropic's curated catalogs are a separate, one-time submit.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

YES=0
PUSH=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help|\?) awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "$0"; exit 0 ;;
    --yes|-y) YES=1; shift ;;
    --push) PUSH=1; shift ;;
    *) echo "release: unknown arg: $1 (try -h)" >&2; exit 2 ;;
  esac
done

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

name="$(python3 - <<'PY'
import json
print(json.load(open("plugins/acqai/.claude-plugin/plugin.json"))["name"])
PY
)"
claude_tag="${name}--v${version}"
semver_tag="v${version}"
msg="${name} ${version}"
head="$(git rev-parse HEAD)"

_tag_ok() {
  local tag="$1"
  if ! git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
    return 1
  fi
  local at
  at="$(git rev-list -n1 "$tag")"
  [[ "$at" == "$head" ]]
}

need=()
for tag in "$claude_tag" "$semver_tag"; do
  if git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
    at="$(git rev-list -n1 "$tag")"
    if [[ "$at" == "$head" ]]; then
      echo "release: $tag already points at HEAD"
    else
      # Same version already released on an earlier commit (docs landed after).
      echo "release: $tag already released at ${at:0:7} (HEAD is ${head:0:7})"
    fi
  else
    need+=("$tag")
  fi
done

if [[ ${#need[@]} -eq 0 ]]; then
  if [[ "$PUSH" -eq 1 ]]; then
    git push origin "refs/tags/$claude_tag" "refs/tags/$semver_tag"
    echo "release: pushed $claude_tag and $semver_tag"
  fi
  exit 0
fi

# One tag exists and the other does not: refuse rather than split a release.
if git rev-parse -q --verify "refs/tags/$claude_tag" >/dev/null \
  || git rev-parse -q --verify "refs/tags/$semver_tag" >/dev/null; then
  echo "release: partial tags for $version; fix by hand before re-running" >&2
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "release: working tree is dirty; commit the version bump first" >&2
  exit 1
fi

if [[ "$YES" -eq 0 ]]; then
  echo "DRY RUN, would tag HEAD as:"
  for tag in "${need[@]}"; do
    echo "  $tag"
  done
  echo "  $(git rev-parse --short HEAD)  $(git log -1 --format=%s)"
  echo "  message: $msg"
  echo
  echo "Nothing changed. Re-run with --yes to create the tag(s)."
  exit 0
fi

# Prefer Claude Code's own tagger when it is on PATH: it validates the
# marketplace entry against plugin.json before writing acqai--vX.Y.Z.
if command -v claude >/dev/null 2>&1 && [[ " ${need[*]} " == *" $claude_tag "* ]]; then
  claude plugin tag plugins/acqai -m "$msg"
fi

for tag in "${need[@]}"; do
  if git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
    echo "tagged $tag → $(git rev-parse --short HEAD)"
    continue
  fi
  git tag -a "$tag" -m "$msg"
  echo "tagged $tag → $(git rev-parse --short HEAD)"
done

if [[ "$PUSH" -eq 1 ]]; then
  git push origin "refs/tags/$claude_tag" "refs/tags/$semver_tag"
  echo "release: pushed $claude_tag and $semver_tag"
else
  echo "push with: git push origin $claude_tag $semver_tag"
fi
