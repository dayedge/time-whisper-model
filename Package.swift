// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "TimeWhisperModel",
    platforms: [.macOS("15.0")],
    products: [
        .library(name: "TimeWhisperModelAll", targets: ["TimeWhisperModelAll"])
    ],
    targets: [
        .binaryTarget(name: "TimeWhisperModelAll", url: "https://github.com/dayedge/time-whisper-model/releases/download/0.7.0/TimeWhisperModelAll-0.7.0.xcframework.zip", checksum: "db1651160c5a9af6c71e308871474e2f3a10ae38271feaf1ad11fee856dc2884")
    ]
)
