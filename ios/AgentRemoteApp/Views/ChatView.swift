import AgentRemoteKit
import SwiftUI
import UniformTypeIdentifiers

struct ChatView: View {
    @ObservedObject var viewModel: ChatViewModel
    @EnvironmentObject private var appModel: AppModel
    @FocusState private var inputFocused: Bool
    @State private var didInitialScroll = false
    @State private var showLiveTui = false
    @State private var showQueue = false
    @State private var showAttachmentPicker = false
    @State private var rewindTargetId: String?

    private var accent: ProviderAccent { viewModel.accent }

    /// The status-stream frame for this chat's turn (nil when idle or stream down).
    private var liveStatus: ActiveJobStatus? { appModel.activeStatus(for: viewModel) }

    var body: some View {
        VStack(spacing: 0) {
            if let status = liveStatus, viewModel.isBusy {
                StatusBanner(status: status, accent: accent)
            }
            if viewModel.parkedQuestion != nil {
                answerBanner(text: "A question is waiting for you") { viewModel.reopenQuestion() }
            } else if viewModel.parkedPermission != nil {
                answerBanner(text: "A permission request is waiting") { viewModel.reopenPermission() }
            }
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: Theme.Space.row) {
                        ForEach(viewModel.items) { item in
                            TimelineItemView(item: item, accent: accent)
                                .id(item.id)
                                .contextMenu { contextMenu(for: item) }
                        }
                        if viewModel.isBusy {
                            HStack(spacing: 7) {
                                ProgressView().controlSize(.small)
                                Text(busyLabel).font(.caption).foregroundStyle(.secondary)
                            }
                            .padding(.vertical, 2)
                            .id("busy-indicator")
                        }
                    }
                    .readableColumn()
                    .padding(.horizontal, Theme.Space.gutter)
                    .padding(.vertical, 10)
                }
                .defaultScrollAnchor(.bottom)
                .onChange(of: viewModel.items) { _, _ in scrollToEnd(proxy) }
                .onChange(of: viewModel.isBusy) { _, _ in scrollToEnd(proxy) }
                .overlay { statusOverlay }
            }

            if !viewModel.commandSuggestions.isEmpty {
                commandSuggestions
            }
            if !viewModel.attachments.isEmpty {
                attachmentChips
            }
            if !viewModel.isBusy {
                optionsBar
            }
            if let line = viewModel.statusLine {
                statusLineBar(line)
            }
            inputBar
        }
        .background(Color(.systemGroupedBackground))
        .navigationBarTitleDisplayMode(.inline)
        .tint(accent.tint)
        .toolbar {
            ToolbarItem(placement: .principal) {
                VStack(spacing: 1) {
                    Text(viewModel.displayTitle)
                        .font(.headline)
                        .lineLimit(1)
                    Text(viewModel.subtitle)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            ToolbarItemGroup(placement: .primaryAction) {
                if !viewModel.queued.isEmpty {
                    Button {
                        showQueue = true
                    } label: {
                        Image(systemName: "text.badge.plus")
                            .overlay(alignment: .topTrailing) {
                                Text("\(viewModel.queued.count)")
                                    .font(.system(size: 9, weight: .bold))
                                    .foregroundStyle(.white)
                                    .padding(3)
                                    .background(accent.tint, in: Circle())
                                    .offset(x: 8, y: -8)
                            }
                    }
                }
                if viewModel.liveTuiEnabled && !viewModel.sessionId.isEmpty {
                    Button {
                        showLiveTui = true
                    } label: {
                        Image(systemName: "terminal")
                    }
                }
                Menu {
                    Button {
                        viewModel.reloadHistory()
                    } label: {
                        Label("Refresh", systemImage: "arrow.clockwise")
                    }
                    .disabled(viewModel.isBusy || viewModel.sessionId.isEmpty)
                    Divider()
                    Button {
                        UIPasteboard.general.string = viewModel.plainTranscript
                    } label: {
                        Label("Copy transcript", systemImage: "doc.on.doc")
                    }
                    Button {
                        UIPasteboard.general.string = viewModel.sessionId
                    } label: {
                        Label("Copy session id", systemImage: "number")
                    }
                    .disabled(viewModel.sessionId.isEmpty)
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
            }
        }
        // Attach to a turn started elsewhere (desktop TUI, queued chain, or an orphaned
        // AskUserQuestion gate the daemon keeps alive) as soon as the status stream shows it.
        .onAppear { attachIfNeeded() }
        .onChange(of: liveStatus?.jobId) { _, _ in attachIfNeeded() }
        .sheet(item: Binding(
            get: { viewModel.pendingPermission },
            // Swipe-dismiss parks the gate (banner takes over) — it must never silently deny.
            set: { newValue in if newValue == nil { viewModel.parkPermission() } }
        )) { prompt in
            PermissionSheetView(prompt: prompt, agentLabel: accent.label) { approved in
                viewModel.respondToPermission(approved: approved)
            }
            .presentationDetents([.medium])
        }
        .sheet(item: Binding(
            get: { viewModel.pendingQuestion },
            set: { newValue in if newValue == nil { viewModel.parkQuestion() } }
        )) { question in
            QuestionSheetView(prompt: question, accent: accent) { answers, notes, cancel in
                viewModel.respondToQuestion(answers: answers, notes: notes, cancel: cancel)
            }
            .presentationDetents([.medium, .large])
        }
        .sheet(isPresented: $showLiveTui) {
            LiveTuiView(client: viewModel.liveDaemonClient, sessionId: viewModel.sessionId, title: viewModel.displayTitle)
        }
        .sheet(isPresented: $showQueue) {
            queueSheet
        }
        .fileImporter(isPresented: $showAttachmentPicker, allowedContentTypes: [.item]) { result in
            handlePickedFile(result)
        }
        .confirmationDialog(
            "Rewind the conversation?",
            isPresented: Binding(get: { rewindTargetId != nil }, set: { if !$0 { rewindTargetId = nil } }),
            titleVisibility: .visible
        ) {
            Button("Rewind", role: .destructive) {
                if let id = rewindTargetId, let count = viewModel.rewindDropCount(itemId: id) {
                    viewModel.rewind(dropping: count)
                }
                rewindTargetId = nil
            }
            Button("Cancel", role: .cancel) { rewindTargetId = nil }
        } message: {
            if let id = rewindTargetId, let count = viewModel.rewindDropCount(itemId: id) {
                Text(count == 1
                     ? "This drops your last message and everything after it. This cannot be undone. Conversation only — file changes on the host are not reverted."
                     : "This drops your last \(count) messages and everything after them. This cannot be undone. Conversation only — file changes on the host are not reverted.")
            }
        }
    }

    private var busyLabel: String {
        if let status = liveStatus {
            let phase = (status.phase ?? "").trimmingCharacters(in: .whitespaces)
            if !phase.isEmpty { return phase.capitalized + "…" }
        }
        return "Working…"
    }

    private func attachIfNeeded() {
        guard let status = liveStatus else { return }
        viewModel.attach(to: status)
    }

    @ViewBuilder
    private func contextMenu(for item: TimelineItem) -> some View {
        switch item {
        case .userText(let id, let text), .assistantText(let id, let text):
            Button {
                UIPasteboard.general.string = text
            } label: {
                Label("Copy message", systemImage: "doc.on.doc")
            }
            if case .userText = item, viewModel.canRewind, !viewModel.sessionId.isEmpty,
               !viewModel.isBusy, let count = viewModel.rewindDropCount(itemId: id) {
                Button(role: .destructive) {
                    rewindTargetId = id
                } label: {
                    Label(count == 1 ? "Rewind to here (undo your last message)"
                                     : "Rewind to here (undo the last \(count))",
                          systemImage: "arrow.uturn.backward")
                }
            }
        default:
            EmptyView()
        }
    }

    private func answerBanner(text: String, action: @escaping () -> Void) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "hand.raised.fill").font(.caption)
            Text(text).font(.caption.weight(.medium))
            Spacer()
            Button("Answer", action: action)
                .font(.caption.weight(.semibold))
        }
        .padding(.horizontal, Theme.Space.gutter)
        .padding(.vertical, 8)
        .background(Color.orange.opacity(0.15))
        .foregroundStyle(.orange)
    }

    private var queueSheet: some View {
        NavigationStack {
            List {
                Section {
                    ForEach(viewModel.queued) { entry in
                        HStack(alignment: .top) {
                            Text(entry.prompt)
                                .font(.subheadline)
                                .lineLimit(3)
                            Spacer()
                            Button("Cancel", role: .destructive) {
                                viewModel.cancelQueued(id: entry.id)
                            }
                            .font(.caption)
                        }
                    }
                } footer: {
                    Text("The daemon owns this queue — each prompt starts as its own turn when the running one finishes.")
                }
            }
            .navigationTitle("Queued prompts")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { showQueue = false }
                }
            }
        }
        .presentationDetents([.medium])
    }

    private func handlePickedFile(_ result: Result<URL, Error>) {
        guard case .success(let url) = result else { return }
        let secured = url.startAccessingSecurityScopedResource()
        defer { if secured { url.stopAccessingSecurityScopedResource() } }
        let name = url.lastPathComponent
        guard let data = try? Data(contentsOf: url) else {
            viewModel.statusLine = "Could not read that file."
            return
        }
        guard !data.isEmpty else {
            viewModel.statusLine = "File is empty on this device (cloud-only?)."
            return
        }
        viewModel.addAttachment(name: name, data: data)
    }

    private var attachmentChips: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(viewModel.attachments) { chip in
                    HStack(spacing: 5) {
                        if chip.uploading {
                            ProgressView().controlSize(.mini)
                        } else if chip.error != nil {
                            Image(systemName: "exclamationmark.triangle.fill")
                                .font(.caption2)
                                .foregroundStyle(.orange)
                        } else {
                            Image(systemName: "paperclip").font(.caption2)
                        }
                        Text(chip.name).font(.caption).lineLimit(1)
                        Button {
                            viewModel.removeAttachment(id: chip.id)
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(Color(.secondarySystemBackground), in: Capsule())
                }
            }
            .padding(.horizontal, Theme.Space.gutter)
        }
        .padding(.vertical, 4)
        .background(.bar)
    }

    private func statusLineBar(_ line: String) -> some View {
        HStack(spacing: 8) {
            Text(line)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(2)
            Spacer()
            Button {
                viewModel.statusLine = nil
            } label: {
                Image(systemName: "xmark").font(.caption2)
            }
            .foregroundStyle(.secondary)
        }
        .readableColumn()
        .padding(.horizontal, Theme.Space.gutter)
        .padding(.vertical, 5)
        .background(.bar)
    }

    @ViewBuilder
    private var statusOverlay: some View {
        if case .failed(let message) = viewModel.phase, viewModel.items.isEmpty {
            VStack(spacing: 12) {
                Image(systemName: "exclamationmark.triangle").font(.largeTitle).foregroundStyle(.orange)
                Text(message).multilineTextAlignment(.center).foregroundStyle(.secondary).padding(.horizontal)
            }
        }
    }

    private var optionsBar: some View {
        HStack(spacing: 8) {
            if viewModel.canSetModel && !viewModel.availableModels.isEmpty {
                Menu {
                    ForEach(viewModel.availableModels, id: \.self) { option in
                        Button {
                            viewModel.selectModel(option)
                        } label: {
                            if viewModel.isCurrentModel(option) {
                                Label(ChatViewModel.modelLabel(option), systemImage: "checkmark")
                            } else {
                                Text(ChatViewModel.modelLabel(option))
                            }
                        }
                    }
                } label: {
                    chipLabel(icon: "cpu", text: currentModelName)
                }
            }

            if viewModel.canSetEffort && !viewModel.availableEfforts.isEmpty {
                Menu {
                    ForEach(viewModel.availableEfforts, id: \.self) { option in
                        Button {
                            viewModel.selectEffort(option)
                        } label: {
                            if viewModel.isCurrentEffort(option) {
                                Label(option == "default" || option.isEmpty ? "Default" : option.capitalized,
                                      systemImage: "checkmark")
                            } else {
                                Text(option == "default" || option.isEmpty ? "Default" : option.capitalized)
                            }
                        }
                    }
                } label: {
                    chipLabel(icon: "gauge.with.dots.needle.33percent",
                              text: viewModel.selectedEffort.isEmpty ? "Effort" : viewModel.selectedEffort.capitalized)
                }
            }

            Spacer()
        }
        .readableColumn()
        .padding(.horizontal, Theme.Space.gutter)
        .padding(.top, 6)
        .background(.bar)
    }

    private func chipLabel(icon: String, text: String) -> some View {
        HStack(spacing: 4) {
            Image(systemName: icon)
            Text(text)
            Image(systemName: "chevron.up.chevron.down").font(.system(size: 9, weight: .semibold))
        }
        .font(.caption.weight(.medium))
        .foregroundStyle(accent.tint)
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(accent.soft, in: Capsule())
    }

    private var currentModelName: String {
        ChatViewModel.modelLabel(viewModel.selectedModel.isEmpty ? "default" : viewModel.selectedModel)
    }

    private var commandSuggestions: some View {
        ScrollView {
            VStack(spacing: 0) {
                ForEach(viewModel.commandSuggestions, id: \.self) { name in
                    Button {
                        viewModel.applyCommand(name)
                    } label: {
                        HStack(spacing: 8) {
                            Text("/\(name)")
                                .font(.system(.subheadline, design: .monospaced).weight(.medium))
                                .foregroundStyle(accent.tint)
                            Spacer(minLength: 8)
                        }
                        .padding(.horizontal, Theme.Space.gutter)
                        .padding(.vertical, 8)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    Divider().padding(.leading, Theme.Space.gutter)
                }
            }
            .readableColumn()
        }
        .frame(maxHeight: 220)
        .background(.bar)
    }

    private var inputBar: some View {
        HStack(alignment: .bottom, spacing: 8) {
            Button {
                showAttachmentPicker = true
            } label: {
                Image(systemName: "plus.circle")
                    .font(.system(size: 24))
                    .foregroundStyle(.secondary)
            }
            .padding(.bottom, 4)

            TextField(inputPlaceholder, text: $viewModel.draftText, axis: .vertical)
                .lineLimit(1...6)
                .focused($inputFocused)
                .padding(.horizontal, 14)
                .padding(.vertical, 9)
                .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 20, style: .continuous))
                .onSubmit { viewModel.send() }
                .accessibilityIdentifier("chat.input")

            let canSend = !viewModel.draftText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            if viewModel.isBusy {
                // While a turn runs, Send still works (types into the TUI / queues) — Stop is its
                // own always-visible control so it never trades places with Send mid-tap.
                Button {
                    viewModel.interrupt()
                } label: {
                    Image(systemName: "stop.circle.fill")
                        .font(.system(size: 30))
                        .foregroundStyle(Color.red)
                }
            }
            Button {
                viewModel.send()
            } label: {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.system(size: 30))
                    .foregroundStyle(canSend ? accent.tint : Color.secondary.opacity(0.5))
            }
            .disabled(!canSend)
            .accessibilityIdentifier("chat.send")
            .animation(.easeInOut(duration: 0.15), value: viewModel.isBusy)
        }
        .readableColumn()
        .padding(.horizontal, Theme.Space.gutter)
        .padding(.top, 8)
        .padding(.bottom, 6)
        .background(.bar)
    }

    private var inputPlaceholder: String {
        if viewModel.isBusy {
            return viewModel.permissionMode == "interactive" ? "Type into the session…" : "Queue a message…"
        }
        return "Message \(accent.label)…"
    }

    private func scrollToEnd(_ proxy: ScrollViewProxy) {
        let target: String? = viewModel.isBusy ? "busy-indicator" : viewModel.items.last?.id
        guard let target else { return }
        if didInitialScroll {
            withAnimation { proxy.scrollTo(target, anchor: .bottom) }
        } else {
            didInitialScroll = true
            proxy.scrollTo(target, anchor: .bottom)
        }
    }
}

