import SwiftUI

/// Visual system aligned with Android/web: per-harness accents over neutral chrome.
enum Theme {
    enum Space {
        static let row: CGFloat = 6
        static let gutter: CGFloat = 14
    }
    enum Radius {
        static let bubble: CGFloat = 17
        static let card: CGFloat = 11
        static let chip: CGFloat = 7
    }
}

/// Harness accent — Claude coral, Grok cyan, Codex green (matches Android Accent).
enum ProviderAccent: String, CaseIterable {
    case claude, grok, codex, neutral

    static func forProvider(_ raw: String?) -> ProviderAccent {
        switch (raw ?? "").trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "claude": return .claude
        case "grok": return .grok
        case "codex": return .codex
        default: return .neutral
        }
    }

    var label: String {
        switch self {
        case .claude: return "Claude"
        case .grok: return "Grok"
        case .codex: return "Codex"
        case .neutral: return "Agent"
        }
    }

    var tint: Color {
        switch self {
        case .claude:
            return Color(light: Color(red: 0.85, green: 0.47, blue: 0.34),
                         dark: Color(red: 0.90, green: 0.55, blue: 0.40))
        case .grok:
            return Color(light: Color(red: 0.0, green: 0.70, blue: 0.85),
                         dark: Color(red: 0.0, green: 0.83, blue: 1.0))
        case .codex:
            return Color(light: Color(red: 0.06, green: 0.64, blue: 0.50),
                         dark: Color(red: 0.19, green: 0.84, blue: 0.55))
        case .neutral:
            return Color(light: Color(red: 0.45, green: 0.50, blue: 0.58),
                         dark: Color(red: 0.60, green: 0.64, blue: 0.70))
        }
    }

    var soft: Color { tint.opacity(0.15) }
}

extension Color {
    /// Default app accent (neutral list chrome). Session UI recolors via ProviderAccent.
    static let brand = ProviderAccent.claude.tint
    static let brandSoft = ProviderAccent.claude.soft
}

extension Color {
    init(light: Color, dark: Color) {
        #if canImport(UIKit)
        self = Color(uiColor: UIColor { traits in
            traits.userInterfaceStyle == .dark ? UIColor(dark) : UIColor(light)
        })
        #else
        self = light
        #endif
    }
}

extension View {
    /// Comfortable reading column; caps width on iPad detail panes.
    func readableColumn(_ max: CGFloat = 680) -> some View {
        HStack(spacing: 0) {
            Spacer(minLength: 0)
            self.frame(maxWidth: max)
            Spacer(minLength: 0)
        }
    }
}

enum ToolGlyph {
    static func symbol(for tool: String) -> String {
        switch tool {
        case "Bash", "BashOutput", "KillShell": return "terminal"
        case "Read": return "doc.text"
        case "Edit", "Write", "NotebookEdit", "MultiEdit": return "pencil.and.outline"
        case "Grep", "Glob", "Search": return "magnifyingglass"
        case "WebFetch", "WebSearch": return "globe"
        case "Task", "Agent": return "sparkles"
        case "TodoWrite": return "checklist"
        default:
            return tool.hasPrefix("mcp__") ? "puzzlepiece.extension" : "wrench.and.screwdriver"
        }
    }
}
