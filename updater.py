"""GitHub Releases 기반 업데이트 확인."""

from dataclasses import dataclass
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from version import APP_VERSION, GITHUB_REPOSITORY

LATEST_RELEASE_URL = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"


class UpdateCheckError(RuntimeError):
    pass


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    url: str
    body: str


def _version_tuple(version):
    """v1.2.3 같은 태그를 비교 가능한 숫자 튜플로 바꾼다."""
    text = version.strip().removeprefix("v")
    parts = re.split(r"[.+-]", text)
    numbers = []
    for part in parts:
        if not part.isdigit():
            break
        numbers.append(int(part))
    return tuple(numbers) or (0,)


def is_newer_version(candidate, current=APP_VERSION):
    return _version_tuple(candidate) > _version_tuple(current)


def _download_url(release):
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        if name.endswith(".dmg") and asset.get("browser_download_url"):
            return asset["browser_download_url"]
    return release.get("html_url")


def update_from_release(release, current=APP_VERSION):
    version = release.get("tag_name", "").strip()
    if not version or not is_newer_version(version, current):
        return None

    url = _download_url(release)
    if not url:
        raise UpdateCheckError("릴리스에 다운로드 링크가 없습니다.")

    return UpdateInfo(
        version=version.removeprefix("v"),
        url=url,
        body=release.get("body") or "",
    )


def check_for_update(current=APP_VERSION, timeout=5):
    request = Request(
        LATEST_RELEASE_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"webcam-blur/{current}",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            release = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise UpdateCheckError(f"업데이트 정보를 읽지 못했습니다: HTTP {exc.code}") from exc
    except (OSError, URLError, json.JSONDecodeError) as exc:
        raise UpdateCheckError(f"업데이트 정보를 읽지 못했습니다: {exc}") from exc

    return update_from_release(release, current)
