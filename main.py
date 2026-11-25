"""Thin shim: the real FastAPI app lives in app.main.

``uvicorn main:app`` keeps working (deploy/remote-deploy.sh uses it),
so the deploy needs no change. Run the app directly as
``uvicorn app.main:app`` if you prefer.
"""

from app.main import app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True, log_level="info")
