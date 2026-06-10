"""Rotating logger, modeled on the sibling brand_finder logger.

Console handler always; rotating file handler under ``logs/`` when DEV_MODE is on.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("survey_intel")
logger.setLevel(logging.INFO)

formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

if os.getenv("DEV_MODE", "true").lower() != "false":
    log_dir = Path(__file__).resolve().parent.parent.parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    file_handler = RotatingFileHandler(
        log_dir / "survey_intel.log",
        maxBytes=10485760,  # 10MB
        backupCount=5,
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


def get_logger() -> logging.Logger:
    """Get the global logger instance."""
    return logger
