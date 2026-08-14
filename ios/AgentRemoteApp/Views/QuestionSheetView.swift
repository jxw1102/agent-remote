import AgentRemoteKit
import SwiftUI

/// AskUserQuestion panel (Android QuestionSheet parity). Cancel presses Esc on the host panel;
/// swiping the sheet away merely parks the gate (the chat shows an Answer banner).
struct QuestionSheetView: View {
    let prompt: PendingQuestionUI
    var accent: ProviderAccent = .neutral
    var onRespond: (_ answers: [[String]], _ notes: [String]?, _ cancel: Bool) -> Void

    @State private var selections: [[String]] = []
    @State private var notes: [String] = []

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    Text("The turn is paused until you answer or cancel.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    ForEach(Array(prompt.questions.enumerated()), id: \.offset) { index, item in
                        questionBlock(index: index, item: item)
                    }
                }
                .padding()
            }
            .navigationTitle(prompt.questions.count > 1
                             ? "The agent is asking \(prompt.questions.count) things"
                             : "The agent is asking")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel", role: .destructive) { onRespond([], nil, true) }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Send answer") {
                        onRespond(selections, notes.contains(where: { !$0.isEmpty }) ? notes : nil, false)
                    }
                    .disabled(!canSubmit)
                }
            }
            .onAppear {
                selections = prompt.questions.map { _ in [] }
                notes = prompt.questions.map { _ in "" }
            }
        }
    }

    private var canSubmit: Bool {
        zip(prompt.questions, selections).allSatisfy { item, sel in
            if item.options.isEmpty { return true }
            return !sel.isEmpty
        }
    }

    @ViewBuilder
    private func questionBlock(index: Int, item: QuestionItem) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            if !item.header.isEmpty {
                Text(item.header)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(accent.tint)
            }
            // Markdown, not plain text — grok routes whole plan documents through this channel.
            if item.question.isEmpty {
                Text("Choose an option").font(.body.weight(.medium))
            } else {
                MarkdownView(text: item.question)
            }
            if item.multiSelect && !item.options.isEmpty {
                Text("Pick as many as apply")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }

            ForEach(item.options, id: \.label) { option in
                let selected = selections.indices.contains(index) && selections[index].contains(option.label)
                Button {
                    toggle(option.label, at: index, multi: item.multiSelect)
                } label: {
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: selected
                              ? (item.multiSelect ? "checkmark.square.fill" : "checkmark.circle.fill")
                              : (item.multiSelect ? "square" : "circle"))
                            .foregroundStyle(selected ? Color.accentColor : Color.secondary)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(option.label).foregroundStyle(.primary)
                            if !option.description.isEmpty {
                                Text(option.description).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        Spacer()
                    }
                    .padding(10)
                    .background(selected ? Color.accentColor.opacity(0.12) : Color.secondary.opacity(0.08),
                                in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                }
                .buttonStyle(.plain)
            }

            // Free text appears when the picked option asks for it (note_for), or when the
            // question has no options at all — same rule as Android.
            let noteSelected = !item.noteFor.isEmpty
                && selections.indices.contains(index)
                && selections[index].contains(item.noteFor)
            if noteSelected || item.options.isEmpty {
                TextField(item.noteHint.isEmpty ? "Optional note" : item.noteHint, text: noteBinding(at: index), axis: .vertical)
                    .lineLimit(1...4)
                    .textFieldStyle(.roundedBorder)
            }
        }
    }

    private func toggle(_ label: String, at index: Int, multi: Bool) {
        guard selections.indices.contains(index) else { return }
        if multi {
            if let i = selections[index].firstIndex(of: label) {
                selections[index].remove(at: i)
            } else {
                selections[index].append(label)
            }
        } else {
            selections[index] = [label]
        }
    }

    private func noteBinding(at index: Int) -> Binding<String> {
        Binding(
            get: { notes.indices.contains(index) ? notes[index] : "" },
            set: { value in
                while notes.count <= index { notes.append("") }
                notes[index] = value
            }
        )
    }
}
