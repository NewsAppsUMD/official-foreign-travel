"""Command-line interface modules."""

from .download import main as download_main
from .parse import main as parse_main
from .test_matching import main as test_matching_main

__all__ = ["download_main", "parse_main", "test_matching_main"]
