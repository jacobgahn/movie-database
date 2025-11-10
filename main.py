from fastapi import FastAPI
from app.database import init_db

app = FastAPI(
    title="Movie API",
    version="1.0.0",
)


@app.on_event("startup")
def on_startup():
    """Initialize database on startup"""
    init_db()


from app.movies import router as movies_router

app.include_router(movies_router)
