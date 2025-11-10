# Implementation Explanation

Even though the assignment didn't require it, I wanted to make the project a little more production-ready. This included a full featured sql db for query speed and extensibility, and async worker processes to handle large jobs for import/export. There's plenty more that could be done if this were to become a user-facing application, I've logged some future considerations at the end of this file.



## Workers
Since the import/export could be large, I wanted to process this in a celery worker instance to avoid bogging down the api server. 

At this point there were two more dependencies: celery and redis. Both would be extremely useful for any production environment.

With these dependencies it especially made sense to dockerize the application, outside of the obvious advantages to deployment and consistent local development environments


## Job status
One of the more open ended challenged in the assignment was how to implement "Provide progress updates for long running requests > 2 seconds". This could have been done with websockets, server sent events, or simple status polling; all with slightly different use cases that were not fully defined yet. 

I went with a more flexible polling pattern but could have easily used an open connection for the frontend status communication.

The celery task metadata is used to track the status and type, and the status endpoint fetches the task metadata from celery directly.

## File Upload
FastAPI saves large uploaded files to a temporary location before executing the handler. As an efficiency, the save import csv method attempts to find this file and copy it directly into a more permanent storage for processing. This is an alternative to streaming the file data into a new file.

## Database
Since the movie data could potentially be very large, I added a lightweight sqlite database and the sqlmodel ORM (from the same vendor as FastAPI). This allows us to index columns for query optimization for example. 

I began with an sqlite database; a migration to postgres would be trivial. I used sqlmodel + SQLAlchemy for more production ready db interactions. Later, I would add alembic migrations as well.

## Tests
Some basic integration tests were added to test validation. These use an sqlite test database with automatic teardown and test isolation.

# Future Considerations

- User models, authentication, authorization for protecting critical endpoints like import
- Db migrations, simple enough to migrate from create_all.
- Store import and export request data in db
- Filter movies before exporting, useful feature?
- Worker queue priority tweaking
- Blocking import if import is already in progress