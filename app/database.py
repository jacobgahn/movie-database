from sqlmodel import SQLModel, create_engine, Session

# Import models to register them with SQLModel metadata
from app.models import Movie  # noqa: F401

# Database URL - SQLite for simplicity
DATABASE_URL = "sqlite:///./movies.db"

# Create engine
engine = create_engine(DATABASE_URL, echo=False)


def get_session():
    """Dependency for getting database session"""
    with Session(engine) as session:
        yield session


def init_db():
    """Initialize database - create all tables"""
    SQLModel.metadata.create_all(engine)

