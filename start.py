"""HF Spaces entry point with verbose logging."""
import sys
import os

print(">>> start.py: begin", flush=True)
print(f">>> Python: {sys.version}", flush=True)
print(f">>> CWD: {os.getcwd()}", flush=True)
print(f">>> Files: {os.listdir('.')}", flush=True)

try:
    print(">>> importing uvicorn...", flush=True)
    import uvicorn

    print(">>> importing get_notes.web...", flush=True)
    from get_notes.web import app

    print(">>> starting uvicorn on port 7860...", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=7860)
except Exception as e:
    print(f">>> FATAL ERROR: {e}", flush=True)
    import traceback
    traceback.print_exc()
    sys.exit(1)
