// swift-tools-version: 5.10
import PackageDescription

let package = Package(
    name: "HoomyPilot",
    platforms: [.macOS(.v14), .iOS(.v17)],
    products: [.executable(name: "HoomyPilot", targets: ["HoomyPilot"])],
    targets: [
        .executableTarget(name: "HoomyPilot"),
        .testTarget(name: "HoomyPilotTests", dependencies: ["HoomyPilot"])
    ]
)
