# Drop --exclude and delete the binary in a later layer. The tree copies as a
# whole, at the cost of carrying the qemu binary in one intermediate layer.
FROM scratch AS base
COPY --from=qemux/qemu:7.50 / /
FROM base
RUN rm -f /usr/bin/qemu-system-x86_64
CMD ["/bin/true"]
