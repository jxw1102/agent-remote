import Foundation

/// `GET /api/sessions/<id>/tui[?ansi=1]`. `attached == false` (with `error` set) when no live TUI
/// exists for this session or it has exited. Plain mode (`ansi` omitted/false) returns
/// ASCII-simplified pane content with escapes stripped — ship this first; `?ansi=1` returns raw
/// SGR-coded text, which needs a real ANSI-to-attributed-text parser to render (deferred).
public struct TuiFrame: Codable, Sendable, Equatable {
    public let sessionId: String
    public let jobId: String
    public let attached: Bool
    public let text: String
    /// A content hash, not a monotonic counter — use only to detect "did the pane change since my
    /// last poll", never to order frames.
    public let seq: Int
    public let cols: Int
    public let rows: Int
    public let cursor: JSONValue?
    public let error: String
    public let ansi: Bool
    public let ts: Double
}

/// `POST /api/sessions/<id>/tui/keys`. `keys`: named tokens (case-insensitive) —
/// escape/esc, enter/return, backspace/bs, delete/del, tab, up/down/left/right, home/end,
/// pageup/pagedown (pgup/pgdn), ctrl+<letter> (c-<letter>), or any single printable character.
/// `text`: typed literally (tmux `send-keys -l`) for pasting a whole line at once. At least one
/// of `keys`/`text` is required.
public struct TuiKeysRequest: Codable, Sendable, Equatable {
    public var keys: [String]?
    public var text: String?

    public init(keys: [String]? = nil, text: String? = nil) {
        self.keys = keys
        self.text = text
    }
}

public struct TuiKeysResponse: Codable, Sendable, Equatable {
    public let ok: Bool
}
