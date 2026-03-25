"""HF Spaces entry point with error logging."""
import sys
import traceback

try:
    import uvicorn
    uvicorn.run("get_notes.web:app", host="0.0.0.0", port=7860)
except Exception:
    traceback.print_exc()
    sys.exit(1)
