"""管理员行锁保护改密和会话撤销，认证事件与账号变更在同一事务提交。"""

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import AdminAccount, AdminAuditEvent


@dataclass(frozen=True)
class AuditContext:
    ip: str
    user_agent: str = ""


class AdminRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def by_username(self, username: str) -> AdminAccount | None:
        return await self.session.scalar(
            select(AdminAccount).where(AdminAccount.username == username)
        )

    async def by_id(self, admin_id: UUID, *, lock: bool = False) -> AdminAccount | None:
        query = select(AdminAccount).where(AdminAccount.id == admin_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return await self.session.scalar(query)

    def add_event(
        self,
        username: str,
        action: str,
        outcome: str,
        context: AuditContext,
        *,
        admin_id: UUID | None = None,
        reason: str = "",
    ) -> None:
        self.session.add(
            AdminAuditEvent(
                username=username[:64],
                admin_id=admin_id,
                action=action,
                outcome=outcome,
                reason=reason,
                ip=context.ip[:64],
                user_agent=context.user_agent.replace("\r", " ").replace("\n", " ")[:256],
            )
        )

    async def bootstrap(self, username: str, password_hash: str) -> bool:
        result = await self.session.scalar(
            insert(AdminAccount)
            .values(
                id=uuid4(),
                username=username,
                password_hash=password_hash,
            )
            .on_conflict_do_nothing(index_elements=[AdminAccount.username])
            .returning(AdminAccount.id)
        )
        if result:
            self.add_event(
                username, "bootstrap", "success", AuditContext("console"), admin_id=result
            )
        await self.commit()
        return result is not None

    async def logs(self, action: str | None, outcome: str | None, page: int, page_size: int):
        filters = []
        if action:
            filters.append(AdminAuditEvent.action == action)
        if outcome:
            filters.append(AdminAuditEvent.outcome == outcome)
        total = await self.session.scalar(
            select(func.count()).select_from(AdminAuditEvent).where(*filters)
        )
        rows = await self.session.scalars(
            select(AdminAuditEvent)
            .where(*filters)
            .order_by(AdminAuditEvent.created_at.desc(), AdminAuditEvent.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(rows), int(total or 0)

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()