/// The live two-line strip while a turn runs: phase + description (line 1) and the raw
/// command/path (line 2, monospace), plus the elapsed counter — mirrors Android's StatusBanner.
private struct StatusBanner: View {
    let status: ActiveJobStatus
    let accent: ProviderAccent

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                Circle()
                    .fill(accent.tint)
                    .frame(width: 7, height: 7)
                Text(firstLine)
                    .font(.caption.weight(.medium))
                    .lineLimit(1)
                Spacer()
                Text(elapsedText)
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            if let second = secondLine {
                Text(second)
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .padding(.leading, 13)
            }
        }
        .padding(.horizontal, Theme.Space.gutter)
        .padding(.vertical, 6)
        .background(accent.soft)
    }

    private var firstLine: String {
        let phase = (status.phase ?? "").trimmingCharacters(in: .whitespaces)
        let detail = (status.phaseDetail ?? "").trimmingCharacters(in: .whitespaces)
        if !phase.isEmpty && !detail.isEmpty { return "\(phase.capitalized) · \(detail)" }
        if !phase.isEmpty { return phase.capitalized }
        if let tool = status.tool, !tool.isEmpty { return tool }
        return "\(ProviderAccent.forProvider(status.provider).label) is working"
    }

    private var secondLine: String? {
        let toolDetail = (status.toolDetail ?? "").trimmingCharacters(in: .whitespaces)
        let tool = (status.tool ?? "").trimmingCharacters(in: .whitespaces)
        let line = toolDetail.isEmpty ? tool : toolDetail
        guard !line.isEmpty, line != status.phaseDetail else { return nil }
        return line
    }

    private var elapsedText: String {
        let s = max(0, status.elapsedS)
        return s < 60 ? "\(s)s" : "\(s / 60)m \(s % 60)s"
    }
}
