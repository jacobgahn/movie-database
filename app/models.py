from sqlmodel import SQLModel, Field, Index
from typing import Optional


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

