import SwiftUI
import UIKit

/// Render a tmux pane capture that may contain ANSI SGR sequences
/// (`capture-pane -e` / daemon `?ansi=1`). Port of Android `AnsiText.kt`.
///
/// Uses `UITextView` (not `Text(AttributedString)`) so per-run `foregroundColor` /
/// `backgroundColor` from the parser actually show up — SwiftUI `Text` + a view-level
/// `.font(...)` was flattening colours to the default on iOS 18+/26.
struct AnsiText: View {
    let text: String
    var defaultColor: Color = Color(red: 0.82, green: 0.83, blue: 0.86)
    /// Point size for monospaced runs (Live TUI wants readable CLI density).
    var fontSize: CGFloat = 13

    var body: some View {
        AnsiTextView(
            attributed: ansiToNSAttributed(
                text,
                defaultColor: UIColor(defaultColor),
                fontSize: fontSize
            )
        )
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

// MARK: - UIKit host (preserves NSAttributedString colours)

private struct AnsiTextView: UIViewRepresentable {
    let attributed: NSAttributedString

    func makeUIView(context: Context) -> UITextView {
        let tv = UITextView()
        tv.isEditable = false
        tv.isSelectable = true
        tv.isScrollEnabled = false // outer SwiftUI ScrollView owns scrolling
        tv.backgroundColor = .clear
        tv.textContainerInset = .zero
        tv.textContainer.lineFragmentPadding = 0
        tv.adjustsFontForContentSizeCategory = false
        tv.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        tv.setContentHuggingPriority(.defaultLow, for: .horizontal)
        return tv
    }

    func updateUIView(_ uiView: UITextView, context: Context) {
        if uiView.attributedText != attributed {
            uiView.attributedText = attributed
        }
    }

    func sizeThatFits(_ proposal: ProposedViewSize, uiView: UITextView, context: Context) -> CGSize? {
        let width = proposal.width ?? UIScreen.main.bounds.width
        let target = CGSize(width: max(width, 1), height: .greatestFiniteMagnitude)
        let fitted = uiView.sizeThatFits(target)
        return CGSize(width: width, height: max(fitted.height, 1))
    }
}

// MARK: - Palette (xterm-ish, matches Android Live TUI)

private let ansiFG: [UIColor] = [
    UIColor(red: 0.05, green: 0.05, blue: 0.05, alpha: 1),
    UIColor(red: 0.80, green: 0.19, blue: 0.19, alpha: 1),
    UIColor(red: 0.05, green: 0.74, blue: 0.47, alpha: 1),
    UIColor(red: 0.90, green: 0.90, blue: 0.06, alpha: 1),
    UIColor(red: 0.14, green: 0.45, blue: 0.78, alpha: 1),
    UIColor(red: 0.74, green: 0.25, blue: 0.74, alpha: 1),
    UIColor(red: 0.07, green: 0.66, blue: 0.80, alpha: 1),
    UIColor(red: 0.90, green: 0.90, blue: 0.90, alpha: 1),
]

private let ansiBright: [UIColor] = [
    UIColor(red: 0.40, green: 0.40, blue: 0.40, alpha: 1),
    UIColor(red: 0.95, green: 0.30, blue: 0.30, alpha: 1),
    UIColor(red: 0.14, green: 0.82, blue: 0.55, alpha: 1),
    UIColor(red: 0.96, green: 0.96, blue: 0.26, alpha: 1),
    UIColor(red: 0.23, green: 0.56, blue: 0.92, alpha: 1),
    UIColor(red: 0.84, green: 0.44, blue: 0.84, alpha: 1),
    UIColor(red: 0.16, green: 0.72, blue: 0.86, alpha: 1),
    UIColor(red: 0.90, green: 0.90, blue: 0.90, alpha: 1),
]

private func ansi256(_ n: Int) -> UIColor {
    if n < 0 { return .clear }
    if n < 8 { return ansiFG[n] }
    if n < 16 { return ansiBright[n - 8] }
    if n < 232 {
        let v = n - 16
        func c(_ x: Int) -> CGFloat { x == 0 ? 0 : CGFloat(55 + x * 40) / 255 }
        return UIColor(red: c(v / 36), green: c((v % 36) / 6), blue: c(v % 6), alpha: 1)
    }
    let gray = CGFloat(8 + (n - 232) * 10) / 255
    return UIColor(red: gray, green: gray, blue: gray, alpha: 1)
}

private func clampByte(_ n: Int) -> CGFloat {
    CGFloat(min(255, max(0, n))) / 255
}

// MARK: - Parser → NSAttributedString

func ansiToNSAttributed(
    _ raw: String,
    defaultColor: UIColor,
    fontSize: CGFloat = 13
) -> NSAttributedString {
    if raw.isEmpty { return NSAttributedString(string: "") }

    var s = raw
    // Strip OSC sequences (title, etc.)
    if let osc = try? NSRegularExpression(
        pattern: "\u{001B}\\][^\u{0007}\u{001B}]*(?:\u{0007}|\u{001B}\\\\)"
    ) {
        s = osc.stringByReplacingMatches(
            in: s, range: NSRange(location: 0, length: (s as NSString).length), withTemplate: ""
        )
    }

    let mono = UIFont.monospacedSystemFont(ofSize: fontSize, weight: .regular)
    let monoBold = UIFont.monospacedSystemFont(ofSize: fontSize, weight: .semibold)
    let monoItalic: UIFont = {
        if let desc = mono.fontDescriptor.withSymbolicTraits(.traitItalic) {
            return UIFont(descriptor: desc, size: mono.pointSize)
        }
        return mono
    }()
    let monoBoldItalic: UIFont = {
        if let desc = mono.fontDescriptor.withSymbolicTraits([.traitItalic, .traitBold]) {
            return UIFont(descriptor: desc, size: mono.pointSize)
        }
        return monoBold
    }()

    guard s.contains("\u{001B}") else {
        return NSAttributedString(string: s, attributes: [
            .font: mono,
            .foregroundColor: defaultColor,
        ])
    }

    var bold = false
    var dim = false
    var italic = false
    var underline = false
    var fg: UIColor?
    var bg: UIColor?

    func attrs() -> [NSAttributedString.Key: Any] {
        var a: [NSAttributedString.Key: Any] = [:]
        let color: UIColor
        if let fg { color = fg }
        else if dim { color = defaultColor.withAlphaComponent(0.65) }
        else { color = defaultColor }
        a[.foregroundColor] = color
        if let bg { a[.backgroundColor] = bg }
        switch (bold, italic) {
        case (true, true): a[.font] = monoBoldItalic
        case (true, false): a[.font] = monoBold
        case (false, true): a[.font] = monoItalic
        default: a[.font] = mono
        }
        if underline {
            a[.underlineStyle] = NSUnderlineStyle.single.rawValue
        }
        // Slightly tighter terminal density
        a[.paragraphStyle] = {
            let p = NSMutableParagraphStyle()
            p.lineBreakMode = .byCharWrapping
            p.lineSpacing = 1
            return p
        }()
        return a
    }

    let out = NSMutableAttributedString()
    // Match SGR; also consume other CSI so cursor/erase junk never appears as glyphs.
    guard let re = try? NSRegularExpression(pattern: "\u{001B}\\[([0-9;?]*)([A-Za-z])") else {
        return NSAttributedString(string: s, attributes: attrs())
    }
    let ns = s as NSString
    let full = NSRange(location: 0, length: ns.length)
    var last = 0
    re.enumerateMatches(in: s, range: full) { match, _, _ in
        guard let match else { return }
        if match.range.location > last {
            let chunk = ns.substring(with: NSRange(location: last, length: match.range.location - last))
            if !chunk.isEmpty {
                out.append(NSAttributedString(string: chunk, attributes: attrs()))
            }
        }
        last = match.range.location + match.range.length
        let finalChar = ns.substring(with: match.range(at: 2))
        // Only SGR ('m') changes paint state; other CSI is discarded.
        guard finalChar == "m" else { return }
        let paramsRange = match.range(at: 1)
        let paramStr = paramsRange.location != NSNotFound
            ? ns.substring(with: paramsRange) : "0"
        let parts = (paramStr.isEmpty ? ["0"] : paramStr.split(separator: ";").map(String.init))
            .map { Int($0) ?? 0 }
        var p = 0
        while p < parts.count {
            let code = parts[p]
            switch code {
            case 0:
                bold = false; dim = false; italic = false; underline = false
                fg = nil; bg = nil
            case 1: bold = true
            case 2: dim = true
            case 3: italic = true
            case 4: underline = true
            case 22: bold = false; dim = false
            case 23: italic = false
            case 24: underline = false
            case 39: fg = nil
            case 49: bg = nil
            case 30...37: fg = ansiFG[code - 30]
            case 90...97: fg = ansiBright[code - 90]
            case 40...47: bg = ansiFG[code - 40]
            case 100...107: bg = ansiBright[code - 100]
            case 38, 48:
                let isFg = code == 38
                let mode = p + 1 < parts.count ? parts[p + 1] : -1
                if mode == 5, p + 2 < parts.count {
                    let c = ansi256(parts[p + 2])
                    if isFg { fg = c } else { bg = c }
                    p += 2
                } else if mode == 2, p + 4 < parts.count {
                    let c = UIColor(
                        red: clampByte(parts[p + 2]),
                        green: clampByte(parts[p + 3]),
                        blue: clampByte(parts[p + 4]),
                        alpha: 1
                    )
                    if isFg { fg = c } else { bg = c }
                    p += 4
                }
            default:
                break
            }
            p += 1
        }
    }
    if last < ns.length {
        let tail = ns.substring(from: last)
        if !tail.isEmpty {
            out.append(NSAttributedString(string: tail, attributes: attrs()))
        }
    }
    return out
}
