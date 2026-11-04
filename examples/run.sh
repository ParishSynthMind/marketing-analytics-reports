#!/bin/sh
# Run the job with secrets read from the macOS Keychain at run time, so they never sit in a file.
#   security add-generic-password -s mkt-report-meta-token -a "$USER" -w      (paste the Meta token)
#   security add-generic-password -s anthropic-api-key -a "$USER" -w          (optional, for the AI summary)
# Usage: examples/run.sh run --email
set -e
cd "$(dirname "$0")/.."
META_ACCESS_TOKEN="$(security find-generic-password -s mkt-report-meta-token -w 2>/dev/null || true)"
ANTHROPIC_API_KEY="$(security find-generic-password -s anthropic-api-key -w 2>/dev/null || true)"
export META_ACCESS_TOKEN ANTHROPIC_API_KEY
exec .venv/bin/python -m mkt_report "$@"
