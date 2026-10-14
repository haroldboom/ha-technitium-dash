ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.20
FROM ${BUILD_FROM}

RUN apk add --no-cache python3

ARG BUILD_VERSION=dev
ENV BUILD_VERSION=${BUILD_VERSION} PYTHONUNBUFFERED=1

COPY app/ /app/
CMD ["python3", "/app/server.py"]
