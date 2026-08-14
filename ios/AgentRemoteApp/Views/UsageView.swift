import AgentRemoteKit
import SwiftUI

/// `GET /api/usage` fanned out over every daemon whose ping advertises usage support
/// (Android UsageScreen parity). Sections that report the SAME subscription through two daemons
/// merge by `(provider, account_id || account)` — the bucket set with the higher peak wins and
/// the host names union into one "Via …" line. `resetsText` is pre-formatted daemon-side.
struct UsageView: View {
    @EnvironmentObject private var appModel: AppModel
    @EnvironmentObject private var profileStore: ProfileStore
    @Environment(\.dismiss) private var dismiss

    private struct MergedSection: Identifiable {
        let provider: String
        let account: String
        var hosts: [String]
        var buckets: [UsageBucket]
        var id: String { "\(provider)|\(account)|\(hosts.joined(separator: ","))" }
    }

    private struct Unsupported: Identifiable {
        let profileName: String
        let provider: String
        let reason: String
        var id: String { profileName + reason }
    }

    @State private var sections: [MergedSection] = []
    @State private var unsupported: [Unsupported] = []
    @State private var problems: [(name: String, error: String)] = []
    @State private var isLoading = true

    var body: some View {
        NavigationStack {
            List {
                if isLoading {
                    HStack { Spacer(); ProgressView("Reading daemons…"); Spacer() }
                } else {
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
                    ForEach(sections) { section in
                        Section {
                            ForEach(section.buckets) { bucket in
                                UsageBucketRow(bucket: bucket, accent: ProviderAccent.forProvider(section.provider))
                            }
                        } header: {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(sectionTitle(section))
                                if !section.hosts.isEmpty {
                                    Text("Via \(section.hosts.joined(separator: " · "))")
                                        .font(.caption2)
                                        .textCase(nil)
                                }
                            }
                        }
                    }
                    ForEach(unsupported) { entry in
                        Section {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(entry.reason).font(.caption).foregroundStyle(.secondary)
                                if entry.provider == "grok",
                                   let url = URL(string: "https://grok.com/?_s=usage") {
                                    Link("Open grok.com usage", destination: url)
                                        .font(.caption)
                                }
                            }
                        } header: {
                            Text(entry.profileName)
                        }
                    }
                    if sections.isEmpty && unsupported.isEmpty && problems.isEmpty {
                        Text("No usage data returned.").foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle("Usage")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .task { await load() }
        }
    }

    private func sectionTitle(_ section: MergedSection) -> String {
        let label = ProviderAccent.forProvider(section.provider).label
        return section.account.isEmpty || section.account.hasPrefix("host:")
            ? label
            : "\(label) · \(section.account)"
    }

    private func load() async {
        isLoading = true
        var merged: [String: MergedSection] = [:]
        var order: [String] = []
        var noSupport: [Unsupported] = []
        var errors: [(name: String, error: String)] = []

        for profile in profileStore.profiles {
            let ping = appModel.ping(for: profile.id)
            let canShow: Bool = {
                guard let ping else { return true }  // not pinged yet — try anyway
                if ping.caps.canShowUsage { return true }
                return ping.providerDetails?.values.contains {
                    PingResponse.Capabilities(fromCapMap: $0.caps ?? [:], fallback: ping.caps).canShowUsage
                } ?? false
            }()
            guard let agent = appModel.client(for: profile.id)?.agentClient else {
                errors.append((profile.name, "Not connected."))
                continue
            }
            guard canShow else {
                noSupport.append(Unsupported(profileName: profile.name,
                                             provider: ping?.provider ?? "",
                                             reason: "This daemon does not report usage."))
                continue
            }
            do {
                let response = try await agent.usage()
                let host = ping?.host ?? profile.name
                if response.sections.isEmpty {
                    // Single-harness daemon: the root IS the one section.
                    if response.ok, let buckets = response.buckets, !buckets.isEmpty {
                        mergeSection(provider: response.provider.isEmpty ? (ping?.provider ?? "") : response.provider,
                                     account: response.accountId.isEmpty ? response.account : response.accountId,
                                     accountLabel: response.account,
                                     host: host, buckets: buckets,
                                     into: &merged, order: &order)
                    } else if !response.ok {
                        noSupport.append(Unsupported(profileName: profile.name,
                                                     provider: ping?.provider ?? "",
                                                     reason: response.error ?? "This daemon does not report usage."))
                    }
                } else {
                    for section in response.sections {
                        if section.ok, !section.buckets.isEmpty {
                            mergeSection(provider: section.provider,
                                         account: section.accountId.isEmpty ? section.account : section.accountId,
                                         accountLabel: section.account,
                                         host: host, buckets: section.buckets,
                                         into: &merged, order: &order)
                        } else if !section.ok {
                            noSupport.append(Unsupported(profileName: "\(profile.name) · \(ProviderAccent.forProvider(section.provider).label)",
                                                         provider: section.provider,
                                                         reason: section.error.isEmpty ? "No usage reported." : section.error))
                        }
                    }
                }
            } catch {
                errors.append((profile.name, DaemonClient.describe(error)))
            }
        }

        sections = order.compactMap { merged[$0] }
        unsupported = noSupport
        problems = errors
        isLoading = false
    }

    private func mergeSection(
        provider: String, account: String, accountLabel: String, host: String,
        buckets: [UsageBucket],
        into merged: inout [String: MergedSection], order: inout [String]
    ) {
        let key = "\(provider)|\(account.isEmpty ? "host:\(host)" : account)"
        if var existing = merged[key] {
            if !existing.hosts.contains(host) { existing.hosts.append(host) }
            // Same subscription seen twice: keep the fresher (higher-peak) bucket set.
            let existingMax = existing.buckets.map(\.percent).max() ?? 0
            let incomingMax = buckets.map(\.percent).max() ?? 0
            if incomingMax > existingMax { existing.buckets = buckets }
            merged[key] = existing
        } else {
            merged[key] = MergedSection(provider: provider, account: accountLabel,
                                        hosts: [host], buckets: buckets)
            order.append(key)
        }
    }
}

private struct UsageBucketRow: View {
    let bucket: UsageBucket
    var accent: ProviderAccent = .neutral

    private var tint: Color {
        switch bucket.severity.lowercased() {
        case "critical": return .red
        case "warning": return .orange
        default: return accent.tint
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(bucket.title).font(.subheadline.weight(.medium))
                Spacer()
                Text("\(bucket.percent)%").font(.subheadline.weight(.semibold)).foregroundStyle(tint)
            }
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(Color.secondary.opacity(0.15))
                    Capsule().fill(tint).frame(width: max(3, geo.size.width * min(1, Double(bucket.percent) / 100)))
                }
            }
            .frame(height: 5)
            Text(bucket.resetsText).font(.caption).foregroundStyle(.secondary)
        }
        .padding(.vertical, 4)
    }
}
