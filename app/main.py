from fastapi import FastAPI
from app.api.endpoints import router as screening_router

app = FastAPI(
    title="Automated CV Screening & Ranking Engine",
    version="1.0.0",
    description="Multi-stage deterministic + LLM hybrid candidate screening pipeline."
)

app.include_router(screening_router)


@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "CV Screening Engine"}