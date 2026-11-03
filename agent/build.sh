#!/usr/bin/env bash
# Builds the agent image twice from this source, with no cache, and compares the two image IDs (MEASUREMENT.md,
# "Run your own agent"). The image ID is the digest of the image config, which lists every layer's content digest,
# so equal IDs mean identical images. Nothing is pushed. Usage: ./build.sh [tag]   (default: cmdk-agent)
# SOURCE_DATE_EPOCH defaults to the commit time of the last commit that changed the image inputs (Dockerfile,
# Cargo.toml, Cargo.lock, src/), so committing the compose file afterwards does not change the image.
# Needs Docker with BuildKit (Docker 23+) and about 4 GB free in Docker's disk.
set -euo pipefail
cd "$(dirname "$0")"
tag="${1:-cmdk-agent}"
# Builder on the docker-container driver: on the docker driver with the classic image store the daemon's own
# exporter ignores rewrite-timestamp (3 Nov: two builds, identical bytes, but COPY-created directories and the
# binary kept each build's wall-clock mtime, so the image IDs differed). type=docker still loads into the daemon.
docker buildx inspect cmdk-repro >/dev/null 2>&1 || docker buildx create --name cmdk-repro --driver docker-container \
  --driver-opt image=moby/buildkit@sha256:c5f8862f52499773af85bc07695b2379ef08e8002352b0df2c4864c91d78dabe --bootstrap >/dev/null
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git log -1 --format=%ct -- Dockerfile Cargo.toml Cargo.lock src)}"
ids=()
for i in 1 2; do
  docker buildx build --builder cmdk-repro --no-cache --platform linux/amd64 \
    --build-arg SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH" \
    --output "type=docker,name=$tag:repro-$i,rewrite-timestamp=true" \
    --progress plain . > "build-$i.log" 2>&1 || { echo "build $i failed: see build-$i.log"; exit 1; }
  id="$(docker image inspect -f '{{.Id}}' "$tag:repro-$i")"
  bin="$(grep -o '[0-9a-f]\{64\}  target/x86_64-unknown-linux-gnu/release/cmdk-agent' "build-$i.log" | tail -1 | cut -c1-64)"
  echo "build $i: image $id binary sha256 $bin"
  ids+=("$id")
done
if [ "${ids[0]}" = "${ids[1]}" ]; then
  echo "REPRODUCIBLE image ${ids[0]} SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH"
else
  echo "DIFFERENT image IDs; compare: docker history --no-trunc $tag:repro-1 / $tag:repro-2"; exit 1
fi
