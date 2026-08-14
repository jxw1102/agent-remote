import Foundation

/// One entry in the host→phone drop folder (`GET /api/drop`) — a file or, on daemon ≥ 2.6, a
/// folder (`type == "dir"`, with `entries` and a `partial` flag when the walk was truncated).
public struct DropFile: Codable, Sendable, Equatable, Identifiable {
    public let name: String
    /// For a folder: total weight of its contents (the zip that downloads it will be smaller).
    public let size: Int
    public let mtime: FlexibleDate
    /// "file" | "dir". Older daemons omit it — treat as a file.
    public let type: String
    /// Folder-only: how many entries it holds.
    public let entries: Int
    /// Folder-only: true when the size/entry walk hit its cap and the numbers are a floor.
    public let partial: Bool

    public var id: String { name }
    public var isDirectory: Bool { type == "dir" }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        size = try c.decodeIfPresent(Int.self, forKey: .size) ?? 0
        mtime = try c.decodeIfPresent(FlexibleDate.self, forKey: .mtime) ?? FlexibleDate(.distantPast)
        type = try c.decodeIfPresent(String.self, forKey: .type) ?? "file"
        entries = try c.decodeIfPresent(Int.self, forKey: .entries) ?? 0
        partial = try c.decodeIfPresent(Bool.self, forKey: .partial) ?? false
    }

    private enum CodingKeys: String, CodingKey { case name, size, mtime, type, entries, partial }
}

public struct DropListResponse: Codable, Sendable, Equatable {
    public let path: String
    public let files: [DropFile]

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        path = try c.decodeIfPresent(String.self, forKey: .path) ?? ""
        files = try c.decodeIfPresent([DropFile].self, forKey: .files) ?? []
    }

    private enum CodingKeys: String, CodingKey { case path, files }
}

/// One downloaded drop entry: its bytes plus the name the daemon served it as — a folder arrives
/// zipped, so its `name` gains ".zip" over the entry name that was requested.
public struct DropDownload: Sendable {
    public let name: String
    public let data: Data
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

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = try c.decodeIfPresent(Bool.self, forKey: .ok) ?? true
        path = try c.decodeIfPresent(String.self, forKey: .path) ?? ""
        size = try c.decodeIfPresent(Int.self, forKey: .size) ?? 0
    }

    private enum CodingKeys: String, CodingKey { case ok, path, size }
}
