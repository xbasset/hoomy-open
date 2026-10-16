import XCTest
@testable import HoomyPilot

final class WizardTests: XCTestCase {
    func phase(scope: Bool = true, inference: Bool = true, running: Bool = false,
               stage: String = "review", proposals: Int = 0, projects: Int = 0,
               sampled: Int = 0, finished: Bool = false) -> WizardStage {
        WizardStage.resolve(scope: scope, inference: inference, running: running, stage: stage,
            indexed: 10, proposals: proposals, projects: projects, sampled: sampled, finished: finished)
    }
    func testFreshInstallAsksForLocalAccessFirst() {
        XCTAssertEqual(phase(scope: false, inference: false), .access)
    }
    func testLocalReadingDoesNotImplyCloudPermission() {
        XCTAssertEqual(phase(inference: false, running: true), .inventory)
        XCTAssertEqual(phase(inference: false, stage: "awaiting_consent"), .permission)
    }
    func testReviewPrecedesOptionalDeepening() {
        XCTAssertEqual(phase(proposals: 2, projects: 1), .review)
        XCTAssertEqual(phase(projects: 1), .deepen)
        XCTAssertEqual(phase(projects: 1, sampled: 2), .ready)
    }
    func testExistingMapAndDeferredProposalsDoNotForceRestart() {
        XCTAssertEqual(phase(projects: 15, sampled: 514), .ready)
        XCTAssertEqual(phase(proposals: 2, projects: 2, finished: true), .ready)
    }
    func testPauseAndEmptyAreNotCompletion() {
        XCTAssertEqual(phase(stage: "error", projects: 1), .paused)
        XCTAssertEqual(phase(), .empty)
        XCTAssertEqual(phase(running: true, stage: "classifying"), .working)
    }
}
