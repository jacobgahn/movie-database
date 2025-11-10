"""
Service layer for file operations and business logic.
"""
import os
import csv
import io
import shutil
from fastapi import UploadFile, HTTPException
from sqlmodel import Session, delete
from app.models import Movie


# Directory for storing uploaded files
UPLOAD_DIR = "./uploads"
EXPORT_DIR = "./exports"

# Maximum file size: 2GB (adjust as needed)
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB in bytes
CHUNK_SIZE = 8192  # 8KB chunks for efficient streaming


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


async def import_movies_from_upload_file(file: UploadFile, session: Session) -> dict:
    """
    Import movies from uploaded CSV file into the database, overwriting existing data.
    Reads file in chunks to handle large files efficiently.
    
    Args:
        file: FastAPI UploadFile object
        session: Database session
        
    Returns:
        dict with status, imported count, error count, and total rows
        
    Raises:
        HTTPException: If file is invalid or import fails
    """
    # Start transaction
    batch_size = 1000
    batch = []
    imported_count = 0
    error_count = 0
    total_rows = 0
    total_size = 0
    buffer = b""
    header = None
    has_content = False

    try:
        # Clear existing data (overwrite previous state) - part of transaction
        session.exec(delete(Movie))
        # Don't commit yet - wait until import is complete
        
        # Read file in chunks
        while True:
            chunk = await file.read(CHUNK_SIZE)
            if not chunk:
                break
            
            has_content = True
            total_size += len(chunk)
            
            # Check file size limit
            if total_size > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=413,
                    detail=f"File size exceeds maximum allowed size of {MAX_FILE_SIZE / (1024*1024*1024):.1f}GB"
                )
            
            buffer += chunk
            
            # Process complete lines from buffer
            while True:
                # Find the first newline
                newline_pos = buffer.find(b'\n')
                if newline_pos == -1:
                    # No complete line yet, wait for more data
                    break
                
                # Extract complete line (including newline)
                line_bytes = buffer[:newline_pos + 1]
                buffer = buffer[newline_pos + 1:]
                
                # Decode line
                try:
                    line = line_bytes.decode('utf-8').rstrip('\r\n')
                except UnicodeDecodeError:
                    raise HTTPException(status_code=400, detail="File must be valid UTF-8 encoded text")
                
                # Skip empty lines
                if not line.strip():
                    continue
                
                # Parse CSV line
                csv_line = io.StringIO(line)
                csv_reader = csv.reader(csv_line)
                
                try:
                    row_values = next(csv_reader)
                except StopIteration:
                    continue
                
                # First line is header
                if header is None:
                    header = row_values
                    continue
                
                # Create dict from row values
                if len(row_values) != len(header):
                    error_count += 1
                    continue
                
                row = dict(zip(header, row_values))
                total_rows += 1
                
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
                    
                    # Batch insert (but don't commit yet - wait for transaction)
                    if len(batch) >= batch_size:
                        session.add_all(batch)
                        imported_count += len(batch)
                        batch = []
                
                except Exception as e:
                    error_count += 1
                    continue
        
        # Process any remaining data in buffer (last line without newline)
        if buffer.strip():
            try:
                line = buffer.decode('utf-8').strip()
                if line and header:
                    csv_line = io.StringIO(line)
                    csv_reader = csv.reader(csv_line)
                    row_values = next(csv_reader, None)
                    
                    if row_values and len(row_values) == len(header):
                        row = dict(zip(header, row_values))
                        total_rows += 1
                        
                        try:
                            movie_name = row.get('movie_name', '').strip()
                            if movie_name:
                                year_str = row.get('year', '').strip()
                                if year_str:
                                    try:
                                        year = int(year_str)
                                        genres = row.get('genres', '').strip()
                                        rating_str = row.get('rating', '').strip()
                                        
                                        rating = None
                                        if rating_str:
                                            try:
                                                rating = float(rating_str)
                                            except ValueError:
                                                pass
                                        
                                        movie = Movie(
                                            movie_name=movie_name,
                                            year=year,
                                            genres=genres,
                                            rating=rating
                                        )
                                        batch.append(movie)
                                    except ValueError:
                                        error_count += 1
                                else:
                                    error_count += 1
                            else:
                                error_count += 1
                        except Exception:
                            error_count += 1
            except UnicodeDecodeError:
                raise HTTPException(status_code=400, detail="File must be valid UTF-8 encoded text")
            except Exception:
                pass  # Ignore errors in last line processing
        
        if not has_content:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")
        
        if header is None:
            raise HTTPException(status_code=400, detail="CSV file is empty or invalid")
        
        if total_rows == 0:
            raise HTTPException(status_code=400, detail="CSV file contains no data rows")
        
        # Insert remaining batch
        if batch:
            session.add_all(batch)
            imported_count += len(batch)
        
        # Commit transaction
        session.commit()
        
        return {
            "status": "completed",
            "imported": imported_count,
            "errors": error_count,
            "total_rows": total_rows
        }
        
    except HTTPException:
        session.rollback()
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Error importing movies: {str(e)}")
    finally:
        # Close the upload file to clean up temporary resources
        await file.close()


def import_movies_from_csv(file_path: str, session: Session) -> dict:
    """
    Import movies from CSV file into the database, overwriting existing data.
    
    Args:
        file_path: Path to the CSV file to import
        session: Database session
        
    Returns:
        dict with status, imported count, error count, and total rows
        
    Raises:
        HTTPException: If file is invalid or import fails
    """
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="CSV file not found")
    
    # First pass: count total rows for validation
    total_rows = 0
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            total_rows = sum(1 for _ in reader)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error reading CSV file: {str(e)}")
    
    if total_rows == 0:
        raise HTTPException(status_code=400, detail="CSV file is empty or invalid")
    
    # Clear existing data (overwrite previous state)
    try:
        session.exec(delete(Movie))
        session.commit()
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Error clearing existing data: {str(e)}")
    
    # Second pass: import data in batches
    batch_size = 1000
    batch = []
    imported_count = 0
    error_count = 0
    
    try:
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
                        session.add_all(batch)
                        session.commit()
                        imported_count += len(batch)
                        batch = []
                
                except Exception as e:
                    error_count += 1
                    continue
            
            # Insert remaining batch
            if batch:
                session.add_all(batch)
                session.commit()
                imported_count += len(batch)
        
        return {
            "status": "completed",
            "imported": imported_count,
            "errors": error_count,
            "total_rows": total_rows
        }
        
    except HTTPException:
        session.rollback()
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Error importing movies: {str(e)}")

