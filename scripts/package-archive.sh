#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_DIR="$ROOT_DIR/ojet-oci-genai-chat"
ARCHIVE_NAME="${1:-ojet-oci-genai-chat.zip}"
OUTPUT_PATH="$ROOT_DIR/$ARCHIVE_NAME"

if [[ ! -d "$PROJECT_DIR" ]]; then
  echo "Project directory '$PROJECT_DIR' not found" >&2
  exit 1
fi

cd "$ROOT_DIR"
zip -r "$OUTPUT_PATH" "$(basename "$PROJECT_DIR")" \
  -x "*/node_modules/*" \
     "*/.git/*" \
     "*.log" \
     "*.tmp" \
     "*~" >/dev/null

echo "Created archive $OUTPUT_PATH"
