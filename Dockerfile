FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system app \
    && useradd \
        --system \
        --gid app \
        --home-dir /app \
        --no-create-home \
        app

COPY requirements.txt .

RUN pip install \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    -r requirements.txt

COPY --chown=app:app . .

USER app

EXPOSE 8000

CMD ["gunicorn", "--config", "gunicorn.conf.py", "--bind", "0.0.0.0:8000", "app:app"]