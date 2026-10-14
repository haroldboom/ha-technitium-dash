ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.20
FROM ${BUILD_FROM}

RUN apk add --no-cache python3

ARG BUILD_VERSION=dev
ENV BUILD_VERSION=${BUILD_VERSION} PYTHONUNBUFFERED=1

COPY app/ /app/
# s6 starts CMD with an empty environment; with-contenv restores SUPERVISOR_TOKEN and BUILD_VERSION
CMD ["/usr/bin/with-contenv", "python3", "/app/server.py"]
