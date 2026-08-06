import AgentRemoteKit
import SwiftUI

/// `GET /api/usage` — subscription usage buckets, ready-to-render (the daemon pre-formats
/// `resetsText` in its own timezone, so no client-side date math).
struct UsageView: View {
    let client: DaemonClient
    @Environment(\.dismiss) private var dismiss

    @State private var buckets: [UsageBucket] = []
    @State private var isLoading = true
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            List {
                if isLoading {
                    HStack { Spacer(); ProgressView(); Spacer() }
                } else if let errorMessage {
                    Text(errorMessage).foregroundStyle(.secondary)
                } else if buckets.isEmpty {
                    Text("No usage data reported by this server.").foregroundStyle(.secondary)
                } else {
                    ForEach(buckets) { bucket in
                        UsageBucketRow(bucket: bucket)
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

    private func load() async {
        guard let agentClient = client.agentClient else {
            isLoading = false
            errorMessage = "Not connected."
            return
        }
        do {
            let response = try await agentClient.usage()
            isLoading = false
            if response.ok {
                buckets = response.buckets ?? []
            } else {
                errorMessage = response.error ?? "Usage isn't supported by this server."
            }
        } catch {
            isLoading = false
            errorMessage = DaemonClient.describe(error)
        }
    }
}

private struct UsageBucketRow: View {
    let bucket: UsageBucket

    private var tint: Color {
        switch bucket.severity.lowercased() {
        case "critical": return .red
        case "warning": return .orange
        default: return Color.brand
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
