import SwiftUI

struct TimelineItemView: View {
    let item: TimelineItem

    /// Render inline markdown (bold, italic, `code`, links) while preserving the message's own line
    /// breaks. Parsing is done one line at a time so an inline span — especially an unbalanced or
    /// stray `~~` — can only ever affect its own line, never strike through the rest of the message.
    static func markdown(_ text: String) -> AttributedString {
        let options = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        var result = AttributedString()
        let lines = text.components(separatedBy: "\n")
        for (index, line) in lines.enumerated() {
            if index > 0 { result += AttributedString("\n") }
            result += (try? AttributedString(markdown: line, options: options)) ?? AttributedString(line)
        }
        return result
    }

    var body: some View {
        switch item {
        case .userText(_, let text):
            if let command = SlashCommand.parse(text) {
                SlashCommandView(command: command)
            } else {
                HStack {
                    Spacer(minLength: 44)
                    Text(Self.markdown(text))
                        .font(.subheadline)
                        .textSelection(.enabled)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .background(Color.brandSoft, in: RoundedRectangle(cornerRadius: Theme.Radius.bubble, style: .continuous))
                }
                .padding(.top, 4)
            }

        case .assistantText(_, let text):
            MarkdownView(text: text)
                .font(.subheadline)
                .lineSpacing(2.5)
                .frame(maxWidth: .infinity, alignment: .leading)

        case .toolCall(_, let name, let detail):
            ToolCallRow(name: name, detail: detail)

        case .permissionRequest(_, let toolName, let detail, let resolution):
            PermissionRequestRow(toolName: toolName, detail: detail, resolution: resolution)

        case .turnResult(_, let summary, let isError):
            if isError {
                Label(summary, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(.orange)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }

        case .systemNotice(_, let text):
            Text(text)
                .font(.caption)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.vertical, 2)
        }
    }
}

/// A single, dense tool-call line: glyph · name · pre-formatted detail (the daemon's own clipped
/// display string — there's no structured input/result to expand into, unlike a richer protocol).
struct ToolCallRow: View {
    let name: String
    let detail: String

    var body: some View {
        HStack(spacing: 7) {
            Image(systemName: ToolGlyph.symbol(for: name))
                .font(.caption2)
                .foregroundStyle(Color.brand)
                .frame(width: 15)
            Text(name)
                .font(.caption.weight(.semibold))
                .fixedSize()
            if !detail.isEmpty {
                Text(detail)
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            Spacer(minLength: 2)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(
            RoundedRectangle(cornerRadius: Theme.Radius.chip, style: .continuous)
                .fill(Color(.secondarySystemBackground).opacity(0.55))
        )
    }
}

/// A permission ask, inline in the timeline: pending (spinner) until `permission_resolved` arrives,
/// then a check/cross. The actual approve/deny happens in the sheet driven by the chat's
/// `pendingPermission`; this is just the persistent record of what was asked and how it resolved.
struct PermissionRequestRow: View {
    let toolName: String
    let detail: String
    let resolution: PermissionResolution?

    var body: some View {
        HStack(spacing: 7) {
            Image(systemName: "lock.shield")
                .font(.caption2)
                .foregroundStyle(iconColor)
                .frame(width: 15)
            Text(toolName)
                .font(.caption.weight(.semibold))
                .fixedSize()
            if !detail.isEmpty {
                Text(detail)
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            Spacer(minLength: 2)
            statusView
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(
            RoundedRectangle(cornerRadius: Theme.Radius.chip, style: .continuous)
                .fill(Color(.secondarySystemBackground).opacity(0.55))
        )
    }

    private var iconColor: Color {
        switch resolution {
        case .allowed: return .green
        case .denied: return .red
        case nil: return .orange
        }
    }

    @ViewBuilder
    private var statusView: some View {
        switch resolution {
        case .allowed:
            Image(systemName: "checkmark").font(.system(size: 10, weight: .bold)).foregroundStyle(.green)
        case .denied(let reason):
            Image(systemName: reason == "timeout" ? "clock.badge.xmark" : "xmark")
                .font(.system(size: 10, weight: .bold))
                .foregroundStyle(.red)
        case nil:
            ProgressView().controlSize(.mini)
        }
    }
}

/// Claude Code stores a slash-command turn and its local output as XML-tagged user messages
/// (`<command-name>`, `<command-args>`, `<local-command-stdout>`). Rendered raw they're noise; this
/// parses them into a clean invocation chip or an output line.
enum SlashCommand: Equatable {
    case invocation(name: String, args: String)
    case output(text: String, isError: Bool)

    static func parse(_ raw: String) -> SlashCommand? {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if let name = tag(text, "command-name") {
            return .invocation(name: name, args: tag(text, "command-args") ?? "")
        }
        if let out = tag(text, "local-command-stdout") {
            return .output(text: out, isError: false)
        }
        if let err = tag(text, "local-command-stderr") {
            return .output(text: err, isError: true)
        }
        // Raw slash/bash commands the user typed in the input box.
        if text.hasPrefix("!"), text.count > 1 {
            return .invocation(name: "!", args: String(text.dropFirst()).trimmingCharacters(in: .whitespaces))
        }
        if text.hasPrefix("/"), let second = text.dropFirst().first, second.isLetter {
            let parts = text.dropFirst().split(separator: " ", maxSplits: 1)
            return .invocation(name: "/" + parts[0], args: parts.count > 1 ? String(parts[1]) : "")
        }
        return nil
    }

    /// Extract the inner text of `<tag>…</tag>` if present.
    private static func tag(_ s: String, _ name: String) -> String? {
        guard let open = s.range(of: "<\(name)>"),
              let close = s.range(of: "</\(name)>"),
              open.upperBound <= close.lowerBound
        else { return nil }
        return String(s[open.upperBound..<close.lowerBound]).trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

private struct SlashCommandView: View {
    let command: SlashCommand

    var body: some View {
        switch command {
        case .invocation(let name, let args):
            HStack {
                Spacer(minLength: 44)
                (
                    Text("› ").foregroundColor(Color.brand).fontWeight(.bold)
                    + Text(name).fontWeight(.semibold)
                    + Text(args.isEmpty ? "" : " \(args)").foregroundColor(.secondary)
                )
                .font(.system(.subheadline, design: .monospaced))
                .multilineTextAlignment(.leading)
                .textSelection(.enabled)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(Color.brandSoft, in: RoundedRectangle(cornerRadius: Theme.Radius.bubble, style: .continuous))
            }
            .padding(.top, 4)

        case .output(let text, let isError):
            HStack(alignment: .top, spacing: 6) {
                Image(systemName: "arrow.turn.down.right")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .padding(.top, 2)
                Text(text)
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(isError ? .red : .secondary)
                    .textSelection(.enabled)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}
