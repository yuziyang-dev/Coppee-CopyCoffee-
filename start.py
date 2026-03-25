"""HF Spaces entry point."""
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)

import uvicorn
from get_notes.web import app

uvicorn.run(app, host="0.0.0.0", port=7860)
