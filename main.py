from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.database import init_db

app = FastAPI(
    title="Movie API",
    version="1.0.0",
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    yield

from app.movies import router as movies_router

app.include_router(movies_router)
