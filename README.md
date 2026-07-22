# webcam-blur

회의에 원본 웹캠을 그대로 내보내지 않고, 해상도와 디테일을 의도적으로 낮춘
화면을 미리 보고 가상 카메라로 송출하는 작은 macOS 앱입니다.

## 현재 상태

- PySide6 GUI로 카메라 선택, 흐림 정도, 효과 On/Off, 가상 카메라 송출을 조작합니다.
- 카메라 입력은 Qt Multimedia를 사용합니다. OpenCV 카메라 번호와 실제 장치 순서가
  어긋나는 문제를 피하기 위해서입니다.
- 가상 카메라는 별도 패키지인
  [`camsink`](https://github.com/lunartown/camsink)를 사용합니다.
- `camsink` Camera Extension은 macOS 시스템 설정에서 한 번 활성화해야 합니다.

## 실행

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```

가상 카메라를 쓰려면 먼저 `/Applications/CamsinkApp.app`을 열고 `설치`를 누른 뒤,
시스템 설정 > 일반 > 로그인 항목 및 확장 > 카메라 확장 프로그램에서 `camsink`를
켜야 합니다.

## 검증

```bash
source venv/bin/activate
python -m unittest
python -m compileall app.py camera.py quality.py vcam.py main.py tests
```

가상 카메라가 활성화되어 있으면 앱에서 `가상 카메라 시작`을 누른 뒤 Zoom, Google
Meet, Teams 같은 앱에서 `camsink` 카메라를 선택하면 됩니다.

## macOS 앱/DMG 빌드

```bash
./scripts/package_macos.sh
```

결과물은 `release/webcam-blur-<version>.dmg`에 만들어집니다. Developer ID
인증서가 키체인에 있으면 앱과 DMG가 서명됩니다.

공증까지 한 번에 하려면 App Store Connect API 키 환경변수를 넣고 실행합니다.

```bash
ASC_KEY_ID=... ASC_ISSUER_ID=... ./scripts/package_macos.sh --notarize
```

앱의 `업데이트 확인` 버튼은 GitHub Releases의 최신 릴리스를 확인합니다. 새 버전을
배포할 때는 `version.py`의 `APP_VERSION`을 올리고 DMG를 빌드한 뒤, `v<version>`
태그의 GitHub Release에 DMG 파일을 첨부하세요.
