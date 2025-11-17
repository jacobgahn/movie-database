import math
import os
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Query
from fastapi.responses import FileResponse
from sqlmodel import Session
from app.database import get_session
from app.models import JobStatusResponse, JobCreatedResponse, PaginatedMoviesResponse
from app.tasks import import_movies_task, export_movies_zip_task
from app.celery_app import celery_app
from app.services import save_uploaded_file, EXPORT_DIR, IMPORT_LOGS_DIR, cleanup_expired_exports, is_export_expired
from app.selectors import search_movies, get_job_status, get_job_type

router = APIRouter()


@router.put("/movies", response_model=JobCreatedResponse)
async def upload_movies(file: UploadFile = File(...)) -> JobCreatedResponse:
    """
    Upload a CSV file to import movies into the database.
    Returns a job_id for tracking the import progress.
    """
    # Validate file
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")
    
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="File must be a CSV file")
    
    try:
        file_path = await save_uploaded_file(file, file.filename)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error saving file: {str(e)}")
    
    # Start Celery import task
    try:
        task = import_movies_task.delay(file_path)
        return JobCreatedResponse(job_id=task.id, status="started")
    except Exception as e:
        # Clean up file if task creation fails
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except:
                pass
        raise HTTPException(status_code=500, detail=f"Error starting import task: {str(e)}")


@router.get("/movies", response_model=PaginatedMoviesResponse)
async def query_movies(
    start_year: int,
    end_year: int,
    genre: str | None = None,
    page: int | None = Query(default=1, ge=1),
    page_size: int | None = Query(default=25, ge=1, le=100),
    session: Session = Depends(get_session)
) -> PaginatedMoviesResponse:
    """
    Search movies by query parameters.
    """    
    if start_year > end_year:
        raise HTTPException(
            status_code=400,
            detail="start_year must be less than or equal to end_year"
        )
        
    normalized_genre = genre.strip() if genre and genre.strip() else None

    items, total_items = search_movies(
        session=session,
        start_year=start_year,
        end_year=end_year,
        genre=normalized_genre,
        page=page,
        page_size=page_size
    )

    total_pages = math.ceil(total_items / page_size) if total_items else 0

    return PaginatedMoviesResponse(
        page=page,
        page_size=page_size,
        total_items=total_items,
        total_pages=total_pages,
        items=items
    )


@router.get("/movies/zip", response_model=JobCreatedResponse)
async def export_movies() -> JobCreatedResponse:
    """
    Request export of all movies as a gzipped CSV file.
    Returns a job_id for tracking the export progress.
    """
    # Create export directory if it doesn't exist
    os.makedirs(EXPORT_DIR, exist_ok=True)
    
    # Clean up expired export files before creating new one
    cleanup_expired_exports(EXPORT_DIR)
    
    # Start Celery task
    task = export_movies_zip_task.delay(EXPORT_DIR)
    
    return JobCreatedResponse(job_id=task.id, status="started")


@router.get("/jobs/{job_id}/status", response_model=JobStatusResponse)
async def get_job_status_endpoint(job_id: str) -> JobStatusResponse:
    """
    Polling endpoint to get the status of a background job.
    Returns job status, progress, and metadata.
    """
    return get_job_status(job_id)


@router.get("/jobs/{job_id}/download")
async def download_job_result(job_id: str):
    """
    Download the result file from a completed export job.
    """
    task = celery_app.AsyncResult(job_id)
    
    if task.state != 'SUCCESS':
        raise HTTPException(
            status_code=400,
            detail=f"Job is not completed. Current status: {task.state}"
        )
    
    # Check job type - only allow downloads for export jobs
    job_type = get_job_type(job_id)
    if job_type != "export":
        raise HTTPException(
            status_code=400,
            detail="Download is only available for export jobs"
        )
    
    result = task.result
    if not result or result.get('status') != 'completed':
        raise HTTPException(
            status_code=404,
            detail="Export file not found or job did not complete successfully"
        )
    
    file_path = result.get('file_path')
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Export file not found")
    
    # Delete expired export file
    if is_export_expired(file_path):        
        try:
            os.remove(file_path)
        except OSError:
            pass
        raise HTTPException(
            status_code=410,
            detail="Export file has expired and has been deleted. Please request a new export."
        )
    
    filename = result.get('filename', 'movies_export.csv.gz')
    
    return FileResponse(
        file_path,
        media_type='application/gzip',
        filename=filename
    )


@router.get("/import-logs/{log_filename}")
async def download_error_log(log_filename: str):
    """
    Download an import error log file by filename.
    
    Args:
        log_filename: Name of the error log file (e.g., import_errors_20251110_200445_524751ed.log)
    
    Returns:
        FileResponse with the error log file
    """
    # Security: Validate filename to prevent path traversal attacks
    # Only allow alphanumeric, underscore, dash, and dot characters
    # Must end with .log
    if not log_filename.endswith('.log'):
        raise HTTPException(
            status_code=400,
            detail="Invalid log filename. Must end with .log"
        )
    
    # Check for path traversal attempts
    if '..' in log_filename or '/' in log_filename or '\\' in log_filename:
        raise HTTPException(
            status_code=400,
            detail="Invalid log filename. Path traversal not allowed"
        )
    
    if not log_filename.startswith('import_errors_'):
        raise HTTPException(
            status_code=400,
            detail="Invalid log filename format"
        )
    
    file_path = os.path.join(IMPORT_LOGS_DIR, log_filename)
    
    if not os.path.exists(file_path):
        raise HTTPException(
            status_code=404,
            detail=f"Error log file '{log_filename}' not found"
        )
    
    if not os.path.isfile(file_path):
        raise HTTPException(
            status_code=400,
            detail="Invalid log file path"
        )
    
    return FileResponse(
        file_path,
        media_type='text/plain',
        filename=log_filename
    )
