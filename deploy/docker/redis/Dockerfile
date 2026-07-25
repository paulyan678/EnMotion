ARG REDIS_IMAGE=redis:7-alpine@sha256:6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99
FROM ${REDIS_IMAGE}

ARG ENMOTION_VERSION=dev
ARG ENMOTION_SOURCE_REVISION=unknown
ARG ENMOTION_SOURCE_STATE=unknown
ARG ENMOTION_SOURCE_TREE_IDENTITY=unknown

LABEL org.opencontainers.image.title="EnMotion Redis" \
      org.opencontainers.image.version="${ENMOTION_VERSION}" \
      org.opencontainers.image.revision="${ENMOTION_SOURCE_REVISION}" \
      io.enmotion.source.state="${ENMOTION_SOURCE_STATE}" \
      io.enmotion.source.tree="${ENMOTION_SOURCE_TREE_IDENTITY}" \
      io.enmotion.image.role="redis"

RUN chown -R redis:redis /data
USER redis
