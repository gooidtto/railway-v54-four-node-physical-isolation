# syntax=docker/dockerfile:1
ARG XRAY_VERSION=26.3.27
ARG CLOUDFLARED_VERSION=2026.7.3
FROM ghcr.io/xtls/xray-core:${XRAY_VERSION} AS xray
FROM cloudflare/cloudflared:${CLOUDFLARED_VERSION} AS cloudflared

FROM python:3.12-alpine3.22
ARG XRAY_VERSION
ARG CLOUDFLARED_VERSION
ENV XRAY_VERSION=${XRAY_VERSION} CLOUDFLARED_VERSION=${CLOUDFLARED_VERSION} PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apk add --no-cache openssl ca-certificates && mkdir -p /etc/xray /data /opt/xray/scripts /opt/xray/config /opt/xray/site
COPY --from=xray /usr/local/bin/xray /usr/local/bin/xray
COPY --from=cloudflared /usr/local/bin/cloudflared /usr/local/bin/cloudflared
COPY scripts/ /opt/xray/scripts/
COPY config/ /opt/xray/config/
COPY site/ /opt/xray/site/
RUN chmod 0755 /usr/local/bin/xray /usr/local/bin/cloudflared /opt/xray/scripts/*.sh /opt/xray/scripts/*.py && chmod 0644 /opt/xray/config/* /opt/xray/site/*
ENV BUILD_ID=stable-optional-cloudflare-ws \
    PORT=8080 \
    GATEWAY_PORT=8080 \
    XRAY_CONFIG=/etc/xray/config.json \
    DATA_DIR=/data \
    REALITY_RAW_SNI=www.cloudflare.com \
    REALITY_RAW_TARGET=www.cloudflare.com:443 \
    REALITY_FINGERPRINT=chrome \
    REALITY_XHTTP_SNI=www.apple.com \
    REALITY_XHTTP_TARGET=www.apple.com:443 \
    XHTTP_PATH=/xhttp \
    READY_TIMEOUT=90 \
    CLOUDFLARE_READY_TIMEOUT=45
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=5 CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/ready', timeout=3).read()"
WORKDIR /opt/xray
ENTRYPOINT ["/opt/xray/scripts/start.sh"]
