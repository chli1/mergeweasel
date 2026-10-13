# The Forgejo the tests run against, the same way as gitea.Dockerfile: the version mergeweasel is
# tested with, and the canary for Forgejo upgrades. Built without a context; everything else is
# Forgejo's default. The image keeps the `gitea` command and Gitea's data paths.
FROM codeberg.org/forgejo/forgejo:16.0.6@sha256:e2c34a699543ca8a00217ee9ab2bea5feedc98bd6d0ea95ec287d3b7ae1355c6

# Jobs reach it as http://gitea:3000, the name the runner maps to its address.
ENV FORGEJO__security__INSTALL_LOCK=true \
    FORGEJO__service__DISABLE_REGISTRATION=true \
    FORGEJO__actions__ENABLED=true \
    FORGEJO__server__ROOT_URL=http://gitea:3000/
