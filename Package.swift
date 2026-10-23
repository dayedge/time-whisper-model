// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "TimeWhisperModel",
    platforms: [.macOS("15.0")],
    products: [
        .library(name: "TimeWhisperModelAll", targets: ["TimeWhisperModelAll"]),
    ],
    targets: [
        .binaryTarget(name: "TimeWhisperModelAll", path: "build/artifacts/TimeWhisperModelAll.xcframework"),
    ]
)
