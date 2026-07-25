ARG PYTHON_IMAGE=python:3.11-slim@sha256:db3ff2e1800a8581e2c48a27c3995339d47bdf046da21c7627accd3d51053a93
FROM ${PYTHON_IMAGE} AS python-dependencies

ENV VIRTUAL_ENV=/opt/enmotion-venv
RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libopus-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --upgrade "pip==26.1.2"
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install \
        --index-url https://download.pytorch.org/whl/cpu \
        --extra-index-url https://pypi.org/simple \
        "torch==2.13.0+cpu"

COPY requirements-docker.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install -r /tmp/requirements.txt

FROM ${PYTHON_IMAGE} AS runtime

ARG ENMOTION_UID=10001
ARG ENMOTION_GID=10001
ARG ENMOTION_VERSION=dev
ARG ENMOTION_SOURCE_REVISION=unknown
ARG ENMOTION_SOURCE_STATE=unknown
ARG ENMOTION_SOURCE_TREE_IDENTITY=unknown
ARG ENMOTION_PYTHON_REQUIREMENTS_SHA256=unknown

LABEL org.opencontainers.image.title="EnMotion web backend" \
      org.opencontainers.image.version="${ENMOTION_VERSION}" \
      org.opencontainers.image.revision="${ENMOTION_SOURCE_REVISION}" \
      io.enmotion.source.state="${ENMOTION_SOURCE_STATE}" \
      io.enmotion.source.tree="${ENMOTION_SOURCE_TREE_IDENTITY}" \
      io.enmotion.image.role="backend" \
      io.enmotion.dependencies.python="requirements-docker.txt (exact direct pins; transitive resolution occurs at build time)" \
      io.enmotion.dependencies.python.sha256="${ENMOTION_PYTHON_REQUIREMENTS_SHA256}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        fontconfig \
        fonts-wqy-zenhei \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "$ENMOTION_GID" enmotion \
    && useradd --uid "$ENMOTION_UID" --gid "$ENMOTION_GID" \
        --create-home --home-dir /home/enmotion --shell /usr/sbin/nologin enmotion

ENV VIRTUAL_ENV=/opt/enmotion-venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
ENV HOME=/home/enmotion
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV ENMOTION_PACKAGED=true
ENV ENMOTION_DATA_DIR=/data
ENV ENMOTION_LOG_DIR=/data/logs

WORKDIR /app

COPY --from=python-dependencies /opt/enmotion-venv /opt/enmotion-venv
COPY src/ src/
COPY config/model_catalog/generated/model_catalog.json config/model_catalog/generated/model_catalog.json
COPY deploy/bin/container-entrypoint /usr/local/bin/enmotion-entrypoint

RUN chmod 0555 /usr/local/bin/enmotion-entrypoint \
    && mkdir -p \
        /app/output/assets \
        /app/output/audio \
        /app/output/export \
        /app/output/playground \
        /app/output/storyboard \
        /app/output/uploads \
        /app/output/video \
        /app/output/video_inputs \
        /app/output/outputs/videos \
        /home/enmotion/.cache \
        /data/logs \
    && chown -R enmotion:enmotion /app/output /data /home/enmotion

USER enmotion:enmotion

EXPOSE 17177

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/enmotion-entrypoint"]
CMD ["api"]
