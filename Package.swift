// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "TimeWhisperModel",
    platforms: [.macOS("15.0")],
    products: [
        .library(name: "TimeWhisperModelAll", targets: ["TimeWhisperModelAll"])
    ],
    targets: [
        .binaryTarget(name: "TimeWhisperModelAll", url: "https://github.com/dayedge/time-whisper-model/releases/download/0.7.1/TimeWhisperModelAll-0.7.1.xcframework.zip", checksum: "67ebe855b63a8c55cff570c06dd1218599fd3d78bf1fb70e483b375a1a9fccdb")
    ]
)
