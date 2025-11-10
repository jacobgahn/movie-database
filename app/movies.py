import os
from typing import List
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from fastapi.responses import FileResponse
from sqlmodel import Session
from app.database import get_session
from app.models import Movie, JobStatusResponse, JobCreatedResponse
from app.tasks import import_movies_task, export_movies_zip_task
from app.celery_app import celery_app
from app.services import save_uploaded_file, EXPORT_DIR
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


@router.get("/movies")
async def query_movies(
    start_year: int,
    end_year: int,
    genre: str | None = None,
    session: Session = Depends(get_session)
) -> List[Movie]:
    """
    Search movies by query parameters.
    """
    # Validate year range
    if start_year > end_year:
        raise HTTPException(
            status_code=400,
            detail="start_year must be less than or equal to end_year"
        )
    
    # Query movies using selector layer
    movies = search_movies(
        session=session,
        start_year=start_year,
        end_year=end_year,
        genre=genre if genre else None
    )
    
    return movies


@router.get("/movies/zip", response_model=JobCreatedResponse)
async def export_movies() -> JobCreatedResponse:
    """
    Request export of all movies as a gzipped CSV file.
    Returns a job_id for tracking the export progress.
    """
    # Create export directory if it doesn't exist
    os.makedirs(EXPORT_DIR, exist_ok=True)
    
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
    
    filename = result.get('filename', 'movies_export.csv.gz')
    
    return FileResponse(
        file_path,
        media_type='application/gzip',
        filename=filename
    )
