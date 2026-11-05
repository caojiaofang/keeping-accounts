"""在内存 SQLite 实际执行筛选 SQL；不连接用户数据库或模型。"""

import asyncio
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from app.core.config import Settings
from app.core.errors import ServiceError
from app.models.ledger import Book, LedgerEntry
from app.models.shared import BookMember
from app.models.user import User
from app.schemas.chat_v2 import CompareArgs, DateRange, EntryFilter
from app.services.analysis_v2 import FilterQueriesV2
from app.services.ledger import LedgerService


class AsyncReader:
    """复用真实 LedgerService 和 ORM 查询，仅适配内存同步 Session 的调用方式。"""

    def __init__(self, session):
        self.session = session

    async def scalar(self, statement):
        return self.session.scalar(statement)

    async def scalars(self, statement):
        return self.session.scalars(statement)

    async def execute(self, statement):
        return self.session.execute(statement)

    async def rollback(self):
        self.session.rollback()


@pytest.fixture
def data():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    # 仅创建查询需要的四张表，不将 PostgreSQL 的部分索引错误转换成 SQLite 全局唯一索引。
    with engine.begin() as connection:
        for model in (User, Book, BookMember, LedgerEntry):
            connection.execute(CreateTable(model.__table__))
    with Session(engine, expire_on_commit=False) as session:
        users = {key: uuid4() for key in ("owner", "member", "former", "outsider")}
        for key, uid in users.items():
            session.add(User(id=uid, openid="v2-test-" + key, wechat_app_id="wx0f90babab478b2b6"))
        books = {key: uuid4() for key in ("shared", "personal", "other", "foreign")}
        for key, bid in books.items():
            session.add(
                Book(
                    id=bid,
                    name=key,
                    kind="shared" if key == "shared" else "personal",
                    owner_id=users["outsider" if key == "foreign" else "owner"],
                )
            )
        for key, status in (("owner", "active"), ("member", "active"), ("former", "left")):
            session.add(
                BookMember(
                    book_id=books["shared"],
                    user_id=users[key],
                    name=key,
                    role="owner" if key == "owner" else "member",
                    status=status,
                )
            )
        ids = {}

        def add(
            key,
            amount,
            day,
            note,
            *,
            category="coffee",
            creator="owner",
            account="微信",
            book="shared",
            entry_type="expense",
            deleted=False,
        ):
            ids[key] = uuid4()
            session.add(
                LedgerEntry(
                    id=ids[key],
                    book_id=books[book],
                    created_by=users[creator],
                    type=entry_type,
                    category_id=category,
                    amount=amount,
                    date=date.fromisoformat(day),
                    note=note,
                    account=account,
                    is_deleted=deleted,
                )
            )

        add("milk", 1000, "2026-10-26", "奶茶", creator="member")
        add("coffee", 2000, "2026-10-25", "COFFEE 咖啡", account="支付宝")
        add("combo", 3000, "2026-10-24", "奶茶咖啡", category="food", creator="former")
        add("excluded_word", 1500, "2026-10-23", "奶茶退款备注")
        add(
            "literal",
            20000,
            "2026-10-22",
            "折扣100%_到手/券\\票",
            category="shopping",
            account="银行卡",
        )
        add(
            "decoy",
            20001,
            "2026-10-22",
            "折扣100AB到手x券x票",
            category="shopping",
            account="银行卡",
        )
        add(
            "salary",
            50000,
            "2026-10-24",
            "工资",
            category="salary",
            creator="member",
            entry_type="income",
        )
        add("past", 900, "2026-10-20", "奶茶", creator="member")
        add("future", 999, "2026-10-28", "奶茶", creator="member")
        add("deleted", 1000, "2026-10-24", "奶茶", deleted=True)
        add("personal", 100, "2026-10-26", "奶茶", book="personal")
        add("other", 8888, "2026-10-26", "奶茶", book="other")
        add("foreign", 9999, "2026-10-26", "奶茶", book="foreign", creator="outsider")
        add("previous", 700, "2026-10-14", "奶茶", creator="member")
        session.commit()
        ledger = LedgerService(AsyncReader(session), users["owner"])
        yield SimpleNamespace(
            session=session,
            users=users,
            books=books,
            ids=ids,
            ledger=ledger,
            queries=FilterQueriesV2(ledger, Settings(_env_file=None), today=date(2026, 10, 27)),
        )
    engine.dispose()


def prepare(data, book="shared", **changes):
    filters = EntryFilter.model_validate(
        {
            "dateRange": {"start": "2026-10-21", "end": "2026-10-27"},
            **changes,
        }
    )
    return asyncio.run(data.queries.prepare(data.books[book], filters))


def matched(data, **changes):
    prepared = prepare(data, **changes)
    found = {row.id for row in data.session.scalars(prepared.entries_statement())}
    return {key for key, value in data.ids.items() if value in found}


@pytest.mark.parametrize(
    "changes,expected",
    [
        (
            {"keywords": {"include": ["奶茶", "咖啡"], "mode": "any"}},
            {"milk", "coffee", "combo", "excluded_word"},
        ),
        ({"keywords": {"include": ["奶茶", "咖啡"], "mode": "all"}}, {"combo"}),
        ({"keywords": {"include": ["奶茶"], "exclude": ["退款"]}}, {"milk", "combo"}),
        ({"keywords": {"include": ["coffee"]}}, {"coffee"}),
        ({"keywords": {"include": ["不存在的备注"]}}, set()),
        (
            {"categoryIds": ["coffee", "food"], "amountMin": 1000, "amountMax": 2000},
            {"milk", "coffee", "excluded_word"},
        ),
        ({"accounts": ["微信", "支付宝"], "keywords": {"include": ["咖啡"]}}, {"coffee", "combo"}),
        ({"accounts": ["银行卡"], "amountMin": 20000, "minInclusive": False}, {"decoy"}),
        ({"amountMin": 20000, "amountMax": 20000}, {"literal"}),
        (
            {"amountMin": 20000, "amountMax": 20001, "minInclusive": False, "maxInclusive": False},
            set(),
        ),
        ({"weekdays": [6, 7]}, {"coffee", "combo"}),
        ({"entryType": "income", "categoryIds": ["salary"]}, {"salary"}),
        (
            {"entryType": "both", "categoryIds": ["coffee", "salary"], "weekdays": [6, 7]},
            {"coffee", "salary"},
        ),
    ],
)
def test_actual_sql_filters(data, changes, expected):
    assert matched(data, **changes) == expected


