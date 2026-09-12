#!/usr/bin/env bash
set -euo pipefail

case "${1:-}" in
  '') apply_args=(); music_mount=',readonly' ;;
  --apply) apply_args=(--apply); music_mount='' ;;
  *) echo 'Usage: run_unify.sh [--apply]' >&2; exit 2 ;;
esac
if (( $# > 1 )); then
  echo 'Usage: run_unify.sh [--apply]' >&2
  exit 2
fi

bundle_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
library_dir='/zfs/media/Music'
backup_dir='/zfs-ssd/media-tools-backups'
test -d "$library_dir"
mkdir -p -m 700 "$backup_dir"
docker build --quiet -f "$bundle_dir/Dockerfile.unify" \
  -t media-tools-unify:mutagen-1.48.1 "$bundle_dir"
exec docker run --rm --network none --user 0:0 \
  --read-only --tmpfs /tmp:rw,nosuid,nodev,mode=1777 \
  --mount "type=bind,src=$library_dir,dst=$library_dir$music_mount" \
  --mount "type=bind,src=$backup_dir,dst=$backup_dir" \
  media-tools-unify:mutagen-1.48.1 "$library_dir" \
  --backup-dir "$backup_dir" "${apply_args[@]}"
