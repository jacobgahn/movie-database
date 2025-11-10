# Movie Database Service

A FastAPI service for managing and querying a movie database with support for CSV import/export, background job processing, and real-time progress tracking.

## Features

- **CSV Import**: Upload and import movie data from CSV files
- **CSV Export**: Download the entire database as a gzipped CSV file
- **Query Interface**: Search movies by year range and genre
- **Background Processing**: Long-running operations handled asynchronously with Celery
- **Progress Tracking**: Poll job status endpoints for real-time progress updates

## Prerequisites

- Docker and Docker Compose

## Getting Started

Run the application using Docker Compose:

```bash
# Build and start all services (API, Celery worker, and Redis)
docker-compose up --build

# Or run in detached mode
docker-compose up -d --build
```

This will start:
- **FastAPI API** at `http://localhost:8000`
- **Celery Worker** for background task processing
- **Redis** for Celery broker and result backend

Access the API documentation at:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

### Docker Commands

```bash
# View logs
docker-compose logs -f

# Stop all services
docker-compose down

# Stop and remove volumes
docker-compose down -v

# Rebuild after code changes
docker-compose up --build
```

For more Docker details, see [DOCKER.md](DOCKER.md).

## API Endpoints

### Upload Movies CSV
- **PUT** `/movies`
- Upload a CSV file to import movies into the database
- **Request**: Multipart form data with CSV file
- **Response**: `{"job_id": "...", "status": "started"}`
- Returns a job ID for tracking import progress

### Query Movies
- **GET** `/movies?start_year={year}&end_year={year}&genre={genre}`
- Query movies by year range and genre
- **Parameters**:
  - `start_year` (required): Start year (inclusive)
  - `end_year` (required): End year (inclusive)
  - `genre` (required): Genre to filter by
- **Response**: Array of movie objects

### Request Movies Export
- **GET** `/movies/zip`
- Request export of all movies as a gzipped CSV file
- **Response**: `{"job_id": "...", "status": "started"}`
- Returns a job ID for tracking export progress

### Get Job Status
- **GET** `/jobs/{job_id}/status`
- Polling endpoint to get the status of a background job
- **Response**: Job status with progress information
- **Status values**: `pending`, `in_progress`, `completed`, `failed`

### Download Export File
- **GET** `/jobs/{job_id}/download`
- Download the result file from a completed export job
- **Response**: Gzipped CSV file
- Only available when job status is `completed`

## Design Choices

### Database: SQLite
- **Rationale**: Simple, file-based database perfect for a take-home assignment
- **Migration Path**: The codebase uses SQLModel, making migration to PostgreSQL straightforward (just change the database URL)

### ORM: SQLModel
- **Rationale**: Combines SQLAlchemy's power with Pydantic's validation
- **Benefits**:
  - Type-safe models with automatic validation
  - Efficient querying with proper indices
  - Easy serialization for API responses
  - Database schema management

### Background Jobs: Celery
- **Rationale**: Industry-standard solution for async task processing
- **Benefits**:
  - Handles long-running operations without blocking API
  - Built-in progress tracking via result backend
  - Scalable (can run multiple workers)
  - Reliable task execution with retry capabilities

### Progress Updates: Polling
- **Rationale**: Simpler implementation than Server-Sent Events (SSE)
- **Benefits**:
  - Easy to understand and debug
  - Works with any HTTP client
  - No special connection management needed
- **Trade-off**: Slightly more network overhead than SSE, but acceptable for this use case

### Memory Efficiency
- **CSV Import**: Streams CSV file in batches (1000 rows at a time) to avoid loading entire file into memory
- **CSV Export**: Writes directly to gzip file, avoiding intermediate storage
- **Database Queries**: Uses efficient SQL queries with indices on `year` and `genres` columns

### Error Handling
- Comprehensive validation for all inputs
- Graceful error handling with appropriate HTTP status codes
- Detailed error messages for debugging
- Database error handling with proper exception catching
```

## Testing

The project includes comprehensive integration tests for all API endpoints. Tests use a lightweight SQLite database setup with proper isolation between test cases.

### Running Tests

First, install the test dependencies:

```bash
# Using uv (recommended - uses uv.lock for reproducible installs)
uv sync --extra test

# Or using uv's pip-compatible interface
uv pip install -e ".[test]"

# Or using traditional pip
pip install -e ".[test]"
```

Then run the tests:

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run a specific test file
pytest tests/test_movies.py

# Run a specific test class
pytest tests/test_movies.py::TestQueryMovies

# Run a specific test
pytest tests/test_movies.py::TestQueryMovies::test_query_movies_by_year_range

# Run with coverage (if pytest-cov is installed)
pytest --cov=app --cov-report=html
```

### Test Structure

The test suite is organized as follows:

- **`tests/conftest.py`**: Contains pytest fixtures for:
  - `test_db`: Creates a temporary SQLite database for each test
  - `test_session`: Database session for direct database operations
  - `client`: FastAPI TestClient with database dependency override
  - `mock_celery_task`: Mocks Celery tasks (no Redis/Celery worker needed)
  - `sample_movies`: Pre-populated movie data for query tests
  - `sample_csv_content` & `sample_csv_file`: Test CSV data

- **`tests/test_movies.py`**: Integration tests covering:
  - **PUT /movies**: File upload validation and success cases
  - **GET /movies**: Query by year range and genre with various filters
  - **GET /movies/zip**: Export request handling
  - **GET /jobs/{job_id}/status**: Job status polling (pending, in-progress, completed, failed)
  - **GET /jobs/{job_id}/download**: Download export files

### Test Features

- **Lightweight Setup**: Each test gets its own temporary SQLite database file
- **Proper Isolation**: Tests don't interfere with each other
- **No External Dependencies**: Celery tasks are mocked, so no Redis/Celery worker is required
- **Temporary Directories**: Upload/export directories are created per test and cleaned up automatically
- **Comprehensive Coverage**: All endpoints and edge cases are tested

### Test Database

Tests use temporary SQLite databases created for each test function. The database is automatically cleaned up after each test, ensuring:
- No test data pollution
- Fast test execution
- Parallel test execution support
- No need to manage test database state

## Example Usage

### 1. Import Movies
```bash
curl -X PUT "http://localhost:8000/movies" \
  -F "file=@movies.csv"
```

Response:
```json
{
  "job_id": "abc123-def456-...",
  "status": "started"
}
```

### 2. Check Import Progress
```bash
curl "http://localhost:8000/jobs/abc123-def456-.../status"
```

Response:
```json
{
  "job_id": "abc123-def456-...",
  "status": "in_progress",
  "progress": 45,
  "current": 165000,
  "total": 367315,
  "message": "Imported 165000 movies..."
}
```

### 3. Query Movies
```bash
curl "http://localhost:8000/movies?start_year=2020&end_year=2023&genre=Action"
```

### 4. Export Movies
```bash
curl "http://localhost:8000/movies/zip"
```

### 5. Download Export
```bash
curl "http://localhost:8000/jobs/xyz789-abc123-.../download" \
  -o movies_export.csv.gz
```

## Future Considerations

See `FUTURE_CONSIDERATIONS.md` for potential enhancements including:
- Export request data models
- Filtered exports
- SSE performance evaluation
