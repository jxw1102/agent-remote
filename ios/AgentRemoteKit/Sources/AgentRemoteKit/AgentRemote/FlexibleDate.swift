import Foundation

/// The daemon is inconsistent about timestamp shape across endpoints — project lists use float
/// unix seconds, most session rows use ISO-8601 strings (sometimes with fractional seconds,
/// sometimes without) — confirmed inconsistency, not a client bug to work around narrowly.
/// Decodes either shape into a `Date`; always encodes as epoch seconds.
public struct FlexibleDate: Codable, Sendable, Equatable {
    public let date: Date

    public init(_ date: Date) {
        self.date = date
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let seconds = try? container.decode(Double.self) {
            date = Date(timeIntervalSince1970: seconds)
            return
        }
        if let intSec = try? container.decode(Int.self) {
            date = Date(timeIntervalSince1970: TimeInterval(intSec))
            return
        }
        if let string = try? container.decode(String.self) {
            if let parsed = Self.parseISO8601(string) {
                date = parsed
                return
            }
            // Never fail the whole session list for one odd timestamp.
            date = .distantPast
            return
        }
        date = .distantPast
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(date.timeIntervalSince1970)
    }

    private static func parseISO8601(_ string: String) -> Date? {
        let withFractional = ISO8601DateFormatter()
        withFractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = withFractional.date(from: string) { return date }
        let plain = ISO8601DateFormatter()
        plain.formatOptions = [.withInternetDateTime]
        return plain.date(from: string)
    }
}
