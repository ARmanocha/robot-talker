// Encodes raw BGRA frames from stdin into an H.264 MP4 and muxes in an AAC audio file.
// usage: robot_encoder <width> <height> <fps> <audio.m4a> <out.mp4>
// Built automatically by make_robot.py (swiftc -O -swift-version 5).

import AVFoundation
import CoreVideo
import Foundation

func fail(_ msg: String) -> Never {
    FileHandle.standardError.write((msg + "\n").data(using: .utf8)!)
    exit(1)
}

let args = CommandLine.arguments
guard args.count == 6, let width = Int(args[1]), let height = Int(args[2]), let fps = Int32(args[3]) else {
    fail("usage: robot_encoder <width> <height> <fps> <audio.m4a> <out.mp4>")
}
let audioURL = URL(fileURLWithPath: args[4])
let outURL = URL(fileURLWithPath: args[5])
let tmpVideoURL = outURL.deletingLastPathComponent().appendingPathComponent(".robot_video_tmp.mp4")
try? FileManager.default.removeItem(at: tmpVideoURL)
try? FileManager.default.removeItem(at: outURL)

// 1. Video-only pass.
let writer: AVAssetWriter
do { writer = try AVAssetWriter(outputURL: tmpVideoURL, fileType: .mp4) } catch { fail("writer: \(error)") }
let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
    AVVideoCodecKey: AVVideoCodecType.h264,
    AVVideoWidthKey: width,
    AVVideoHeightKey: height,
    AVVideoCompressionPropertiesKey: [
        AVVideoAverageBitRateKey: 10_000_000,
        AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel,
        AVVideoMaxKeyFrameIntervalKey: Int(fps) * 2,
    ],
])
input.expectsMediaDataInRealTime = false
let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
    kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
    kCVPixelBufferWidthKey as String: width,
    kCVPixelBufferHeightKey as String: height,
])
writer.add(input)
writer.startWriting()
writer.startSession(atSourceTime: .zero)

let frameBytes = width * height * 4
let inHandle = FileHandle.standardInput
var frameIndex: Int64 = 0
while true {
    var data = Data()
    while data.count < frameBytes {
        let chunk = inHandle.readData(ofLength: frameBytes - data.count)
        if chunk.isEmpty { break }
        data.append(chunk)
    }
    if data.count < frameBytes { break }
    while !input.isReadyForMoreMediaData { usleep(1000) }
    guard let pool = adaptor.pixelBufferPool else { fail("no pixel buffer pool: \(String(describing: writer.error))") }
    var pbOut: CVPixelBuffer?
    CVPixelBufferPoolCreatePixelBuffer(nil, pool, &pbOut)
    guard let pb = pbOut else { fail("pixel buffer alloc failed") }
    CVPixelBufferLockBaseAddress(pb, [])
    let dst = CVPixelBufferGetBaseAddress(pb)!
    let dstStride = CVPixelBufferGetBytesPerRow(pb)
    let srcStride = width * 4
    data.withUnsafeBytes { (src: UnsafeRawBufferPointer) in
        for y in 0..<height {
            memcpy(dst + y * dstStride, src.baseAddress! + y * srcStride, srcStride)
        }
    }
    CVPixelBufferUnlockBaseAddress(pb, [])
    if !adaptor.append(pb, withPresentationTime: CMTime(value: frameIndex, timescale: fps)) {
        fail("append failed: \(String(describing: writer.error))")
    }
    frameIndex += 1
}
if frameIndex == 0 { fail("no frames received") }
input.markAsFinished()
let written = DispatchSemaphore(value: 0)
writer.finishWriting { written.signal() }
written.wait()
if writer.status != .completed { fail("video write failed: \(String(describing: writer.error))") }

// 2. Mux video + audio without re-encoding.
let videoAsset = AVURLAsset(url: tmpVideoURL)
let audioAsset = AVURLAsset(url: audioURL)
guard let vSrc = videoAsset.tracks(withMediaType: .video).first else { fail("missing video track") }
guard let aSrc = audioAsset.tracks(withMediaType: .audio).first else { fail("missing audio track") }
let vDur = videoAsset.duration
let composition = AVMutableComposition()
let vTrack = composition.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid)!
let aTrack = composition.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid)!
do {
    try vTrack.insertTimeRange(CMTimeRange(start: .zero, duration: vDur), of: vSrc, at: .zero)
    try aTrack.insertTimeRange(CMTimeRange(start: .zero, duration: CMTimeMinimum(audioAsset.duration, vDur)), of: aSrc, at: .zero)
} catch { fail("compose: \(error)") }

guard let export = AVAssetExportSession(asset: composition, presetName: AVAssetExportPresetPassthrough) else {
    fail("could not create export session")
}
export.outputURL = outURL
export.outputFileType = .mp4
export.shouldOptimizeForNetworkUse = true
let exported = DispatchSemaphore(value: 0)
export.exportAsynchronously { exported.signal() }
exported.wait()
try? FileManager.default.removeItem(at: tmpVideoURL)
if export.status != .completed { fail("export failed: \(String(describing: export.error))") }
print("encoded \(frameIndex) frames")
