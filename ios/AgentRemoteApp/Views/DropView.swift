import AgentRemoteKit
import SwiftUI
import UniformTypeIdentifiers

/// Host<->phone file exchange. `GET /api/drop` lists what the host put in its drop folder for you
/// to pull; `POST /api/attachments` is the other direction — a file the agent can then read by
/// path on the host.
struct DropView: View {
    let client: DaemonClient
    @Environment(\.dismiss) private var dismiss

    @State private var files: [DropFile] = []
    @State private var isLoading = true
    @State private var errorMessage: String?
    @State private var isImporting = false
    @State private var isUploading = false
    @State private var downloadedFile: DownloadedFile?

    var body: some View {
        NavigationStack {
            List {
                if isLoading {
                    HStack { Spacer(); ProgressView(); Spacer() }
                } else if let errorMessage {
                    Text(errorMessage).foregroundStyle(.secondary)
                } else if files.isEmpty {
                    Text("Nothing in the drop folder.").foregroundStyle(.secondary)
                } else {
                    ForEach(files) { file in
                        Button {
                            Task { await download(file) }
                        } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(file.name)
                                    Text("\(Self.byteCount(file.size)) · \(file.mtime.date.formatted(date: .abbreviated, time: .shortened))")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                                Spacer()
                                Image(systemName: "arrow.down.circle")
                            }
                        }
                        .swipeActions(edge: .trailing) {
                            Button(role: .destructive) {
                                Task { await delete(file) }
                            } label: {
                                Label("Delete", systemImage: "trash")
                            }
                        }
                    }
                }
            }
            .navigationTitle("Drop Files")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { dismiss() }
                }
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        isImporting = true
                    } label: {
                        if isUploading { ProgressView() } else { Label("Upload", systemImage: "arrow.up.circle") }
                    }
                    .disabled(isUploading)
                }
            }
            .task { await load() }
            .fileImporter(isPresented: $isImporting, allowedContentTypes: [.item]) { result in
                if case .success(let url) = result { Task { await upload(url) } }
            }
            .sheet(item: $downloadedFile) { file in
                ShareLink(item: file.url) { Label("Save \(file.url.lastPathComponent)", systemImage: "square.and.arrow.up") }
                    .padding()
            }
        }
    }

    private func load() async {
        guard let agentClient = client.agentClient else {
            isLoading = false
            errorMessage = "Not connected."
            return
        }
        do {
            let response = try await agentClient.dropList()
            isLoading = false
            files = response.files
        } catch {
            isLoading = false
            errorMessage = DaemonClient.describe(error)
        }
    }

    private func download(_ file: DropFile) async {
        guard let agentClient = client.agentClient else { return }
        do {
            let data = try await agentClient.downloadDropFile(name: file.name)
            let url = FileManager.default.temporaryDirectory.appendingPathComponent(file.name)
            try data.write(to: url)
            downloadedFile = DownloadedFile(url: url)
        } catch {
            errorMessage = DaemonClient.describe(error)
        }
    }

    private func delete(_ file: DropFile) async {
        guard let agentClient = client.agentClient else { return }
        do {
            try await agentClient.deleteDropFile(name: file.name)
            files.removeAll { $0.id == file.id }
        } catch {
            errorMessage = DaemonClient.describe(error)
        }
    }

    private func upload(_ url: URL) async {
        guard let agentClient = client.agentClient else { return }
        isUploading = true
        defer { isUploading = false }
        do {
            let data = try Data(contentsOf: url)
            _ = try await agentClient.uploadAttachment(name: url.lastPathComponent, data: data)
        } catch {
            errorMessage = DaemonClient.describe(error)
        }
    }

    private static func byteCount(_ bytes: Int) -> String {
        ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file)
    }
}

private struct DownloadedFile: Identifiable {
    let url: URL
    var id: String { url.path }
}
