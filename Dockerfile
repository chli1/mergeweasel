FROM python:3.14-slim@sha256:0741d101873c12ab927e6f8653feb8862b9bd58771177acb1b885b95141f91b4

LABEL org.opencontainers.image.title="mergeweasel" \
      org.opencontainers.image.description="A merge queue for Gitea: lands pull requests one at a time, only once they are tested, and releases them." \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/chli1/mergeweasel"

COPY . /tmp/mergeweasel
RUN pip install --no-cache-dir /tmp/mergeweasel && rm -rf /tmp/mergeweasel \
    && useradd --system --uid 10001 --no-create-home mergeweasel

USER 10001
CMD ["mergeweasel", "--help"]
