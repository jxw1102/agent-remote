import AgentRemoteKit
import SwiftUI

/// Polls the host TUI pane and forwards keys (Android LiveTuiScreen parity).
/// Uses `?ansi=1` so SGR colours from the real CLI match web/Android.
struct LiveTuiView: View {
    let client: DaemonClient
    let sessionId: String
    let title: String

    @Environment(\.dismiss) private var dismiss
    @State private var frameText = ""
    @State private var errorMessage: String?
    @State private var inputText = ""
    @State private var pollTask: Task<Void, Never>?
    @State private var lastSeq: Int = -1

    /// Dark terminal background (not pure black — closer to web/Android pane).
    private let terminalBg = Color(red: 0.04, green: 0.05, blue: 0.07)
    private let defaultFg = Color(red: 0.82, green: 0.83, blue: 0.86)

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                ScrollViewReader { proxy in
                    ScrollView {
                        AnsiText(
                            text: frameText.isEmpty ? "Waiting for pane…" : frameText,
                            defaultColor: defaultFg,
                            fontSize: 13.5
                        )
                        .padding(10)
                        .id("pane-end")
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(terminalBg)
                    .onChange(of: frameText) { _, _ in
                        withAnimation(.easeOut(duration: 0.15)) {
                            proxy.scrollTo("pane-end", anchor: .bottom)
                        }
                    }
                }

                if let errorMessage {
                    Text(errorMessage)
                        .font(.caption)
                        .foregroundStyle(.orange)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(8)
                        .background(Color.orange.opacity(0.12))
                }

                HStack(spacing: 8) {
                    TextField("Type into TUI…", text: $inputText)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .font(.subheadline.monospaced())
                        .onSubmit { sendText() }
                    Button("Send") { sendText() }
                        .disabled(inputText.isEmpty)
                }
                .padding(10)
                .background(.bar)

                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        keyButton("Esc", keys: ["Escape"])
                        keyButton("Tab", keys: ["Tab"])
                        keyButton("↑", keys: ["Up"])
                        keyButton("↓", keys: ["Down"])
                        keyButton("←", keys: ["Left"])
                        keyButton("→", keys: ["Right"])
                        keyButton("Enter", keys: ["Enter"])
                        keyButton("Ctrl-C", keys: ["Ctrl-C"])
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 8)
                }
                .background(Color(.secondarySystemBackground))
            }
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
                }
            }
            .task { startPolling() }
            .onDisappear { pollTask?.cancel() }
        }
        // Nearly full-screen — iPad defaults to a small form card without this.
        .presentationDetents([.fraction(0.97), .large])
        .presentationDragIndicator(.visible)
        .presentationContentInteraction(.scrolls)
        .modifier(LiveTuiPageSizing())
    }

    private func keyButton(_ label: String, keys: [String]) -> some View {
        Button(label) {
            Task { try? await client.agentClient?.sendTuiKeys(sessionId: sessionId, keys: keys) }
        }
        .buttonStyle(.bordered)
        .font(.caption.weight(.medium))
    }

    private func sendText() {
        let text = inputText
        guard !text.isEmpty else { return }
        inputText = ""
        Task {
            try? await client.agentClient?.sendTuiKeys(sessionId: sessionId, text: text)
        }
    }

    private func startPolling() {
        pollTask?.cancel()
        pollTask = Task {
            while !Task.isCancelled {
                guard let agent = client.agentClient else {
                    errorMessage = "Not connected"
                    return
                }
                do {
                    // Coloured SGR — same as web/Android Live TUI.
                    let frame = try await agent.tuiFrame(sessionId: sessionId, ansi: true)
                    if frame.seq != lastSeq || frameText.isEmpty {
                        lastSeq = frame.seq
                        frameText = frame.text
                    }
                    errorMessage = frame.attached ? nil : (frame.error.isEmpty
                        ? "No interactive TUI for this session."
                        : frame.error)
                } catch {
                    if case AgentRemoteError.daemon(let status, _) = error, status == 404 {
                        errorMessage = "Live TUI not available for this session."
                        return
                    }
                    errorMessage = DaemonClient.describe(error)
                }
                try? await Task.sleep(nanoseconds: 400_000_000)
            }
        }
    }
}

/// iOS 18+ page sizing so Live TUI is a large sheet on iPad (not the tiny form card).
private struct LiveTuiPageSizing: ViewModifier {
    func body(content: Content) -> some View {
        if #available(iOS 18.0, *) {
            content.presentationSizing(.page)
        } else {
            content
        }
    }
}
