"""PyCharm 可直接运行或调试此文件。"""

import uvicorn

from app.core.config import BACKEND_DIR, Settings

if __name__ == "__main__":
    settings = Settings()
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        app_dir=str(BACKEND_DIR),
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        reload_dirs=[str(BACKEND_DIR / "app")] if settings.reload else None,
        log_level=settings.log_level.lower(),
    )
