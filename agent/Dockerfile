# The agent image, built reproducibly (MEASUREMENT.md, "Run your own agent"): base images pinned by digest, Debian
# packages from a dated snapshot, Cargo.lock enforced (--locked), build paths remapped, file and image timestamps set
# from SOURCE_DATE_EPOCH. Cross-built on the build host's platform for linux/amd64 (measurement E's method).
# Build with ./build.sh, which passes SOURCE_DATE_EPOCH and rewrite-timestamp and compares two builds.

# rust:1.95-bookworm and debian:bookworm-slim, multi-platform index digests resolved on 3 Nov 2026
ARG RUST_IMAGE=rust:1.95-bookworm@sha256:6258907abe69656e41cd992e0b705cdcfabcbbe3db374f92ed2d47121282d4a1
ARG RUNTIME_IMAGE=debian:bookworm-slim@sha256:99c0b25c46d0214d5a2091092e7908907dcaecd6a962cde0ac700acee222b6f3

FROM --platform=$BUILDPLATFORM ${RUST_IMAGE} AS build
ARG SOURCE_DATE_EPOCH
# Debian packages as published on this date (snapshot.debian.org), so the cross linker and libc never drift
ARG DEBIAN_SNAPSHOT=20261101T000000Z
RUN rm -f /etc/apt/sources.list /etc/apt/sources.list.d/* \
 && printf 'Types: deb\nURIs: http://snapshot.debian.org/archive/debian/%s\nSuites: bookworm bookworm-updates\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n\nTypes: deb\nURIs: http://snapshot.debian.org/archive/debian-security/%s\nSuites: bookworm-security\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n' \
      "$DEBIAN_SNAPSHOT" "$DEBIAN_SNAPSHOT" > /etc/apt/sources.list.d/debian.sources \
 && apt-get -o Acquire::Check-Valid-Until=false update \
 && apt-get install -y --no-install-recommends gcc-x86-64-linux-gnu libc6-dev-amd64-cross \
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
