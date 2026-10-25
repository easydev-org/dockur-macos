# Upstream's exact line, to reproduce the failure on demand.
FROM scratch AS base
COPY --from=qemux/qemu:7.50 --exclude=usr/bin/qemu-system-x86_64 / /
FROM base
CMD ["/bin/true"]
