ARG CADDY_IMAGE=caddy:2.10-alpine@sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d
FROM ${CADDY_IMAGE}

ARG ENMOTION_EDGE_UID=10002
ARG ENMOTION_EDGE_GID=10002
ARG ENMOTION_VERSION=dev
ARG ENMOTION_SOURCE_REVISION=unknown
ARG ENMOTION_SOURCE_STATE=unknown
ARG ENMOTION_SOURCE_TREE_IDENTITY=unknown

LABEL org.opencontainers.image.title="EnMotion web edge" \
      org.opencontainers.image.version="${ENMOTION_VERSION}" \
      org.opencontainers.image.revision="${ENMOTION_SOURCE_REVISION}" \
      io.enmotion.source.state="${ENMOTION_SOURCE_STATE}" \
      io.enmotion.source.tree="${ENMOTION_SOURCE_TREE_IDENTITY}" \
      io.enmotion.image.role="edge"

# The upstream binary carries cap_net_bind_service for ports below 1024.
# EnMotion uses 8080/8443 and drops every capability, so retaining that file
# capability makes Linux reject execve with EPERM under no-new-privileges.
RUN addgroup -S -g "$ENMOTION_EDGE_GID" enmotionedge \
    && adduser -S -D -H -u "$ENMOTION_EDGE_UID" -G enmotionedge enmotionedge \
    && mkdir -p /data /config \
    && chown -R enmotionedge:enmotionedge /data /config \
    && setcap -r /usr/bin/caddy

COPY Caddyfile /etc/caddy/Caddyfile

USER enmotionedge:enmotionedge

EXPOSE 8080 8443 8443/udp

CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
