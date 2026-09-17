#!/usr/bin/env bash
# Print a self-contained installer, so installing needs a single SSH connection:
#   ssh hb-gpu-0 bash /home/ubuntu/kariyama/BreakLLM/deploy/coding-agent/local/bundle.sh | bash
set -euo pipefail
deploy=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
printf 'QWEN_CODE_BUNDLE=%s\n' \
  "$(tar -C "$deploy" -cf - client.py opencode.json context-guard.mjs versions.env local | base64 | tr -d '\n')"
printf 'QWEN_CODE_BUNDLE_SERVER_DIR=%q\n' "$(cd -- "$deploy/../.." && pwd)"
cat "$deploy/local/install.sh"
