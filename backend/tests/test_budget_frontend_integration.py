"""前端真实 API/Pinia 经本机 HTTP 连接隔离 PostgreSQL；仅替换微信令牌解析。"""

import asyncio
import json
import os
import shutil
import socket

import pytest
import uvicorn
from test_budget_plan_integration import local_api, run_case

from app.core.config import BACKEND_DIR

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("MOJI_RUN_INTEGRATION_TESTS") != "1", reason="需显式启用本机前后端联调"
    ),
]


def test_frontend_api_and_store_over_http_with_real_postgres(monkeypatch):
    run_case(monkeypatch, frontend_case)


async def frontend_case(clock):
    node = shutil.which("node")
    assert node, "前后端联调需要 Node.js 和已安装的前端依赖"
    async with local_api() as api:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(128)
            port = listener.getsockname()[1]
            server = uvicorn.Server(
                uvicorn.Config(api.app, host="127.0.0.1", port=port, log_level="error")
            )
            task = asyncio.create_task(server.serve(sockets=[listener]))
            process = None
            try:
                async with asyncio.timeout(15):
                    while not server.started:
                        if task.done():
                            await task
                            raise AssertionError("本机联调服务未启动")
                        await asyncio.sleep(0.02)
                process = await asyncio.create_subprocess_exec(
                    node,
                    "scripts/check-budget-integration.mjs",
                    cwd=BACKEND_DIR.parent / "frontend",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                config = {
                    "baseUrl": f"http://127.0.0.1:{port}",
                    "books": [str(book) for book in api.books],
                    "users": [str(user) for user in api.users],
                    "plan": api.plan,
                }
                output, _ = await asyncio.wait_for(
                    process.communicate(json.dumps(config).encode()), timeout=90
                )
                result = output.decode("utf-8", errors="replace")
                print(result)
                assert process.returncode == 0, result[-6000:]
                assert "真实 HTTP / PostgreSQL 联调：9 组通过" in result
            finally:
                if process is not None and process.returncode is None:
                    process.kill()
                    await process.wait()
                server.should_exit = True
                await asyncio.wait_for(task, timeout=15)
