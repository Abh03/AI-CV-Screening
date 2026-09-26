import json
import time
import torch
from app.stage2_retrieval.reranker import get_reranker_model

model = get_reranker_model()
pairs = [["Build secure Python SQL APIs and support production releases",
          "Software engineer built Python API services using PostgreSQL, Docker and unit testing."] for _ in range(12)]
results = []
for threads in (torch.get_num_threads(), 2, 1):
    torch.set_num_threads(threads)
    model.predict(pairs)
    started = time.monotonic()
    scores = None
    for _ in range(8):
        scores = model.predict(pairs)
    results.append({"threads": threads, "iterations": 8, "pairs_per_iteration": len(pairs),
                    "seconds": round(time.monotonic() - started, 4),
                    "first_score": float(scores[0])})
print(json.dumps({"input": pairs[:1], "output": results}), flush=True)
