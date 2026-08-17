# syntax=docker/dockerfile:1
ARG XRAY_VERSION=26.3.27
FROM ghcr.io/xtls/xray-core:${XRAY_VERSION} AS xray

FROM python:3.12-alpine3.22
ARG XRAY_VERSION
ARG RELEASE_ID=fixed-4-node-physical-isolation-v4
ENV XRAY_VERSION=${XRAY_VERSION} RELEASE_ID=${RELEASE_ID} PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apk add --no-cache openssl && mkdir -p /etc/xray /data /opt/xray/scripts /opt/xray/config /opt/xray/site
COPY --from=xray /usr/local/bin/xray /usr/local/bin/xray
COPY scripts/ /opt/xray/scripts/
COPY config/ /opt/xray/config/
COPY site/ /opt/xray/site/
RUN chmod 0755 /usr/local/bin/xray /opt/xray/scripts/*.sh /opt/xray/scripts/*.py && chmod 0644 /opt/xray/config/* /opt/xray/site/*
ENV BUILD_ID=fixed-4-node-physical-isolation-v4 \
    PORT=8080 \
    GATEWAY_PORTS=8080,8081,8082,8083 \
    XRAY_HTTP_PORT=10086 \
    XRAY_REALITY_PORT=10087 \
    XRAY_CONFIG=/etc/xray/config.json \
    DATA_DIR=/data \
    REALITY_TARGET=www.cloudflare.com:443 \
    REALITY_FINGERPRINT=chrome \
    XHTTP_PATH=/xhttp \
    GRPC_SERVICE_NAME=grpc-service \
    WS_PATH=/ws \
    READY_TIMEOUT=90
EXPOSE 8080 8081 8082 8083
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/ready', timeout=3).read()"
WORKDIR /opt/xray
ENTRYPOINT ["/opt/xray/scripts/start.sh"]
