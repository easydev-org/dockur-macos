# Copy through a real rootfs: tar inside the source container, untar into a
# scratch image. This never asks BuildKit to walk the source tree file by file,
# so dangling symlinks cannot upset it.
FROM qemux/qemu:7.50 AS src
RUN mkdir -p /out && tar -C / --exclude=usr/bin/qemu-system-x86_64 -cf - . | tar -C /out -xf -
FROM scratch AS base
COPY --from=src /out /
FROM base
CMD ["/bin/true"]
