"""macOS 카메라 권한 처리.

Qt 권한 API가 번들 앱에서 TCC 프롬프트를 안정적으로 띄우지 못하는 경우가 있어
AVFoundation의 네이티브 권한 API를 직접 사용한다.
"""

import AVFoundation


AUTHORIZED = AVFoundation.AVAuthorizationStatusAuthorized
DENIED = AVFoundation.AVAuthorizationStatusDenied
NOT_DETERMINED = AVFoundation.AVAuthorizationStatusNotDetermined
RESTRICTED = AVFoundation.AVAuthorizationStatusRestricted


def authorization_status():
    return AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(
        AVFoundation.AVMediaTypeVideo
    )


def request_access(callback):
    def completion(granted):
        callback(bool(granted))

    AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
        AVFoundation.AVMediaTypeVideo,
        completion,
    )
