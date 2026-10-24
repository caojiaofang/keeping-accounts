"""共享生命周期按账本串行变更，邀请凭证只在请求/响应中出现，数据库只保存摘要。"""

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ServiceError
from app.models.ledger import Book
from app.models.shared import BookActivity, BookInvitation, BookMember
from app.schemas.shared import (
    ActivityResponse,
    CreateInvitation,
    InvitationPreview,
    InvitationResponse,
)
from app.services.ledger import LedgerService


class SharedBookService:
    def __init__(self, session: AsyncSession, settings: Settings, user_id: UUID | None = None):
        self.session, self.settings, self.user_id = session, settings, user_id
        self.ledger = LedgerService(session, user_id) if user_id else None

    def token(self, invitation_id: UUID) -> str:
        key = self.settings.book_invite_secret
        if key is None:
            raise ServiceError(503, "邀请服务尚未配置，请联系管理员")
        payload = (
            f"moji-invite:v1:{self.settings.app_env}:"
            f"{self.settings.wechat_app_id}:{invitation_id.hex}"
        )
        signature = hmac.new(
            key.get_secret_value().encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        return f"s1_{invitation_id.hex}_{signature}"

    async def managed_book(self, book_id):
        book = await self.ledger.require_book(book_id, write=True)
        if book.kind != "shared":
            raise ServiceError(404, "多人账本不存在或无权访问")
        self.ledger.require_owner(book)
        return book

    async def member(self, book_id, user_id):
        return await self.session.get(BookMember, (book_id, user_id), populate_existing=True)

    @staticmethod
    def check_version(book, version):
        if book.version != version:
            raise ServiceError(409, "账本已更新，请刷新后再操作")

    async def revoke_all(self, book_id):
        await self.session.execute(
            update(BookInvitation)
            .where(
                BookInvitation.book_id == book_id,
                BookInvitation.revoked_at.is_(None),
            )
            .values(revoked_at=func.now())
        )

    def invitation_response(self, invitation):
        token = self.token(invitation.id)
        if not secrets.compare_digest(
            invitation.token_hash, hashlib.sha256(token.encode()).hexdigest()
        ):
            raise ServiceError(410, "邀请配置已更新，请重新生成邀请")
        return InvitationResponse(
            id=invitation.id,
            token=token,
            book_id=invitation.book_id,
            expires_at=int(invitation.expires_at.timestamp() * 1000),
        )

    async def create_invitation(self, book_id, body: CreateInvitation):
        book = await self.managed_book(book_id)
        now = datetime.now(UTC)
        existing = await self.session.get(BookInvitation, body.id, populate_existing=True)
        if existing:
            if existing.book_id != book_id:
                raise ServiceError(404, "邀请不存在或无权访问")
            if existing.revoked_at or existing.expires_at <= now:
                raise ServiceError(410, "此邀请已经失效，请重新生成")
            return self.invitation_response(existing)
        if not body.replace:
            existing = await self.session.scalar(
                select(BookInvitation)
                .where(
                    BookInvitation.book_id == book_id,
                    BookInvitation.revoked_at.is_(None),
                    BookInvitation.expires_at > now,
                )
                .order_by(BookInvitation.created_at.desc())
                .limit(1)
            )
            if existing:
                return self.invitation_response(existing)
        token = self.token(body.id)
        await self.revoke_all(book_id)
        invitation = BookInvitation(
            id=body.id,
            book_id=book_id,
            invited_by=self.user_id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=now + timedelta(days=7),
        )
        self.session.add(invitation)
        book.version += 1
        self.ledger.activity(book_id, "invite", body.id)
        await self.session.flush()
        result = self.invitation_response(invitation)
        await self.session.commit()
        return result

    async def revoke_invitation(self, book_id, invitation_id):
        book = await self.managed_book(book_id)
        invitation = await self.session.get(BookInvitation, invitation_id, populate_existing=True)
        if invitation is None or invitation.book_id != book_id:
            raise ServiceError(404, "邀请不存在或无权访问")
        if invitation.revoked_at is None:
            invitation.revoked_at = datetime.now(UTC)
            book.version += 1
            self.ledger.activity(book_id, "revoke_invite", invitation_id)
        await self.session.commit()

    async def valid_invitation(self, token: str, write=False):
        if not re.fullmatch(r"s1_[0-9a-f]{32}_[0-9a-f]{64}", token):
            raise ServiceError(410, "邀请已失效或不存在")
        invitation_id = UUID(hex=token.split("_")[1])
        if not secrets.compare_digest(token, self.token(invitation_id)):
            raise ServiceError(410, "邀请已失效或不存在")
        invitation = await self.session.get(BookInvitation, invitation_id)
        if invitation is None:
            raise ServiceError(410, "邀请已失效或不存在")
        book = await self.session.scalar(
            select(Book)
            .where(
                Book.id == invitation.book_id,
                Book.kind == "shared",
                Book.status == "active",
            )
            .with_for_update(read=not write, of=Book)
            .execution_options(populate_existing=True)
        )
        await self.session.refresh(invitation)
        if (
            book is None
            or invitation.revoked_at
            or invitation.expires_at <= datetime.now(UTC)
            or invitation.invited_by != book.owner_id
            or not secrets.compare_digest(
                invitation.token_hash, hashlib.sha256(token.encode()).hexdigest()
            )
        ):
            raise ServiceError(410, "邀请已失效或不存在")
        return invitation, book

    async def preview(self, token):
        invitation, book = await self.valid_invitation(token)
        inviter = await self.member(book.id, book.owner_id)
        return InvitationPreview(
            book_id=book.id,
            book_name=book.name,
            inviter_name=inviter.name if inviter else "创建者",
            expires_at=int(invitation.expires_at.timestamp() * 1000),
        )

    async def accept(self, token, name):
        invitation, book = await self.valid_invitation(token, write=True)
        member = await self.member(book.id, self.user_id)
        if member is None or member.status != "active":
            await self.session.execute(
                insert(BookMember)
                .values(
                    book_id=book.id,
                    user_id=self.user_id,
                    name=name,
                    role="member",
                )
                .on_conflict_do_update(
                    index_elements=[BookMember.book_id, BookMember.user_id],
                    set_={
                        "name": name,
                        "role": "member",
                        "status": "active",
                        "left_at": None,
                        "joined_at": func.now(),
                    },
                )
            )
            book.version += 1
            self.ledger.activity(
                book.id, "join", self.user_id, {"invitationId": str(invitation.id)}
            )
        result = await self.ledger.describe(book)
        await self.session.commit()
        return result

    async def remove(self, book_id, user_id, version):
        book = await self.managed_book(book_id)
        member = await self.member(book_id, user_id)
        if member is None:
            raise ServiceError(404, "成员不存在")
        if member.role == "owner" or user_id == self.user_id:
            raise ServiceError(403, "创建者不能移出自己，请使用转让或解散功能")
        if member.status == "active":
            self.check_version(book, version)
            member.status, member.left_at = "removed", datetime.now(UTC)
            book.version += 1
            await self.revoke_all(book_id)
            self.ledger.activity(book_id, "remove_member", user_id)
            await self.session.flush()
        result = await self.ledger.describe(book)
        await self.session.commit()
        return result

    async def leave(self, book_id, version):
        book = await self.session.scalar(
            select(Book)
            .where(Book.id == book_id, Book.kind == "shared", Book.status == "active")
            .with_for_update()
        )
        member = await self.member(book_id, self.user_id)
        if book is None or member is None or member.status == "removed":
            raise ServiceError(404, "账本不存在或无权访问")
        if member.status == "left":
            return
        if book.owner_id == self.user_id:
            raise ServiceError(403, "创建者请先转让账本或解散账本")
        self.check_version(book, version)
        member.status, member.left_at = "left", datetime.now(UTC)
        book.version += 1
        await self.revoke_all(book_id)
        self.ledger.activity(book_id, "leave", self.user_id)
        await self.session.commit()

    async def transfer(self, book_id, new_owner_id, version):
        book = await self.ledger.require_book(book_id, write=True)
        if book.kind != "shared":
            raise ServiceError(404, "多人账本不存在或无权访问")
        if book.owner_id == new_owner_id and book.version == version + 1:
            return await self.ledger.describe(book)
        self.ledger.require_owner(book)
        self.check_version(book, version)
        new_owner = await self.member(book_id, new_owner_id)
        if new_owner_id == self.user_id or new_owner is None or new_owner.status != "active":
            raise ServiceError(422, "请选择另一位有效成员作为创建者")
        old_owner = await self.member(book_id, self.user_id)
        old_owner.role = "member"
        await self.session.flush()
        new_owner.role, book.owner_id = "owner", new_owner_id
        book.version += 1
        await self.revoke_all(book_id)
        self.ledger.activity(book_id, "transfer", new_owner_id)
        await self.session.flush()
        result = await self.ledger.describe(book)
        await self.session.commit()
        return result

    async def close(self, book_id, version):
        book = await self.session.scalar(
            select(Book)
            .where(Book.id == book_id, Book.kind == "shared", Book.owner_id == self.user_id)
            .with_for_update()
        )
        if book is None:
            raise ServiceError(404, "账本不存在或无权访问")
        if book.status == "closed":
            return
        self.check_version(book, version)
        book.status, book.version = "closed", book.version + 1
        await self.revoke_all(book_id)
        self.ledger.activity(book_id, "close", book_id)
        await self.session.commit()

    async def activities(self, book_id, before=None):
        await self.ledger.require_book(book_id)
        query = select(BookActivity).where(BookActivity.book_id == book_id)
        if before:
            cursor = await self.session.get(BookActivity, before)
            if cursor is None or cursor.book_id != book_id:
                raise ServiceError(404, "操作记录不存在")
            from sqlalchemy import tuple_

            query = query.where(
                tuple_(BookActivity.created_at, BookActivity.id) < (cursor.created_at, cursor.id)
            )
        rows = await self.session.scalars(
            query.order_by(BookActivity.created_at.desc(), BookActivity.id.desc()).limit(20)
        )
        return [
            ActivityResponse(
                id=row.id,
                actor_id=row.actor_id,
                action=row.action,
                target_id=row.target_id,
                payload=row.payload,
                created_at=int(row.created_at.timestamp() * 1000),
            )
            for row in rows
        ]
