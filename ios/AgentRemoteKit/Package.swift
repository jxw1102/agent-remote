// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "AgentRemoteKit",
    platforms: [
        .iOS(.v17),
        .macOS(.v14),
    ],
    products: [
        .library(name: "AgentRemoteKit", targets: ["AgentRemoteKit"]),
    ],
    targets: [
        .target(name: "AgentRemoteKit"),
        .testTarget(
            name: "AgentRemoteKitTests",
            dependencies: ["AgentRemoteKit"]
        ),
    ]
)
