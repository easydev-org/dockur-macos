# Keep upstream's --exclude, but remove the dangling symlink that BuildKit
# chokes on first. This is the smallest possible change to the upstream
# Dockerfile: one extra RUN in the source stage.
#
# The find expression is deliberately spelled with -xtype l (link whose target
# is missing) rather than ! -exec test -e, so it needs no shell negation.
FROM qemux/qemu:7.50 AS src
RUN find /etc/alternatives -maxdepth 1 -xtype l -delete || true
FROM scratch AS base
COPY --from=src --exclude=usr/bin/qemu-system-x86_64 / /
FROM base
CMD ["/bin/true"]
