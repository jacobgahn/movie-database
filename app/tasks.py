"""
Celery tasks for long-running operations:
- CSV import
- CSV export and zipping
"""
import csv
import os
import gzip
import uuid
import logging
from datetime import datetime
from sqlmodel import Session, select, delete
from app.celery_app import celery_app
from app.database import engine
from app.models import Movie
from app.services import IMPORT_LOGS_DIR, _write_error_log, cleanup_expired_exports

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


@celery_app.task(bind=True, name="import_movies_task")
def import_movies_task(self, file_path: str):
    """
    Import movies from CSV file with progress tracking.
    Replaces all existing data.
    
    Args:
        file_path: Path to the CSV file to import
        
    Returns:
        dict with status and count of imported movies
    """
    try:
        # Update state: starting
        self.update_state(
            state="PROGRESS",
            meta={"current": 0, "total": 0, "status": "Starting import...", "job_type": "import"}
        )
        
        # First pass: count total rows for progress tracking
        total_rows = 0
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                total_rows = sum(1 for _ in reader)
        
        if total_rows == 0:
            return {"status": "error", "message": "CSV file is empty or invalid"}
        
        # Clear existing data
        with Session(engine) as session:
            session.exec(delete(Movie))
            session.commit()
        
        self.update_state(
            state="PROGRESS",
            meta={"current": 0, "total": total_rows, "status": "Cleared existing data. Importing movies...", "job_type": "import"}
        )
        
        # Second pass: import data in batches
        batch_size = 1000
        batch = []
        imported_count = 0
        error_count = 0
        errors = []  # Track errors for logging
        log_file_path = None  # Will be set if errors occur
        
        with open(file_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            for row_num, row in enumerate(reader, start=1):
                try:
                    # Validate and parse row
                    movie_name = row.get('movie_name', '').strip()
                    if not movie_name:
                        error_count += 1
                        error_msg = "Missing required field: movie_name"
                        errors.append({
                            "row_num": row_num,
                            "error": error_msg,
                            "row_data": str(row)
                        })
                        logger.warning(f"Row {row_num}: {error_msg}")
                        continue
                    
                    year_str = row.get('year', '').strip()
                    if not year_str:
                        error_count += 1
                        error_msg = "Missing required field: year"
                        errors.append({
                            "row_num": row_num,
                            "error": error_msg,
                            "row_data": str(row)
                        })
                        logger.warning(f"Row {row_num}: {error_msg}")
                        continue
                    
                    try:
                        year = int(year_str)
                    except ValueError:
                        error_count += 1
                        error_msg = f"Invalid year format: '{year_str}'"
                        errors.append({
                            "row_num": row_num,
                            "error": error_msg,
                            "row_data": str(row)
                        })
                        logger.warning(f"Row {row_num}: {error_msg}")
                        continue
                    
                    genres = row.get('genres', '').strip()
                    rating_str = row.get('rating', '').strip()
                    
                    rating = None
                    if rating_str:
                        try:
                            rating = float(rating_str)
                        except ValueError:
                            pass  # Rating is optional
                    
                    movie = Movie(
                        movie_name=movie_name,
                        year=year,
                        genres=genres,
                        rating=rating
                    )
                    batch.append(movie)
                    
                    # Batch insert
                    if len(batch) >= batch_size:
                        with Session(engine) as session:
                            session.add_all(batch)
                            session.commit()
                        imported_count += len(batch)
                        batch = []
                        
                        # Update progress
                        progress = min(100, int((row_num / total_rows) * 100))
                        self.update_state(
                            state="PROGRESS",
                            meta={
                                "current": row_num,
                                "total": total_rows,
                                "status": f"Imported {imported_count} movies...",
                                "progress": progress,
                                "job_type": "import"
                            }
                        )
                
                except Exception as e:
                    error_count += 1
                    error_msg = f"Unexpected error: {str(e)}"
                    errors.append({
                        "row_num": row_num,
                        "error": error_msg,
                        "row_data": str(row)
                    })
                    logger.error(f"Row {row_num}: {error_msg}", exc_info=True)
                    continue
            
            # Insert remaining batch
            if batch:
                with Session(engine) as session:
                    session.add_all(batch)
                    session.commit()
                imported_count += len(batch)
        
        # Write error log if there were errors
        if errors:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            unique_id = uuid.uuid4().hex[:8]
            log_filename = f"import_errors_{timestamp}_{unique_id}.log"
            log_file_path = os.path.join(IMPORT_LOGS_DIR, log_filename)
            try:
                _write_error_log(errors, log_file_path)
                logger.info(f"Error log written to: {log_file_path}")
            except Exception as e:
                logger.error(f"Failed to write error log: {str(e)}", exc_info=True)
                # Still include the path in the result even if write failed
                # so the user knows we tried to create it
                log_file_path = log_file_path  # Keep the intended path
        else:
            log_file_path = None
        
        result = {
            "status": "completed",
            "imported": imported_count,
            "errors": error_count,
            "total_rows": total_rows,
            "error_log": log_file_path
        }
        
        return result
        
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }


@celery_app.task(bind=True, name="export_movies_zip_task")
def export_movies_zip_task(self, output_dir: str = "./exports"):
    """
    Export all movies to a gzipped CSV file with progress tracking.
    
    Args:
        output_dir: Directory to store the zip file
        
    Returns:
        dict with status and path to zip file
    """
    try:
        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)
        
        # Clean up expired export files before creating new one
        cleanup_expired_exports(output_dir)
        
        # Update state: starting
        self.update_state(
            state="PROGRESS",
            meta={"current": 0, "total": 0, "status": "Starting export...", "job_type": "export"}
        )
        
        # Count total movies for progress tracking
        with Session(engine) as session:
            statement = select(Movie)
            movies = session.exec(statement).all()
            total_movies = len(movies)
        
        if total_movies == 0:
            return {"status": "error", "message": "No movies in database"}
        
        # Generate unique filename
        zip_filename = f"movies_export_{uuid.uuid4().hex[:8]}.csv.gz"
        zip_path = os.path.join(output_dir, zip_filename)
        
        self.update_state(
            state="PROGRESS",
            meta={"current": 0, "total": total_movies, "status": f"Exporting {total_movies} movies...", "job_type": "export"}
        )
        
        # Write CSV and compress in one pass
        exported_count = 0
        with gzip.open(zip_path, 'wt', encoding='utf-8', newline='') as gz_file:
            writer = csv.DictWriter(gz_file, fieldnames=['movie_name', 'year', 'genres', 'rating'])
            writer.writeheader()
            
            # Query and write in batches for progress updates
            batch_size = 1000
            with Session(engine) as session:
                statement = select(Movie)
                movies = session.exec(statement)
                
                batch = []
                for movie in movies:
                    row = {
                        'movie_name': movie.movie_name,
                        'year': str(movie.year),
                        'genres': movie.genres,
                        'rating': str(movie.rating) if movie.rating is not None else ''
                    }
                    writer.writerow(row)
                    exported_count += 1
                    
                    # Update progress periodically
                    if exported_count % batch_size == 0:
                        progress = min(100, int((exported_count / total_movies) * 100))
                        self.update_state(
                            state="PROGRESS",
                            meta={
                                "current": exported_count,
                                "total": total_movies,
                                "status": f"Exported {exported_count}/{total_movies} movies...",
                                "progress": progress,
                                "job_type": "export"
                            }
                        )
        
        result = {
            "status": "completed",
            "exported": exported_count,
            "file_path": zip_path,
            "filename": zip_filename
        }
        
        return result
        
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }

