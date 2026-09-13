#!/usr/bin/env bash
set -euo pipefail
remote_project=${BREAKLLM_REMOTE_PROJECT:-/home/ubuntu/kariyama/BreakLLM-dev}
if [[ ${1:-} == --project ]]; then
  [[ $# -ge 2 ]] || { echo '--project requires a remote directory' >&2; exit 2; }
  remote_project=$2
  shift 2
elif [[ ${1:-} == /* || ${1:-} == ./* || ${1:-} == ../* ]]; then
  remote_project=$1
  shift
fi
# Quote every argument for the remote POSIX shell; never interpolate commands.
remote_command=/home/ubuntu/.local/bin/qwen-code
for argument in --project "$remote_project" "$@"; do
  escaped_argument=${argument//\'/\'\\\'\'}
  remote_command+=" '$escaped_argument'"
done
terminal=-T
if [[ -t 0 && -t 1 ]]; then terminal=-t; fi
exec ssh "$terminal" -o BatchMode=yes -o ConnectTimeout=12 \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ClearAllForwardings=yes \
  hb-gpu-0 "$remote_command"
