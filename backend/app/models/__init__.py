"""数据库模型统一导入，供 Alembic 发现。"""

from app.models.user import User

__all__ = ["User"]
