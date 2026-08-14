import XCTest

/// On-simulator smoke tour against a LIVE agentremoted daemon.
///
/// Prerequisites (not self-contained on purpose — this exercises the real protocol):
///   - a daemon reachable at http://127.0.0.1:8473 (the Mac running the simulator)
///   - one profile seeded into the app's defaults (see ios/README.md "UI smoke test")
///
/// `testLiveHeadlessTurn` runs one real (tiny) agent turn on the host in /tmp — skip it with
/// `-skip-testing:AgentRemoteUITests/SmokeUITests/testLiveHeadlessTurn` when that's unwanted.
final class SmokeUITests: XCTestCase {

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    private func launch() -> XCUIApplication {
        let app = XCUIApplication()
        app.launch()
        return app
    }

    /// Any session row — every row shows its host cwd, which always starts with a slash-path.
    private func firstSessionRow(_ app: XCUIApplication) -> XCUIElement {
        app.buttons.matching(NSPredicate(format: "label CONTAINS '/Users/'")).firstMatch
    }

    /// The composer. SwiftUI exposes a `TextField(axis: .vertical)` as a text VIEW once it can
    /// grow, and as a text field before that — accept either.
    private func chatInput(_ app: XCUIApplication) -> XCUIElement {
        let field = app.textFields["chat.input"]
        if field.exists { return field }
        return app.textViews["chat.input"]
    }

