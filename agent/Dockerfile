# Cross-built on an ARM Mac for linux/amd64, as in measurement E. Not yet reproducible (see results.md, P3).
FROM --platform=$BUILDPLATFORM rust:1.95-bookworm AS build
RUN apt-get update && apt-get install -y --no-install-recommends gcc-x86-64-linux-gnu libc6-dev-amd64-cross && rm -rf /var/lib/apt/lists/*
RUN rustup target add x86_64-unknown-linux-gnu
ENV CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER=x86_64-linux-gnu-gcc CC_x86_64_unknown_linux_gnu=x86_64-linux-gnu-gcc
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo build --release --locked --target x86_64-unknown-linux-gnu

FROM --platform=linux/amd64 debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/target/x86_64-unknown-linux-gnu/release/prime-p3-agent /usr/local/bin/prime-p3-agent
ENTRYPOINT ["/usr/local/bin/prime-p3-agent"]
