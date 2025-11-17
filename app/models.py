from sqlmodel import SQLModel, Field, Index
from typing import Optional, Literal, Any, List
from pydantic import BaseModel


class Movie(SQLModel, table=True):
    """Movie model with indices on year and genres for query performance"""
    
    __tablename__ = "movies"
    __table_args__ = (
        Index("idx_year", "year"),
        Index("idx_genres", "genres"),
    )
    
    id: Optional[int] = Field(default=None, primary_key=True)
    movie_name: str = Field(index=False)
    year: int = Field(index=True)
    genres: str = Field(index=True)  # Stored as comma-separated string
    rating: Optional[float] = Field(default=None)


class JobCreatedResponse(BaseModel):
    """Pydantic model for job creation response"""
    job_id: str
    status: str


class JobStatusResponse(BaseModel):
    """Pydantic model for job status response"""
    job_id: str
    type: Literal["export", "import"]
    status: str
    progress: int
    message: Optional[str] = None
    current: Optional[int] = None
    total: Optional[int] = None
    result: Optional[Any] = None
    error: Optional[str] = None


class PaginatedMoviesResponse(BaseModel):
    """Paginated response for movie queries"""
    page: int
    page_size: int
    total_items: int
    total_pages: int
    items: List[Movie]

