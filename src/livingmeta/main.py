"""ASGI application entry point."""

from .api import create_app

app = create_app()
