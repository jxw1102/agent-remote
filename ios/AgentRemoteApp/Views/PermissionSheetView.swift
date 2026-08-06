import SwiftUI

struct PermissionSheetView: View {
    let prompt: PendingPermissionUI
    let onRespond: (Bool) -> Void

    var body: some View {
        VStack(spacing: 20) {
            Image(systemName: "hand.raised.fill")
                .font(.largeTitle)
                .foregroundStyle(.orange)
                .padding(.top, 24)

            Text("Claude wants to use \(prompt.toolName)")
                .font(.headline)
                .multilineTextAlignment(.center)
                .padding(.horizontal)

            if !prompt.detail.isEmpty {
                ScrollView {
                    Text(prompt.detail)
                        .font(.system(.caption, design: .monospaced))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding()
                }
                .background(Color(.secondarySystemBackground))
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .padding(.horizontal)
            }

            Spacer()

            HStack(spacing: 12) {
                Button(role: .destructive) {
                    onRespond(false)
                } label: {
                    Text("Deny").frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)

                Button {
                    onRespond(true)
                } label: {
                    Text("Allow").frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
            }
            .padding()
        }
    }
}
