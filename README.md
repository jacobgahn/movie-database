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

## Testing

The project includes comprehensive integration tests for all API endpoints. Tests use a lightweight SQLite database setup with proper isolation between test cases.

### Running Tests

First, install the test dependencies:

```bash
uv sync --extra test
```

Then run the tests:

```bash
# Run all tests
uv run pytest

# Run with coverage (if pytest-cov is installed)
pytest --cov=app --cov-report=html
```

### Test Database

Tests use temporary SQLite databases created for each test function. The database is automatically cleaned up after each test, ensuring:
- No test data pollution
- Fast test execution
- Parallel test execution support
- No need to manage test database state
