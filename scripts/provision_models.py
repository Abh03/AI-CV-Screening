"""Download fixed model revisions into the image at build time."""
from huggingface_hub import snapshot_download

MODELS = (
    ("sentence-transformers/all-MiniLM-L6-v2", "bc57282bc374d33e0d6c4de27f12dc1c2a87f37a", "/opt/models/embedding"),
    ("cross-encoder/ms-marco-MiniLM-L6-v2", "ce0834f22110de6d9222af7a7a03628121708969", "/opt/models/reranker"),
)

for repo, revision, destination in MODELS:
    snapshot_download(repo_id=repo, revision=revision, local_dir=destination,
                      ignore_patterns=["*.bin", "*.onnx", "*.msgpack", "*.h5", "*.ot", "*.xml"])
