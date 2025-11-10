"""
Selector layer for database queries and job status queries.
"""
from typing import List
from sqlmodel import Session, select
from sqlalchemy.exc import SQLAlchemyError
from fastapi import HTTPException
from app.models import Movie, JobStatusResponse
from app.celery_app import celery_app


def search_movies(
    session: Session,
    start_year: int,
    end_year: int,
    genre: str | None = None
) -> List[Movie]:
    """
    Query movies by year range and optional genre.
    
    Args:
        session: Database session
        start_year: Start year (inclusive)
        end_year: End year (inclusive)
        genre: Optional genre to filter by
        
    Returns:
        List of Movie objects matching the criteria
        
    Raises:
        HTTPException: If database error occurs
    """
    try:
        # Build query
        statement = select(Movie).where(
            Movie.year >= start_year,
            Movie.year <= end_year
        )
        
        # Filter by genre if provided
        if genre:
            # Genre is stored as comma-separated string, so we check if it contains the genre
            statement = statement.where(Movie.genres.contains(genre))
        
        # Execute query
        movies = session.exec(statement).all()
        
        return list(movies)
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error querying movies: {str(e)}")


def get_job_type(job_id: str) -> str:
    """
    Get the job type (export or import) from a job ID.
    
    Args:
        job_id: The job ID to check
        
    Returns:
        "export" or "import" (defaults to "import" if cannot be determined)
    """
    task = celery_app.AsyncResult(job_id)
    job_type = "unknown"  # default
    
    try:
        # Try to get job_type from task metadata (available when task is running or has run)
        if task.info:
            if isinstance(task.info, dict):
                job_type = task.info.get('job_type', job_type)
            # For FAILURE state, task.info might be a string, so check backend metadata
            elif task.state == 'FAILURE':
                if hasattr(task, 'backend') and task.backend:
                    try:
                        task_meta = task.backend.get_task_meta(task.id)
                        if isinstance(task_meta, dict):
                            # Check the last known metadata
                            meta = task_meta.get('meta', {})
                            if isinstance(meta, dict):
                                job_type = meta.get('job_type', job_type)
                    except Exception:
                        pass
        # Fallback: check result structure if task is completed and type is still unknown
        if job_type == "unknown" and task.state == 'SUCCESS' and task.result:
            result = task.result if isinstance(task.result, dict) else {}
            if "file_path" in result or "exported" in result:
                job_type = "export"
            elif "imported" in result:
                job_type = "import"
    except Exception:
        # If we can't determine type, default to import
        pass
    
    return job_type


def get_job_status(job_id: str) -> JobStatusResponse:
    """
    Get the status of a background job.
    
    Args:
        job_id: The job ID to check
        
    Returns:
        JobStatusResponse with job status, progress, and metadata
        
    Raises:
        HTTPException: If job_id is invalid or job not found
    """
    if not job_id or not job_id.strip():
        raise HTTPException(status_code=400, detail="Invalid job_id")
    
    try:
        task = celery_app.AsyncResult(job_id)
        
        # Get job type from task metadata
        job_type = get_job_type(job_id)
        
        # Check if task exists
        if task.state == 'PENDING' and not task.ready():
            # Task might not exist
            try:
                task.get(timeout=0.1)
            except:
                raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        
        if task.state == 'PENDING':
            response = JobStatusResponse(
                job_id=job_id,
                type=job_type,
                status="pending",
                progress=0,
                message="Job is waiting to start"
            )
        elif task.state == 'PROGRESS':
            info = task.info if isinstance(task.info, dict) else {}
            response = JobStatusResponse(
                job_id=job_id,
                type=job_type,
                status="in_progress",
                progress=info.get('progress', 0),
                current=info.get('current', 0),
                total=info.get('total', 0),
                message=info.get('status', 'Processing...')
            )
        elif task.state == 'SUCCESS':
            response = JobStatusResponse(
                job_id=job_id,
                type=job_type,
                status="completed",
                progress=100,
                result=task.result
            )
        elif task.state == 'FAILURE':
            error_info = str(task.info) if task.info else "Unknown error"
            response = JobStatusResponse(
                job_id=job_id,
                type=job_type,
                status="failed",
                progress=0,
                error=error_info
            )
        else:
            response = JobStatusResponse(
                job_id=job_id,
                type=job_type,
                status=task.state.lower(),
                progress=0,
                message=f"Job state: {task.state}"
            )
        
        return response
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting job status: {str(e)}")

