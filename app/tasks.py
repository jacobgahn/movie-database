from app.celery_app import celery_app


@celery_app.task(bind=True)
def example_task(self, message: str) -> str:
    """
    Example Celery task.
    Replace this with your actual background tasks.
    """
    return f"Task completed: {message}"


