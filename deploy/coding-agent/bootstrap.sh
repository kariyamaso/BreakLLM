#!/usr/bin/env bash
set -euo pipefail
deployment_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_dir=$(cd -- "$deployment_dir/../.." && pwd)
runtime="$project_dir/.coding-agent"
source "$deployment_dir/versions.env"
mkdir -p "$runtime"/{models,logs,downloads,bin}

download_model() {
  cd "$runtime/models"
  if ! test -f "$MODEL_FILE"; then
    /home/ubuntu/.local/bin/uv venv --python python3 --allow-existing "$runtime/download-venv"
    # This standalone downloader has its own pins, separate from the research project.
    /home/ubuntu/.local/bin/uv --no-config pip install --python "$runtime/download-venv/bin/python" \
      huggingface-hub==1.30.0 hf-xet==1.6.0
    HF_XET_NUM_CONCURRENT_RANGE_GETS=8 "$runtime/download-venv/bin/python" \
      "$deployment_dir/download.py" "$MODEL_REPO" "$MODEL_REVISION" "$MODEL_FILE" "$runtime/models"
  fi
  printf '%s  %s\n' "$MODEL_SHA256" "$MODEL_FILE" | sha256sum --check
  date -Is > "$runtime/logs/model-ready"
}

build_llama() {
  local source_dir="$runtime/llama.cpp-$LLAMA_REVISION"
  if ! test -d "$source_dir"; then
    curl --fail --location --retry 5 \
      "https://github.com/ggml-org/llama.cpp/archive/$LLAMA_REVISION.tar.gz" \
      --output "$runtime/downloads/llama-source.tar.gz"
    tar -xzf "$runtime/downloads/llama-source.tar.gz" -C "$runtime"
  fi
  /home/ubuntu/.local/bin/uv venv --python python3 --allow-existing "$runtime/build-venv"
  /home/ubuntu/.local/bin/uv pip install --python "$runtime/build-venv/bin/python" 'cmake>=3.28,<5'
  export PATH="$runtime/build-venv/bin:$PATH"
  export CUDACXX=/usr/local/cuda/bin/nvcc
  cmake -S "$source_dir" -B "$source_dir/build" \
    -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=120 \
    -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF
  cmake --build "$source_dir/build" --parallel 4 --target llama-server
  ln -sfn "$source_dir/build/bin/llama-server" "$runtime/bin/llama-server"
  "$runtime/bin/llama-server" --version
  date -Is > "$runtime/logs/llama-ready"
}

install_opencode() {
  local archive="$runtime/downloads/opencode-linux-x64.tar.gz"
  curl --fail --location --retry 5 \
    "https://github.com/anomalyco/opencode/releases/download/v$OPENCODE_VERSION/opencode-linux-x64.tar.gz" \
    --output "$archive"
  printf '%s  %s\n' "$OPENCODE_SHA256" "$archive" | sha256sum --check
  tar -xzf "$archive" -C "$runtime/bin" opencode
  "$runtime/bin/opencode" --version
  date -Is > "$runtime/logs/opencode-ready"
}

if [[ ${1:-} == --download-only ]]; then
  download_model
  exit
fi

download_model > "$runtime/logs/download.log" 2>&1 &
model_pid=$!
build_llama > "$runtime/logs/build.log" 2>&1 &
llama_pid=$!
install_opencode > "$runtime/logs/opencode-install.log" 2>&1 &
opencode_pid=$!
failed=0
for pid in "$model_pid" "$llama_pid" "$opencode_pid"; do
  wait "$pid" || failed=1
done
if (( failed )); then
  echo "Setup failed. Inspect $runtime/logs/." >&2
  exit 1
fi
date -Is > "$runtime/logs/setup-ready"
echo "Model, llama.cpp, and OpenCode are ready in $runtime"
