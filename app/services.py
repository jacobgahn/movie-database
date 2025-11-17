"""
Service layer for file operations and business logic.
"""
import os
import csv
import io
import shutil
import logging
import time
from datetime import datetime
from uuid import uuid4
from fastapi import UploadFile, HTTPException
from sqlmodel import Session, delete
from app.models import Movie


UPLOAD_DIR = "./uploads"
EXPORT_DIR = "./exports"
IMPORT_LOGS_DIR = "./import_logs"

EXPORT_TTL_HOURS = int(os.getenv("EXPORT_TTL_HOURS", "24"))
EXPORT_TTL_SECONDS = EXPORT_TTL_HOURS * 3600

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB in bytes
CHUNK_SIZE = 8192  # 8KB chunks for efficient streaming
MAX_MOVIE_NAME_LENGTH = 500  # Maximum length for movie names


def validate_movie_name(movie_name: str) -> tuple[bool, str | None]:
    """
    Validate a movie name for import.
    
    Args:
        movie_name: The movie name to validate (should already be stripped)
        
    Returns:
        tuple: (is_valid, error_message)
        - is_valid: True if the name is valid, False otherwise
        - error_message: None if valid, error description if invalid
    """
    if not movie_name:
        return False, "Movie name cannot be empty"
    
    if len(movie_name) > MAX_MOVIE_NAME_LENGTH:
        return False, f"Movie name exceeds maximum length of {MAX_MOVIE_NAME_LENGTH} characters"
    
    # Check for control characters (non-printable except common whitespace)
    # Allow: space, tab, newline, carriage return (though CSV parsing should handle these)
    # Reject: null bytes, other control characters
    for char in movie_name:
        code = ord(char)
        # Reject null bytes and other control characters (except common whitespace)
        if code == 0 or (code < 32 and char not in ' \t\n\r'):
            return False, f"Movie name contains invalid control character (code {code})"
    
    # Check for excessive whitespace (more than 2 consecutive spaces)
    if '   ' in movie_name:
        return False, "Movie name contains excessive whitespace"
    
    return True, None


def parse_and_validate_movie_row(row: dict, row_num: int) -> tuple[Movie | None, str | None]:
    """
    Parse and validate a CSV row into a Movie object.
    
    This is the single source of truth for row parsing and validation logic.
    All import functions should use this to ensure consistent behavior.
    
    Args:
        row: Dictionary of CSV row values (from csv.DictReader)
        row_num: Row number for error reporting
        
    Returns:
        tuple: (movie, error_message)
        - movie: Movie object if valid, None otherwise
        - error_message: None if valid, error description if invalid
    """
    movie_name = row.get('movie_name', '').strip()
    is_valid, validation_error = validate_movie_name(movie_name)
    if not is_valid:
        return None, validation_error or "Missing required field: movie_name"
    
    year_str = row.get('year', '').strip()
    if not year_str:
        return None, "Missing required field: year"
    
    try:
        year = int(year_str)
    except ValueError:
        return None, f"Invalid year format: '{year_str}'"
    
    genres = row.get('genres', '').strip()
    rating_str = row.get('rating', '').strip()
    
    rating = None
    if rating_str:
        try:
            rating = float(rating_str)
        except ValueError:
            pass  # Rating is optional, invalid values become None
    
    try:
        movie = Movie(
            movie_name=movie_name,
            year=year,
            genres=genres,
            rating=rating
        )
        return movie, None
    except Exception as e:
        return None, f"Error creating movie object: {str(e)}"


def _write_error_log(errors: list[dict], log_file_path: str):
    """
    Write import errors to a log file.
    
    Args:
        errors: List of error dicts with keys: row_num, error, row_data
        log_file_path: Path to the error log file
        
    Raises:
        Exception: If the error log cannot be written
    """
    os.makedirs(IMPORT_LOGS_DIR, exist_ok=True)
    with open(log_file_path, 'w', encoding='utf-8') as f:
        f.write(f"Import Error Log\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n")
        f.write(f"Total Errors: {len(errors)}\n")
        f.write("=" * 80 + "\n\n")
        
        for error in errors:
            f.write(f"Row {error['row_num']}: {error['error']}\n")
            f.write(f"Row Data: {error.get('row_data', 'N/A')}\n")
            f.write("-" * 80 + "\n")


