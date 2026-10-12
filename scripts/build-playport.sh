#!/bin/sh
# Run on a Docker host. JDK archive must be hash-verified before use.
set -eu
source_dir=${1:?Usage: build-playport.sh SOURCE_DIR JDK25_LINUX_TAR_GZ}
jdk_archive=${2:?Provide verified Linux x86_64 JDK 25 archive}
name=${BUILD_CONTAINER:-f50-playport-build}
image=${BUILD_IMAGE:-python@sha256:d764629ce0ddd8c71fd371e9901efb324a95789d2315a47db7e4d27e78f1b0e9}
test -f "$source_dir/gradlew"
test -f "$jdk_archive"
if docker container inspect "$name" >/dev/null 2>&1; then
    echo "Container exists; inspect before reuse: $name" >&2
    exit 1
fi
docker run -d --name "$name" --label project=f50-carplay \
    --memory 4g --cpus 2 --pids-limit 512 --cap-drop ALL \
    --security-opt no-new-privileges --workdir /work \
    "$image" sh -c 'while :; do sleep 3600; done'
# Run on Linux to avoid macOS extended attributes in tar streams.
tar --owner=0 --group=0 -C "$source_dir" --exclude=.git --exclude=node_modules --exclude=build \
    --exclude=.gradle --exclude=identity --exclude='*.pk8' --exclude='*.p7b' -cf - . |
    docker cp - "$name:/work"
docker exec -i "$name" sh -c 'mkdir -p /opt/jdk; tar -xz --strip-components=1 -C /opt/jdk' < "$jdk_archive"
docker exec -e JAVA_HOME=/opt/jdk "$name" sh -c \
    'export PATH=/opt/jdk/bin:$PATH; sh gradlew --no-daemon --max-workers=2 :protocol:test :server:test :server:installDist'
