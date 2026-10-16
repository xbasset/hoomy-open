import XCTest
@testable import HoomyPilot

final class ActModelsTests: XCTestCase {
    func testResearchGraphAndTargetedFeedbackDecode() throws {
        let wire = """
        {"request_id":"r","status":"ready","background":"Idle","next_step":"Clarify the goal",
         "projects":[{"id":"p","name":"Example"}],
         "nodes":[{"id":"request","column":0,"title":"Request","status":"done","detail":"Five minutes"},
                  {"id":"outcome","column":4,"title":"Outcome","status":"blocked","detail":"Goal unknown"}],
         "edges":[{"source":"request","target":"outcome"}],
         "feedback":[{"id":"f","node_id":"outcome","text":"The goal is personal use","at":"2026-10-07"}]}
        """
        let graph = try APIClient().decode(ResearchGraph.self, from: Data(wire.utf8))
        XCTAssertEqual(graph.feedback[0].nodeId, graph.edges[0].target)
        XCTAssertEqual(graph.nodes[1].column, 4)
        XCTAssertEqual(graph.nodes[1].status, "blocked")
    }
    func testOpenProjectQuestionKeepsResearchAndFreeAnswerSeparateFromCompletion() throws {
        let wire = """
        {"ideas":[],"policy":"ready-ideas-v2","selections":[
          {"id":"flow","kind":"context_question","status":"ready","message":"Clarify the goal","questions":[],
           "research_report":{"request_id":"r","summary":"Read the playback discussion",
             "unresolved":["The intended audience is unknown"],"checks":[],
             "sources":[{"key":"s","title":"Playback","read":true}]},
           "context_question":{"request_id":"r","project_id":"p","project_name":"Example","dimension":"goal",
             "prompt":"Who should benefit?","why":"The intended audience is absent",
             "changes_decision":"This changes the useful next review","options":["Myself","Testers"]},
           "context_answer":{"answer":"Personal use","response":"answer","answered_at":"2026-10-07"}}]}
        """
        let state = try APIClient().decode(ReadyIdeasState.self, from: Data(wire.utf8))
        let selection = state.selections[0]
        XCTAssertEqual(selection.contextQuestion?.options.count, 2)
        XCTAssertEqual(selection.contextAnswer?.answer, "Personal use")
        XCTAssertEqual(selection.researchReport?.sources.first?.title, "Playback")
        XCTAssertNil(selection.activity)
        XCTAssertFalse(selection.isPending)
    }
    func testAttentionDurationUsesTheHumanRequest() {
        XCTAssertEqual(explicitAttentionMinutes("dimanche soir, j’ai 15 minutes devant moi"), 15)
        XCTAssertEqual(explicitAttentionMinutes("J’ai 2h"), 120)
        XCTAssertNil(explicitAttentionMinutes("un moment tranquille"))
    }

    func testPreparedStateAndFastSelectionDecode() throws {
        let wire = """
        {"ideas":[],"preparation":null,"policy":"ready-ideas-v1","selections":[
        {"id":"choice","kind":"choosing","status":"queued","message":"","activity":null,"questions":[]}]}
        """
        let state = try APIClient().decode(ReadyIdeasState.self, from: Data(wire.utf8))
        XCTAssertTrue(state.selections[0].isPending)
        XCTAssertNil(state.selections[0].activity)
        XCTAssertTrue(state.ideas.isEmpty)
    }

    func testQueuedAndCompletedWireFormat() throws {
        let client = APIClient()
        let queued = """
        {"id":"r","created_at":"2026-09-26T10:00:00Z","status":"queued",
        "instruction":"","attention_budget":30,"candidates":[],"selected_id":null,
        "rationale":"","policy":"act-learn-v1","sources":[]}
        """
        let request = try client.decode(ActivityRequest.self, from: Data(queued.utf8))
        XCTAssertTrue(request.isPending)
        XCTAssertNil(request.selected)
        XCTAssertNil(request.feedback)
        let ready = """
        {"id":"r","created_at":"2026-09-26T10:00:00Z","status":"ready",
        "instruction":"Check playback","attention_budget":30,"policy":"act-learn-v1",
        "candidates":[{"id":"a","project_id":"p","title":"Check","action":"Reproduce playback",
        "why_now":"Recent report","expected_impact":3,"expected_attention_minutes":20,"source_refs":["s"]}],
        "selected_id":"a","rationale":"Useful","sources":[],
        "feedback":{"decision":"done","impact":0,"attention_minutes":0,"reason":"","updated_at":"2026-09-26"}}
        """
        let completed = try client.decode(ActivityRequest.self, from: Data(ready.utf8))
        XCTAssertFalse(completed.isPending)
        XCTAssertEqual(completed.selected?.expectedAttentionMinutes, 20)
        XCTAssertEqual(completed.feedback?.impact, 0)
        XCTAssertEqual(completed.feedback?.attentionMinutes, 0)
    }

    func testBackgroundQuestionPoolAndRecoveryDecode() throws {
        let wire = """
        {"ideas":[],"ready_questions":[],"policy":"ready-ideas-v2",
         "question_pool":{"active":10,"ready":4,"checking":3,"maximum":10,"target":5},
         "preparation":{"id":"background","status":"running","stage":"verify"},
         "selections":[{"id":"flow","kind":"preparing","status":"preparing","message":"Working",
          "questions":[],"context":{"instruction":"Five minutes","attention_budget":5,"project_id":null,"questions_answered":2}}]}
        """
        let state = try APIClient().decode(ReadyIdeasState.self, from: Data(wire.utf8))
        XCTAssertEqual(state.questionPool?.ready, 4)
        XCTAssertTrue(state.selections[0].isPreparing)
        XCTAssertFalse(state.selections[0].isPending)
        XCTAssertEqual(state.selections[0].context?.questionsAnswered, 2)
        XCTAssertEqual(state.preparation?.stage, "verify")
    }
}
