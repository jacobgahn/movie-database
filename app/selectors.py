"""
Selector layer for database queries and job status queries.
"""
from typing import List, Tuple, cast, Literal
from sqlmodel import Session, select
from sqlalchemy import func
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.exc import SQLAlchemyError
from fastapi import HTTPException
from app.models import Movie, JobStatusResponse
from app.celery_app import celery_app


def search_movies(
    session: Session,
    start_year: int,
    end_year: int,
    genre: str | None = None,
    page: int = 1,
    page_size: int = 25
) -> Tuple[List[Movie], int]:
    """
    Query movies by year range and optional genre.
    
    Args:
        session: Database session
        start_year: Start year (inclusive)
        end_year: End year (inclusive)
        genre: Optional genre to filter by
        
    Returns:
        Tuple containing the list of Movie objects for the requested page and the total number of matching records
        
    Raises:
        HTTPException: If database error occurs
    """
    try:
        # Build query filters
        year_column = cast(ColumnElement[int], Movie.year)
        id_column = cast(ColumnElement[int], Movie.id)
        genres_column = cast(ColumnElement[str], Movie.genres)

        filters = [
            year_column >= start_year,
            year_column <= end_year,
        ]

        if genre:
            # Genre is stored as comma-separated string, so we check if it contains the genre
            filters.append(genres_column.contains(genre))

        # Count total matching records
        total_statement = select(func.count()).select_from(Movie).where(*filters)
        total_result = session.exec(total_statement).one()
        total_items = total_result if isinstance(total_result, int) else total_result[0]

        # Execute paginated query
        statement = (
            select(Movie)
            .where(*filters)
            .order_by(year_column, id_column)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )

        movies = session.exec(statement).all()

        return list(movies), total_items
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error querying movies: {str(e)}")


JobType = Literal["export", "import"]


def get_job_type(job_id: str) -> JobType:
    """
    Get the job type (export or import) from a job ID.
    
    Args:
        job_id: The job ID to check
        
    Returns:
        "export" or "import" (defaults to "import" if cannot be determined)
    """
    task = celery_app.AsyncResult(job_id)
    job_type: JobType = "import"  # default
    
    try:
        # Try to get job_type from task metadata (available when task is running or has run)
        if task.info:
            if isinstance(task.info, dict):
                candidate = task.info.get('job_type')
                if candidate in ("export", "import"):
                    job_type = candidate
            # For FAILURE state, task.info might be a string, so check backend metadata
            elif task.state == 'FAILURE':
                if hasattr(task, 'backend') and task.backend:
                    try:
                        task_meta = task.backend.get_task_meta(task.id)
                        if isinstance(task_meta, dict):
                            # Check the last known metadata
                            meta = task_meta.get('meta', {})
                            if isinstance(meta, dict):
                                candidate = meta.get('job_type')
                                if candidate in ("export", "import"):
                                    job_type = candidate
                    except Exception:
                        pass
        # Fallback: check result structure if task is completed and type is still unknown
        if task.state == 'SUCCESS' and task.result and job_type == "import":
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

