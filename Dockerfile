FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f

LABEL org.opencontainers.image.title="mergeweasel" \
      org.opencontainers.image.description="A merge queue for Gitea: lands pull requests one at a time, only once they are tested, and releases them." \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/chli1/mergeweasel"

COPY . /tmp/mergeweasel
RUN pip install --no-cache-dir /tmp/mergeweasel && rm -rf /tmp/mergeweasel \
    && useradd --system --uid 10001 --no-create-home mergeweasel

USER 10001
CMD ["mergeweasel", "--help"]
