"""
Pytest configuration and fixtures for integration tests.
"""
import os
import tempfile
from pathlib import Path
from typing import Generator
import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine
from unittest.mock import Mock, patch, MagicMock

from app.database import get_session
from app.models import Movie
from app.celery_app import celery_app
from main import app

# Configure Celery to run tasks synchronously in tests
celery_app.conf.task_always_eager = True
celery_app.conf.task_eager_propagates = True
celery_app.conf.task_store_eager_result = True  # Store results so they can be retrieved


# Temporary directories will be created per-test


@pytest.fixture(scope="function")
def test_db() -> Generator[str, None, None]:
    """
    Create a temporary SQLite database for each test.
    Returns the database URL.
    """
    # Create a temporary database file
    db_fd, db_path = tempfile.mkstemp(suffix=".db", prefix="test_movies_")
    os.close(db_fd)
    
    # Create engine with the test database
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, echo=False)
    
    # Create all tables
    SQLModel.metadata.create_all(engine)
    
    yield database_url
    
    # Cleanup: remove the database file
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture(scope="function")
def test_session(test_db: str) -> Generator[Session, None, None]:
    """
    Create a database session for testing.
    Uses the same database file as the client fixture.
    """
    engine = create_engine(test_db, echo=False)
    with Session(engine) as session:
        yield session


@pytest.fixture(scope="function")
def client(test_db: str, tmp_path: Path) -> Generator[TestClient, None, None]:
    """
    Create a test client with overridden database dependency.
    """
    engine = create_engine(test_db, echo=False)
    
    def get_test_session():
        with Session(engine) as session:
            yield session
    
    # Create temporary directories for this test
    test_upload_dir = tmp_path / "uploads"
    test_export_dir = tmp_path / "exports"
    test_import_logs_dir = tmp_path / "import_logs"
    test_upload_dir.mkdir(exist_ok=True)
    test_export_dir.mkdir(exist_ok=True)
    test_import_logs_dir.mkdir(exist_ok=True)
    
    # Override the database dependency
    app.dependency_overrides[get_session] = get_test_session
    
    # Override upload, export, and import_logs directories using patches
    upload_patcher = patch('app.services.UPLOAD_DIR', str(test_upload_dir))
    export_patcher = patch('app.services.EXPORT_DIR', str(test_export_dir))
    import_logs_patcher = patch('app.services.IMPORT_LOGS_DIR', str(test_import_logs_dir))
    upload_patcher.start()
    export_patcher.start()
    import_logs_patcher.start()
    
    try:
        yield TestClient(app)
    finally:
        # Cleanup
        app.dependency_overrides.clear()
        upload_patcher.stop()
        export_patcher.stop()
        import_logs_patcher.stop()
        
        # Clean up test directories
        for file_path in test_upload_dir.glob("*"):
            if file_path.is_file():
                file_path.unlink()
        for file_path in test_export_dir.glob("*"):
            if file_path.is_file():
                file_path.unlink()
        for file_path in test_import_logs_dir.glob("*"):
            if file_path.is_file():
                file_path.unlink()


@pytest.fixture(scope="function")
def mock_celery_task():
    """
    Mock Celery task to avoid needing Redis/Celery worker in tests.
    """
    mock_task = Mock()
    mock_task.id = "test-job-id-12345"
    mock_task.state = "PENDING"
    mock_task.ready.return_value = False
    mock_task.info = {}
    mock_task.result = None
    
    return mock_task


@pytest.fixture(scope="function")
def sample_movies(test_session: Session) -> list[Movie]:
    """
    Create sample movie data for testing.
    """
    movies = [
        Movie(
            movie_name="The Matrix",
            year=1999,
            genres="Action,Sci-Fi",
            rating=8.7
        ),
        Movie(
            movie_name="Inception",
            year=2010,
            genres="Action,Sci-Fi,Thriller",
            rating=8.8
        ),
        Movie(
            movie_name="The Dark Knight",
            year=2008,
            genres="Action,Crime,Drama",
            rating=9.0
        ),
        Movie(
            movie_name="Pulp Fiction",
            year=1994,
            genres="Crime,Drama",
            rating=8.9
        ),
        Movie(
            movie_name="Interstellar",
            year=2014,
            genres="Adventure,Drama,Sci-Fi",
            rating=8.6
        ),
    ]
    
    for movie in movies:
        test_session.add(movie)
    test_session.commit()
    
    # Refresh to get IDs
    for movie in movies:
        test_session.refresh(movie)
    
    return movies


@pytest.fixture
def sample_csv_content() -> str:
    """
    Sample CSV content for testing uploads.
    """
    return """movie_name,year,genres,rating
The Matrix,1999,Action Sci-Fi,8.7
Inception,2010,Action Sci-Fi Thriller,8.8
The Dark Knight,2008,Action Crime Drama,9.0
"""


@pytest.fixture
def sample_csv_file(tmp_path: Path, sample_csv_content: str) -> Path:
    """
    Create a temporary CSV file for testing.
    """
    csv_file = tmp_path / "test_movies.csv"
    csv_file.write_text(sample_csv_content)
    return csv_file

