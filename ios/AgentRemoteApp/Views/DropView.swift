import AgentRemoteKit
import SwiftUI
import UniformTypeIdentifiers

/// Host↔phone file exchange, merged across every daemon (Android DropScreen parity).
/// `GET /api/drop` lists what each host staged; identical `(name, size, mtime)` entries from
/// different profiles are one file — the common cause is two profiles reaching the SAME daemon
/// via different URLs — so the first-listed profile's copy wins and the hidden sources ride
/// along as "also on". `POST /api/attachments` is the other direction.
struct DropView: View {
    @EnvironmentObject private var appModel: AppModel
    @EnvironmentObject private var profileStore: ProfileStore
    @Environment(\.dismiss) private var dismiss

    /// One row of the merged inbox: an entry plus the daemon that holds it.
    private struct DropRow: Identifiable {
        let profileId: UUID
        let profileName: String
        let provider: String
        let file: DropFile
        var alsoOn: [String] = []
        var id: String { "\(profileId.uuidString)/\(file.name)" }
    }

    private struct Feed {
        var loading = false
        var path = ""
        var error: String?
        var files: [DropFile] = []
    }

    @State private var feeds: [UUID: Feed] = [:]
    @State private var isImporting = false
    @State private var isUploading = false
    @State private var uploadTargetId: UUID?
    @State private var downloading: Set<String> = []
    @State private var message: String?
    @State private var downloadedFile: DownloadedFile?

    private var rows: [DropRow] {
        var byKey: [String: DropRow] = [:]
        var order: [String] = []
        for profile in profileStore.profiles {
            guard let feed = feeds[profile.id] else { continue }
            let provider = appModel.ping(for: profile.id)?.provider ?? ""
            for file in feed.files {
                let key = "\(file.name)|\(file.size)|\(file.mtime.date.timeIntervalSince1970)"
                if var existing = byKey[key] {
                    existing.alsoOn.append(profile.name)
                    byKey[key] = existing
                } else {
                    byKey[key] = DropRow(profileId: profile.id, profileName: profile.name,
                                         provider: provider, file: file)
                    order.append(key)
                }
            }
        }
        return order.compactMap { byKey[$0] }
            .sorted { $0.file.mtime.date > $1.file.mtime.date }
    }

    private var problems: [(name: String, error: String)] {
        profileStore.profiles.compactMap { profile in
            guard let error = feeds[profile.id]?.error else { return nil }
            return (profile.name, error)
        }
    }

    private var isLoading: Bool { feeds.values.contains { $0.loading } }

