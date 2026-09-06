#!/usr/bin/env bash
set -euo pipefail
deployment_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_dir=$(cd -- "$deployment_dir/../.." && pwd)
runtime="$project_dir/.coding-agent"
test -f "$runtime/logs/setup-ready"
test -x "$runtime/bin/llama-server"
test -x "$runtime/bin/opencode"
if ! grep -qxF '/.coding-agent/' "$project_dir/.gitignore"; then
  printf '\n/.coding-agent/\n' >> "$project_dir/.gitignore"
fi
install -d /home/ubuntu/.config/systemd/user /home/ubuntu/.local/bin
install -m 644 "$deployment_dir/breakllm-coding-model.service" \
  /home/ubuntu/.config/systemd/user/breakllm-coding-model.service
install -m 755 "$deployment_dir/qwen-code" /home/ubuntu/.local/bin/qwen-code
systemctl --user daemon-reload
systemctl --user enable breakllm-coding-model.service
systemctl --user restart breakllm-coding-model.service
echo 'Started. Readiness: curl --fail http://127.0.0.1:8787/health'
