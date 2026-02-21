"""Database dependency — provides RedditDatabase context to route handlers."""

from collections.abc import Generator

from sentinel.db import RedditDatabase


def get_db() -> Generator[RedditDatabase, None, None]:
    """FastAPI dependency that yields a RedditDatabase connection."""
    with RedditDatabase() as db:
        yield db
