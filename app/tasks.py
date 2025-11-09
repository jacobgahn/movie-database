"""
Celery tasks for long-running operations:
- CSV import
- CSV export and zipping
"""
import csv
import os
import gzip
import uuid
from sqlmodel import Session, select, delete
from app.celery_app import celery_app
from app.database import engine
from app.models import Movie


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
            meta={"current": 0, "total": 0, "status": "Starting import..."}
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
            meta={"current": 0, "total": total_rows, "status": "Cleared existing data. Importing movies..."}
        )
        
        # Second pass: import data in batches
        batch_size = 1000
        batch = []
        imported_count = 0
        error_count = 0
        
        with open(file_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            for row_num, row in enumerate(reader, start=1):
                try:
                    # Validate and parse row
                    movie_name = row.get('movie_name', '').strip()
                    if not movie_name:
                        error_count += 1
                        continue
                    
                    year_str = row.get('year', '').strip()
                    if not year_str:
                        error_count += 1
                        continue
                    
                    try:
                        year = int(year_str)
                    except ValueError:
                        error_count += 1
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
                                "progress": progress
                            }
                        )
                
                except Exception as e:
                    error_count += 1
                    continue
            
            # Insert remaining batch
            if batch:
                with Session(engine) as session:
                    session.add_all(batch)
                    session.commit()
                imported_count += len(batch)
        
        result = {
            "status": "completed",
            "imported": imported_count,
            "errors": error_count,
            "total_rows": total_rows
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
        
        # Update state: starting
        self.update_state(
            state="PROGRESS",
            meta={"current": 0, "total": 0, "status": "Starting export..."}
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
            meta={"current": 0, "total": total_movies, "status": f"Exporting {total_movies} movies..."}
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
                                "progress": progress
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

