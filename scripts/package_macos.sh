#!/bin/bash
#
# webcam-blur.app 과 배포용 DMG를 만든다.
#
# 기본 동작:
#   ./scripts/package_macos.sh
#
# 공증까지 할 때:
#   ASC_KEY_ID=... ASC_ISSUER_ID=... ./scripts/package_macos.sh --notarize
#
set -euo pipefail

cd "$(dirname "$0")/.."

NOTARIZE=0
if [[ "${1:-}" == "--notarize" ]]; then
    NOTARIZE=1
fi

if [ ! -x venv/bin/python ]; then
    python3 -m venv venv
fi

source venv/bin/activate
python -m pip install -r requirements.txt -r requirements-build.txt

APP_NAME="$(python - <<'PY'
from version import APP_NAME
print(APP_NAME)
PY
)"
VERSION="$(python - <<'PY'
from version import APP_VERSION
print(APP_VERSION)
PY
)"

SIGN_ID="${SIGN_ID:-Developer ID Application}"
RELEASE_DIR="$PWD/release"
DMG_ROOT="$PWD/build/dmg-root"
APP="$PWD/dist/${APP_NAME}.app"
DMG="$RELEASE_DIR/${APP_NAME}-${VERSION}.dmg"

step() { echo; echo "==> $1"; }

step "아이콘 생성"
python scripts/make_icon.py

step "PyInstaller 앱 번들 생성"
python -m PyInstaller --noconfirm --clean webcam-blur.spec

if security find-identity -v -p codesigning | grep -q "$SIGN_ID"; then
    step "앱 서명"
    codesign --force --deep --options runtime --timestamp \
        --entitlements packaging/entitlements.plist \
        --sign "$SIGN_ID" "$APP"
    codesign --verify --deep --strict "$APP"
else
    echo "서명 인증서를 찾지 못해 앱 서명을 건너뜁니다: $SIGN_ID" >&2
fi

step "DMG 생성"
rm -rf "$DMG_ROOT"
mkdir -p "$DMG_ROOT" "$RELEASE_DIR"
cp -R "$APP" "$DMG_ROOT/"
ln -s /Applications "$DMG_ROOT/Applications"
rm -f "$DMG"
hdiutil create -volname "${APP_NAME} ${VERSION}" \
    -srcfolder "$DMG_ROOT" -fs HFS+ -format UDZO "$DMG"

if security find-identity -v -p codesigning | grep -q "$SIGN_ID"; then
    step "DMG 서명"
    codesign --force --timestamp --sign "$SIGN_ID" "$DMG"
fi

if [ "$NOTARIZE" -eq 1 ]; then
    step "DMG 공증"
    : "${ASC_KEY_ID:?ASC_KEY_ID 가 필요합니다.}"
    : "${ASC_ISSUER_ID:?ASC_ISSUER_ID 가 필요합니다.}"
    ASC_KEY_PATH="${ASC_KEY_PATH:-$HOME/.appstoreconnect/private_keys/AuthKey_${ASC_KEY_ID}.p8}"
    xcrun notarytool submit "$DMG" \
        --key "$ASC_KEY_PATH" --key-id "$ASC_KEY_ID" --issuer "$ASC_ISSUER_ID" \
        --wait | tail -3

    step "공증 티켓 첨부"
    xcrun stapler staple "$DMG"

    step "Gatekeeper 확인"
    spctl -a -vvv -t open --context context:primary-signature "$DMG" 2>&1 | head -3
fi

echo
echo "$DMG"
