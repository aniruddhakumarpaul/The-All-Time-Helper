from __future__ import annotations

import os
import sys
from pathlib import Path


# Support IDE/VS Code "Run Python File" execution, e.g.:
#   python app/main.py
# In that mode Python puts app/ on sys.path instead of the repository root,
# so absolute imports such as `from app.factory import ...` fail unless the
# root is added before importing the package.
if __package__ in {None, ""}:
    repo_root = Path(__file__).resolve().parent.parent
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)

    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    pythonpath_entries = [entry for entry in existing_pythonpath.split(os.pathsep) if entry]
    if repo_root_str not in pythonpath_entries:
        os.environ["PYTHONPATH"] = os.pathsep.join([repo_root_str, *pythonpath_entries])

    # Keep relative runtime paths, .env lookup, and uvicorn reload imports rooted
    # at the repository when launched from an IDE run button.
    os.chdir(repo_root_str)

from app.factory import BASE_DIR, append_cors_origin, create_app
from app.logger import logger
from app.services.ngrok import start_ngrok_if_enabled, stop_ngrok

app = create_app()


def run_local_server() -> None:
    import uvicorn

    port = int(os.getenv("PORT", "9000"))
    session = start_ngrok_if_enabled(port)
    if session.public_url:
        os.environ["NGROK_PUBLIC_URL"] = session.public_url
        append_cors_origin(app, session.public_url)
        logger.info(f"[Main] Public Ngrok URL: {session.public_url}")

    logger.info(f"[Main] Binding to http://0.0.0.0:{port}")
    try:
        uvicorn.run(
            "app.main:app",
            host="0.0.0.0",
            port=port,
            reload=True,
            reload_dirs=[str(BASE_DIR / path) for path in ("app", "static", "templates")],
        )
    finally:
        stop_ngrok(session)


if __name__ == "__main__":
    run_local_server()
