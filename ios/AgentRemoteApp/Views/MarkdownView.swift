import SwiftUI

/// A lightweight block-level Markdown renderer for chat messages. Handles headings, bullet/numbered
/// lists, fenced code blocks, blockquotes, and horizontal rules; inline styling (bold, italic,
/// `code`, links) inside each block reuses `TimelineItemView.markdown`. Not a full CommonMark engine
/// — just the constructs that show up in assistant replies, laid out properly instead of inlined.
struct MarkdownView: View {
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            ForEach(MarkdownBlock.parse(text)) { block in
                block.view
            }
        }
    }
}

/// Renders a GFM table as a bordered grid. Columns size to content and the whole table scrolls
/// horizontally so wide tables don't blow out the chat width.
private struct TableView: View {
    let header: [String]
    let rows: [[String]]

    private var columnCount: Int {
        max(header.count, rows.map(\.count).max() ?? 0)
    }

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            VStack(alignment: .leading, spacing: 0) {
                row(header, isHeader: true)
                ForEach(Array(rows.enumerated()), id: \.offset) { _, cells in
                    Divider()
                    row(cells, isHeader: false)
                }
            }
            .overlay(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .stroke(Color.secondary.opacity(0.25), lineWidth: 1)
            )
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func row(_ cells: [String], isHeader: Bool) -> some View {
        HStack(spacing: 0) {
            ForEach(0..<columnCount, id: \.self) { col in
                let value = col < cells.count ? cells[col] : ""
                Text(TimelineItemView.markdown(value))
                    .font(.caption)
                    .fontWeight(isHeader ? .semibold : .regular)
                    .frame(minWidth: 64, alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                if col < columnCount - 1 {
                    Divider()
                }
            }
        }
        .background(isHeader ? Color.secondary.opacity(0.10) : Color.clear)
    }
}

struct MarkdownBlock: Identifiable {
    enum Kind {
        case heading(level: Int, text: String)
        case paragraph(String)
        case listItem(marker: String, text: String)
        case code(String)
        case quote(String)
        case table(header: [String], rows: [[String]])
        case rule
    }

    let id: Int
    let kind: Kind

    @ViewBuilder
    var view: some View {
        switch kind {
        case .heading(let level, let text):
            Text(TimelineItemView.markdown(text))
                .font(headingFont(level))
                .fontWeight(.semibold)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)

        case .paragraph(let text):
            Text(TimelineItemView.markdown(text))
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)

        case .listItem(let marker, let text):
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(marker)
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
                Text(TimelineItemView.markdown(text))
                    .textSelection(.enabled)
                Spacer(minLength: 0)
            }
            .padding(.leading, 2)

        case .code(let content):
            ScrollView(.horizontal, showsIndicators: false) {
                Text(content)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                    .padding(9)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(.tertiarySystemBackground), in: RoundedRectangle(cornerRadius: 8, style: .continuous))

        case .quote(let text):
            HStack(spacing: 8) {
                RoundedRectangle(cornerRadius: 1).fill(Color.brand.opacity(0.5)).frame(width: 3)
                Text(TimelineItemView.markdown(text))
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                Spacer(minLength: 0)
            }

        case .table(let header, let rows):
            TableView(header: header, rows: rows)

