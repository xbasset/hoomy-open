import XCTest
@testable import HoomyPilot

final class ModelsTests: XCTestCase {
    func testAutomaticAssociationKeepsAmbiguitySeparateFromHumanCorrection() throws {
        let data = Data(#"{"id":"source","title":"Irrigation","project_id":"orchid","alternative_id":"garden","current_project_id":"garden","rationale":"Evidence","ambiguity_reason":"Shared topic","corrected":1}"#.utf8)
        let row = try APIClient().decode(AutomaticAssociation.self, from: data)
        XCTAssertEqual(row.alternativeId, "garden")
        XCTAssertEqual(row.currentProjectId, "garden")
        XCTAssertEqual(row.corrected, 1)
    }
    func testAgentPublicProgressDecodes() throws {
        let data = Data(#"{"elapsed_seconds":12.5,"events":[{"kind":"tool","text":"Searching","elapsed_seconds":10}],"plan":[{"step":"Inspect","status":"inProgress"}],"usage":{"inputTokens":100,"outputTokens":20,"totalTokens":120}}"#.utf8)
        let progress = try APIClient().decode(AgentProgress.self, from: data)
        XCTAssertEqual(progress.events.first?.kind, "tool")
        XCTAssertEqual(progress.usage?.inputTokens, 100)
        XCTAssertEqual(progress.elapsedSeconds, 12.5)
    }
    func testEmptyStateDecodesScopeAndCoverage() throws {
        let data = Data(#"{"control":{"running":false,"inference_enabled":false,"scope_confirmed":false,"exclusions":[]},"coverage":{"discovered":0,"analyzed":0,"pending":0,"failed":0,"excluded":0},"worker":{"last_seen":null,"status":null,"message":null},"projects":[],"changes":[],"sessions":[]}"#.utf8)
        let state = try APIClient().decode(DiscoveryState.self, from: data)
        XCTAssertFalse(state.control.scopeConfirmed)
        XCTAssertFalse(state.control.running)
        XCTAssertEqual(state.coverage.discovered, 0)
        XCTAssertTrue(state.projects.isEmpty)
    }

    func testRemovedStatementEncodesExplicitNull() throws {
        let data = try JSONEncoder().encode(StatementEdit(section: "intentions", index: 0, text: nil))
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertTrue(object["text"] is NSNull)
    }

    func testChangeDescriptionIsDeterministic() throws {
        let value = try JSONDecoder().decode(JSONValue.self, from: Data(#"{"status":"paused","confirmed":true}"#.utf8))
        XCTAssertEqual(value.readable, "confirmed: Yes\nstatus: paused")
    }

    func testIDCannotInjectAnotherPath() {
        XCTAssertEqual(APIClient().safeID("a/b?c"), "a%2Fb%3Fc")
    }

    func testDateFormatterDoesNotInventDates() {
        XCTAssertEqual(displayDate(nil), "Not yet connected")
        XCTAssertEqual(displayDate("unknown"), "unknown")
    }

    func testActivityIsIndependentFromInferredStatus() throws {
        let data = Data(#"{"id":"shape","name":"Shape","purpose":"Build shape","status":"unclear","confirmed_fields":["activity"],"dismissed":false,"session_ids":[],"updated_at":"2026-01-01T00:00:00Z","activity":"inactive","recognition_rules":"Match by purpose"}"#.utf8)
        let project = try APIClient().decode(Project.self, from: data)
        XCTAssertTrue(project.isInactive)
        XCTAssertEqual(project.status, "unclear")
        XCTAssertEqual(project.recognitionRules, "Match by purpose")
    }

    func testCumulativeCoverageAndBatchProgressDecodeSeparately() throws {
        let data = Data(#"{"stage":"classifying","inventory_complete":true,"indexed":20,"remaining":12,"considered":5,"candidates":[],"deep_done":0,"deep_total":0,"failures":[],"examined":8,"unexamined":12,"pass_done":3,"pass_total":15,"batches_done":1,"batches_total":5}"#.utf8)
        let overview = try APIClient().decode(ProjectOverview.self, from: data)
        XCTAssertEqual(overview.examined, 8)
        XCTAssertEqual(overview.considered, 5)
        XCTAssertEqual(overview.passDone, 3)
        XCTAssertEqual(overview.batchesTotal, 5)
    }
}
