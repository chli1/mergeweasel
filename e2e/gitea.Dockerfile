# The Gitea the end-to-end test runs against: the version mergeweasel is tested with, and the canary
# for Gitea upgrades - a bump that changes behaviour the queue relies on fails the test first.
# Built without a context; everything else is Gitea's default, ADD_CO_COMMITTER_TRAILERS included.
FROM gitea/gitea:28.0.0@sha256:a9dc25679ebdd33356242d2e0667cc220043745e8339922879f187c20329a42e

ENV GITEA__security__INSTALL_LOCK=true \
    GITEA__service__DISABLE_REGISTRATION=true
