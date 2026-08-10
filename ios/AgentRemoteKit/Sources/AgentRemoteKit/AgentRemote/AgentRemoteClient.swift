import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

public enum AgentRemoteError: Error, Sendable, Equatable, LocalizedError, CustomNSError {
    case invalidURL
    case invalidResponse
    /// A daemon error body, tagged with the HTTP status so callers can distinguish e.g. 401 vs 404.
    case daemon(status: Int, message: String)
    case decoding(String)
    /// URLSession / network failure (connection refused, ATS, offline, …).
    case network(String)

    public static var errorDomain: String { "AgentRemoteKit.AgentRemoteError" }

    public var errorCode: Int {
        switch self {
        case .invalidURL: return 0
        case .invalidResponse: return 1
        case .daemon: return 2
        case .decoding: return 3
        case .network: return 4
        }
    }

    public var errorUserInfo: [String: Any] {
        [NSLocalizedDescriptionKey: errorDescription ?? "Unknown Agent Remote error."]
    }

    public var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "Invalid server URL."
        case .invalidResponse:
            return "Unexpected response from the daemon (not HTTP)."
        case .daemon(let status, let message):
            if status == 401 { return "Invalid auth token." }
            if status == 0 { return message }
            return "Server error (\(status)): \(message)"
        case .decoding(let detail):
            return "Couldn't read the daemon response: \(detail)"
        case .network(let detail):
            return detail
        }
    }
}

/// Talks to one `agentremoted` daemon instance over plain HTTP + token auth. No persistent
/// connection is held for the request/response calls — `/ws/status` (via `statusStream`) is the
/// only long-lived connection this client opens.
public struct AgentRemoteClient: Sendable {
    public let baseURL: URL
    public let token: String
    private let session: URLSession

