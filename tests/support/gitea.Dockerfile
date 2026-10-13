# The Gitea the tests run against: the version mergeweasel is tested with, and the canary
# for Gitea upgrades - a bump that changes behaviour the queue relies on fails the test first.
# Built without a context; everything else is Gitea's default, ADD_CO_COMMITTER_TRAILERS included.
FROM gitea/gitea:28.1.0@sha256:f505a0d3a41323b786e05942fc7a4075e07ae7c169fd9485649aa55ee543a21a

# Jobs reach it as http://gitea:3000, the name the runner maps to its address.
ENV GITEA__security__INSTALL_LOCK=true \
    GITEA__service__DISABLE_REGISTRATION=true \
    GITEA__actions__ENABLED=true \
    GITEA__server__ROOT_URL=http://gitea:3000/
