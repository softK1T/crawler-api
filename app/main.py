from fastapi import FastAPI
from app.api.v1.router import api_router

app = FastAPI(
    title="Crawler API",
    version="1.0.0",
    description="High-performance web crawling microservice"
)

app.include_router(api_router)


@app.get("/")
async def root():
    return {"message": "Crawler API v1.0.0"}


@app.get("/health")
async def health_check():
    return {"status": "healthy"}
