"""绑定查询范围的短期分页签名；不复用邀请或模型密钥。"""

import base64
import binascii
import hashlib
import hmac
import json
import re
from datetime import date
from uuid import UUID

from pydantic import SecretStr

from app.core.errors import ServiceError
from app.schemas.ledger import MAX_CENTS


class CursorCodec:
    ttl_seconds = 1800

    def __init__(self, secret: SecretStr | None):
        self.key = secret.get_secret_value().encode() if secret else None

    def _require_key(self):
        if self.key is None:
            raise ServiceError(503, "尚未配置分页服务，请稍后重试")

    @staticmethod
    def scope(user_id, book_id, filters, sort, limit):
        values = filters.model_dump(mode="json", by_alias=True)
        for field in ("categoryIds", "accounts", "creatorIds", "excludedEntryIds", "weekdays"):
            values[field] = sorted(values[field])
        for field in ("include", "exclude"):
            values["keywords"][field] = sorted(word.lower() for word in values["keywords"][field])
        text = json.dumps(
            [str(user_id), str(book_id), values, sort, limit],
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(text.encode()).hexdigest()

    def encode(self, scope, sort, row, now_ms):
        self._require_key()
        position = (
            [row["date"].isoformat(), row["time"] or "", str(row["id"])]
            if sort == "date_desc"
            else [int(row["amount"]), row["date"].isoformat(), str(row["id"])]
        )
        raw = json.dumps(
            {"v": 1, "scope": scope, "position": position, "issued_at": now_ms // 1000},
            separators=(",", ":"),
        ).encode()
        signature = hmac.digest(self.key, raw, "sha256")
        return base64.urlsafe_b64encode(raw + signature).rstrip(b"=").decode()

    def decode(self, token, scope, sort, now_ms):
        self._require_key()
        try:
            if len(token) > 2048 or not re.fullmatch(r"[A-Za-z0-9_-]+", token):
                raise ValueError("分页格式错误")
            decoded = base64.b64decode(
                token + "=" * (-len(token) % 4), altchars=b"-_", validate=True
            )
            raw, signature = decoded[:-32], decoded[-32:]
            if not hmac.compare_digest(hmac.digest(self.key, raw, "sha256"), signature):
                raise ValueError("分页签名错误")
            payload = json.loads(raw)
            if set(payload) != {"v", "scope", "position", "issued_at"}:
                raise ValueError("分页字段错误")
            if type(payload["v"]) is not int or payload["v"] != 1 or payload["scope"] != scope:
                raise ValueError("查询范围变化")
            issued_at = payload["issued_at"]
            if (
                type(issued_at) is not int
                or not -30 <= now_ms // 1000 - issued_at <= self.ttl_seconds
            ):
                raise ValueError("分页已过期")
            position = payload["position"]
            if not isinstance(position, list) or len(position) != 3:
                raise ValueError("分页位置错误")
            entry_id = UUID(position[2])
            if sort == "date_desc":
                day = self._day(position[0])
                if position[1] != "" and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", position[1]):
                    raise ValueError("分页时刻错误")
                return day, position[1], entry_id
            if type(position[0]) is not int or not 0 < position[0] <= MAX_CENTS:
                raise ValueError("分页金额错误")
            return position[0], self._day(position[1]), entry_id
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, binascii.Error):
            raise ServiceError(422, "分页信息无效或已过期，请从第一页重新查询") from None

    @staticmethod
    def _day(value):
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("分页日期格式错误")
        day = date.fromisoformat(value)
        if not 2000 <= day.year <= 2100:
            raise ValueError("分页日期范围错误")
        return day
