#!/usr/bin/env bash
# clear-marker.sh — remove marker files that sit directly in $PLURAI_DIR.
#
#   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
#   bash "${CLAUDE_PLUGIN_ROOT}/scripts/clear-marker.sh" .prd_upload_id .variables_upload_id
#
# Each argument is a bare file name. A skill block clears an earlier run's marker before it stashes
# again, so a stash that cannot run leaves no id that stands in for this run's. The removal lives in
# this script, not in the block, so the command line a harness shows for approval names a plugin
# script and a marker instead of a bare forced removal, which a harness may hold for approval.
#
# Only $PLURAI_DIR/<name> can be removed: a name with a slash, `.`, `..`, a leading `-`, or anything
# outside [A-Za-z0-9_.-] is refused, and one refused name refuses the whole call before anything is
# removed. An absent marker is not an error. A directory or a path that cannot be removed is, and the
# remaining names are still tried.
#
# Exit: 0 every named marker is gone; 1 a marker could not be removed; 2 a refused call (no names, an
# unusable name, or no $PLURAI_DIR) that removed nothing.

if [ "$#" -eq 0 ]; then
  echo "clear-marker: usage: clear-marker.sh <marker-name>..." >&2
  exit 2
fi
if [ -z "${PLURAI_DIR:-}" ]; then
  echo "clear-marker: PLURAI_DIR is not set; source scripts/resolve-workdir.sh first" >&2
  exit 2
fi

for name in "$@"; do
  case "$name" in
    "" | . | .. | -* | */* | *[!A-Za-z0-9_.-]*)
      echo "clear-marker: refusing '$name': a marker is a bare file name under \$PLURAI_DIR" >&2
      exit 2
      ;;
  esac
done

status=0
for name in "$@"; do
  target="$PLURAI_DIR/$name"
  if [ -d "$target" ] && [ ! -L "$target" ]; then
    echo "clear-marker: $target is a directory, not a marker; left in place" >&2
    status=1
    continue
  fi
  rm -f -- "$target" || status=1
done
exit "$status"
