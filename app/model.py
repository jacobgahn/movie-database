from typing import Annotated
from pydantic import BaseModel, Field


class MoviesQuery(BaseModel):
    start_year: Annotated[str, Field(description="Start year of the movie, inclusive")]
    end_year: Annotated[str, Field(description="Start year of the movie, inclusive")]
    genre: Annotated[str, Field(description="genre to include")]
