#!/usr/bin/env bash
set -euo pipefail
# First argument is a project directory on hb-gpu-0, not on the local computer.
remote_project=${1:-/home/ubuntu/kariyama/BreakLLM-dev}
# POSIX shell quoting preserves spaces, apostrophes, and shell metacharacters.
quoted_project="'${remote_project//\'/\'\\\'\'}'"
exec ssh -t -o BatchMode=yes -o ConnectTimeout=12 -o ClearAllForwardings=yes \
  hb-gpu-0 "/home/ubuntu/.local/bin/qwen-code $quoted_project"