    private func waitForChatInput(_ app: XCUIApplication, timeout: TimeInterval) -> XCUIElement? {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            let input = chatInput(app)
            if input.exists { return input }
            RunLoop.current.run(until: Date().addingTimeInterval(0.5))
        }
        return nil
    }

    private func openMoreMenu(_ app: XCUIApplication, item: String) {
        let more = app.buttons["sessions.more"]
        XCTAssertTrue(more.waitForExistence(timeout: 10), "sessions overflow menu missing")
        more.tap()
        let entry = app.buttons[item]
        XCTAssertTrue(entry.waitForExistence(timeout: 5), "menu item \(item) missing")
        entry.tap()
    }

    func testSessionsListLoadsAndFocusToggles() {
        let app = launch()
        XCTAssertTrue(firstSessionRow(app).waitForExistence(timeout: 20),
                      "no session rows loaded from the daemon")

        // Focus chip only exists against a daemon ≥ 2.6 — ours is 2.6.5.
        let focusChip = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Focus'")).firstMatch
        XCTAssertTrue(focusChip.waitForExistence(timeout: 10), "Focus chip missing")
        focusChip.tap()
        // Focus list may be empty or populated — either way the list must stay alive.
        XCTAssertTrue(app.navigationBars["Sessions"].waitForExistence(timeout: 10))
        let allChip = app.buttons["All"]
        XCTAssertTrue(allChip.waitForExistence(timeout: 5))
        allChip.tap()
        XCTAssertTrue(firstSessionRow(app).waitForExistence(timeout: 20),
                      "rows did not come back after leaving Focus mode")
    }

    func testDropInboxListsHostFiles() {
        let app = launch()
        _ = firstSessionRow(app).waitForExistence(timeout: 20)
        openMoreMenu(app, item: "Files from host")

        XCTAssertTrue(app.navigationBars["Files from host"].waitForExistence(timeout: 10))
        // The Mac daemon's drop folder is ~/Public — the Android release APK always lands there.
        let apk = app.staticTexts["AgentRemote.apk"]
        XCTAssertTrue(apk.waitForExistence(timeout: 20), "expected ~/Public contents in the inbox")
        // Daemon 2.6.5 hides the macOS "Drop Box" share folder from the listing.
        XCTAssertFalse(app.staticTexts["Drop Box"].exists, "protected 'Drop Box' folder leaked into the inbox")
        app.buttons["Done"].tap()
    }

    func testUsageShowsMergedSections() {
        let app = launch()
        _ = firstSessionRow(app).waitForExistence(timeout: 20)
        openMoreMenu(app, item: "Usage")

        XCTAssertTrue(app.navigationBars["Usage"].waitForExistence(timeout: 10))
        // Bucket rows show "<percent>%" — grok's scrape can take a while, so be generous.
        let percent = app.staticTexts.matching(NSPredicate(format: "label ENDSWITH '%'")).firstMatch
        XCTAssertTrue(percent.waitForExistence(timeout: 90), "no usage buckets rendered")
        app.buttons["Done"].tap()
    }

    func testOpenTranscriptLoadsHistory() {
        let app = launch()
        let row = firstSessionRow(app)
        XCTAssertTrue(row.waitForExistence(timeout: 20))
        row.tap()

        XCTAssertNotNil(waitForChatInput(app, timeout: 15), "composer missing on the transcript")
        // History actually arrived: some message text is on screen beyond the chrome.
        let anyMessage = app.staticTexts.matching(
            NSPredicate(format: "label MATCHES %@", "(?s).{40,}")
        ).firstMatch
        XCTAssertTrue(anyMessage.waitForExistence(timeout: 20), "transcript looks empty")
    }

    /// Process view: toggling it paints step rows from `?detail=steps`, and toggling it
    /// off drops them again. Uses the idle live-turn scratch session (a running session
    /// defers its reload to turn end, and this test must not depend on turn timing).
    func testProcessViewShowsSteps() {
        let app = launch()
        _ = firstSessionRow(app).waitForExistence(timeout: 20)
        // The scratch session ages down the list — scroll until its row is
        // tappable. (Search-open is exercised manually; XCUITest fights the
        // searchable overlay's duplicate element tree.)
        let query = app.buttons.matching(
            NSPredicate(format: "label CONTAINS 'agent-remote-uitest'")
        )
        var row: XCUIElement?
        for _ in 0..<12 {
            if let hit = query.allElementsBoundByIndex.first(where: { $0.isHittable }) {
                row = hit
                break
            }
            app.swipeUp()
        }
        guard let row else {
            XCTFail("no scratch session row — run testLiveHeadlessTurn first")
            return
        }
        row.tap()
        XCTAssertNotNil(waitForChatInput(app, timeout: 15))

        let more = app.buttons["chat.more"]
        XCTAssertTrue(more.waitForExistence(timeout: 10))
        more.tap()
        let toggle = app.buttons["Process view"]
        XCTAssertTrue(toggle.waitForExistence(timeout: 5))
        toggle.tap()

        // A step row is one Button whose flattened label starts with its mark
        // (▸ tool, ↳ result, ✻ thinking) — SwiftUI merges the child Texts.
        let stepRow = app.buttons.matching(NSPredicate(
            format: "label BEGINSWITH '▸' OR label BEGINSWITH '↳' OR label BEGINSWITH '✻'"
        )).firstMatch
        XCTAssertTrue(stepRow.waitForExistence(timeout: 30), "no step rows painted")

        // Cleanup: off again — steps must leave the transcript.
        more.tap()
        app.buttons["Process view"].tap()
        let gone = NSPredicate(format: "exists == false")
        let expectation = XCTNSPredicateExpectation(predicate: gone, object: stepRow)
        XCTAssertEqual(XCTWaiter.wait(for: [expectation], timeout: 30), .completed,
                       "step rows survived toggling process view off")
    }

    /// End-to-end proof: new headless session in /tmp → one real turn → reply renders.
    func testLiveHeadlessTurn() {
        let app = launch()
        _ = firstSessionRow(app).waitForExistence(timeout: 20)

        app.buttons["sessions.new"].tap()
        XCTAssertTrue(app.navigationBars["New session"].waitForExistence(timeout: 10))

        // The cwd field and Execution toggle sit below up to 20 project rows, and SwiftUI only
        // materializes visible Form rows in the accessibility tree — scroll until they exist.
        let cwdField = app.textFields["newsession.cwd"]
        let interactive = app.switches["newsession.interactive"]
        var swipes = 0
        while !interactive.exists && swipes < 8 {
            app.swipeUp()
            swipes += 1
        }
        // Headless: a one-shot CLI turn, no host TUI to clean up afterwards.
        if interactive.waitForExistence(timeout: 5), (interactive.value as? String) == "1" {
            interactive.switches.firstMatch.tap()
        }
        XCTAssertTrue(cwdField.waitForExistence(timeout: 10), "cwd field missing")
        // Wipe the auto-filled project pick via the clear button — cursor games with a
        // middle-truncated path merge the two strings — then point at the scratch dir.
        let clear = app.buttons["newsession.cwd.clear"]
        if clear.exists { clear.tap() }
        cwdField.tap()
        cwdField.typeText("/tmp/agent-remote-uitest")

        app.buttons["Start"].tap()

        guard let input = waitForChatInput(app, timeout: 15) else {
            XCTFail("composer missing after Start")
            return
        }
        input.tap()
        input.typeText("Reply with exactly: PONG42 — nothing else, no punctuation.")
        app.buttons["chat.send"].tap()

        // The echo of our own prompt CONTAINS the token; the reply IS the token. Exact match only.
        let reply = app.staticTexts["PONG42"]
        XCTAssertTrue(reply.waitForExistence(timeout: 180), "no agent reply rendered")
    }
}
