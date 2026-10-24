from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


async def upsert_wechat_user(session: AsyncSession, app_id: str, openid: str) -> tuple[User, bool]:
    candidate_id = uuid4()
    statement = (
        insert(User)
        .values(id=candidate_id, wechat_app_id=app_id, openid=openid)
        .on_conflict_do_update(
            constraint="uq_users_wechat_app_id_openid",
            set_={"last_login_at": func.now()},
        )
        .returning(User)
        .execution_options(populate_existing=True)
    )
    user = (await session.scalars(statement)).one()
    return user, user.id == candidate_id


async def find_user(session: AsyncSession, user_id: UUID, app_id: str) -> User | None:
    return await session.scalar(
        select(User).where(User.id == user_id, User.wechat_app_id == app_id)
    )
