FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

LABEL org.opencontainers.image.title="mergeweasel" \
      org.opencontainers.image.description="A merge queue for Gitea: lands pull requests one at a time, only once they are tested, and releases them." \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/chli1/mergeweasel"

COPY . /tmp/mergeweasel
RUN pip install --no-cache-dir /tmp/mergeweasel && rm -rf /tmp/mergeweasel \
    && useradd --system --uid 10001 --no-create-home mergeweasel

USER 10001
CMD ["mergeweasel", "--help"]
