FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170

LABEL org.opencontainers.image.title="mergeweasel" \
      org.opencontainers.image.description="A merge queue for Gitea: lands pull requests one at a time, only once they are tested, and releases them." \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/chli1/mergeweasel"

COPY . /tmp/mergeweasel
RUN pip install --no-cache-dir /tmp/mergeweasel && rm -rf /tmp/mergeweasel \
    && useradd --system --uid 10001 --no-create-home mergeweasel

USER 10001
CMD ["mergeweasel", "--help"]
