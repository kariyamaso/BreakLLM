#!/usr/bin/env bash
set -euo pipefail
# Run from the repository root. The target is the explicitly configured hb-gpu-0.
test -f chat-ui/dist/index.html
rsync -az --exclude .git --exclude .venv --exclude node_modules --exclude __pycache__ \
  --exclude .ruff_cache --exclude prompt-runs --exclude uv.lock \
  -e 'ssh -o BatchMode=yes -o ConnectTimeout=12 -o ClearAllForwardings=yes' ./ hb-gpu-0:/home/ubuntu/kariyama/BreakLLM/
ssh -o BatchMode=yes -o ConnectTimeout=12 -o ClearAllForwardings=yes hb-gpu-0 \
  'install -m 644 /home/ubuntu/kariyama/BreakLLM/deploy/breakllm-chat.service /home/ubuntu/.config/systemd/user/breakllm-chat.service && systemctl --user daemon-reload && systemctl --user enable breakllm-chat.service && systemctl --user restart breakllm-chat.service'
