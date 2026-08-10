import AgentRemoteKit
import SwiftUI

/// AskUserQuestion panel (Android QuestionSheet parity).
struct QuestionSheetView: View {
    let prompt: PendingQuestionUI
    var onRespond: (_ answers: [[String]], _ notes: [String]?, _ cancel: Bool) -> Void

    @State private var selections: [[String]] = []
    @State private var notes: [String] = []

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    ForEach(Array(prompt.questions.enumerated()), id: \.offset) { index, item in
                        questionBlock(index: index, item: item)
                    }
                }
                .padding()
            }
            .navigationTitle(prompt.questions.first?.header.isEmpty == false
                             ? prompt.questions[0].header
                             : "Question")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Dismiss") { onRespond([], nil, true) }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Submit") {
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
            if !item.header.isEmpty && index > 0 {
                Text(item.header).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            }
            Text(item.question.isEmpty ? "Choose an option" : item.question)
                .font(.body.weight(.medium))

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

            if !item.noteFor.isEmpty || item.options.isEmpty {
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
