"""模拟微信 HTTP 响应，不调用真实微信接口。"""

import asyncio
import logging

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import ServiceError
from app.integrations.wechat import WeChatClient


def exchange(payload, status_code=200):
    async def run():
        transport = httpx.MockTransport(lambda _: httpx.Response(status_code, json=payload))
        async with httpx.AsyncClient(transport=transport) as http:
            client = WeChatClient(
                Settings(_env_file=None, wechat_app_secret="test-only-app-secret"), http
            )
            return await client.exchange_code("temporary-code")

    return asyncio.run(run())


def test_only_trusted_openid_is_returned_and_secrets_are_not_logged(caplog):
    caplog.set_level(logging.DEBUG)

    async def run():
        def respond(request):
            assert request.url.host == "api.weixin.qq.com"
            assert request.url.path == "/sns/jscode2session"
            assert request.url.params["appid"] == "wx0f90babab478b2b6"
            assert request.url.params["secret"] == "test-only-app-secret"
            assert request.url.params["js_code"] == "temporary-code"
            assert request.url.params["grant_type"] == "authorization_code"
            return httpx.Response(
                200, json={"openid": "trusted-openid", "session_key": "private-session-key"}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            client = WeChatClient(
                Settings(_env_file=None, wechat_app_secret="test-only-app-secret"), http
            )
            return await client.exchange_code("temporary-code")

    identity = asyncio.run(run())
    assert vars(identity) == {"openid": "trusted-openid"}
    for secret in ("test-only-app-secret", "temporary-code", "private-session-key"):
        assert secret not in caplog.text


@pytest.mark.parametrize(
    ("code", "status"),
    [(40029, 401), (40163, 401), (40226, 403), (45011, 429), (40125, 503), (-1, 503)],
)
def test_wechat_errors_are_sanitized(code, status):
    with pytest.raises(ServiceError) as error:
        exchange({"errcode": code, "errmsg": "upstream-private-data"})
    assert error.value.status_code == status
    assert "upstream-private-data" not in error.value.detail


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"openid": "trusted-openid"},
        {"openid": " ", "session_key": "key"},
        {"errcode": True},
    ],
)
def test_malformed_success_is_rejected(payload):
    with pytest.raises(ServiceError) as error:
        exchange(payload)
    assert error.value.status_code == 502


def test_non_success_http_status_is_sanitized():
    with pytest.raises(ServiceError) as error:
        exchange({"private": "upstream-private-data"}, status_code=500)
    assert error.value.status_code == 502
    assert "test-only-app-secret" not in str(error.value)


def test_timeout_does_not_retry_a_one_time_code():
    calls = []

    async def run():
        def respond(request):
            calls.append(request)
            raise httpx.ReadTimeout("private-url-and-secret", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            await WeChatClient(
                Settings(_env_file=None, wechat_app_secret="test-only-app-secret"), http
            ).exchange_code("temporary-code")

    with pytest.raises(ServiceError) as error:
        asyncio.run(run())
    assert error.value.status_code == 504
    assert len(calls) == 1
    assert "private-url-and-secret" not in str(error.value)
