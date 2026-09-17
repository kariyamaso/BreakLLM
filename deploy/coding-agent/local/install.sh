#!/usr/bin/env bash
# Install qwen-code on your own computer (macOS or Linux, including WSL):
#   ssh hb-gpu-0 bash /home/ubuntu/kariyama/BreakLLM/deploy/coding-agent/local/bundle.sh | bash
# bundle.sh prepends the client files, so no second SSH connection is needed.
# Options after `bash -s --`: --host SSH_HOST  --port LOCAL_PORT  --server-dir DIR
set -euo pipefail
host=hb-gpu-0
port=18787
server_dir=${QWEN_CODE_BUNDLE_SERVER_DIR:-/home/ubuntu/kariyama/BreakLLM}
while (($#)); do
  case $1 in
    --host) host=$2; shift 2 ;;
    --port) port=$2; shift 2 ;;
    --server-dir) server_dir=$2; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
share=${QWEN_CODE_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/qwen-code}
bin_dir=${QWEN_CODE_BIN_DIR:-$HOME/.local/bin}

for command in ssh curl tar python3; do
  command -v "$command" >/dev/null || { echo "qwen-code install: $command is required" >&2; exit 1; }
done
case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) asset=opencode-darwin-arm64.zip; key=OPENCODE_SHA256_DARWIN_ARM64 ;;
  Darwin-x86_64) asset=opencode-darwin-x64.zip; key=OPENCODE_SHA256_DARWIN_X64 ;;
  Linux-x86_64) asset=opencode-linux-x64.tar.gz; key=OPENCODE_SHA256 ;;
  Linux-aarch64 | Linux-arm64) asset=opencode-linux-arm64.tar.gz; key=OPENCODE_SHA256_LINUX_ARM64 ;;
  *) echo "qwen-code install: unsupported platform $(uname -sm); on Windows use WSL" >&2; exit 1 ;;
esac

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir "$work/files"
if [[ -n ${QWEN_CODE_BUNDLE:-} ]]; then
  printf '%s' "$QWEN_CODE_BUNDLE" |
    python3 -c 'import base64, sys; sys.stdout.buffer.write(base64.b64decode(sys.stdin.read()))' |
    tar -C "$work/files" -xf -
else
  # stdin is this script when piped into bash, so ssh must not read it.
  echo "Fetching client files from $host:$server_dir"
  ssh -n -o ClearAllForwardings=yes -o ConnectTimeout=15 "$host" \
    "tar -C '$server_dir/deploy/coding-agent' -cf - client.py opencode.json context-guard.mjs versions.env local" |
    tar -C "$work/files" -xf -
fi
source "$work/files/versions.env"
expected=${!key}

echo "Downloading OpenCode $OPENCODE_VERSION ($asset)"
curl --fail --location --retry 3 --silent --show-error \
  "https://github.com/anomalyco/opencode/releases/download/v$OPENCODE_VERSION/$asset" \
  --output "$work/$asset"
if command -v sha256sum >/dev/null; then
  actual=$(sha256sum "$work/$asset" | cut -d' ' -f1)
else
  actual=$(shasum -a 256 "$work/$asset" | cut -d' ' -f1)
fi
if [[ $actual != "$expected" ]]; then
  echo "qwen-code install: checksum mismatch for $asset" >&2
  exit 1
fi
mkdir "$work/opencode"
case $asset in
  *.zip) unzip -q "$work/$asset" -d "$work/opencode" ;;
  *) tar -xzf "$work/$asset" -C "$work/opencode" ;;
esac
"$work/opencode/opencode" --version >/dev/null

mkdir -p "$share/bin" "$bin_dir"
cp "$work/files/client.py" "$work/files/opencode.json" "$work/files/context-guard.mjs" \
  "$work/files/versions.env" "$share/"
cp "$work/files/local/RUNTIME.md" "$share/RUNTIME.md"
install -m 755 "$work/opencode/opencode" "$share/bin/opencode"
printf 'QWEN_CODE_HOST=%q\nQWEN_CODE_PORT=%q\nQWEN_CODE_SERVER_DIR=%q\n' \
  "$host" "$port" "$server_dir" > "$share/config.env"
install -m 755 "$work/files/local/qwen-code" "$bin_dir/qwen-code"

echo "Installed qwen-code (OpenCode $OPENCODE_VERSION) to $bin_dir/qwen-code"
case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *) echo "Add to your shell profile: export PATH=\"$bin_dir:\$PATH\"" ;;
esac
echo "Start in any project directory: cd your-project && qwen-code --new"
