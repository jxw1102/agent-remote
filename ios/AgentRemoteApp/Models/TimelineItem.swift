import AgentRemoteKit
import Foundation

/// One row in a chat transcript. There's no tool-result or thinking-block concept in this
/// daemon's protocol (headless mode never emits a tool result, and no separate thinking event
/// exists) — `toolCall` and `permissionRequest` are both fire-and-forget markers, not
/// pending/spinner states.
enum TimelineItem: Identifiable, Equatable {
    case userText(id: String, text: String)
    case assistantText(id: String, text: String)
    /// `detail` is the daemon's own pre-formatted, already-clipped display string.
    case toolCall(id: String, name: String, detail: String)
    case permissionRequest(id: String, toolName: String, detail: String, resolution: PermissionResolution?)
    case turnResult(id: String, summary: String, isError: Bool)
    case systemNotice(id: String, text: String)
    /// Process view: one working step (tool call / result / thinking) under its message.
    case step(id: String, step: ProcessStep)

    var id: String {
        switch self {
        case .userText(let id, _), .assistantText(let id, _), .toolCall(let id, _, _),
             .permissionRequest(let id, _, _, _), .turnResult(let id, _, _), .systemNotice(let id, _),
             .step(let id, _):
            return id
        }
    }
}

enum PermissionResolution: Equatable {
    case allowed
    case denied(reason: String?)
}

/// Drives the approve/deny sheet — mirrors the job snapshot's `pendingPermission` field.
struct PendingPermissionUI: Identifiable, Equatable {
    let requestId: String
    let toolName: String
    let detail: String
    var id: String { requestId }
}

// PendingQuestionUI lives next to ChatViewModel (uses QuestionItem from the kit).