    var body: some View {
        NavigationStack {
            List {
                if !problems.isEmpty {
                    Section {
                        ForEach(problems, id: \.name) { problem in
                            Label {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(problem.name).font(.subheadline.weight(.semibold))
                                    Text(problem.error).font(.caption).foregroundStyle(.secondary)
                                }
                            } icon: {
                                Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                            }
                        }
                    }
                }
                if isLoading && rows.isEmpty {
                    HStack { Spacer(); ProgressView(); Spacer() }
                } else if rows.isEmpty {
                    Text(profileStore.profiles.isEmpty
                         ? "No servers yet."
                         : "Nothing staged. Ask the agent to copy a file into a drop folder.")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(rows) { row in
                        rowView(row)
                    }
                }
            }
            .navigationTitle("Files from host")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { dismiss() }
                }
                ToolbarItemGroup(placement: .primaryAction) {
                    Button {
                        Task { await load() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    uploadButton
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
            .safeAreaInset(edge: .bottom) {
                if let message {
                    HStack {
                        Text(message).font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        Button {
                            self.message = nil
                        } label: {
                            Image(systemName: "xmark").font(.caption2)
                        }
                        .foregroundStyle(.secondary)
                    }
                    .padding(.horizontal)
                    .padding(.vertical, 6)
                    .background(.bar)
                }
            }
        }
    }

    @ViewBuilder
    private var uploadButton: some View {
        let connected = profileStore.profiles.filter { appModel.client(for: $0.id)?.agentClient != nil }
        if connected.count > 1 {
            Menu {
                ForEach(connected) { profile in
                    Button(profile.name) {
                        uploadTargetId = profile.id
                        isImporting = true
                    }
                }
            } label: {
                if isUploading { ProgressView() } else { Image(systemName: "arrow.up.circle") }
            }
            .disabled(isUploading)
        } else {
            Button {
                uploadTargetId = connected.first?.id
                isImporting = true
            } label: {
                if isUploading { ProgressView() } else { Image(systemName: "arrow.up.circle") }
            }
            .disabled(isUploading || connected.isEmpty)
        }
    }

    private func rowView(_ row: DropRow) -> some View {
        Button {
            Task { await download(row) }
        } label: {
            HStack {
                Image(systemName: row.file.isDirectory ? "folder" : "doc")
                    .foregroundStyle(ProviderAccent.forProvider(row.provider).tint)
                VStack(alignment: .leading, spacing: 2) {
                    Text(row.file.name)
                    Text(subtitle(for: row))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    if !row.alsoOn.isEmpty {
                        Text("Identical copy on \(row.alsoOn.joined(separator: ", "))")
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                }
                Spacer()
                if downloading.contains(row.id) {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: "arrow.down.circle")
                }
            }
        }
        .swipeActions(edge: .trailing) {
            Button(role: .destructive) {
                Task { await delete(row) }
            } label: {
                Label("Delete", systemImage: "trash")
            }
        }
    }

    private func subtitle(for row: DropRow) -> String {
        var parts: [String] = []
        if row.file.isDirectory {
            var entries = "\(row.file.entries) item\(row.file.entries == 1 ? "" : "s")"
            if row.file.partial { entries = "≥" + entries }
            parts.append(entries)
            parts.append("downloads as .zip")
        }
        parts.append(Self.byteCount(row.file.size))
        parts.append(row.file.mtime.date.formatted(date: .abbreviated, time: .shortened))
        parts.append(row.profileName)
        return parts.joined(separator: " · ")
    }

    private func load() async {
        for profile in profileStore.profiles {
            feeds[profile.id] = Feed(loading: true)
        }
        await withTaskGroup(of: (UUID, Feed).self) { group in
            for profile in profileStore.profiles {
                let agent = appModel.client(for: profile.id)?.agentClient
                group.addTask {
                    guard let agent else {
                        return (profile.id, Feed(error: "Not connected."))
                    }
                    do {
                        let response = try await agent.dropList()
                        return (profile.id, Feed(path: response.path, files: response.files))
                    } catch {
                        return (profile.id, Feed(error: DaemonClient.describe(error)))
                    }
                }
            }
            for await (profileId, feed) in group {
                feeds[profileId] = feed
            }
        }
    }

    private func download(_ row: DropRow) async {
        guard let agent = appModel.client(for: row.profileId)?.agentClient else { return }
        guard !downloading.contains(row.id) else { return }
        downloading.insert(row.id)
        defer { downloading.remove(row.id) }
        do {
            // Save under the name the daemon served — a folder arrives zipped as "<name>.zip".
            let payload = try await agent.downloadDropEntry(name: row.file.name)
            let url = FileManager.default.temporaryDirectory.appendingPathComponent(payload.name)
            try payload.data.write(to: url)
            downloadedFile = DownloadedFile(url: url)
        } catch {
            message = DaemonClient.describe(error)
        }
    }

    private func delete(_ row: DropRow) async {
        guard let agent = appModel.client(for: row.profileId)?.agentClient else { return }
        do {
            try await agent.deleteDropFile(name: row.file.name)
            if var feed = feeds[row.profileId] {
                feed.files.removeAll { $0.name == row.file.name }
                feeds[row.profileId] = feed
            }
            message = "Deleted \(row.file.name) on the host"
        } catch {
            message = DaemonClient.describe(error)
        }
    }

    private func upload(_ url: URL) async {
        guard let targetId = uploadTargetId ?? profileStore.profiles.first?.id,
              let agent = appModel.client(for: targetId)?.agentClient else { return }
        isUploading = true
        defer { isUploading = false }
        let secured = url.startAccessingSecurityScopedResource()
        defer { if secured { url.stopAccessingSecurityScopedResource() } }
        do {
            let data = try Data(contentsOf: url)
            let response = try await agent.uploadAttachment(name: url.lastPathComponent, data: data)
            message = "Uploaded to \(response.path)"
        } catch {
            message = DaemonClient.describe(error)
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
