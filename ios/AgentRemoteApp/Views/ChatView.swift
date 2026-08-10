import SwiftUI

struct ChatView: View {
    @ObservedObject var viewModel: ChatViewModel
    @FocusState private var inputFocused: Bool
    @State private var didInitialScroll = false
    @State private var showLiveTui = false
    @State private var showSessionOptions = false

    private var accent: ProviderAccent { viewModel.accent }

    var body: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: Theme.Space.row) {
                        ForEach(viewModel.items) { item in
                            TimelineItemView(item: item, accent: accent)
                                .id(item.id)
                        }
                        if viewModel.isBusy {
                            HStack(spacing: 7) {
                                ProgressView().controlSize(.small)
                                Text("Working…").font(.caption).foregroundStyle(.secondary)
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
            if !viewModel.isBusy {
                optionsBar
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
            ToolbarItem(placement: .primaryAction) {
                if viewModel.liveTuiEnabled && !viewModel.sessionId.isEmpty {
                    Button {
                        showLiveTui = true
                    } label: {
                        Image(systemName: "terminal")
                    }
                }
            }
        }
        .sheet(item: Binding(
            get: { viewModel.pendingPermission },
            set: { newValue in if newValue == nil { viewModel.respondToPermission(approved: false) } }
        )) { prompt in
            PermissionSheetView(prompt: prompt) { approved in
                viewModel.respondToPermission(approved: approved)
            }
            .presentationDetents([.medium])
        }
        .sheet(item: Binding(
            get: { viewModel.pendingQuestion },
            set: { _ in }
        )) { question in
            QuestionSheetView(prompt: question) { answers, notes, cancel in
                viewModel.respondToQuestion(answers: answers, notes: notes, cancel: cancel)
            }
            .presentationDetents([.medium, .large])
        }
        .sheet(isPresented: $showLiveTui) {
            if let client = liveClient {
                // Detents / page sizing live on LiveTuiView so the sheet is near full-screen
                // (especially on iPad, which otherwise uses a small form card).
                LiveTuiView(client: client, sessionId: viewModel.sessionId, title: viewModel.displayTitle)
            }
        }
    }

    /// Access DaemonClient through the chat's connection — Live TUI needs the same agent client.
    private var liveClient: DaemonClient? {
        // ChatViewModel holds the client privately; use a thin wrapper via environment would be
        // cleaner, but LiveTuiView can take AgentRemoteClient from a callback. For now pass via
        // the viewModel-exposed helper.
        viewModel.liveDaemonClient
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
            TextField("Message \(accent.label)…", text: $viewModel.draftText, axis: .vertical)
                .lineLimit(1...6)
                .focused($inputFocused)
                .padding(.horizontal, 14)
                .padding(.vertical, 9)
                .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 20, style: .continuous))
                .onSubmit { viewModel.send() }

            let canSend = !viewModel.draftText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            Button {
                viewModel.isBusy ? viewModel.interrupt() : viewModel.send()
            } label: {
                Image(systemName: viewModel.isBusy ? "stop.circle.fill" : "arrow.up.circle.fill")
                    .font(.system(size: 30))
                    .foregroundStyle(viewModel.isBusy ? Color.red : (canSend ? accent.tint : Color.secondary.opacity(0.5)))
            }
            .disabled(!viewModel.isBusy && !canSend)
            .animation(.easeInOut(duration: 0.15), value: viewModel.isBusy)
        }
        .readableColumn()
        .padding(.horizontal, Theme.Space.gutter)
        .padding(.top, 8)
        .padding(.bottom, 6)
        .background(.bar)
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
