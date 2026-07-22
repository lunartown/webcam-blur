#!/bin/bash
#
# camsink 가상 카메라를 빌드·서명·공증하고 /Applications에 설치한다.
#
# macOS는 서명되고 공증된 시스템 익스텐션만 로드한다. SIP를 끄면 개발용
# 서명으로도 되지만 그건 권장하지 않으므로, 여기서는 Developer ID 경로만 쓴다.
#
# 필요한 것:
#   - Apple 개발자 계정과 Developer ID Application 인증서
#   - App Store Connect API 키 (공증용)
#   - 이 맥이 계정에 기기로 등록돼 있을 것
#
# 설정은 환경변수로 덮어쓸 수 있다:
#   TEAM_ID, ASC_KEY_ID, ASC_ISSUER_ID, ASC_KEY_PATH
#
set -euo pipefail

TEAM_ID="${TEAM_ID:-M56H79B979}"
ASC_KEY_ID="${ASC_KEY_ID:-88F853BMS9}"
ASC_ISSUER_ID="${ASC_ISSUER_ID:-e58be8ac-34b7-49ce-8355-7fbba221dcaa}"
ASC_KEY_PATH="${ASC_KEY_PATH:-$HOME/.appstoreconnect/private_keys/AuthKey_${ASC_KEY_ID}.p8}"

cd "$(dirname "$0")"
BUILD_DIR="$PWD/build"
APP_NAME="CamsinkApp.app"

step() { echo; echo "==> $1"; }

if [ ! -f "$ASC_KEY_PATH" ]; then
    echo "API 키를 찾을 수 없습니다: $ASC_KEY_PATH" >&2
    exit 1
fi

step "이전 빌드 정리"
rm -rf "$BUILD_DIR"

step "archive"
xcodebuild -project camsink.xcodeproj -scheme CamsinkApp -configuration Release \
    -archivePath "$BUILD_DIR/camsink.xcarchive" archive \
    | grep -E "error:|ARCHIVE (SUCCEEDED|FAILED)"

step "export (Developer ID)"
cat > "$BUILD_DIR/exportOptions.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>method</key><string>developer-id</string>
    <key>teamID</key><string>${TEAM_ID}</string>
    <key>signingStyle</key><string>manual</string>
    <key>provisioningProfiles</key>
    <dict>
        <key>com.lunartown.camsink</key><string>camsink app devid</string>
        <key>com.lunartown.camsink.extension</key><string>camsink ext devid</string>
    </dict>
</dict>
</plist>
EOF
xcodebuild -exportArchive -archivePath "$BUILD_DIR/camsink.xcarchive" \
    -exportPath "$BUILD_DIR/export" -exportOptionsPlist "$BUILD_DIR/exportOptions.plist" \
    | grep -E "error:|EXPORT (SUCCEEDED|FAILED)"

APP="$BUILD_DIR/export/$APP_NAME"

step "공증 제출 (몇 분 걸립니다)"
ditto -c -k --keepParent "$APP" "$BUILD_DIR/camsink.zip"
xcrun notarytool submit "$BUILD_DIR/camsink.zip" \
    --key "$ASC_KEY_PATH" --key-id "$ASC_KEY_ID" --issuer "$ASC_ISSUER_ID" \
    --wait | tail -3

step "티켓 첨부"
xcrun stapler staple "$APP"

step "Gatekeeper 확인"
spctl -a -vvv -t exec "$APP" 2>&1 | head -3

step "/Applications 설치"
# 실행 중이면 교체가 실패하므로 먼저 종료한다.
osascript -e 'quit app "CamsinkApp"' 2>/dev/null || true
rm -rf "/Applications/$APP_NAME"
cp -R "$APP" /Applications/

echo
echo "설치 완료: /Applications/$APP_NAME"
echo "처음이라면 앱을 열어 activate를 누르고, 시스템 설정 > 일반 >"
echo "로그인 항목 및 확장 > 카메라 확장 프로그램에서 켜주세요."
