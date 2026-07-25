ARG NODE_IMAGE=node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293
ARG NGINX_IMAGE=nginxinc/nginx-unprivileged:1.27-alpine@sha256:65e3e85dbaed8ba248841d9d58a899b6197106c23cb0ff1a132b7bfe0547e4c0
FROM ${NODE_IMAGE} AS builder

WORKDIR /app

ARG NEXT_PUBLIC_SERVER_MODE=true

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./

ENV DOCKER_BUILD=true
ENV NEXT_PUBLIC_SERVER_MODE=${NEXT_PUBLIC_SERVER_MODE}

RUN npm run lint \
    && npm run test:all \
    && npm run check:colors \
    && ./node_modules/.bin/tsc --noEmit

ENV NODE_ENV=production

RUN npm run build \
    && npm run check:export


FROM ${NGINX_IMAGE} AS runtime

ARG ENMOTION_VERSION=dev
ARG ENMOTION_SOURCE_REVISION=unknown
ARG ENMOTION_SOURCE_STATE=unknown
ARG ENMOTION_SOURCE_TREE_IDENTITY=unknown
ARG ENMOTION_JAVASCRIPT_LOCK_SHA256=unknown

LABEL org.opencontainers.image.title="EnMotion web frontend" \
      org.opencontainers.image.version="${ENMOTION_VERSION}" \
      org.opencontainers.image.revision="${ENMOTION_SOURCE_REVISION}" \
      io.enmotion.source.state="${ENMOTION_SOURCE_STATE}" \
      io.enmotion.source.tree="${ENMOTION_SOURCE_TREE_IDENTITY}" \
      io.enmotion.image.role="frontend" \
      io.enmotion.dependencies.javascript="frontend/package-lock.json (npm ci)" \
      io.enmotion.dependencies.javascript.sha256="${ENMOTION_JAVASCRIPT_LOCK_SHA256}"

COPY --from=builder --chown=101:101 /app/out /usr/share/nginx/html
COPY --chown=101:101 deploy/docker/nginx-frontend.conf /etc/nginx/conf.d/default.conf

USER 101:101

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
