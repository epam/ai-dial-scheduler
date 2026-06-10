FROM --platform=linux/amd64 python:3.14-alpine

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV APP_HOME=/home/app
ENV HOME=/home/app

WORKDIR $APP_HOME

# postgresql-dev stays outside the virtual group: asyncpg links against libpq at runtime.
# Build-only tools (compiler, headers) are removed after uv sync to keep the image slim.
RUN apk add --no-cache postgresql-dev \
    && apk add --no-cache --virtual .build-deps alpine-sdk linux-headers gcc python3-dev musl-dev

COPY pyproject.toml uv.lock ./
RUN pip install uv==0.9.28 \
    && uv sync --frozen --no-dev \
    && apk del .build-deps

COPY src/ ./src
COPY alembic.ini ./
COPY migrations/ ./migrations

COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh
COPY entrypoint_api.py ./
COPY entrypoint_worker.py ./

# 1001 uid also used in HELM chart
RUN adduser -u 1001 --system --disabled-password --gecos "" app && chown -R app $APP_HOME

ENV PATH="$APP_HOME/.venv/bin:$PATH"

USER app

CMD ["sh", "entrypoint.sh"]
