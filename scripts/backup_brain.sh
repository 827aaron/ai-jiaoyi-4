#!/bin/sh
# Bundle brain/'s local git history onto the external disk. The backup never leaves this machine.
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
DEST=/Volumes/jiaoyi/ai-jiaoyi-4-backup
KEEP=14
[ -d /Volumes/jiaoyi ] || { echo "external disk not mounted" >&2; exit 1; }
mkdir -p "$DEST"
OUT="$DEST/brain-$(date +%Y%m%d-%H%M%S).bundle"
git -C "$ROOT/brain" bundle create "$OUT.tmp" --all
git -C "$ROOT/brain" bundle verify -q "$OUT.tmp"
mv "$OUT.tmp" "$OUT"
ls -1t "$DEST"/brain-*.bundle | tail -n +$((KEEP + 1)) | while read -r old; do rm -- "$old"; done
echo "$OUT"
