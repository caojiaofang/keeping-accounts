"""开发 PostgreSQL/Redis + 真实模型的显式评测，不修改环境文件或发布配置。

默认只列出场景；--live 才读取服务配置和发送模型请求。
每个表达使用独立测试账号、账本和外层回滚事务；报告不保存密钥或模型推理。
"""

import argparse
import asyncio
import hashlib
import json
import secrets
import time
from contextlib import aclosing
from contextvars import ContextVar
from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from uuid import uuid4

import httpx
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import Settings
from app.core.errors import ServiceError
from app.evaluations.chat_v2_cases import CURRENT, RECORDS, TODAY, matching, selected_cases
from app.integrations.llm import LlmClient
from app.models.chat import ChatConversation, ChatTurn
from app.models.ledger import Book, LedgerEntry
from app.models.shared import BookMember
from app.models.user import User
from app.schemas.chat_v2 import (
    AnswerPreviewV2,
    ChatContextV2,
    ContextPatch,
    CreateConversationV2,
    DateRange,
    EntryFilter,
    SendMessageV2,
)
from app.services.chat_v2 import ChatServiceV2
from app.services.ledger import LedgerService

REQUEST_METRIC = ContextVar("chat_v2_eval_request", default=None)


async def observe_response(response):
    metric = REQUEST_METRIC.get()
    if metric is not None:
        metric["http_status"] = response.status_code


def milliseconds(start):
    return round((time.monotonic() - start) * 1000)


def require(condition, detail):
    if not condition:
        raise AssertionError(detail)


def oracle(result, arguments, records):
    """每次真实工具调用均核对全范围事实和返回 ID，独立于模型措辞。"""
    require(result.status == "complete", "查询工具未完整完成")
    if result.name == "get_entry":
        rows = [row for row in records if str(row["id"]) == str(arguments["entryId"])]
    else:
        rows = matching(records, EntryFilter.model_validate(arguments["filters"]))
    previous = []
    if result.name == "compare_periods":
        filters = {**arguments["filters"], "dateRange": arguments["previousRange"]}
        previous = matching(records, EntryFilter.model_validate(filters))
    known = {row["id"]: row for row in rows if not row.get("foreign") and not row["is_deleted"]}
    checked = 0
    for fact in result.fact_refs:
        for kind in ("expense", "income"):
            current = [row for row in rows if row["type"] == kind]
            old = [row for row in previous if row["type"] == kind]
            expected = {
                "total": sum(row["amount"] for row in current),
                "count": len(current),
                "previous_total": sum(row["amount"] for row in old),
                "previous_count": len(old),
                "difference": sum(row["amount"] for row in current)
                - sum(row["amount"] for row in old),
            }
            if fact.id == f"{result.call_id}.{kind}.{fact.metric}" and fact.metric in expected:
                require(fact.value == expected[fact.metric], "工具金额或笔数与独立预期不符")
                checked += 1
    facts = {fact.id: fact for fact in result.fact_refs}
    for card in result.cards:
        for row in card.rows:
            if row.entry_id:
                require(row.entry_id in known, "返回了范围外、已删除或跨账本的账单 ID")
                original = known[row.entry_id]
                require(row.date == original["date"], "账单日期不匹配")
                for fid in row.fact_ids:
                    if facts[fid].metric == "amount":
                        require(facts[fid].value == original["amount"], "单笔金额不匹配")
            elif arguments.get("groupBy"):
                group = arguments["groupBy"]

                def belongs(record, *, old=False, row=row, group=group, card=card):
                    if record["type"] != row.entry_type:
                        return False
                    if group == "date" and result.name == "compare_periods":
                        start = date.fromisoformat(
                            arguments["previousRange"]["start"]
                            if old
                            else arguments["filters"]["dateRange"]["start"]
                        )
                        key = str((record["date"] - start).days)
                    else:
                        field = {
                            "date": "date",
                            "category": "category_id",
                            "account": "account",
                            "creator": "created_by",
                        }[group]
                        key = str(record[field])
                    if row.is_other:
                        shown = {item.group_key for item in card.rows if not item.is_other}
                        return key not in shown
                    return key == row.group_key

                current = [record for record in rows if belongs(record)]
                old = [record for record in previous if belongs(record, old=True)]
                total, prior = (
                    sum(item["amount"] for item in current),
                    sum(item["amount"] for item in old),
                )
                aggregate = sum(item["amount"] for item in rows if item["type"] == row.entry_type)
                prior_aggregate = sum(
                    item["amount"] for item in previous if item["type"] == row.entry_type
                )
                expected = {
                    "total": total,
                    "count": len(current),
                    "previous_total": prior,
                    "previous_count": len(old),
                    "difference": total - prior,
                }

                def percentage(numerator, denominator):
                    return int(
                        (Decimal(numerator) * 10000 / denominator).quantize(
                            Decimal(1), rounding=ROUND_HALF_UP
                        )
                    )

                if prior:
                    expected["change_rate"] = percentage(total - prior, prior)
                denominator = (
                    aggregate - prior_aggregate if result.name == "compare_periods" else aggregate
                )
                if denominator:
                    expected["share"] = percentage(
                        total - prior if result.name == "compare_periods" else total, denominator
                    )
                for fid in row.fact_ids:
                    metric = facts[fid].metric
                    require(
                        metric in expected and facts[fid].value == expected[metric],
                        "分组金额、笔数或比例与独立预期不符",
                    )
    require(checked > 0, "未找到可核对的统计事实")


