//
//  camsink-feed
//
//  표준입력으로 받은 raw BGRA 프레임을 camsink 가상 카메라의 sink stream 에
//  밀어넣는다. Python 쪽에서 이 프로그램을 자식 프로세스로 띄우고 프레임을
//  파이프로 흘려보내는 구조다.
//
//  CoreMediaIO 의 sink stream 은 C API 로만 접근할 수 있어서 Python 에서
//  직접 다루기 어렵다. 이 작은 프로그램이 그 다리 역할만 한다.
//
//  ldenoue/cameraextension (MIT, © 2022 Laurent Denoue) 의 sink 연결 방식을
//  참고했다.
//
//  빌드: build-feed.sh
//

import AVFoundation
import CoreMediaIO
import Foundation

// MARK: - 설정

struct Options {
    var cameraName = "camsink"
    var width: Int32 = 1280
    var height: Int32 = 720

    static func parse() -> Options {
        var opts = Options()
        var args = Array(CommandLine.arguments.dropFirst())
        while !args.isEmpty {
            let flag = args.removeFirst()
            guard !args.isEmpty else { break }
            let value = args.removeFirst()
            switch flag {
            case "--camera-name": opts.cameraName = value
            case "--width": opts.width = Int32(value) ?? opts.width
            case "--height": opts.height = Int32(value) ?? opts.height
            default: break
            }
        }
        return opts
    }
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(("camsink-feed: " + message + "\n").data(using: .utf8)!)
    exit(1)
}

/// 진행 상황을 stderr 로 알린다. stdout 은 프레임 전용이라 쓰지 않는다.
func note(_ message: String) {
    FileHandle.standardError.write(("camsink-feed: " + message + "\n").data(using: .utf8)!)
}

// MARK: - CMIO 장치 찾기

/// 확장 프로그램이 제공하는 카메라도 CMIO 목록에 나오도록 한다.
func allowExtensionDevices() {
    var prop = CMIOObjectPropertyAddress(
        mSelector: CMIOObjectPropertySelector(kCMIOHardwarePropertyAllowScreenCaptureDevices),
        mScope: CMIOObjectPropertyScope(kCMIOObjectPropertyScopeGlobal),
        mElement: CMIOObjectPropertyElement(kCMIOObjectPropertyElementMain))
    var allow: UInt32 = 1
    CMIOObjectSetPropertyData(
        CMIOObjectID(kCMIOObjectSystemObject), &prop, 0, nil,
        UInt32(MemoryLayout<UInt32>.size), &allow)
}

func findCaptureDevice(named name: String) -> AVCaptureDevice? {
    let session = AVCaptureDevice.DiscoverySession(
        deviceTypes: [.external, .deskViewCamera, .builtInWideAngleCamera],
        mediaType: .video, position: .unspecified)
    return session.devices.first { $0.localizedName == name }
}

func findCMIODevice(uid: String) -> CMIOObjectID? {
    var dataSize: UInt32 = 0
    var dataUsed: UInt32 = 0
    var opa = CMIOObjectPropertyAddress(
        mSelector: CMIOObjectPropertySelector(kCMIOHardwarePropertyDevices),
        mScope: CMIOObjectPropertyScope(kCMIOObjectPropertyScopeGlobal),
        mElement: CMIOObjectPropertyElement(kCMIOObjectPropertyElementMain))

    CMIOObjectGetPropertyDataSize(
        CMIOObjectPropertySelector(kCMIOObjectSystemObject), &opa, 0, nil, &dataSize)
    let count = Int(dataSize) / MemoryLayout<CMIOObjectID>.size
    guard count > 0 else { return nil }

    var devices = [CMIOObjectID](repeating: 0, count: count)
    CMIOObjectGetPropertyData(
        CMIOObjectPropertySelector(kCMIOObjectSystemObject), &opa, 0, nil,
        dataSize, &dataUsed, &devices)

    for device in devices {
        opa.mSelector = CMIOObjectPropertySelector(kCMIODevicePropertyDeviceUID)
        CMIOObjectGetPropertyDataSize(device, &opa, 0, nil, &dataSize)
        var deviceUID: CFString = "" as NSString
        CMIOObjectGetPropertyData(device, &opa, 0, nil, dataSize, &dataUsed, &deviceUID)
        if String(deviceUID) == uid { return device }
    }
    return nil
}

func streams(of device: CMIODeviceID) -> [CMIOStreamID] {
    var dataSize: UInt32 = 0
    var dataUsed: UInt32 = 0
    var opa = CMIOObjectPropertyAddress(
        mSelector: CMIOObjectPropertySelector(kCMIODevicePropertyStreams),
        mScope: CMIOObjectPropertyScope(kCMIOObjectPropertyScopeGlobal),
        mElement: CMIOObjectPropertyElement(kCMIOObjectPropertyElementMain))
    CMIOObjectGetPropertyDataSize(device, &opa, 0, nil, &dataSize)
    let count = Int(dataSize) / MemoryLayout<CMIOStreamID>.size
    var ids = [CMIOStreamID](repeating: 0, count: count)
    CMIOObjectGetPropertyData(device, &opa, 0, nil, dataSize, &dataUsed, &ids)
    return ids
}

// MARK: - 프레임 송출

final class Feeder {
    private let width: Int32
    private let height: Int32
    private var queue: CMSimpleQueue?
    private var pool: CVPixelBufferPool?
    private var format: CMFormatDescription?

