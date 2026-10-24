"""可安全返回前端的业务错误，不包含上游原始响应或连接凭据。"""


class ServiceError(Exception):
    def __init__(self, status_code: int, detail: str, headers: dict[str, str] | None = None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.headers = headers or {}
