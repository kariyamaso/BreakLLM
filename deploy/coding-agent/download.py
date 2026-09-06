"""Download the pinned public GGUF through Hugging Face's resumable Xet client."""

import sys

from huggingface_hub import hf_hub_download

if __name__ == "__main__":
    repo, revision, filename, destination = sys.argv[1:]
    print(
        hf_hub_download(
            repo_id=repo, revision=revision, filename=filename, local_dir=destination
        ),
        flush=True,
    )