    init(width: Int32, height: Int32) {
        self.width = width
        self.height = height
    }

    func connect(cameraName: String) throws {
        allowExtensionDevices()

        guard let device = findCaptureDevice(named: cameraName) else {
            fail("'\(cameraName)' 카메라를 찾지 못했습니다. 확장 프로그램이 켜져 있는지 확인하세요.")
        }
        guard let cmioDevice = findCMIODevice(uid: device.uniqueID) else {
            fail("'\(cameraName)' 의 CMIO 장치를 찾지 못했습니다.")
        }

        // 익스텐션은 스트림을 두 개 제공한다: [0] 화상회의 앱이 읽는 source,
        // [1] 우리가 프레임을 넣는 sink.
        let ids = streams(of: cmioDevice)
        guard ids.count >= 2 else {
            fail("sink stream 이 없습니다 (스트림 \(ids.count)개).")
        }
        let sink = ids[1]

        CMVideoFormatDescriptionCreate(
            allocator: kCFAllocatorDefault, codecType: kCVPixelFormatType_32BGRA,
            width: width, height: height, extensions: nil, formatDescriptionOut: &format)

        let attributes: NSDictionary = [
            kCVPixelBufferWidthKey: width,
            kCVPixelBufferHeightKey: height,
            kCVPixelBufferPixelFormatTypeKey: kCVPixelFormatType_32BGRA,
            kCVPixelBufferIOSurfacePropertiesKey: [:],
        ]
        CVPixelBufferPoolCreate(kCFAllocatorDefault, nil, attributes, &pool)

        let queuePtr = UnsafeMutablePointer<Unmanaged<CMSimpleQueue>?>.allocate(capacity: 1)
        defer { queuePtr.deallocate() }
        let result = CMIOStreamCopyBufferQueue(sink, { _, _, _ in }, nil, queuePtr)
        guard result == 0, let q = queuePtr.pointee?.takeUnretainedValue() else {
            fail("sink 큐를 열지 못했습니다 (오류 \(result)).")
        }
        queue = q

        guard CMIODeviceStartStream(cmioDevice, sink) == 0 else {
            fail("sink 스트림을 시작하지 못했습니다.")
        }
        note("'\(cameraName)' 연결됨 (\(width)x\(height))")
    }

    /// BGRA 바이트 한 장을 큐에 넣는다. 큐가 가득 차면 조용히 버린다.
    func send(_ bytes: UnsafeRawPointer) {
        guard let queue, let pool, let format else { return }
        // 큐가 차 있는데 계속 넣으면 지연이 쌓이므로 이번 프레임은 버린다.
        guard CMSimpleQueueGetCount(queue) < CMSimpleQueueGetCapacity(queue) else { return }

        var pixelBuffer: CVPixelBuffer?
        guard CVPixelBufferPoolCreatePixelBuffer(kCFAllocatorDefault, pool, &pixelBuffer) == 0,
              let pixelBuffer else { return }

        CVPixelBufferLockBaseAddress(pixelBuffer, [])
        if let base = CVPixelBufferGetBaseAddress(pixelBuffer) {
            let dstStride = CVPixelBufferGetBytesPerRow(pixelBuffer)
            let srcStride = Int(width) * 4
            if dstStride == srcStride {
                memcpy(base, bytes, srcStride * Int(height))
            } else {
                // 픽셀 버퍼는 행마다 여백이 붙을 수 있어 행 단위로 복사한다.
                for row in 0..<Int(height) {
                    memcpy(base.advanced(by: row * dstStride),
                           bytes.advanced(by: row * srcStride), srcStride)
                }
            }
        }
        CVPixelBufferUnlockBaseAddress(pixelBuffer, [])

        var timing = CMSampleTimingInfo()
        timing.presentationTimeStamp = CMClockGetTime(CMClockGetHostTimeClock())
        var sampleBuffer: CMSampleBuffer?
        guard CMSampleBufferCreateForImageBuffer(
            allocator: kCFAllocatorDefault, imageBuffer: pixelBuffer, dataReady: true,
            makeDataReadyCallback: nil, refcon: nil, formatDescription: format,
            sampleTiming: &timing, sampleBufferOut: &sampleBuffer) == 0,
            let sampleBuffer else { return }

        // 큐가 소유권을 가져가므로 retain 해서 넘긴다.
        CMSimpleQueueEnqueue(queue, element: UnsafeMutableRawPointer(
            Unmanaged.passRetained(sampleBuffer).toOpaque()))
    }
}

// MARK: - 표준입력 읽기

/// 정확히 count 바이트를 읽는다. 짧게 읽히면 채울 때까지 반복한다.
func readFully(into buffer: UnsafeMutableRawPointer, count: Int) -> Bool {
    var filled = 0
    while filled < count {
        let n = read(0, buffer.advanced(by: filled), count - filled)
        if n <= 0 { return false }   // EOF 또는 오류
        filled += n
    }
    return true
}

// MARK: - 진입점

let options = Options.parse()
let feeder = Feeder(width: options.width, height: options.height)
try feeder.connect(cameraName: options.cameraName)

let frameSize = Int(options.width) * Int(options.height) * 4
let buffer = UnsafeMutableRawPointer.allocate(
    byteCount: frameSize, alignment: MemoryLayout<UInt32>.alignment)
defer { buffer.deallocate() }

while readFully(into: buffer, count: frameSize) {
    feeder.send(buffer)
}
note("입력이 끝나 종료합니다.")