def validate_turn(turn, case, variant, fixture, tools):
    require(turn.completeness == "complete", "回答仅部分完成")
    if case.scenario == "clarify":
        require(
            not turn.cards and not turn.fact_refs and bool(turn.answer), "模糊指代未澄清或擅自统计"
        )
        return
    filters = turn.context.filters
    require(
        (str(filters.date_range.start), str(filters.date_range.end)) == case.dates,
        "保存的实际日期范围不正确",
    )
    if case.previous:
        require(turn.context.previous_range is not None, "比较未保存上期范围")
        require(
            (str(turn.context.previous_range.start), str(turn.context.previous_range.end))
            == case.previous,
            "上期日期范围不正确",
        )
    if case.keywords is not None:
        require(set(filters.keywords.include) == set(case.keywords), "备注关键词追加或继承不正确")
        require(filters.keywords.mode == "any", "任意包含关键词被误改为全部包含")
    if case.name == "shared_creator":
        require(filters.creator_ids == [fixture["member"]], "未将成员姓名解析为正确的记账人")
    if case.name == "exclude_entry":
        require(
            filters.excluded_entry_ids == [fixture["ids"]["headphones"]], "排除未绑定真实展示的账单"
        )
    for name in case.tools:
        require(name in tools, "未使用此场景所需的工具")
    facts = {fact.id: fact for fact in turn.fact_refs}
    values = {
        fact.metric: fact.value
        for fact in facts.values()
        if fact.id.endswith((".expense.total", ".expense.count"))
    }
    require(
        values.get("total") == case.total and values.get("count") == case.count,
        "最终回答金额或笔数不符合场景预期",
    )
    if case.name == "zero_base":
        require(
            not any(fact.metric == "change_rate" for fact in facts.values()), "零基期生成了增长率"
        )
    require(
        all(suggestion.source_turn_id == turn.id for suggestion in turn.suggestions),
        "建议来源轮次不正确",
    )


