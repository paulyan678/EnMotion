ARG POSTGRES_IMAGE=postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777
FROM ${POSTGRES_IMAGE}

ARG ENMOTION_VERSION=dev
ARG ENMOTION_SOURCE_REVISION=unknown
ARG ENMOTION_SOURCE_STATE=unknown
ARG ENMOTION_SOURCE_TREE_IDENTITY=unknown

LABEL org.opencontainers.image.title="EnMotion PostgreSQL" \
      org.opencontainers.image.version="${ENMOTION_VERSION}" \
      org.opencontainers.image.revision="${ENMOTION_SOURCE_REVISION}" \
      io.enmotion.source.state="${ENMOTION_SOURCE_STATE}" \
      io.enmotion.source.tree="${ENMOTION_SOURCE_TREE_IDENTITY}" \
      io.enmotion.image.role="database"

# The upstream image prepares these paths for the postgres account. Selecting
# it explicitly means the database process never starts as root.
USER postgres
