# The agent image, built reproducibly (MEASUREMENT.md, "Run your own agent"): base images pinned by digest, Debian
# packages at pinned versions, Cargo.lock enforced (--locked), build paths remapped, file and image timestamps set
# from SOURCE_DATE_EPOCH. Cross-built on the build host's platform for linux/amd64 (measurement E's method).
# Build with ./build.sh, which passes SOURCE_DATE_EPOCH and rewrite-timestamp and compares two builds.

# rust:1.95-bookworm and debian:bookworm-slim, multi-platform index digests resolved on 3 Nov 2026
ARG RUST_IMAGE=rust:1.95-bookworm@sha256:6258907abe69656e41cd992e0b705cdcfabcbbe3db374f92ed2d47121282d4a1
ARG RUNTIME_IMAGE=debian:bookworm-slim@sha256:99c0b25c46d0214d5a2091092e7908907dcaecd6a962cde0ac700acee222b6f3

FROM --platform=$BUILDPLATFORM ${RUST_IMAGE} AS build
ARG SOURCE_DATE_EPOCH
# The cross linker and libc: 21 Debian bookworm packages at pinned versions (apt checks every file against the
# archive's signed index, so the server only affects availability). Bookworm is in LTS since July 2026 and its main
# suite no longer changes, so these versions stay. Default source: the main archive. Fallback for a rebuild after
# bookworm is archived: --build-arg DEBIAN_APT_URI=http://snapshot.debian.org/archive/debian/20261101T000000Z
# (the snapshot of 1 Nov 2026, which carries the same files; that service was unavailable for a day on 4 Nov 2026).
ARG DEBIAN_APT_URI=http://deb.debian.org/debian
RUN rm -f /etc/apt/sources.list /etc/apt/sources.list.d/* \
 && printf 'Types: deb\nURIs: %s\nSuites: bookworm\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n' \
      "$DEBIAN_APT_URI" > /etc/apt/sources.list.d/debian.sources \
 && apt-get -o Acquire::Check-Valid-Until=false -o Acquire::Retries=3 update \
 && apt-get install -y --no-install-recommends -o Acquire::Retries=3 \
      binutils-x86-64-linux-gnu=2.40-2 cpp-12-x86-64-linux-gnu=12.2.0-14cross1 cpp-x86-64-linux-gnu=4:12.2.0-3 \
      gcc-12-cross-base=12.2.0-14cross1 gcc-12-x86-64-linux-gnu=12.2.0-14cross1 gcc-12-x86-64-linux-gnu-base=12.2.0-14cross1 \
      gcc-x86-64-linux-gnu=4:12.2.0-3 libasan8-amd64-cross=12.2.0-14cross1 libatomic1-amd64-cross=12.2.0-14cross1 \
      libc6-amd64-cross=2.36-8cross1 libc6-dev-amd64-cross=2.36-8cross1 libgcc-12-dev-amd64-cross=12.2.0-14cross1 \
      libgcc-s1-amd64-cross=12.2.0-14cross1 libgomp1-amd64-cross=12.2.0-14cross1 libitm1-amd64-cross=12.2.0-14cross1 \
      liblsan0-amd64-cross=12.2.0-14cross1 libquadmath0-amd64-cross=12.2.0-14cross1 libstdc++6-amd64-cross=12.2.0-14cross1 \
      libtsan2-amd64-cross=12.2.0-14cross1 libubsan1-amd64-cross=12.2.0-14cross1 linux-libc-dev-amd64-cross=6.1.4-1cross1 \
 && rm -rf /var/lib/apt/lists/*
RUN rustup target add x86_64-unknown-linux-gnu && rustc --version && x86_64-linux-gnu-gcc --version | head -1
ENV CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER=x86_64-linux-gnu-gcc \
    CC_x86_64_unknown_linux_gnu=x86_64-linux-gnu-gcc \
    CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUSTFLAGS="--remap-path-prefix=/src=/build --remap-path-prefix=/usr/local/cargo/registry/src=/cargo --remap-path-prefix=/usr/local/rustup=/rustup" \
    CFLAGS_x86_64_unknown_linux_gnu="-ffile-prefix-map=/src=/build -ffile-prefix-map=/usr/local/cargo/registry/src=/cargo" \
    CARGO_INCREMENTAL=0
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo build --release --locked --target x86_64-unknown-linux-gnu \
 && sha256sum target/x86_64-unknown-linux-gnu/release/cmdk-agent

FROM --platform=linux/amd64 ${RUNTIME_IMAGE}
# The TLS roots dcap-qvl's HTTP client reads (rustls-platform-verifier); copied, not installed, so no apt state or
# package timestamps enter the image. reqwest in the agent itself uses the compiled-in webpki roots.
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=build /src/target/x86_64-unknown-linux-gnu/release/cmdk-agent /usr/local/bin/cmdk-agent
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/cmdk-agent"]
