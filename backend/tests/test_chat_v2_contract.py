"""固定 V2 边界、可执行示例和旧接口隔离，不访问数据库或模型。"""

import copy
import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from app.core.config import Settings
from app.main import create_app
from app.schemas.chat_v2 import (
    ChatStreamEventV2,
    CompareArgs,
    EntryFilter,
    FactRef,
    SearchArgs,
    SendMessageV2,
    ToolDecision,
    ToolResult,
    TurnResponseV2,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "docs/examples/chat-v2-turn.json"


def test_documented_example_and_sse_roundtrip():
    payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    turn = TurnResponseV2.model_validate(payload)
    saved = turn.model_dump(mode="json", by_alias=True)
    assert saved["schemaVersion"] == 2
    assert saved["factRefs"][0]["value"] == 2050
    adapter = TypeAdapter(ChatStreamEventV2)
    for event in (
        {"event": "status", "data": {"text": "正在查询账单…"}},
        {
            "event": "snapshot",
            "data": {
                key: saved[key]
                for key in (
                    "schemaVersion",
                    "answer",
                    "completeness",
                    "missing",
                    "cards",
                    "factRefs",
                    "suggestions",
                )
            },
        },
        {"event": "delta", "data": {"text": "可以继续比较上期。"}},
        {"event": "ping", "data": {}},
        {"event": "done", "data": saved},
        {"event": "error", "data": {"status": 502, "detail": "分析服务暂时不可用"}},
    ):
        assert adapter.validate_json(json.dumps(event)).event == event["event"]


@pytest.mark.parametrize("extra", ["userId", "bookId", "sql", "tool", "factRefs"])
def test_client_cannot_supply_identity_or_tool_execution(extra):
    with pytest.raises(ValidationError):
        SendMessageV2.model_validate({"id": str(uuid4()), "text": "看看收支", extra: "注入"})


def test_filters_and_tools_share_strict_identity_free_arguments():
    filters = {"dateRange": {"start": "2026-10-21", "end": "2026-10-27"}}
    assert SearchArgs(filters=EntryFilter.model_validate(filters)).limit == 20
    for changes in (
        {"dateRange": {"start": "2026-10-28", "end": "2026-10-27"}},
        {"amountMin": 200, "amountMax": 100},
        {"amountMin": 2.5},
        {"amountMin": True},
        {"creatorIds": ["其他用户"]},
        {"weekdays": [0]},
        {"userId": str(uuid4())},
        {"bookId": str(uuid4())},
    ):
        with pytest.raises(ValidationError):
            EntryFilter.model_validate({**filters, **changes})
    with pytest.raises(ValidationError):
        SearchArgs.model_validate({"filters": filters, "limit": 21})
    decision = ToolDecision.model_validate(
        {
            "action": "tool",
            "call": {
                "name": "compare_periods",
                "arguments": {
                    "filters": filters,
                    "previousRange": {"start": "2026-10-14", "end": "2026-10-20"},
                },
            },
        }
    )
    assert isinstance(decision.call.arguments, CompareArgs)
    for value in (
        {"action": "tool"},
        {"action": "answer", "text": " "},
        {"action": "tool", "call": {"name": "delete_entries", "arguments": {}}},
        {**decision.model_dump(), "text": "同时回答"},
        {**decision.model_dump(), "action": "answer"},
    ):
        with pytest.raises(ValidationError):
            ToolDecision.model_validate(value)


@pytest.mark.parametrize(
    "value,unit",
    [
        (1.5, "cents"),
        (True, "count"),
        ("2050", "cents"),
        (-1, "count"),
        (42, "text"),
        ("错误日期", "date"),
    ],
)
def test_facts_do_not_accept_float_or_ambiguous_units(value, unit):
    with pytest.raises(ValidationError):
        FactRef(id="q1.total", value=value, unit=unit, label="合计", source_call_id="q1")


@pytest.mark.parametrize(
    "variant",
    [
        "missing_fact",
        "duplicate_fact",
        "duplicate_card",
        "partial_without_missing",
        "complete_with_missing",
    ],
)
def test_turn_rejects_broken_references_and_completeness(variant):
    payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    if variant == "missing_fact":
        payload["cards"][0]["factIds"].append("q1.unknown")
    elif variant == "duplicate_fact":
        payload["factRefs"].append(copy.deepcopy(payload["factRefs"][0]))
    elif variant == "duplicate_card":
        payload["cards"].append(copy.deepcopy(payload["cards"][0]))
    elif variant == "partial_without_missing":
        payload["completeness"] = "partial"
    else:
        payload["missing"] = ["明细尚未查询完成"]
    with pytest.raises(ValidationError):
        TurnResponseV2.model_validate(payload)


def test_tool_failure_cannot_masquerade_as_empty_result():
    error = {"code": "unavailable", "detail": "查询服务暂时不可用"}
    for changes in (
        {"status": "error"},
        {"status": "complete", "error": error},
        {"status": "error", "error": error, "nextCursor": "next"},
    ):
        with pytest.raises(ValidationError):
            ToolResult.model_validate({"callId": "q1", "name": "search_entries", **changes})


def test_v2_routes_are_registered_without_replacing_v1_and_default_to_disabled():
    app = create_app(Settings(_env_file=None))
    paths = set(app.openapi()["paths"])
    assert "/api/v1/assistant/conversations/{conversation_id}/messages/stream" in paths
    assert "/api/v1/assistant/v2/conversations/{conversation_id}/messages/stream" in paths
    assert "/api/v1/assistant/v2/capabilities" in paths
    response = app.openapi()["paths"][
        "/api/v1/assistant/v2/conversations/{conversation_id}/messages/stream"
    ]["post"]["responses"]["200"]
    assert set(response["content"]) == {"text/event-stream"}
    assert Settings(_env_file=None).chat_v2_enabled is False


def test_v2_configuration_defaults_and_bounds():
    settings = Settings(_env_file=None)
    assert settings.chat_v2_tool_mode == "structured"
    assert settings.chat_v2_max_tool_calls == settings.chat_v2_max_model_requests == 6
    for changes in (
        {"chat_v2_tool_mode": "auto"},
        {"chat_v2_max_tool_calls": 0},
        {"chat_v2_max_model_requests": 21},
        {"chat_v2_max_range_days": 367},
    ):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **changes)