    private static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    private static let encoder: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        return encoder
    }()

    public init(baseURL: URL, token: String, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.token = token
        self.session = session
    }

    // MARK: - Core request plumbing

    private func buildURL(_ path: String, query: [String: String?] = [:]) throws -> URL {
        // Prefer URLComponents path join so "/api/ping" and "api/ping" both work.
        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else {
            throw AgentRemoteError.invalidURL
        }
        let basePath = components.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let rel = path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        if basePath.isEmpty {
            components.path = "/" + rel
        } else {
            components.path = "/" + basePath + "/" + rel
        }
        let items = query.compactMap { key, value -> URLQueryItem? in
            guard let value else { return nil }
            return URLQueryItem(name: key, value: value)
        }
        if !items.isEmpty { components.queryItems = items }
        guard let url = components.url else { throw AgentRemoteError.invalidURL }
        return url
    }

    private func send<Response: Decodable>(
        _ method: String,
        _ path: String,
        query: [String: String?] = [:],
        jsonBody: Encodable? = nil,
        rawBody: Data? = nil,
        contentType: String? = nil,
        requiresAuth: Bool = true
    ) async throws -> Response {
        let url = try buildURL(path, query: query)
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 30
        if requiresAuth {
            request.setValue(token, forHTTPHeaderField: "X-Auth-Token")
        }
        if let jsonBody {
            request.httpBody = try Self.encoder.encode(AnyEncodable(jsonBody))
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        } else if let rawBody {
            request.httpBody = rawBody
            request.setValue(contentType ?? "application/octet-stream", forHTTPHeaderField: "Content-Type")
        }

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            let msg = (error as? URLError).map { urlErrorMessage($0, url: url) }
                ?? error.localizedDescription
            throw AgentRemoteError.network(msg)
        }
        guard let http = response as? HTTPURLResponse else {
            throw AgentRemoteError.invalidResponse
        }

        guard (200..<300).contains(http.statusCode) else {
            let message = (try? Self.decoder.decode(DaemonErrorBody.self, from: data))?.error
                ?? String(data: data, encoding: .utf8) ?? "HTTP \(http.statusCode)"
            throw AgentRemoteError.daemon(status: http.statusCode, message: message)
        }

        do {
            return try Self.decoder.decode(Response.self, from: data)
        } catch {
            throw AgentRemoteError.decoding(String(describing: error))
        }
    }

    private func urlErrorMessage(_ error: URLError, url: URL) -> String {
        switch error.code {
        case .appTransportSecurityRequiresSecureConnection:
            return "HTTP blocked by App Transport Security for \(url.host ?? url.absoluteString). Use https:// or allow local networking."
        case .cannotConnectToHost, .networkConnectionLost, .notConnectedToInternet:
            return "Could not reach \(url.host ?? "daemon") (\(error.localizedDescription)). Is agentremoted running?"
        case .timedOut:
            return "Timed out connecting to \(url.host ?? "daemon")."
        default:
            return error.localizedDescription
        }
    }

    /// Fire-and-forget-shaped call for endpoints whose success body we don't need to inspect
    /// beyond "it didn't throw" — still fully awaits and validates the HTTP status.
    private func sendDiscardingResponse(
        _ method: String,
        _ path: String,
        query: [String: String?] = [:],
        jsonBody: Encodable? = nil
    ) async throws {
        let _: EmptyResponse = try await send(method, path, query: query, jsonBody: jsonBody)
    }

    // MARK: - Ping

    public func ping() async throws -> PingResponse {
        try await send("GET", "/api/ping", requiresAuth: true)
    }

    // MARK: - Projects / sessions

    public func projects() async throws -> [Project] {
        let response: ProjectsResponse = try await send("GET", "/api/projects")
        return response.projects
    }

    public func sessions(project: String? = nil, limit: Int = 25, all: Bool = false) async throws -> [SessionSummary] {
        let response: SessionsResponse = try await send("GET", "/api/sessions", query: [
            "project": project,
            "limit": String(limit),
            "all": all ? "1" : nil,
        ])
        return response.sessions
    }

    public func searchSessions(query: String, project: String? = nil, limit: Int = 25, all: Bool = false) async throws -> SessionSearchResponse {
        try await send("GET", "/api/sessions/search", query: [
            "q": query,
            "project": project,
            "limit": String(limit),
            "all": all ? "1" : nil,
        ])
    }

    /// Search and return rows as plain `SessionSummary` (snippet + provider preserved).
    public func searchSessionSummaries(query: String, project: String? = nil, limit: Int = 25, all: Bool = false) async throws -> [SessionSummary] {
        try await searchSessions(query: query, project: project, limit: limit, all: all)
            .results.map { $0.asSummary() }
    }

    public func session(id: String) async throws -> SessionSummary {
        try await send("GET", "/api/sessions/\(id)")
    }

    public func messages(sessionId: String, offset: Int? = nil, limit: Int? = nil) async throws -> MessagesResponse {
        try await send("GET", "/api/sessions/\(sessionId)/messages", query: [
            "offset": offset.map(String.init),
            "limit": limit.map(String.init),
        ])
    }

    // MARK: - Sessions -> jobs

    public func startSession(_ request: NewSessionRequest) async throws -> String {
        let response: JobIdResponse = try await send("POST", "/api/sessions/new", jsonBody: request)
        return response.jobId
    }

    public func continueSession(id: String, _ request: ContinueSessionRequest) async throws -> String {
        let response: JobIdResponse = try await send("POST", "/api/sessions/\(id)/continue", jsonBody: request)
        return response.jobId
    }

    // MARK: - Jobs

    public func job(id: String, since: Int = 0) async throws -> JobSnapshot {
        try await send("GET", "/api/jobs/\(id)", query: ["since": String(since)])
    }

    public func queuePrompt(jobId: String, prompt: String) async throws -> [QueuedPrompt] {
        let response: QueueResponse = try await send("POST", "/api/jobs/\(jobId)/queue", jsonBody: QueuePromptRequest(prompt: prompt))
        return response.queued
    }

    public func cancelQueued(jobId: String, queuedId: String) async throws -> CancelQueuedResponse {
        try await send("POST", "/api/jobs/\(jobId)/queue/\(queuedId)/cancel")
    }

    public func stopJob(jobId: String) async throws {
        try await sendDiscardingResponse("POST", "/api/jobs/\(jobId)/stop")
    }

    public func resolvePermission(jobId: String, requestId: String, allow: Bool) async throws {
        try await sendDiscardingResponse("POST", "/api/jobs/\(jobId)/permission", jsonBody: PermissionDecisionRequest(requestId: requestId, allow: allow))
    }

    public func resolveQuestion(jobId: String, _ answer: QuestionAnswerRequest) async throws {
        try await sendDiscardingResponse("POST", "/api/jobs/\(jobId)/question", jsonBody: answer)
    }

    /// Only valid mid-run on an interactive job — types a line into the live TUI's own input,
    /// bypassing the queue. `409` (surfaced as `.daemon(409, reason)`) if the job isn't
    /// running/interactive yet.
    public func typeIntoTui(jobId: String, prompt: String) async throws {
        try await sendDiscardingResponse("POST", "/api/jobs/\(jobId)/input", jsonBody: InputPromptRequest(prompt: prompt))
    }

    // MARK: - Live TUI

    public func tuiFrame(sessionId: String, ansi: Bool = false) async throws -> TuiFrame {
        try await send("GET", "/api/sessions/\(sessionId)/tui", query: ["ansi": ansi ? "1" : nil])
    }

    public func sendTuiKeys(sessionId: String, keys: [String]? = nil, text: String? = nil) async throws {
        try await sendDiscardingResponse("POST", "/api/sessions/\(sessionId)/tui/keys", jsonBody: TuiKeysRequest(keys: keys, text: text))
    }

    // MARK: - File drop / attachments

    public func dropList() async throws -> DropListResponse {
        try await send("GET", "/api/drop")
    }

    public func downloadDropFile(name: String) async throws -> Data {
        let url = try buildURL("/api/drop/\(name)")
        var request = URLRequest(url: url)
        request.setValue(token, forHTTPHeaderField: "X-Auth-Token")
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw AgentRemoteError.invalidResponse
        }
        return data
    }

    public func deleteDropFile(name: String) async throws {
        try await sendDiscardingResponse("POST", "/api/drop/\(name)/delete")
    }

    /// Phone -> host. `data` is sent as the raw request body (not JSON).
    public func uploadAttachment(name: String, data: Data, contentType: String = "application/octet-stream") async throws -> AttachmentUploadResponse {
        try await send("POST", "/api/attachments", query: ["name": name], rawBody: data, contentType: contentType)
    }

    // MARK: - Usage

    public func usage() async throws -> UsageResponse {
        try await send("GET", "/api/usage")
    }

    // MARK: - Status stream

    /// Opens `/ws/status` and yields each push as it arrives. The daemon pushes ~1x/sec only when
    /// the active-job set changes, plus a ping every 15s. Cancel the returned task / end iteration
    /// to close the socket.
    public func statusStream() -> AsyncThrowingStream<StatusPush, Error> {
        AsyncThrowingStream { continuation in
            guard var components = URLComponents(url: baseURL.appendingPathComponent("/ws/status"), resolvingAgainstBaseURL: false) else {
                continuation.finish(throwing: AgentRemoteError.invalidURL)
                return
            }
            components.scheme = components.scheme == "https" ? "wss" : "ws"
            components.queryItems = [URLQueryItem(name: "token", value: token)]
            guard let url = components.url else {
                continuation.finish(throwing: AgentRemoteError.invalidURL)
                return
            }

            let task = session.webSocketTask(with: url)
            task.resume()

            let pump = Task {
                while true {
                    let message: URLSessionWebSocketTask.Message
                    do {
                        message = try await task.receive()
                    } catch {
                        continuation.finish(throwing: error)
                        return
                    }
                    let data: Data?
                    switch message {
                    case .data(let value): data = value
                    case .string(let value): data = Data(value.utf8)
                    @unknown default: data = nil
                    }
                    if let data, let push = try? Self.decoder.decode(StatusPush.self, from: data) {
                        continuation.yield(push)
                    }
                }
            }

            continuation.onTermination = { _ in
                pump.cancel()
                task.cancel(with: .goingAway, reason: nil)
            }
        }
    }
}

/// Type-erasing box so `Encodable` existentials can be passed into `JSONEncoder.encode`.
private struct AnyEncodable: Encodable {
    private let value: Encodable
    init(_ value: Encodable) { self.value = value }
    func encode(to encoder: Encoder) throws { try value.encode(to: encoder) }
}

private struct EmptyResponse: Decodable {}
