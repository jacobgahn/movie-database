"""
Integration tests for movie endpoints.
"""
import os
import gzip
import csv
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
from fastapi import status

from app.models import Movie




class TestUploadMovies:
    """Tests for PUT /movies endpoint"""
    
    def test_upload_movies_no_filename(self, client):
        """Test upload without filename"""
        response = client.put(
            "/movies",
            files={"file": ("", b"content", "text/csv")}
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    def test_upload_movies_invalid_extension(self, client):
        """Test upload with non-CSV file"""
        response = client.put(
            "/movies",
            files={"file": ("test.txt", b"content", "text/plain")}
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "File must be a CSV file" in response.json()["detail"]
    
    def test_upload_movies_empty_file(self, client):
        """Test upload with empty file"""
        response = client.put(
            "/movies",
            files={"file": ("test.csv", b"", "text/csv")}
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "empty" in response.json()["detail"].lower()


class TestImportMovies:
    """Tests for importing movies into the database via PUT /movies endpoint"""
    
    def test_import_movies_success(self, client, test_session):
        """Test successful import of valid CSV file"""
        csv_content = """movie_name,year,genres,rating
The Matrix,1999,Action Sci-Fi,8.7
Inception,2010,Action Sci-Fi Thriller,8.8
The Dark Knight,2008,Action Crime Drama,9.0"""
        
        # With task_always_eager=True, tasks run synchronously
        # Patch the engine to use the test database
        from app.database import engine as original_engine
        test_engine = test_session.bind
        
        with patch('app.tasks.engine', test_engine):
            response = client.put(
                "/movies",
                files={"file": ("test_movies.csv", csv_content.encode('utf-8'), "text/csv")}
            )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "started"
        
        # Verify movies were actually imported into database
        from sqlmodel import select
        movies = test_session.exec(select(Movie)).all()
        assert len(movies) == 3
        
        movie_names = [m.movie_name for m in movies]
        assert "The Matrix" in movie_names
        assert "Inception" in movie_names
        assert "The Dark Knight" in movie_names
    
    def test_import_movies_overwrites_existing_data(self, client, test_session, test_db, sample_movies):
        """Test that import overwrites existing movies in database"""
        # Verify initial movies exist
        from sqlmodel import select, create_engine
        initial_movies = test_session.exec(select(Movie)).all()
        assert len(initial_movies) == 5
        
        # Import new CSV with different movies
        csv_content = """movie_name,year,genres,rating
New Movie 1,2020,Action,7.5
New Movie 2,2021,Comedy,8.0"""
        
        # Create engine from test_db URL to ensure same database
        test_engine = create_engine(test_db, echo=False)
        with patch('app.tasks.engine', test_engine):
            response = client.put(
                "/movies",
                files={"file": ("new_movies.csv", csv_content.encode('utf-8'), "text/csv")}
            )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "started"
        
        # Refresh session to see changes
        test_session.expire_all()
        # Verify old movies are gone and new ones are present
        movies = test_session.exec(select(Movie)).all()
        assert len(movies) == 2
        
        movie_names = [m.movie_name for m in movies]
        assert "New Movie 1" in movie_names
        assert "New Movie 2" in movie_names
        assert "The Matrix" not in movie_names  # Old movie should be gone
    
    def test_import_movies_with_missing_required_fields(self, client, test_session):
        """Test import with rows missing required fields (movie_name or year)"""
        csv_content = """movie_name,year,genres,rating
The Matrix,1999,Action Sci-Fi,8.7
,2010,Action,8.0
Inception,,Sci-Fi,8.8
The Dark Knight,2008,Action,9.0"""
        
        test_engine = test_session.bind
        with patch('app.tasks.engine', test_engine):
            response = client.put(
                "/movies",
                files={"file": ("test_movies.csv", csv_content.encode('utf-8'), "text/csv")}
            )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "started"
        
        # Verify only valid movies were imported
        from sqlmodel import select
        movies = test_session.exec(select(Movie)).all()
        assert len(movies) == 2
        movie_names = [m.movie_name for m in movies]
        assert "The Matrix" in movie_names
        assert "The Dark Knight" in movie_names
    
    def test_import_movies_with_invalid_year(self, client, test_session):
        """Test import with invalid year format"""
        csv_content = """movie_name,year,genres,rating
The Matrix,1999,Action Sci-Fi,8.7
Inception,not-a-year,Sci-Fi,8.8
The Dark Knight,2008,Action,9.0"""
        
        test_engine = test_session.bind
        with patch('app.tasks.engine', test_engine):
            response = client.put(
                "/movies",
                files={"file": ("test_movies.csv", csv_content.encode('utf-8'), "text/csv")}
            )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "started"
        
        # Verify only valid movies were imported
        from sqlmodel import select
        movies = test_session.exec(select(Movie)).all()
        assert len(movies) == 2
        movie_names = [m.movie_name for m in movies]
        assert "The Matrix" in movie_names
        assert "The Dark Knight" in movie_names
    
    def test_import_movies_with_invalid_movie_name(self, client, test_session):
        """Test import with invalid movie names (control characters, excessive length, etc.)"""
        csv_content = """movie_name,year,genres,rating
The Matrix,1999,Action Sci-Fi,8.7
Valid Movie,2000,Action,8.0
""" + "A" * 501 + """,2001,Action,7.0
Movie with\x00null,2002,Action,6.0
Movie   with   spaces,2003,Action,5.0"""
        
        test_engine = test_session.bind
        with patch('app.tasks.engine', test_engine):
            response = client.put(
                "/movies",
                files={"file": ("test_movies.csv", csv_content.encode('utf-8'), "text/csv")}
            )
        
        assert response.status_code == status.HTTP_200_OK
        job_id = response.json()["job_id"]
        
        # Wait for task to complete
        import time
        max_retries = 10
        for _ in range(max_retries):
            status_response = client.get(f"/jobs/{job_id}/status")
            status_data = status_response.json()
            if status_data["status"] == "completed":
                break
            time.sleep(0.1)
        
        assert status_data["status"] == "completed"
        result = status_data["result"]
        
        # Should have errors for invalid movie names
        assert result["errors"] > 0
        # Valid movies should still be imported
        assert result["imported"] > 0
        
        # Verify only valid movies were imported
        from sqlmodel import select
        movies = test_session.exec(select(Movie)).all()
        movie_names = [m.movie_name for m in movies]
        assert "The Matrix" in movie_names
        assert "Valid Movie" in movie_names
        # Invalid ones should not be imported
        assert "Movie   with   spaces" not in movie_names
    
    def test_import_movies_with_optional_rating(self, client, test_session):
        """Test import with optional rating field (can be empty or invalid)"""
        csv_content = """movie_name,year,genres,rating
The Matrix,1999,Action Sci-Fi,8.7
Inception,2010,Sci-Fi,
The Dark Knight,2008,Action,invalid-rating
Pulp Fiction,1994,Crime,8.9"""
        
        test_engine = test_session.bind
        with patch('app.tasks.engine', test_engine):
            response = client.put(
                "/movies",
                files={"file": ("test_movies.csv", csv_content.encode('utf-8'), "text/csv")}
            )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "started"
        
        # Verify all movies imported, check rating values
        from sqlmodel import select
        movies = test_session.exec(select(Movie)).all()
        assert len(movies) == 4
        
        movie_dict = {m.movie_name: m for m in movies}
        assert movie_dict["The Matrix"].rating == 8.7
        assert movie_dict["Inception"].rating is None  # Empty rating
        assert movie_dict["The Dark Knight"].rating is None  # Invalid rating becomes None
        assert movie_dict["Pulp Fiction"].rating == 8.9
    
    def test_import_movies_empty_csv(self, client, test_session):
        """Test import with empty CSV (only headers)"""
        csv_content = """movie_name,year,genres,rating"""
        
        test_engine = test_session.bind
        with patch('app.tasks.engine', test_engine):
            response = client.put(
                "/movies",
                files={"file": ("empty.csv", csv_content.encode('utf-8'), "text/csv")}
            )
        
        # Task will run and return error, but endpoint returns job_id
        # The task error will be in the job status, not the upload response
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "job_id" in data
        
        # Verify no movies were imported
        from sqlmodel import select
        movies = test_session.exec(select(Movie)).all()
        assert len(movies) == 0
    
    def test_import_movies_invalid_csv_format(self, client, test_session):
        """Test import with invalid CSV format"""
        csv_content = """not,a,valid,csv,format
some random text
more random text"""
        
        test_engine = test_session.bind
        with patch('app.tasks.engine', test_engine):
            response = client.put(
                "/movies",
                files={"file": ("invalid.csv", csv_content.encode('utf-8'), "text/csv")}
            )
        
        # Should return job_id (task will handle the error)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "job_id" in data
    
    def test_import_movies_large_batch(self, client, test_session):
        """Test import with a large number of movies (tests batching)"""
        # Create CSV with 2500 movies (more than batch size of 1000)
        csv_lines = ["movie_name,year,genres,rating"]
        for i in range(2500):
            csv_lines.append(f"Movie {i},2000,Action,7.5")
        
        csv_content = "\n".join(csv_lines)
        
        test_engine = test_session.bind
        with patch('app.tasks.engine', test_engine):
            response = client.put(
                "/movies",
                files={"file": ("large_batch.csv", csv_content.encode('utf-8'), "text/csv")}
            )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "started"
        
        # Verify all movies were imported
        from sqlmodel import select
        movies = test_session.exec(select(Movie)).all()
        assert len(movies) == 2500
    
    def test_import_movies_preserves_data_types(self, client, test_session):
        """Test that imported data has correct types"""
        csv_content = """movie_name,year,genres,rating
Test Movie,2020,Action Comedy,8.5"""
        
        test_engine = test_session.bind
        with patch('app.tasks.engine', test_engine):
            response = client.put(
                "/movies",
                files={"file": ("test.csv", csv_content.encode('utf-8'), "text/csv")}
            )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "started"
        
        # Verify data types
        from sqlmodel import select
        movies = test_session.exec(select(Movie)).all()
        assert len(movies) == 1
        
        movie = movies[0]
        assert isinstance(movie.movie_name, str)
        assert isinstance(movie.year, int)
        assert isinstance(movie.genres, str)
        assert isinstance(movie.rating, float)
        assert movie.year == 2020
        assert movie.rating == 8.5


class TestQueryMovies:
    """Tests for GET /movies endpoint"""
    
    def test_query_movies_by_year_range(self, client, sample_movies):
        """Test querying movies by year range"""
        response = client.get(
            "/movies",
            params={"start_year": "2000", "end_year": "2010", "genre": ""}
        )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 2  # Inception (2010) and The Dark Knight (2008)
        movie_years = [movie["year"] for movie in data]
        assert 2008 in movie_years
        assert 2010 in movie_years
    
    def test_query_movies_by_year_range_and_genre(self, client, sample_movies):
        """Test querying movies by year range and genre"""
        response = client.get(
            "/movies",
            params={"start_year": "2000", "end_year": "2010", "genre": "Action"}
        )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 2  # Inception and The Dark Knight
        for movie in data:
            assert "Action" in movie["genres"]
            assert 2000 <= movie["year"] <= 2010
    
    def test_query_movies_no_results(self, client, sample_movies):
        """Test query with no matching results"""
        response = client.get(
            "/movies",
            params={"start_year": "2020", "end_year": "2025", "genre": ""}
        )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 0
    
    def test_query_movies_invalid_year_format(self, client):
        """Test query with invalid year format"""
        response = client.get(
            "/movies",
            params={"start_year": "not-a-year", "end_year": "2010", "genre": ""}
        )
        
        # FastAPI returns 422 for validation errors
        assert response.status_code in (status.HTTP_400_BAD_REQUEST, status.HTTP_422_UNPROCESSABLE_CONTENT)
    
    def test_query_movies_invalid_year_range(self, client):
        """Test query with start_year > end_year"""
        response = client.get(
            "/movies",
            params={"start_year": "2010", "end_year": "2000", "genre": ""}
        )
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "start_year must be less than or equal to end_year" in response.json()["detail"]

    
    def test_query_movies_all_genres(self, client, sample_movies):
        """Test querying all movies in a year range"""
        response = client.get(
            "/movies",
            params={"start_year": "1990", "end_year": "2020", "genre": ""}
        )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 5  # All sample movies
    
    def test_query_movies_specific_genre(self, client, sample_movies):
        """Test querying by specific genre"""
        response = client.get(
            "/movies",
            params={"start_year": "1990", "end_year": "2020", "genre": "Sci-Fi"}
        )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # Should return The Matrix, Inception, and Interstellar
        assert len(data) == 3
        for movie in data:
            assert "Sci-Fi" in movie["genres"]


class TestRequestMoviesZip:
    """Tests for GET /movies/zip endpoint"""
    
    def test_export_movies_success(self, client, mock_celery_task):
        """Test successful export request"""
        with patch('app.movies.export_movies_zip_task') as mock_task:
            mock_task.delay.return_value = mock_celery_task
            
            response = client.get("/movies/zip")
            
            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert "job_id" in data
            assert data["status"] == "started"
            assert data["job_id"] == "test-job-id-12345"
            mock_task.delay.assert_called_once()


class TestJobStatus:
    """Tests for GET /jobs/{job_id}/status endpoint"""
    
    def test_job_status_pending(self, client):
        """Test getting status of pending job"""
        mock_task = MagicMock()
        mock_task.id = "test-job-id"
        mock_task.state = "PENDING"
        mock_task.ready.return_value = True
        mock_task.info = {"job_type": "import"}
        
        with patch('app.selectors.celery_app.AsyncResult', return_value=mock_task):
            response = client.get("/jobs/test-job-id/status")
            
            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["job_id"] == "test-job-id"
            assert data["status"] == "pending"
            assert data["progress"] == 0
    
    def test_job_status_in_progress(self, client):
        """Test getting status of in-progress job"""
        mock_task = MagicMock()
        mock_task.id = "test-job-id"
        mock_task.state = "PROGRESS"
        mock_task.info = {
            "progress": 50,
            "current": 100,
            "total": 200,
            "status": "Processing...",
            "job_type": "import"
        }
        
        with patch('app.selectors.celery_app.AsyncResult', return_value=mock_task):
            response = client.get("/jobs/test-job-id/status")
            
            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["status"] == "in_progress"
            assert data["progress"] == 50
            assert data["current"] == 100
            assert data["total"] == 200
    
    def test_job_status_completed(self, client):
        """Test getting status of completed job"""
        mock_task = MagicMock()
        mock_task.id = "test-job-id"
        mock_task.state = "SUCCESS"
        mock_task.info = {"job_type": "import"}
        mock_task.result = {"status": "completed", "imported": 100}
        
        with patch('app.selectors.celery_app.AsyncResult', return_value=mock_task):
            response = client.get("/jobs/test-job-id/status")
            
            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["status"] == "completed"
            assert data["progress"] == 100
            assert "result" in data
    
    def test_job_status_failed(self, client):
        """Test getting status of failed job"""
        mock_task = MagicMock()
        mock_task.id = "test-job-id"
        mock_task.state = "FAILURE"
        mock_task.info = "Task failed with error"
        # Mock backend for getting job_type from metadata
        mock_backend = MagicMock()
        mock_backend.get_task_meta.return_value = {
            "meta": {"job_type": "import"}
        }
        mock_task.backend = mock_backend
        
        with patch('app.selectors.celery_app.AsyncResult', return_value=mock_task):
            response = client.get("/jobs/test-job-id/status")
            
            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["status"] == "failed"
            assert "error" in data
    
    def test_job_status_not_found(self, client):
        """Test getting status of non-existent job"""
        mock_task = MagicMock()
        mock_task.id = "nonexistent-job"
        mock_task.state = "PENDING"
        mock_task.ready.return_value = False
        
        def raise_exception(*args, **kwargs):
            raise Exception("Task not found")
        
        mock_task.get.side_effect = raise_exception
        
        with patch('app.selectors.celery_app.AsyncResult', return_value=mock_task):
            response = client.get("/jobs/nonexistent-job/status")
            
            assert response.status_code == status.HTTP_404_NOT_FOUND
            assert "not found" in response.json()["detail"].lower()
    
    def test_job_status_invalid_job_id(self, client):
        """Test getting status with invalid job_id"""
        response = client.get("/jobs/ /status")
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid job_id" in response.json()["detail"]


class TestDownloadJobResult:
    """Tests for GET /jobs/{job_id}/download endpoint"""
    
    def test_download_job_result_success(self, client, tmp_path):
        """Test successful download of export file"""
        # Create a test export file
        export_file = tmp_path / "movies_export_test.csv.gz"
        test_content = b"compressed csv content"
        with gzip.open(export_file, 'wb') as f:
            f.write(test_content)
        
        mock_task = MagicMock()
        mock_task.id = "test-job-id"
        mock_task.state = "SUCCESS"
        mock_task.info = {"job_type": "export"}
        mock_task.result = {
            "status": "completed",
            "file_path": str(export_file),
            "filename": "movies_export_test.csv.gz"
        }
        
        with patch('app.movies.celery_app.AsyncResult', return_value=mock_task), \
             patch('app.selectors.celery_app.AsyncResult', return_value=mock_task):
            response = client.get("/jobs/test-job-id/download")
            
            assert response.status_code == status.HTTP_200_OK
            assert response.headers["content-type"] == "application/gzip"
            assert "movies_export_test.csv.gz" in response.headers.get("content-disposition", "")
    
    def test_download_job_result_not_completed(self, client):
        """Test download when job is not completed"""
        mock_task = MagicMock()
        mock_task.id = "test-job-id"
        mock_task.state = "PROGRESS"
        
        with patch('app.movies.celery_app.AsyncResult', return_value=mock_task):
            response = client.get("/jobs/test-job-id/download")
            
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "not completed" in response.json()["detail"].lower()
    
    def test_download_job_result_file_not_found(self, client):
        """Test download when file doesn't exist"""
        mock_task = MagicMock()
        mock_task.id = "test-job-id"
        mock_task.state = "SUCCESS"
        mock_task.info = {"job_type": "export"}
        mock_task.result = {
            "status": "completed",
            "file_path": "/nonexistent/path/file.csv.gz",
            "filename": "file.csv.gz"
        }
        
        with patch('app.movies.celery_app.AsyncResult', return_value=mock_task), \
             patch('app.selectors.celery_app.AsyncResult', return_value=mock_task):
            response = client.get("/jobs/test-job-id/download")
            
            assert response.status_code == status.HTTP_404_NOT_FOUND
            assert "not found" in response.json()["detail"].lower()
    
    def test_download_job_result_invalid_result(self, client):
        """Test download when job result is invalid"""
        mock_task = MagicMock()
        mock_task.id = "test-job-id"
        mock_task.state = "SUCCESS"
        mock_task.info = {"job_type": "export"}
        mock_task.result = {"status": "error", "message": "Export failed"}
        
        with patch('app.movies.celery_app.AsyncResult', return_value=mock_task), \
             patch('app.selectors.celery_app.AsyncResult', return_value=mock_task):
            response = client.get("/jobs/test-job-id/download")
            
            assert response.status_code == status.HTTP_404_NOT_FOUND
            assert "not found" in response.json()["detail"].lower() or "not complete" in response.json()["detail"].lower()


class TestErrorLogging:
    """Tests for error log creation and retrieval"""
    
    def test_import_with_errors_creates_error_log(self, client, test_session, tmp_path):
        """Test that import errors are logged and error_log path is returned in status"""
        # CSV with multiple errors: missing fields, invalid year, etc.
        csv_content = """movie_name,year,genres,rating
The Matrix,1999,Action Sci-Fi,8.7
,2010,Action,8.0
Inception,,Sci-Fi,8.8
Invalid Year,not-a-year,Action,7.5
The Dark Knight,2008,Action,9.0
Missing Name,,Comedy,6.0"""
        
        test_import_logs_dir = tmp_path / "import_logs"
        test_engine = test_session.bind
        
        # Patch both the services and tasks modules to use the test import_logs directory
        with patch('app.tasks.engine', test_engine), \
             patch('app.services.IMPORT_LOGS_DIR', str(test_import_logs_dir)), \
             patch('app.tasks.IMPORT_LOGS_DIR', str(test_import_logs_dir)):
            # Start import
            response = client.put(
                "/movies",
                files={"file": ("test_movies.csv", csv_content.encode('utf-8'), "text/csv")}
            )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        job_id = data["job_id"]
        assert job_id is not None
        
        # Wait for task to complete (task_always_eager=True means it runs synchronously)
        # Get job status - may need to retry if task is still processing
        import time
        max_retries = 10
        for _ in range(max_retries):
            status_response = client.get(f"/jobs/{job_id}/status")
            assert status_response.status_code == status.HTTP_200_OK
            
            status_data = status_response.json()
            if status_data["status"] == "completed":
                break
            time.sleep(0.1)
        
        assert status_data["status"] == "completed"
        assert "result" in status_data
        
        result = status_data["result"]
        assert "error_log" in result
        assert result["error_log"] is not None
        
        # Verify error log file exists
        error_log_path = result["error_log"]
        assert os.path.exists(error_log_path)
        
        # Verify error log content
        with open(error_log_path, 'r', encoding='utf-8') as f:
            log_content = f.read()
            assert "Import Error Log" in log_content
            assert "Total Errors" in log_content
            # Should have errors for rows with missing/invalid data
            assert "Missing required field" in log_content or "Invalid year format" in log_content
        
        # Verify that some movies were still imported (valid ones)
        from sqlmodel import select
        movies = test_session.exec(select(Movie)).all()
        assert len(movies) > 0  # At least The Matrix and The Dark Knight should be imported
    
    def test_import_without_errors_no_error_log(self, client, test_session):
        """Test that successful imports don't create error logs"""
        csv_content = """movie_name,year,genres,rating
The Matrix,1999,Action Sci-Fi,8.7
Inception,2010,Action Sci-Fi Thriller,8.8
The Dark Knight,2008,Action Crime Drama,9.0"""
        
        test_engine = test_session.bind
        with patch('app.tasks.engine', test_engine):
            response = client.put(
                "/movies",
                files={"file": ("test_movies.csv", csv_content.encode('utf-8'), "text/csv")}
            )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        job_id = data["job_id"]
        
        # Get job status - may need to retry if task is still processing
        import time
        max_retries = 10
        for _ in range(max_retries):
            status_response = client.get(f"/jobs/{job_id}/status")
            assert status_response.status_code == status.HTTP_200_OK
            
            status_data = status_response.json()
            if status_data["status"] == "completed":
                break
            time.sleep(0.1)
        
        assert status_data["status"] == "completed"
        assert "result" in status_data
        
        result = status_data["result"]
        # error_log should be None when there are no errors
        assert result.get("error_log") is None
    
    def test_import_error_log_contains_row_details(self, client, test_session, tmp_path):
        """Test that error log contains detailed information about each error"""
        csv_content = """movie_name,year,genres,rating
The Matrix,1999,Action Sci-Fi,8.7
Missing Name,,Comedy,6.0
Invalid Year,not-a-number,Action,7.5"""
        
        test_import_logs_dir = tmp_path / "import_logs"
        test_engine = test_session.bind
        
        # Patch both the services and tasks modules to use the test import_logs directory
        with patch('app.tasks.engine', test_engine), \
             patch('app.services.IMPORT_LOGS_DIR', str(test_import_logs_dir)), \
             patch('app.tasks.IMPORT_LOGS_DIR', str(test_import_logs_dir)):
            response = client.put(
                "/movies",
                files={"file": ("test_movies.csv", csv_content.encode('utf-8'), "text/csv")}
            )
        
        assert response.status_code == status.HTTP_200_OK
        job_id = response.json()["job_id"]
        
        # Get job status - may need to retry if task is still processing
        import time
        max_retries = 10
        for _ in range(max_retries):
            status_response = client.get(f"/jobs/{job_id}/status")
            status_data = status_response.json()
            if status_data["status"] == "completed":
                break
            time.sleep(0.1)
        
        result = status_data["result"]
        
        if result.get("error_log"):
            error_log_path = result["error_log"]
            assert os.path.exists(error_log_path)
            
            with open(error_log_path, 'r', encoding='utf-8') as f:
                log_content = f.read()
                # Check for row numbers
                assert "Row" in log_content
                # Check for error messages
                assert "Missing required field" in log_content or "Invalid year format" in log_content
                # Check for row data
                assert "Row Data" in log_content


class TestDownloadErrorLog:
    """Tests for GET /import-logs/{log_filename} endpoint"""
    
    def test_download_error_log_success(self, client, tmp_path):
        """Test successful download of error log file"""
        # Create test import_logs directory and a log file
        test_import_logs_dir = tmp_path / "import_logs"
        test_import_logs_dir.mkdir(exist_ok=True)
        
        # Create a test error log file
        log_filename = "import_errors_20251110_200445_524751ed.log"
        log_file = test_import_logs_dir / log_filename
        log_content = """Import Error Log
Generated: 2025-11-10T20:04:45.968611
Total Errors: 2
================================================================================

Row 2: Missing required field: year
Row Data: {'movie_name': 'Test Movie', 'year': ''}
--------------------------------------------------------------------------------
Row 3: Invalid year format: 'not-a-year'
Row Data: {'movie_name': 'Another Movie', 'year': 'not-a-year'}
--------------------------------------------------------------------------------
"""
        log_file.write_text(log_content)
        
        # Patch IMPORT_LOGS_DIR to use test directory
        with patch('app.services.IMPORT_LOGS_DIR', str(test_import_logs_dir)), \
             patch('app.movies.IMPORT_LOGS_DIR', str(test_import_logs_dir)):
            response = client.get(f"/import-logs/{log_filename}")
        
        assert response.status_code == status.HTTP_200_OK
        assert "text/plain" in response.headers["content-type"]
        assert log_filename in response.headers.get("content-disposition", "")
        assert "Import Error Log" in response.text
        assert "Total Errors: 2" in response.text
    
    def test_download_error_log_not_found(self, client, tmp_path):
        """Test download when log file doesn't exist"""
        test_import_logs_dir = tmp_path / "import_logs"
        test_import_logs_dir.mkdir(exist_ok=True)
        
        with patch('app.services.IMPORT_LOGS_DIR', str(test_import_logs_dir)), \
             patch('app.movies.IMPORT_LOGS_DIR', str(test_import_logs_dir)):
            response = client.get("/import-logs/import_errors_nonexistent.log")
        
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "not found" in response.json()["detail"].lower()
    
    def test_download_error_log_invalid_filename(self, client):
        """Test download with invalid filename"""
        # Test with wrong extension
        response = client.get("/import-logs/import_errors_20251110.txt")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "must end with .log" in response.json()["detail"].lower()
        
        # Test with wrong prefix
        response = client.get("/import-logs/wrong_prefix_20251110.log")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid log filename format" in response.json()["detail"]
        
        # Test with slash in filename (path traversal attempt)
        # FastAPI normalizes paths, but we still validate the parameter
        response = client.get("/import-logs/import_errors_20251110/etc/passwd.log")
        # FastAPI might normalize this, but our validation should catch it
        assert response.status_code in (status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND)

