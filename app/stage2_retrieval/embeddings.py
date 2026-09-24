from typing import List
from sentence_transformers import SentenceTransformer
from threading import BoundedSemaphore
import os
from app.config import settings

# Load compact local embedding model (384-dimensional dense vectors)
_MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_MODEL_NAME = _MODEL_NAME
EMBEDDING_MODEL_VERSION = "bc57282bc374d33e0d6c4de27f12dc1c2a87f37a"
_model_instance = None
_embedding_slots = BoundedSemaphore(settings.EMBEDDING_CONCURRENCY_LIMIT)


def get_embedding_model() -> SentenceTransformer:
    """Lazy initializer for sentence transformer model."""
    global _model_instance
    if _model_instance is None:
        _model_instance = SentenceTransformer(os.getenv("EMBEDDING_MODEL_PATH", _MODEL_NAME))
    return _model_instance


def generate_embeddings(texts: List[str]) -> List[List[float]]:
    """
    Generates 384-dimensional dense vector embeddings for input texts.
    """
    if not texts:
        return []

    with _embedding_slots:
        model = get_embedding_model()
        embeddings = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
        return embeddings.tolist()


def generate_single_embedding(text: str) -> List[float]:
    """Generates embedding vector for a single text query."""
    if not text:
        return [0.0] * 384
    return generate_embeddings([text])[0]