def cleanup_expired_exports(export_dir: str = EXPORT_DIR) -> int:
    """
    Delete export files that have exceeded their TTL.
    
    Args:
        export_dir: Directory containing export files
        
    Returns:
        int: Number of files deleted
    """
    if not os.path.exists(export_dir):
        return 0
    
    deleted_count = 0
    current_time = time.time()
    
    try:
        for filename in os.listdir(export_dir):
            # Only process export files (gzipped CSV files)
            if not filename.endswith('.csv.gz') or not filename.startswith('movies_export_'):
                continue
            
            file_path = os.path.join(export_dir, filename)
            
            if not os.path.isfile(file_path):
                continue
            
            file_mtime = os.path.getmtime(file_path)
            file_age = current_time - file_mtime
            
            if file_age > EXPORT_TTL_SECONDS:
                try:
                    os.remove(file_path)
                    deleted_count += 1
                    logger.info(f"Deleted expired export file: {filename} (age: {file_age / 3600:.2f} hours)")
                except OSError as e:
                    logger.error(f"Failed to delete expired export file {filename}: {str(e)}")
    
    except OSError as e:
        logger.error(f"Error cleaning up export directory: {str(e)}")
    
    if deleted_count > 0:
        logger.info(f"Cleaned up {deleted_count} expired export file(s)")
    
    return deleted_count


def is_export_expired(file_path: str) -> bool:
    """
    Check if an export file has exceeded its TTL.
    
    Args:
        file_path: Path to the export file
        
    Returns:
        bool: True if file is expired, False otherwise
    """
    if not os.path.exists(file_path):
        return True
    
    current_time = time.time()
    file_mtime = os.path.getmtime(file_path)
    file_age = current_time - file_mtime
    
    return file_age > EXPORT_TTL_SECONDS


async def save_uploaded_file(file: UploadFile, filename: str) -> str:
    """
    Save an uploaded file to disk, handling large files efficiently.
    
    For large files (> 1MB), FastAPI automatically spools them to a temporary file.
    This function attempts to move that temporary file directly if available,
    otherwise streams the file in chunks.
    
    Args:
        file: FastAPI UploadFile object
        filename: Desired filename for the saved file
        
    Returns:
        str: Path to the saved file
        
    Raises:
        HTTPException: If file validation fails or file cannot be saved
    """
    # Ensure upload directory exists
    try:
        os.makedirs(UPLOAD_DIR, exist_ok=True)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Error creating upload directory: {str(e)}")
    
    file_path = os.path.join(UPLOAD_DIR, f"import_{filename}")
    
    try:
        # Check if the file has been spooled to disk (SpooledTemporaryFile behavior)
        # FastAPI's UploadFile uses SpooledTemporaryFile which stores files > 1MB on disk
        spooled_file_path = None
        
        # Access the underlying SpooledTemporaryFile
        if hasattr(file.file, '_file'):
            underlying_file = file.file._file
            # If it's been spooled to disk, _file will be a file object with a 'name' attribute
            # If it's still in memory, _file will be a BytesIO object (no 'name' attribute)
            if hasattr(underlying_file, 'name'):
                name = underlying_file.name
                # Ensure name is a string path, not a file descriptor (int)
                if isinstance(name, str) and os.path.exists(name):
                    spooled_file_path = name
        
        if spooled_file_path:
            # File is already on disk - move it directly (much faster than copying)
            # First, check file size
            file_size = os.path.getsize(spooled_file_path)
            if file_size == 0:
                raise HTTPException(status_code=400, detail="Uploaded file is empty")
            
            if file_size > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=413,
                    detail=f"File size exceeds maximum allowed size of {MAX_FILE_SIZE / (1024*1024*1024):.1f}GB"
                )
            
            # Close the file handle before moving to avoid issues on some systems
            # The underlying file will be closed when we close the UploadFile in finally block
            # But we ensure it's flushed here
            try:
                if hasattr(file.file, 'flush'):
                    file.file.flush()
            except:
                pass
            
            # Move the spooled temporary file to our destination
            # This is a fast filesystem operation (rename), not a copy
            # On Linux/Unix, this is atomic and very fast even for large files
            shutil.move(spooled_file_path, file_path)
        else:
            # File is still in memory (< 1MB) or we can't access the path
            # Stream it in chunks
            total_size = 0
            has_content = False
            
            with open(file_path, 'wb') as f:
                # Stream file in chunks instead of loading entire file into memory
                while True:
                    chunk = await file.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    
                    has_content = True
                    total_size += len(chunk)
                    
                    # Check file size limit
                    if total_size > MAX_FILE_SIZE:
                        # Clean up partial file
                        f.close()
                        if os.path.exists(file_path):
                            os.remove(file_path)
                        raise HTTPException(
                            status_code=413,
                            detail=f"File size exceeds maximum allowed size of {MAX_FILE_SIZE / (1024*1024*1024):.1f}GB"
                        )
                    
                    f.write(chunk)
            
            if not has_content:
                if os.path.exists(file_path):
                    os.remove(file_path)
                raise HTTPException(status_code=400, detail="Uploaded file is empty")
            
    except HTTPException:
        raise
    except OSError as e:
        # Clean up partial file on disk errors
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except:
                pass
        raise HTTPException(status_code=500, detail=f"Error saving file: {str(e)}")
    except Exception as e:
        # Clean up partial file on any other error
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except:
                pass
        raise HTTPException(status_code=500, detail=f"Error saving file: {str(e)}")
    finally:
        # Close the upload file to clean up temporary resources
        await file.close()
    
    return file_path

