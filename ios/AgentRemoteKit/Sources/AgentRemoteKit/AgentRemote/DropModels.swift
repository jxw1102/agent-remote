import Foundation

/// One file in the host→phone drop folder (`GET /api/drop`).
public struct DropFile: Codable, Sendable, Equatable, Identifiable {
    public let name: String
    public let size: Int
    public let mtime: FlexibleDate

    public var id: String { name }
}

public struct DropListResponse: Codable, Sendable, Equatable {
    public let path: String
    public let files: [DropFile]
}

public struct DropDeleteResponse: Codable, Sendable, Equatable {
    public let ok: Bool
    public let name: String
}

/// `POST /api/attachments?name=<filename>` (phone→host) response — distinct from drop (host→phone).
public struct AttachmentUploadResponse: Codable, Sendable, Equatable {
    public let ok: Bool
    public let path: String
    public let size: Int
}
