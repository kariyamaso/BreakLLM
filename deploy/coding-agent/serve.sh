#!/usr/bin/env bash
set -euo pipefail
deployment_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_dir=$(cd -- "$deployment_dir/../.." && pwd)
source "$deployment_dir/versions.env"
export CUDA_VISIBLE_DEVICES=0
exec "$project_dir/.coding-agent/bin/llama-server" \
  --model "$project_dir/.coding-agent/models/$MODEL_FILE" \
  --alias qwen3.8-27b-turbo \
  --host 127.0.0.1 --port 8787 \
  --ctx-size 32768 --parallel 1 --n-gpu-layers 99 \
  --flash-attn on --cache-type-k q8_0 --cache-type-v q8_0 \
  --batch-size 512 --ubatch-size 128 --threads 4 \
  --cache-ram 1024 --jinja --reasoning-format deepseek --reasoning-effort low \
  --temp 0.6 --top-p 0.95 --top-k 20 --min-p 0 --repeat-penalty 1 \
  --n-predict 8192 --metrics
