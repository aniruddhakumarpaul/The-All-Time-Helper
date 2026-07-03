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

from app.factory import BASE_DIR, create_app
from app.logger import logger

app = create_app()


if __name__ == "__main__":
    import uvicorn

    logger.info("[Main] BINDING TO: 0.0.0.0:9000")
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=9000,
        reload=True,
        reload_dirs=[str(BASE_DIR / path) for path in ("app", "static", "templates")],
    )
