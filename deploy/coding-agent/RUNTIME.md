# Remote coding environment

Respond in Japanese unless the user requests another language. Work in the current project directory.
This is hb-gpu-0, reached over SSH; localhost in a browser on the user's computer is a different machine.

## Sessions and context

The wrapper resumes the latest conversation for the exact working directory. `qwen-code history` lists it,
`qwen-code --new` starts a new conversation, and `qwen-code --session ID` resumes a specific one.
Original sessions are stored in ~/.local/share/opencode/opencode.db (or XDG_DATA_HOME/opencode).
Do not delete sessions or clear caches to fix context errors. Use /compact for an existing long conversation.
Search with rg and read bounded line ranges. Save long logs to files and inspect selected lines;
avoid dumping entire result datasets, lockfiles, process command lines, or generated distributions.
The input budget is 24,576 with 8,192 reserved, so automatic compaction begins at 16,384 tokens.

## Launching applications

Use `qwen-code app start` for this repository's development OpenUI. It starts a project-scoped systemd
process on the Tailscale address, waits for HTTP readiness, and reports the verified URL.
It shares the existing API at http://100.91.77.114:8768; it does not load another GPU model.
Use `qwen-code app status`, `qwen-code app logs`, and `qwen-code app stop` to manage it.
Use `qwen-code app --help` for other HTTP applications. Pass a command that binds the specified host and port.
Do not use `timeout N ...`, bare `... &`, or an unverified printed PID as evidence that a server is running.
If a shell tool times out, inspect process state and HTTP responses before reporting success.
Never kill an unrelated process to free a port; choose an unused port or stop only a managed app you own.
The canonical app is :8768; :8770 redirects there. Development UI changes appear at :8771.
After a UI change, check the served page and its API calls. A successful build alone does not prove deployment.
Report the exact URL, health response, and remaining failures. Do not claim success from a failed HTTP request.

## GPU memory and other people's processes

The GPU is shared with other researchers and with the llama.cpp server that runs this coding agent itself
(`breakllm-coding-model.service`, llama-server on 127.0.0.1:8787). Never kill, stop, or restart a process to
free GPU memory; never kill a process you did not start in this session. If `nvidia-smi` shows no room for a
model or a training run, stop and report the memory table to the user instead. Prefer CPU or smaller models for
tests. Loading a second copy of a model that the chat service already holds is the usual cause of CUDA OOM.