class Trial:
    def __init__(self, settings, engine, redis, model_client, case, variant):
        self.settings, self.engine, self.redis = settings, engine, redis
        self.model_client, self.case, self.variant = model_client, case, variant
        self.owner, self.member, self.foreign = (uuid4() for _ in range(3))
        self.book, self.other_book, self.cid = (uuid4() for _ in range(3))
        self.cids = [self.cid]
        self.ids = {record[0]: uuid4() for record in RECORDS}
        self.records, self.requests, self.tools, self.rounds = [], [], [], []
        self.prefix = settings.redis_key_prefix + f"eval-v2:{self.owner.hex}:"
        self.settings = settings.model_copy(update={"redis_key_prefix": self.prefix})
        self.started = time.monotonic()
        self.first_tool_ms = self.first_snapshot_ms = None

    async def seed(self, connection):
        await connection.execute(
            insert(User),
            [
                {
                    "id": uid,
                    "openid": f"eval-v2-{uid}",
                    "wechat_app_id": self.settings.wechat_app_id,
                }
                for uid in (self.owner, self.member, self.foreign)
            ],
        )
        await connection.execute(
            insert(Book),
            [
                {"id": self.book, "owner_id": self.owner, "name": "评测固定账本", "kind": "shared"},
                {
                    "id": self.other_book,
                    "owner_id": self.foreign,
                    "name": "不可见账本",
                    "kind": "personal",
                },
            ],
        )
        await connection.execute(
            insert(BookMember),
            [
                {
                    "book_id": self.book,
                    "user_id": uid,
                    "name": name,
                    "role": role,
                    "status": "active",
                }
                for uid, name, role in (
                    (self.owner, "小周", "owner"),
                    (self.member, "小林", "member"),
                )
            ],
        )
        for key, author, kind, category, amount, day, note, deleted in RECORDS:
            self.records.append(
                {
                    "id": self.ids[key],
                    "book_id": self.book,
                    "created_by": self.owner if author == "owner" else self.member,
                    "type": kind,
                    "category_id": category,
                    "amount": amount,
                    "date": date.fromisoformat(day),
                    "note": note,
                    "account": "微信",
                    "is_deleted": deleted,
                }
            )
        await connection.execute(insert(LedgerEntry), self.records)
        hidden = {
            **self.records[0],
            "id": uuid4(),
            "book_id": self.other_book,
            "created_by": self.foreign,
            "amount": 400000,
        }
        await connection.execute(insert(LedgerEntry), hidden)
        self.records.append({**hidden, "foreign": True})

    def instrument(self, service):
        post = service.answer.loop.model.post
        execute = service.answer.loop.tools.execute

        async def measured_post(payload):
            started = time.monotonic()
            metric = {"elapsed_ms": None, "status": "started"}
            self.requests.append(metric)
            token = REQUEST_METRIC.set(metric)
            try:
                effort = payload.get("reasoning", {}).get("effort", payload.get("reasoning_effort"))
                require(effort == "xhigh", "评测请求未使用现有推理强度")
                result = await post(payload)
                metric["status"] = "completed"
                # 只留固定虚构评测的结构化输出，不留 reasoning/encrypted_content 或 HTTP 原文。
                metric["output_status"] = result.get("status")
                metric["returned_model"] = result.get("model")
                metric["decisions"] = [
                    part.get("text", "")
                    for item in result.get("output", [])
                    if item.get("type") == "message"
                    for part in item.get("content", [])
                    if part.get("type") == "output_text"
                ]
                metric["native_calls"] = [
                    {"name": item.get("name"), "arguments": item.get("arguments")}
                    for item in result.get("output", [])
                    if item.get("type") == "function_call"
                ]
                usage = result.get("usage") or {}
                metric["usage"] = {
                    key: value for key, value in usage.items() if isinstance(value, int)
                }
                return result
            except BaseException as error:
                metric["status"] = type(error).__name__
                if isinstance(error, ServiceError):
                    metric["error_status"] = error.status_code
                raise
            finally:
                metric["elapsed_ms"] = milliseconds(started)
                REQUEST_METRIC.reset(token)

        async def measured_execute(call_id, book_id, name, arguments):
            started = time.monotonic()
            metric = {"name": name, "status": "started"}
            self.tools.append(metric)
            try:
                if self.case.scenario == "tool_failure":
                    result = service.answer.loop.tools._error(
                        call_id, name, "unavailable", "评测注入的查询暂时不可用"
                    )
                else:
                    result = await execute(call_id, book_id, name, arguments)
                metric["status"] = result.status
                if result.status == "complete":
                    oracle(result, arguments, self.records)
                    if self.first_tool_ms is None:
                        self.first_tool_ms = milliseconds(self.message_started)
                    if self.round_first_tool_ms is None:
                        self.round_first_tool_ms = milliseconds(self.message_started)
                    if getattr(self, "disconnect_after_tool", None) is not None:
                        self.disconnect_after_tool.set()
                        await asyncio.Event().wait()
                return result
            finally:
                metric["elapsed_ms"] = milliseconds(started)

        service.answer.loop.model.post = measured_post
        service.answer.loop.tools.execute = measured_execute

    async def seed_history(self, store, service):
        filters = EntryFilter(date_range=DateRange(start=CURRENT[0], end=CURRENT[1]))
        if self.case.prelude == "tea":
            filters.keywords.include = ["奶茶"]
        await store.create(
            CreateConversationV2(id=self.cid, book_id=self.book, initial_month="2026-10"),
            context=ChatContextV2(filters=filters) if self.case.prelude else None,
        )
        if self.case.prelude:
            draft = await store.revise(self.cid, ContextPatch())
            search_filter = filters.model_copy(deep=True)
            if self.case.prelude == "headphones":
                search_filter.keywords.include = ["耳机"]
            name = "search_entries" if self.case.prelude == "headphones" else "summarize_entries"
            # 准备真实 SQL 生成的已保存历史快照，当前待测自然语言仍走真实模型。
            result = await service.answer.loop.tools.__class__.execute(
                service.answer.loop.tools,
                "seed",
                self.book,
                name,
                {"filters": search_filter.model_dump(mode="json", by_alias=True)},
            )
            require(result.status == "complete", "评测历史准备失败")
            await store.save_turn(
                draft,
                SendMessageV2(
                    id=uuid4(),
                    text="查看耳机账单" if name == "search_entries" else "最近七天奶茶支出",
                ),
                AnswerPreviewV2(
                    answer="准备的历史统计快照。",
                    completeness="complete",
                    cards=result.cards,
                    fact_refs=result.fact_refs,
                ),
            )

    async def send(self, service, text, *, message=None):
        body = message or SendMessageV2(id=uuid4(), text=text)
        started = time.monotonic()
        self.message_started = started
        self.round_first_tool_ms = None
        first_snapshot = None
        request_start, tool_start = len(self.requests), len(self.tools)
        final = None
        async with aclosing(service.events(self.cid, body)) as stream:
            async for event in stream:
                if event["event"] == "snapshot" and self.first_snapshot_ms is None:
                    self.first_snapshot_ms = milliseconds(started)
                if event["event"] == "snapshot" and first_snapshot is None:
                    first_snapshot = milliseconds(started)
                if event["event"] == "error":
                    raise ServiceError(event["data"]["status"], event["data"]["detail"])
                if event["event"] == "done":
                    from app.schemas.chat_v2 import TurnResponseV2

                    final = TurnResponseV2.model_validate(event["data"])
                    self.last_missing = final.missing
        require(final is not None, "SSE 结束时没有已保存回答")
        self.rounds.append(
            {
                "elapsed_ms": milliseconds(started),
                "model_requests": len(self.requests) - request_start,
                "tool_calls": len(self.tools) - tool_start,
                "completeness": final.completeness,
                "first_tool_ms": self.round_first_tool_ms,
                "first_snapshot_ms": first_snapshot,
                "first_result_ms": first_snapshot
                if first_snapshot is not None
                else milliseconds(started),
            }
        )
        return final, body

    async def exercise(self, session):
        service = ChatServiceV2(
            LedgerService(session, self.owner),
            LlmClient(self.settings, self.model_client),
            self.redis,
            self.settings,
            today=TODAY,
        )
        self.instrument(service)
        await self.seed_history(service.store, service)
        if self.case.scenario == "legacy":
            legacy = uuid4()
            self.cids.append(legacy)
            session.add(
                ChatConversation(
                    id=legacy,
                    owner_id=self.owner,
                    book_id=self.book,
                    month="2026-09",
                    title="旧版餐饮会话",
                    context={"month": "2026-09", "entryType": "expense", "categoryId": "food"},
                    version=1,
                )
            )
            session.add(
                ChatTurn(
                    id=uuid4(),
                    conversation_id=legacy,
                    sequence=1,
                    text="九月餐饮支出",
                    answer="旧版已保存回答。",
                    context={"month": "2026-09", "entryType": "expense", "categoryId": "food"},
                )
            )
            await session.commit()
            old = await service.store.read(legacy, legacy=True)
            require(old.source_schema_version == 1, "旧会话未按只读适配读取")
            require(
                old.readonly and len(old.turns) == 1 and old.turns[0].answer == "旧版已保存回答。",
                "旧版回答读取不完整",
            )
            self.cid = uuid4()
            self.cids.append(self.cid)
            await service.fork_legacy(
                legacy,
                CreateConversationV2(id=self.cid, book_id=self.book, initial_month="2026-09"),
            )
        if self.case.scenario == "permission":
            await session.execute(
                update(BookMember)
                .where(BookMember.book_id == self.book, BookMember.user_id == self.owner)
                .values(status="removed")
            )
            await session.commit()
        retry_body = None
        if self.case.scenario == "replay":
            self.disconnect_after_tool = asyncio.Event()
            retry_body = SendMessageV2(id=uuid4(), text=self.case.phrases[self.variant])
            consuming = asyncio.create_task(self.send(service, retry_body.text, message=retry_body))
            observed = asyncio.create_task(self.disconnect_after_tool.wait())
            try:
                done, _ = await asyncio.wait(
                    {consuming, observed},
                    timeout=self.settings.chat_timeout_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if observed not in done:
                    if consuming in done:
                        await consuming
                    raise TimeoutError("断连前未取得工具结果")
            finally:
                consuming.cancel()
                observed.cancel()
                await asyncio.gather(consuming, observed, return_exceptions=True)
                self.disconnect_after_tool = None
            require(
                await self.redis.get(f"{self.prefix}assistant:chat-lock:{self.cid}") is None,
                "断连后会话锁未释放",
            )
            require((await service.store.load(self.cid)).version == 0, "断连后写入了未确认轮次")
        try:
            turn, body = await self.send(
                service, self.case.phrases[self.variant], message=retry_body
            )
        except ServiceError as error:
            if self.case.scenario == "permission":
                require(
                    error.status_code == 404 and not self.requests, "失权后仍启动了模型或返回了统计"
                )
                return
            if self.case.scenario == "tool_failure":
                require(
                    bool(self.tools) and error.status_code in {502, 503}, "工具失败被伪装成零消费"
                )
                state = await service.store.load(self.cid)
                require(state.version == 0, "工具失败后保存了成功历史")
                return
            raise
        require(self.case.scenario not in {"permission", "tool_failure"}, "预期错误未发生")
        fixture = {"member": self.member, "ids": self.ids}
        validate_turn(turn, self.case, self.variant, fixture, [tool["name"] for tool in self.tools])
        # 相同 ID 的 done 丢失后重试；已提交回答不得再调用模型或重复保存。
        requests_before = len(self.requests)
        replay, _ = await self.send(service, body.text, message=body)
        require(replay == turn and len(self.requests) == requests_before, "原消息重试不是幂等重放")
        if self.case.scenario == "legacy":
            restored = await service.store.read(legacy, legacy=True)
            require(restored == old, "续聊覆盖了原旧版历史")
        if self.case.scenario == "multi":
            for text, total, count, words in (
                ("咖啡也算上", 9500, 5, ("奶茶", "咖啡")),
                ("备注里包含退款的不要算", 8500, 4, ("奶茶", "咖啡")),
                ("清空备注关键词，仍然统计最近七天支出", 40000, 8, ()),
                ("只看餐饮分类", 12000, 3, ()),
                ("和之前七天相比呢？", 12000, 3, ()),
            ):
                from dataclasses import replace

                following, _ = await self.send(service, text)
                expected = replace(
                    self.case,
                    total=total,
                    count=count,
                    keywords=words,
                    previous=("2026-10-14", "2026-10-20") if text.startswith("和之前") else None,
                )
                validate_turn(
                    following,
                    expected,
                    self.variant,
                    fixture,
                    [tool["name"] for tool in self.tools],
                )
            history = await service.store.read(self.cid)
            require(
                len(history.turns) == 6
                and [turn.sequence for turn in history.turns] == list(range(1, 7)),
                "六轮历史恢复或幂等有误",
            )

    async def run(self):
        result = {
            "scenario": self.case.name,
            "variant": self.variant + 1,
            "question": self.case.phrases[self.variant],
            "status": "failed",
            "cleanup": False,
        }
        try:
            async with self.engine.connect() as connection:
                outer = await connection.begin()
                try:
                    await self.seed(connection)
                    async with AsyncSession(
                        bind=connection,
                        expire_on_commit=False,
                        join_transaction_mode="create_savepoint",
                    ) as session:
                        await self.exercise(session)
                    result["status"] = "passed"
                except ServiceError as error:
                    result["error"] = {
                        "kind": "ServiceError",
                        "status": error.status_code,
                        "detail": error.detail,
                    }
                except AssertionError as error:
                    result["error"] = {"kind": "AssertionError", "detail": str(error)}
                except Exception as error:
                    result["error"] = {
                        "kind": type(error).__name__,
                        "detail": "评测运行失败；不记录连接信息、SQL 参数或模型原文",
                    }
                finally:
                    await outer.rollback()
                for model, condition in (
                    (User, User.id.in_([self.owner, self.member, self.foreign])),
                    (Book, Book.id.in_([self.book, self.other_book])),
                    (LedgerEntry, LedgerEntry.book_id.in_([self.book, self.other_book])),
                    (ChatConversation, ChatConversation.id.in_(self.cids)),
                    (ChatTurn, ChatTurn.conversation_id.in_(self.cids)),
                ):
                    require(
                        await connection.scalar(
                            select(func.count()).select_from(model).where(condition)
                        )
                        == 0,
                        "评测数据库回滚核对失败",
                    )
                result["cleanup"] = True
        except Exception as error:
            result["status"] = "failed"
            result["error"] = {"kind": type(error).__name__, "detail": "开发连接或清理核对失败"}
        finally:
            keys = [
                f"{self.prefix}assistant:chat-rate:{self.owner}",
                *[f"{self.prefix}assistant:chat-lock:{cid}" for cid in self.cids],
            ]
            try:
                await self.redis.delete(*keys)
                require(await self.redis.exists(*keys) == 0, "评测 Redis 清理核对失败")
            except Exception:
                result["status"], result["cleanup"] = "failed", False
                result["error"] = {"kind": "CleanupError", "detail": "评测专用 Redis 键清理失败"}
        result.update(
            elapsed_ms=milliseconds(self.started),
            first_tool_ms=self.first_tool_ms,
            first_snapshot_ms=self.first_snapshot_ms,
            model_requests=len(self.requests),
            tool_calls=len(self.tools),
            requests=self.requests,
            tools=self.tools,
            rounds=self.rounds,
            missing=getattr(self, "last_missing", []),
        )
        return result


async def live(args, cases):
    settings = Settings()
    if settings.app_env not in {"development", "test"} or not all(
        (settings.database_url, settings.redis_url, settings.llm_api_key)
    ):
        raise RuntimeError("真实评测仅允许已配置数据库、Redis 和模型的开发环境")
    settings = settings.model_copy(
        update={
            "chat_v2_enabled": True,
            "chat_v2_tool_mode": args.mode,
            "chat_v2_cursor_secret": SecretStr(secrets.token_urlsafe(32)),
        }
    )
    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        hide_parameters=True,
        # 测试外层事务也有等待上限；断网时不复用长期失活的池连接。
        connect_args={"timeout": 10, "command_timeout": 20},
        pool_pre_ping=True,
        pool_size=args.workers,
    )
    redis = Redis.from_url(
        settings.redis_url.get_secret_value(),
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "today": str(TODAY),
        "environment": settings.app_env,
        "model": settings.llm_model,
        "reasoning_effort": "xhigh",
        "protocol": settings.llm_api_mode,
        "tool_mode": args.mode,
        "timeout_seconds": settings.chat_timeout_seconds,
        "workers": args.workers,
        "planned_trials": len(cases) * args.variants,
        "status": "running",
        "fixture_sha256": hashlib.sha256(
            json.dumps(RECORDS, ensure_ascii=False).encode()
        ).hexdigest(),
        "real_model": True,
        "real_database": True,
        "real_redis": True,
        "results": [],
    }
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(args.workers)

    def save():
        report["passed"] = sum(row["status"] == "passed" for row in report["results"])
        report["failed"] = len(report["results"]) - report["passed"]
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    async def evaluate(case, variant, client):
        async with semaphore:
            print(f"开始 {case.name}/{variant + 1}", flush=True)
            row = await Trial(settings, engine, redis, client, case, variant).run()
            report["results"].append(row)
            save()
            print(
                f"{row['status']} {case.name}/{variant + 1} {row['elapsed_ms']}ms "
                f"模型={row['model_requests']} 工具={row['tool_calls']} "
                f"{row.get('error', {}).get('detail', '')}",
                flush=True,
            )

    try:
        await redis.ping()
        async with httpx.AsyncClient(
            trust_env=False, event_hooks={"response": [observe_response]}
        ) as client:
            await asyncio.gather(
                *(
                    evaluate(case, variant, client)
                    for case in cases
                    for variant in range(args.variants)
                )
            )
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        report["status"] = (
            "passed"
            if len(report["results"]) == report["planned_trials"]
            and all(row["status"] == "passed" for row in report["results"])
            else "incomplete_or_failed"
        )
        save()
        await redis.aclose()
        await engine.dispose()
    return 1 if report["failed"] else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="显式允许开发服务、真实模型及可回滚测试数据"
    )
    parser.add_argument("--cases", nargs="*", default=[])
    parser.add_argument("--variants", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--workers", type=int, choices=(1, 2, 3), default=2)
    parser.add_argument("--mode", choices=("native", "structured"), default="native")
    parser.add_argument("--output", default="../output/chat-v2-evaluation/report.json")
    args = parser.parse_args()
    cases = selected_cases(args.cases)
    if not args.live:
        print(
            json.dumps(
                {
                    "real_model": False,
                    "status": "not_run",
                    "cases": [asdict(case) for case in cases],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    return asyncio.run(live(args, cases))


if __name__ == "__main__":
    raise SystemExit(main())
