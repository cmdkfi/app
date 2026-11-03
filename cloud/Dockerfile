# cmdk relayer + rebalancer container (Cloudflare Containers, linux/amd64). Testnet only. No key in the image:
# front.py writes the keys from Worker secrets into files at start-up. Build context: the repository root
# (wrangler.jsonc image_build_context ".."); only the files copied below enter the image (Dockerfile.dockerignore).
FROM python:3.14-slim-trixie@sha256:930e1abba7c4e8f96ed93e8012c35d174d7732752731bbd5e8e757d7ec6f3966

# Pinned releases, checked by SHA-256 (computed 3 Nov 2026 from the GitHub release assets; Foundry's matches its
# published foundry_v1.8.6_linux_amd64.sha256, near-cli-rs's its .tar.gz.sha256).
ARG FOUNDRY_VERSION=v1.8.6
ARG FOUNDRY_SHA256=58850106984038f3a7200f38a4182c8cb423d3a4f4c7848eacbc2453e6bc35fc
ARG NEAR_CLI_VERSION=v0.30.1
ARG NEAR_CLI_SHA256=996e9f390d1cc446d17ea24e39c48f6ff906507f2d1dba96fc41f596a7e5de14

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl jq \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 cmdk

RUN curl -fsSL -o /tmp/foundry.tgz https://github.com/foundry-rs/foundry/releases/download/${FOUNDRY_VERSION}/foundry_${FOUNDRY_VERSION}_linux_amd64.tar.gz \
 && echo "${FOUNDRY_SHA256}  /tmp/foundry.tgz" | sha256sum -c - \
 && mkdir -p /home/cmdk/.foundry/bin \
 && tar -xzf /tmp/foundry.tgz -C /home/cmdk/.foundry/bin cast \
 && rm /tmp/foundry.tgz \
 && curl -fsSL -o /tmp/near.tgz https://github.com/near/near-cli-rs/releases/download/${NEAR_CLI_VERSION}/near-cli-rs-x86_64-unknown-linux-gnu.tar.gz \
 && echo "${NEAR_CLI_SHA256}  /tmp/near.tgz" | sha256sum -c - \
 && tar -xzf /tmp/near.tgz -C /tmp near-cli-rs-x86_64-unknown-linux-gnu/near \
 && install -m 755 /tmp/near-cli-rs-x86_64-unknown-linux-gnu/near /usr/local/bin/near \
 && rm -rf /tmp/near.tgz /tmp/near-cli-rs-x86_64-unknown-linux-gnu \
 && chown -R cmdk:cmdk /home/cmdk/.foundry

# What the two scripts read at runtime: relayer.py (stdlib, near, ~/.foundry/bin/cast for `derive` only),
# relayer.sh (its restart loop), rebalance_pool.py (cast, deployments/testnet.json, scripts/state/, the Base key).
WORKDIR /app
COPY --chown=cmdk:cmdk scripts/relayer.py scripts/relayer.sh scripts/rebalance_pool.py scripts/
COPY --chown=cmdk:cmdk deployments/testnet.json deployments/
COPY --chown=cmdk:cmdk cloud/front.py cloud/
RUN mkdir -p scripts/state && chown cmdk:cmdk scripts/state

USER cmdk
ENV HOME=/home/cmdk PYTHONUNBUFFERED=1
EXPOSE 8080
ENTRYPOINT ["python3", "-I", "/app/cloud/front.py"]
