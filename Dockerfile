# syntax=docker/dockerfile:1
ARG XRAY_VERSION=26.3.27
FROM ghcr.io/xtls/xray-core:${XRAY_VERSION} AS xray

FROM python:3.12-alpine3.22
ARG XRAY_VERSION
ENV XRAY_VERSION=${XRAY_VERSION} \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN mkdir -p /etc/xray /data /opt/xray/scripts /opt/xray/config /opt/xray/site
COPY --from=xray /usr/local/bin/xray /usr/local/bin/xray
COPY scripts/ /opt/xray/scripts/
COPY config/ /opt/xray/config/
COPY site/ /opt/xray/site/

RUN chmod 0755 /usr/local/bin/xray /opt/xray/scripts/*.sh /opt/xray/scripts/*.py \
    && chmod 0644 /opt/xray/config/* /opt/xray/site/*

ENV BUILD_ID=fixed-8-node-unified \
    PORT=8080 \
    GATEWAY_PORTS=8080,8081 \
    XRAY_HTTP_PORT=10086 \
    XRAY_REALITY_PORT=10087 \
    XRAY_CONFIG=/etc/xray/config.json \
    DATA_DIR=/data \
    REALITY_TARGET=www.cloudflare.com:443 \
    REALITY_FINGERPRINT=chrome \
    XHTTP_PATH=/xhttp \
    XHTTP_MODE=auto \
    REALITY_SNI_CANDIDATES_FILE=/opt/xray/config/reality-sni-candidates.txt \
    READY_TIMEOUT=90

EXPOSE 8080 8081

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/ready', timeout=3).read()"

WORKDIR /opt/xray
ENTRYPOINT ["/opt/xray/scripts/start.sh"]