@pytest.mark.parametrize("keyword", ["%", "_", "/", "\\", "%_", "/券\\"])
def test_keyword_wildcards_and_escape_characters_are_literal(data, keyword):
    assert matched(data, keywords={"include": [keyword]}) == {"literal"}
    assert "literal" not in matched(data, keywords={"exclude": [keyword]})


def test_sql_injection_stays_bound_and_does_not_expand_matching(data):
    injection = "' OR 1=1 --"
    prepared = prepare(data, keywords={"include": [injection]})
    compiled = prepared.entries_statement().compile(dialect=postgresql.dialect())
    assert injection not in str(compiled)
    assert injection in compiled.params.values()
    assert not list(data.session.scalars(prepared.entries_statement()))


def test_combined_scope_exclusion_and_integer_aggregation(data):
    assert matched(
        data,
        categoryIds=["coffee", "food"],
        accounts=["微信"],
        creatorIds=[data.users["member"], data.users["former"]],
        excludedEntryIds=[data.ids["combo"]],
        keywords={"include": ["奶茶"]},
        amountMin=1000,
    ) == {"milk"}
    prepared = prepare(data, categoryIds=["coffee", "food"])
    total, count = data.session.execute(
        select(func.sum(LedgerEntry.amount), func.count())
        .join(Book, Book.id == LedgerEntry.book_id)
        .where(*prepared.conditions)
    ).one()
    assert (total, count) == (7500, 4) and isinstance(total, int)
    assert matched(data, creatorIds=[data.users["former"]]) == {"combo"}
    assert matched(data, book="personal", creatorIds=[data.users["owner"]]) == {"personal"}


def test_future_records_are_excluded_and_actual_range_is_returned(data):
    prepared = prepare(data, dateRange={"start": "2026-10-21", "end": "2026-10-31"})
    assert prepared.filters.date_range.end == date(2026, 10, 27)
    assert prepared.notices
    assert data.ids["future"] not in {
        row.id for row in data.session.scalars(prepared.entries_statement())
    }


def test_configured_range_limit_is_enforced_before_query_execution(data):
    data.queries = FilterQueriesV2(
        data.ledger,
        Settings(_env_file=None, chat_v2_max_range_days=3),
        today=date(2026, 10, 27),
    )
    with pytest.raises(ServiceError, match="最多查询 3 天"):
        prepare(data)
    assert matched(data, dateRange={"start": "2026-10-25", "end": "2026-10-27"}) == {
        "milk",
        "coffee",
    }


def test_compare_reuses_every_filter_and_only_changes_dates(data):
    filters = EntryFilter.model_validate(
        {
            "dateRange": {"start": "2026-10-21", "end": "2026-10-27"},
            "creatorIds": [data.users["member"]],
            "keywords": {"include": ["奶茶"]},
        }
    )
    current, previous = asyncio.run(
        data.queries.prepare_compare(
            data.books["shared"],
            CompareArgs(
                filters=filters, previous_range=DateRange(start="2026-10-14", end="2026-10-20")
            ),
        )
    )
    assert current.filters.model_dump(exclude={"date_range"}) == previous.filters.model_dump(
        exclude={"date_range"}
    )
    assert {row.id for row in data.session.scalars(current.entries_statement())} == {
        data.ids["milk"]
    }
    assert {row.id for row in data.session.scalars(previous.entries_statement())} == {
        data.ids["past"],
        data.ids["previous"],
    }


@pytest.mark.parametrize("reference", ["foreign", "other", "deleted", "random"])
def test_inaccessible_and_missing_entry_references_have_the_same_error(data, reference):
    with pytest.raises(ServiceError) as error:
        prepare(data, excludedEntryIds=[data.ids.get(reference, uuid4())])
    assert error.value.status_code == 404
    assert error.value.detail == "账单不存在或无权访问，请重新选择要排除的账单"


@pytest.mark.parametrize("book,creator", [("shared", "outsider"), ("personal", "member")])
def test_inaccessible_creator_is_rejected(data, book, creator):
    with pytest.raises(ServiceError) as error:
        prepare(data, book=book, creatorIds=[data.users[creator]])
    assert error.value.status_code == 404


def test_book_permissions_apply_before_and_after_preparation(data):
    with pytest.raises(ServiceError) as error:
        prepare(data, book="foreign")
    assert error.value.status_code == 404
    ledger = LedgerService(AsyncReader(data.session), data.users["member"])
    data.queries = FilterQueriesV2(ledger, Settings(_env_file=None), today=date(2026, 10, 27))
    prepared = prepare(data)
    assert list(data.session.scalars(prepared.entries_statement()))
    data.session.execute(
        update(BookMember)
        .where(
            BookMember.book_id == data.books["shared"], BookMember.user_id == data.users["member"]
        )
        .values(status="removed")
    )
    assert not list(data.session.scalars(prepared.entries_statement()))
    with pytest.raises(ServiceError):
        prepare(data)
