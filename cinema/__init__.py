"""Монолитное веб-приложение кинотеатра."""

from .config import Config
from .web import CinemaApplication, create_application

__all__ = ["CinemaApplication", "Config", "create_application"]
