"""FastAPI application entrypoint.

This module exists to maintain the historic `web_server:app` import
path used by deployment environments.
"""
from server.main import create_app

app = create_app()
