import SwiftUI

/// The app's visual system. One warm accent (Claude coral) over neutral system surfaces, a small set
/// of spacing/radius constants, and tool iconography — kept in one place so every screen stays coherent.
enum Theme {
    enum Space {
        static let row: CGFloat = 6      // between timeline items
        static let gutter: CGFloat = 14  // screen horizontal inset
    }
    enum Radius {
        static let bubble: CGFloat = 17
        static let card: CGFloat = 11
        static let chip: CGFloat = 7
    }
}

extension Color {
    /// Warm terracotta accent — adapts a touch lighter in dark mode so it stays legible on black.
    static let brand = Color(
        light: Color(red: 0.76, green: 0.38, blue: 0.26),
        dark: Color(red: 0.88, green: 0.52, blue: 0.40)
    )
    /// Low-tint fills for user bubbles, selected chips, meters.
    static let brandSoft = Color(
        light: Color(red: 0.76, green: 0.38, blue: 0.26).opacity(0.13),
        dark: Color(red: 0.88, green: 0.52, blue: 0.40).opacity(0.20)
    )
}

extension Color {
    /// Builds a color that resolves differently in light vs dark mode.
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
    /// Constrain content to a comfortable reading column and center it. A no-op on a phone (the
    /// screen is already narrower than `max`); on iPad it stops text and bubbles from stretching
    /// across the full width of the detail pane. Uses flanking spacers rather than chained frames,
    /// which reliably caps a `LazyVStack` inside a `ScrollView` (chained `maxWidth` frames don't).
    func readableColumn(_ max: CGFloat = 680) -> some View {
        HStack(spacing: 0) {
            Spacer(minLength: 0)
            self.frame(maxWidth: max)
            Spacer(minLength: 0)
        }
    }
}

enum ToolGlyph {
    /// Maps a Claude Code tool name to an SF Symbol so a collapsed tool row reads at a glance.
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
