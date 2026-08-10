import XCTest
@testable import AgentRemoteKit

/// Fixtures below are real captured responses from the live agentremoted/bb10d daemon (see
/// PROTOCOL_SPEC.md) — decode with the same `.convertFromSnakeCase` strategy `AgentRemoteClient`
/// actually uses, not a bespoke decoder, so a mismatch here would also break the real client.
final class ProtocolTests: XCTestCase {
    private func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }

    func testDecodePingWithAllCaps() throws {
        let json = """
        {
          "ok": true, "app": "agentremoted", "version": "2.4.5", "host": "jr-cloud-agent-kr",
          "provider": "claude",
          "caps": {
            "queue": true, "stop": true, "projects": true, "ws_status": true,
            "permissions": true, "permission_modes": true, "requires_cwd": true,
            "can_set_model": true, "can_set_effort": false, "can_show_usage": true,
            "interactive": true, "live_tui": true, "rewind": true
          },
          "slash_commands": ["/briefing", "/clear"],
          "models": ["default", "claude-opus-5"],
          "efforts": [],
          "drop_path": "/home/jr/.bb10d/drop"
        }
        """
        let ping = try decoder().decode(PingResponse.self, from: Data(json.utf8))
        XCTAssertEqual(ping.app, "agentremoted")
        XCTAssertEqual(ping.version, "2.4.5")
        XCTAssertTrue(ping.caps.liveTuiEnabled)
        XCTAssertTrue(ping.caps.queue)
        XCTAssertFalse(ping.caps.canSetEffort)
        XCTAssertEqual(ping.slashCommands, ["/briefing", "/clear"])
        XCTAssertEqual(ping.dropPath, "/home/jr/.bb10d/drop")
    }

    func testPingMissingLiveTuiCapDefaultsFalse() throws {
        // Older daemon builds omit `live_tui` entirely — must decode as false, not crash or assume true.
        let json = """
        {"ok": true, "app": "bb10d", "version": "1.9.0", "host": "h", "provider": "claude",
         "caps": {"queue": true, "stop": true, "projects": true, "ws_status": true,
                  "permissions": true, "permission_modes": true, "requires_cwd": true,
                  "can_set_model": true, "can_set_effort": false, "can_show_usage": true,
                  "interactive": true, "rewind": true}}
        """
        let ping = try decoder().decode(PingResponse.self, from: Data(json.utf8))
        XCTAssertFalse(ping.caps.liveTuiEnabled)
        XCTAssertNil(ping.caps.liveTui)
    }

    func testDecodeProjectsWithFloatEpochDate() throws {
        let json = """
        {"projects": [
          {"id": "-home-jr-claude", "cwd": "/home/jr/claude", "name": "claude", "session_count": 6, "last_active": 1785987668.75}
        ]}
        """
        let response = try decoder().decode(ProjectsResponse.self, from: Data(json.utf8))
        XCTAssertEqual(response.projects.count, 1)
        XCTAssertEqual(response.projects[0].sessionCount, 6)
        XCTAssertEqual(response.projects[0].lastActive.date.timeIntervalSince1970, 1785987668.75, accuracy: 0.01)
    }

    func testDecodeSessionsWithISO8601Date() throws {
        let json = """
        {"sessions": [
          {"id": "469fffcd-ff3b-4127-b104-79c5968c269d", "project_id": "-home-jr-claude",
           "cwd": "/home/jr/claude", "git_branch": "main", "title": "PONG",
           "started": "2026-08-06T03:43:17.928Z", "last_active": "2026-08-06T03:43:21.128Z",
           "last_role": "assistant", "last_text": "PONG", "model": "claude-sonnet-5", "size_bytes": 13152}
        ]}
        """
        let response = try decoder().decode(SessionsResponse.self, from: Data(json.utf8))
        let session = try XCTUnwrap(response.sessions.first)
        XCTAssertEqual(session.id, "469fffcd-ff3b-4127-b104-79c5968c269d")
        XCTAssertEqual(session.gitBranch, "main")
        XCTAssertEqual(session.sizeBytes, 13152)
        // Both fixtures parse to real dates regardless of float-epoch vs ISO8601 wire shape.
        XCTAssertGreaterThan(session.started.date.timeIntervalSince1970, 0)
    }

    func testDecodeMessagesResponse() throws {
        let json = """
        {"session_id": "469fffcd-ff3b-4127-b104-79c5968c269d", "total": 2, "offset": 0,
         "messages": [
           {"uuid": "a8cf", "role": "user", "ts": "2026-08-06T03:43:18.324Z", "text": "Reply with exactly the word: PONG", "blocks": []},
           {"uuid": "0c78", "role": "assistant", "ts": "2026-08-06T03:43:21.128Z", "text": "PONG", "blocks": []}
         ]}
        """
        let response = try decoder().decode(MessagesResponse.self, from: Data(json.utf8))
        XCTAssertEqual(response.total, 2)
        XCTAssertEqual(response.messages.map(\.role), ["user", "assistant"])
        XCTAssertEqual(response.messages.last?.text, "PONG")
    }

    func testDecodeJobSnapshotEvents() throws {
        let json = """
        {
          "id": "722c3ad62289", "session_id": "", "new_session_id": "469fffcd-ff3b-4127-b104-79c5968c269d",
          "status": "done", "error": "", "result_text": "PONG",
          "pending_permission": null, "pending_question": null,
          "queued": [], "next_job_id": "", "dropped_queued": 0, "next_seq": 3,
          "events": [
            {"seq": 0, "kind": "init", "session_id": "469fffcd-ff3b-4127-b104-79c5968c269d", "model": "claude-sonnet-5"},
            {"seq": 1, "kind": "text", "text": "PONG", "blocks": []},
            {"seq": 2, "kind": "result", "is_error": false, "duration_ms": 2879, "cost_usd": 0.0630829}
          ]
        }
        """
        let job = try decoder().decode(JobSnapshot.self, from: Data(json.utf8))
        XCTAssertEqual(job.status, .done)
        XCTAssertEqual(job.resultText, "PONG")
        XCTAssertEqual(job.resolvedSessionId, "469fffcd-ff3b-4127-b104-79c5968c269d")
        XCTAssertNil(job.pendingPermission)
        XCTAssertEqual(job.events.count, 3)

        guard case .initEvent(0, let sessionId, let model) = job.events[0] else {
            return XCTFail("expected init event, got \(job.events[0])")
        }
        XCTAssertEqual(sessionId, "469fffcd-ff3b-4127-b104-79c5968c269d")
        XCTAssertEqual(model, "claude-sonnet-5")

        guard case .text(1, let text) = job.events[1] else { return XCTFail("expected text event") }
        XCTAssertEqual(text, "PONG")

        guard case .result(2, let isError, let durationMs, let costUsd) = job.events[2] else {
            return XCTFail("expected result event")
        }
        XCTAssertFalse(isError)
        XCTAssertEqual(durationMs, 2879)
        XCTAssertEqual(costUsd, 0.0630829, accuracy: 0.0000001)
    }

    func testDecodeJobWithPendingPermission() throws {
        let json = """
        {"id": "j1", "session_id": "s1", "new_session_id": "", "status": "running", "error": "",
         "result_text": "", "pending_permission": {"request_id": "j1-p1", "tool_name": "Bash", "detail": "rm -rf /tmp/x"},
         "pending_question": null, "queued": [{"id": "j1-q1", "prompt": "next"}],
         "next_job_id": "", "dropped_queued": 0, "next_seq": 2,
         "events": [{"seq": 0, "kind": "permission", "request_id": "j1-p1", "tool_name": "Bash", "detail": "rm -rf /tmp/x"}]}
        """
        let job = try decoder().decode(JobSnapshot.self, from: Data(json.utf8))
        XCTAssertEqual(job.status, .running)
        XCTAssertEqual(job.pendingPermission?.requestId, "j1-p1")
        XCTAssertEqual(job.pendingPermission?.toolName, "Bash")
        XCTAssertEqual(job.queued.first?.prompt, "next")
        guard case .permission(0, let requestId, let toolName, let detail) = job.events[0] else {
            return XCTFail("expected permission event")
        }
        XCTAssertEqual(requestId, "j1-p1")
        XCTAssertEqual(toolName, "Bash")
        XCTAssertEqual(detail, "rm -rf /tmp/x")
    }

    func testDecodePermissionResolvedWithTimeoutReason() throws {
        let json = """
        {"seq": 5, "kind": "permission_resolved", "request_id": "j1-p1", "allow": false, "reason": "timeout"}
        """
        let event = try decoder().decode(JobEvent.self, from: Data(json.utf8))
        guard case .permissionResolved(5, let requestId, let allow, let reason) = event else {
            return XCTFail("expected permission_resolved event")
        }
        XCTAssertEqual(requestId, "j1-p1")
        XCTAssertFalse(allow)
        XCTAssertEqual(reason, "timeout")
    }

    func testUnknownEventKindFallsBackToUnknown() throws {
        let json = """
        {"seq": 9, "kind": "some_future_kind", "foo": "bar"}
        """
        let event = try decoder().decode(JobEvent.self, from: Data(json.utf8))
        guard case .unknown(9, let kind, let payload) = event else { return XCTFail("expected unknown event") }
        XCTAssertEqual(kind, "some_future_kind")
        XCTAssertEqual(payload["foo"], .string("bar"))
    }

    func testDecodeStatusPush() throws {
        let json = """
        {"type": "status", "active": [
          {"job_id": "j1", "session_id": "s1", "new_session_id": "", "status": "running",
           "prompt": "first 120 chars...", "elapsed_s": 12, "queued_count": 0,
           "tool": "Read", "tool_detail": "/path/to/file.ts",
           "phase": "tool", "phase_detail": "...",
           "pending_permission": false, "pending_question": false, "next_seq": 4}
        ]}
        """
        let push = try decoder().decode(StatusPush.self, from: Data(json.utf8))
        XCTAssertEqual(push.active.count, 1)
        XCTAssertEqual(push.active[0].jobId, "j1")
        XCTAssertEqual(push.active[0].status, .running)
        XCTAssertEqual(push.active[0].tool, "Read")
        XCTAssertEqual(push.active[0].nextSeq, 4)
    }

    func testDecodeUsageBuckets() throws {
        let json = """
        {"ok": true, "buckets": [
          {"title": "5-hour limit", "percent": 3, "resets_text": "Resets in 4 hr 45 min", "severity": "normal"}
        ]}
        """
        let usage = try decoder().decode(UsageResponse.self, from: Data(json.utf8))
        XCTAssertTrue(usage.ok)
        XCTAssertEqual(usage.buckets?.first?.percent, 3)
    }

    func testDecodeErrorBody() throws {
        let json = """
        {"error": "missing or invalid token"}
        """
        let body = try decoder().decode(DaemonErrorBody.self, from: Data(json.utf8))
        XCTAssertEqual(body.error, "missing or invalid token")
    }

    // MARK: - Encoding (request bodies)

    func testEncodeNewSessionRequestSnakeCase() throws {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        let request = NewSessionRequest(cwd: "/home/jr/claude", prompt: "hi", permissionMode: "acceptEdits")
        let data = try encoder.encode(request)
        let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        XCTAssertEqual(obj?["cwd"] as? String, "/home/jr/claude")
        XCTAssertEqual(obj?["prompt"] as? String, "hi")
        XCTAssertEqual(obj?["permission_mode"] as? String, "acceptEdits")
        XCTAssertNil(obj?["model"])
    }

    func testEncodeQuestionAnswerRequest() throws {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        let request = QuestionAnswerRequest(requestId: "j1-q1", answers: [["Yes"]], notes: nil, cancel: nil)
        let data = try encoder.encode(request)
        let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        XCTAssertEqual(obj?["request_id"] as? String, "j1-q1")
        XCTAssertEqual((obj?["answers"] as? [[String]])?.first, ["Yes"])
    }

    func testDecodeMultiProviderPing() throws {
        let json = """
        {
          "ok": true, "app": "agentremoted", "version": "2.5.3", "host": "mac",
          "provider": "claude",
          "multi": true,
          "providers": ["claude", "grok", "codex"],
          "caps": {
            "queue": true, "stop": true, "projects": true, "ws_status": true,
            "permissions": true, "permission_modes": true, "requires_cwd": true,
            "can_set_model": true, "can_set_effort": true, "can_show_usage": true,
            "interactive": true, "live_tui": true, "rewind": true
          },
          "models": ["default", "claude-opus"],
          "efforts": ["low", "high"],
          "provider_details": {
            "claude": {
              "caps": {"can_set_effort": false, "requires_cwd": true},
              "models": ["default", "claude-sonnet"],
              "efforts": [],
              "slash_commands": ["/compact"]
            },
            "grok": {
              "caps": {"can_set_effort": true, "requires_cwd": false},
              "models": ["grok-4"],
              "efforts": ["low", "high"]
            }
          }
        }
        """
        let ping = try decoder().decode(PingResponse.self, from: Data(json.utf8))
        XCTAssertTrue(ping.isMulti)
        XCTAssertEqual(ping.harnesses, ["claude", "grok", "codex"])
        XCTAssertFalse(ping.caps(for: "claude").canSetEffort)
        XCTAssertTrue(ping.caps(for: "grok").canSetEffort)
        XCTAssertEqual(ping.models(for: "claude"), ["default", "claude-sonnet"])
        XCTAssertEqual(ping.efforts(for: "claude"), [])
        XCTAssertEqual(ping.efforts(for: "grok"), ["low", "high"])
    }

    func testDecodeSessionWithProvider() throws {
        let json = """
        {"sessions": [
          {"id": "2b7f6b3b-aee6-4388-b307-acd9c9987d5c", "project_id": "-tmp",
           "cwd": "/tmp", "git_branch": "", "title": "Hello",
           "started": "2026-08-06T03:43:17.928Z", "last_active": "2026-08-06T03:43:21.128Z",
           "last_role": "assistant", "last_text": "Hi", "model": "claude-sonnet",
           "size_bytes": 100, "provider": "claude"}
        ]}
        """
        let response = try decoder().decode(SessionsResponse.self, from: Data(json.utf8))
        XCTAssertEqual(response.sessions.first?.provider, "claude")
        XCTAssertEqual(response.sessions.first?.displayTitle, "Hello")
    }

    func testDecodePendingQuestion() throws {
        let json = """
        {"id": "j1", "session_id": "s1", "new_session_id": "", "status": "running", "error": "",
         "result_text": "", "pending_permission": null,
         "pending_question": {
           "request_id": "j1-q1",
           "questions": [
             {"question": "Ship it?", "header": "Confirm", "options": [
               {"label": "Yes", "description": "Deploy"},
               {"label": "No", "description": ""}
             ], "multi_select": false}
           ]
         },
         "queued": [], "next_job_id": "", "dropped_queued": 0, "next_seq": 1, "events": []}
        """
        let job = try decoder().decode(JobSnapshot.self, from: Data(json.utf8))
        XCTAssertEqual(job.pendingQuestion?.requestId, "j1-q1")
        XCTAssertEqual(job.pendingQuestion?.questions.first?.question, "Ship it?")
        XCTAssertEqual(job.pendingQuestion?.questions.first?.options.count, 2)
    }
}
