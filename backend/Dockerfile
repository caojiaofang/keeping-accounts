FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --disable-pip-version-check -r requirements.txt \
    && groupadd --gid 10001 moji \
    && useradd --uid 10001 --gid moji --no-create-home moji

COPY --chown=moji:moji app ./app
COPY --chown=moji:moji alembic ./alembic
COPY --chown=moji:moji alembic.ini check_connections.py admin_password.py ./
USER moji

EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
