import os

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