        case .rule:
            Divider()
        }
    }

    private func headingFont(_ level: Int) -> Font {
        switch level {
        case 1: return .title3
        case 2: return .headline
        default: return .subheadline
        }
    }

    // MARK: - Parsing

    static func parse(_ text: String) -> [MarkdownBlock] {
        var blocks: [MarkdownBlock] = []
        var nextId = 0
        func add(_ kind: Kind) { blocks.append(MarkdownBlock(id: nextId, kind: kind)); nextId += 1 }

        let lines = text.components(separatedBy: "\n")
        var i = 0
        while i < lines.count {
            let raw = lines[i]
            let line = raw.trimmingCharacters(in: .whitespaces)

            if line.hasPrefix("```") {                       // fenced code block
                var body: [String] = []
                i += 1
                while i < lines.count, !lines[i].trimmingCharacters(in: .whitespaces).hasPrefix("```") {
                    body.append(lines[i]); i += 1
                }
                i += 1                                        // consume closing fence
                add(.code(body.joined(separator: "\n")))
                continue
            }
            if line.isEmpty { i += 1; continue }
            // GFM table: a "| … |" header row immediately followed by a "|---|:--:|" separator row.
            if isTableRow(line), i + 1 < lines.count, isTableSeparator(lines[i + 1]) {
                let header = tableCells(line)
                var rows: [[String]] = []
                i += 2
                while i < lines.count, isTableRow(lines[i].trimmingCharacters(in: .whitespaces)) {
                    rows.append(tableCells(lines[i])); i += 1
                }
                add(.table(header: header, rows: rows))
                continue
            }
            if let heading = heading(line) { add(.heading(level: heading.0, text: heading.1)); i += 1; continue }
            if line == "---" || line == "***" || line == "___" { add(.rule); i += 1; continue }
            if let item = listItem(line) { add(.listItem(marker: item.0, text: item.1)); i += 1; continue }
            if line.hasPrefix(">") {
                add(.quote(String(line.dropFirst()).trimmingCharacters(in: .whitespaces))); i += 1; continue
            }

            // Paragraph: gather consecutive plain lines.
            var para: [String] = [raw]
            i += 1
            while i < lines.count {
                let t = lines[i].trimmingCharacters(in: .whitespaces)
                if t.isEmpty || t.hasPrefix("```") || t.hasPrefix(">")
                    || t == "---" || t == "***" || t == "___"
                    || heading(t) != nil || listItem(t) != nil
                    || (isTableRow(t) && i + 1 < lines.count && isTableSeparator(lines[i + 1])) { break }
                para.append(lines[i]); i += 1
            }
            add(.paragraph(para.joined(separator: "\n")))
        }
        return blocks
    }

    private static func heading(_ line: String) -> (Int, String)? {
        guard line.hasPrefix("#") else { return nil }
        let hashes = line.prefix { $0 == "#" }
        guard hashes.count <= 6, line.dropFirst(hashes.count).first == " " else { return nil }
        return (hashes.count, String(line.dropFirst(hashes.count)).trimmingCharacters(in: .whitespaces))
    }

    // MARK: - Tables (GFM)

    /// A pipe-delimited row: contains a `|` and, once trimmed, isn't a lone rule.
    private static func isTableRow(_ line: String) -> Bool {
        let t = line.trimmingCharacters(in: .whitespaces)
        return t.contains("|") && t.count > 1
    }

    /// The delimiter row under a table header: cells made only of dashes/colons, e.g. `|:--|--:|`.
    private static func isTableSeparator(_ line: String) -> Bool {
        let t = line.trimmingCharacters(in: .whitespaces)
        guard t.contains("|"), t.contains("-") else { return false }
        return tableCells(t).allSatisfy { cell in
            let c = cell.trimmingCharacters(in: .whitespaces)
            return !c.isEmpty && c.allSatisfy { $0 == "-" || $0 == ":" }
        }
    }

    /// Split a `| a | b |` row into its trimmed cells (dropping the leading/trailing empties).
    private static func tableCells(_ line: String) -> [String] {
        var t = line.trimmingCharacters(in: .whitespaces)
        if t.hasPrefix("|") { t.removeFirst() }
        if t.hasSuffix("|") { t.removeLast() }
        return t.components(separatedBy: "|").map { $0.trimmingCharacters(in: .whitespaces) }
    }

    private static func listItem(_ line: String) -> (String, String)? {
        for bullet in ["- ", "* ", "+ "] where line.hasPrefix(bullet) {
            return ("•", String(line.dropFirst(bullet.count)))
        }
        // Ordered: "1. ", "2) " …
        let prefix = line.prefix { $0.isNumber }
        if !prefix.isEmpty, let sep = line.dropFirst(prefix.count).first, sep == "." || sep == ")" {
            let rest = line.dropFirst(prefix.count + 1)
            if rest.first == " " {
                return ("\(prefix).", String(rest).trimmingCharacters(in: .whitespaces))
            }
        }
        return nil
    }
}
