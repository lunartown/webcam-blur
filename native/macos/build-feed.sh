#!/bin/bash
#
# camsink-feed 헬퍼를 빌드한다.
#
# 앱 번들이 아닌 단순 실행 파일이라 Xcode 프로젝트 없이 swiftc 로 바로 만든다.
# 시스템 익스텐션과 달리 공증이 필요 없다.
#
set -euo pipefail

cd "$(dirname "$0")"
mkdir -p bin

swiftc -O CamsinkFeed/main.swift -o bin/camsink-feed \
    -framework AVFoundation \
    -framework CoreMediaIO \
    -framework CoreMedia \
    -framework CoreVideo

echo "빌드 완료: $PWD/bin/camsink-feed"
