import Foundation

// Models and review views stay platform-neutral; only the app shell is macOS-specific.
struct DiscoveryState: Decodable {
    var reviewBlocked: Bool { control.running && !(overview?.completionPhase == "agent" && overview?.agentContinuous == true) }
    let control: DiscoveryControl
    let coverage: Coverage
    let worker: Worker
    let projects: [Project]
    let changes: [UnderstandingChange]
    let sessions: [SessionSummary]
    let discoveryFailures: [DiscoveryFailure]?
    let overview: ProjectOverview?
}

struct DiscoveryFailure: Decodable {
    let sourceId: String
    let reason: String
}

struct DiscoveryControl: Decodable {
    let running: Bool
    let inferenceEnabled: Bool
    let scopeConfirmed: Bool
    let generation: String?
    let exclusions: [String]
    let mode: String?
}

struct ProjectOverview: Decodable {
    let agentContinuous: Bool?
    let automaticAssociations: [AutomaticAssociation]?
    let agentRemaining: Int?
    let agentChecked: Int?
    let agentProgress: AgentProgress?
    let completionPhase: String?
    let sampled: Int?
    let requestId: String?
    let stage: String
    let inventoryComplete: Bool
    let indexed: Int
    let remaining: Int
    let considered: Int
    let model: String?
    let elapsedSeconds: Double?
    let message: String?
    let candidates: [ProjectOutline]
    let deepDone: Int
    let deepTotal: Int
    let deepFailures: [DeepFailure]?
    let failures: [DiscoveryFailure]
    let examined: Int?
    let unexamined: Int?
    let decided: Int?
    let linked: Int?
    let pendingSources: Int?
    let noProject: Int?
    let ambiguous: Int?
    let passDone: Int?
    let passTotal: Int?
    let passNew: Int?
    let passReexamined: Int?
    let batchesDone: Int?
    let batchesTotal: Int?
    let coverageNote: String?
    let humanNoProject: Int?
    let deferred: Int?
}

struct AutomaticAssociation: Decodable, Identifiable {
    let id: String
    let title: String
    let projectId: String
    let alternativeId: String?
    let currentProjectId: String?
    let rationale: String
    let ambiguityReason: String
    let corrected: Int
}

struct AgentProgress: Decodable {
    let elapsedSeconds: Double
    let events: [AgentProgressEvent]
    let plan: [AgentPlanStep]
    let usage: AgentUsage?
}

struct AgentProgressEvent: Decodable {
    let kind: String
    let text: String
    let elapsedSeconds: Double
}

struct AgentPlanStep: Decodable {
    let step: String
    let status: String
}

struct AgentUsage: Decodable {
    let inputTokens: Int?
    let outputTokens: Int?
    let totalTokens: Int?
}

struct DeepFailure: Decodable {
    let sessionId: String
    let reason: String
}

struct ProjectOutline: Decodable, Identifiable {
    let id: String
    let name: String
    let purpose: String
    let rationale: String
    let existingProjectId: String?
    let sources: [OutlineSource]
}

struct OutlineSource: Decodable, Identifiable {
    let excerptText: String?
    let id: String
    let title: String
    let directory: String
}

struct Coverage: Decodable {
    let discovered: Int
    let analyzed: Int
    let pending: Int
    let failed: Int
    let excluded: Int
}

struct Worker: Decodable {
    let lastSeen: String?
    let status: String?
    let message: String?
}

struct Project: Decodable, Identifiable, Hashable {
    let id: String
    let name: String
    let purpose: String
    let status: String
    let confirmedFields: [String]
    let note: String?
    let dismissed: Bool
    let sessionIds: [String]
    let updatedAt: String
    let activity: String?
    let recognitionRules: String?

    var isInactive: Bool { activity == "inactive" }
}

struct CatalogConversation: Decodable, Identifiable {
    let excerptText: String?
    let id: String
    let title: String
    let directory: String
    let projectIds: [String]
    let outcome: String
    let reason: String
}

struct RecognitionMemory: Decodable, Identifiable {
    let id: String
    let name: String
    let examples: [RecognitionExample]
    let patternHints: [String]
}

struct RecognitionExample: Decodable {
    let title: String
    let folder: String
    let belongs: Bool
}

struct UnderstandingChange: Decodable, Identifiable {
    let id: String
    let projectId: String?
    let kind: String
    let summary: String
    let before: JSONValue?
    let after: JSONValue?
    let evidence: JSONValue?
    let createdAt: String

    var snapshotIds: [String] {
        guard case .array(let items) = evidence else { return [] }
        var result: [String] = []
        for item in items {
            if case .object(let fields) = item,
               case .string(let id) = fields["snapshot_id"], !result.contains(id) {
                result.append(id)
            }
        }
        return result
    }
}

struct SessionSummary: Decodable, Identifiable {
    let id: String
    let snapshotId: String
    let title: String
    let modifiedAt: String
    let status: String
    let warning: String?
}

struct SessionDetail: Decodable, Identifiable {
    let id: String
    let snapshotId: String
    let title: String
    let modifiedAt: String
    let status: String
    let warning: String?
    let messages: [ConversationMessage]
    let brief: ConversationBrief?
    let review: SavedReview?
}

struct ConversationMessage: Decodable, Identifiable {
    let id: String
    let role: String
    let text: String
    let lineStart: Int
    let lineEnd: Int
    let timestamp: String?
}

struct ConversationBrief: Decodable {
    let intentions: [Statement]
    let outcomes: [Statement]
    let openQuestions: [Statement]
    let nextSteps: [Statement]

    var sections: [(key: String, title: String, statements: [Statement])] {
        [("intentions", "Intentions", intentions), ("outcomes", "Reported outcomes", outcomes),
         ("open_questions", "Unresolved questions", openQuestions), ("next_steps", "Possible next steps", nextSteps)]
    }
}

struct Statement: Decodable {
    let text: String
    let kind: String
    let evidence: [Evidence]
}

struct Evidence: Decodable, Hashable {
    let messageId: String
    let quote: String
}

struct SavedReview: Decodable {
    let verdict: String
    let seconds: Double
    let evidenceValid: Bool
    let edits: [StatementEdit]
}

struct StatementEdit: Codable {
    let section: String
    let index: Int
    let text: String?

    // A removed statement must be encoded as JSON null, not an absent key.
    func encode(to encoder: Encoder) throws {
        var values = encoder.container(keyedBy: CodingKeys.self)
        try values.encode(section, forKey: .section)
        try values.encode(index, forKey: .index)
        if let text { try values.encode(text, forKey: .text) }
        else { try values.encodeNil(forKey: .text) }
    }
}

enum JSONValue: Decodable {
    case string(String), number(Double), bool(Bool), object([String: JSONValue]), array([JSONValue]), null

    init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer()
        if value.decodeNil() { self = .null }
        else if let v = try? value.decode(String.self) { self = .string(v) }
        else if let v = try? value.decode(Bool.self) { self = .bool(v) }
        else if let v = try? value.decode(Double.self) { self = .number(v) }
        else if let v = try? value.decode([String: JSONValue].self) { self = .object(v) }
        else { self = .array(try value.decode([JSONValue].self)) }
    }

    var readable: String {
        switch self {
        case .string(let value): return value
        case .number(let value): return String(value)
        case .bool(let value): return value ? "Yes" : "No"
        case .null: return "—"
        case .array(let values): return values.map(\.readable).joined(separator: ", ")
        case .object(let values):
            return values.keys.sorted().map { "\($0.replacingOccurrences(of: "_", with: " ")): \(values[$0]!.readable)" }.joined(separator: "\n")
        }
    }
}

func displayDate(_ value: String?) -> String {
    guard let value else { return "Not yet connected" }
    let parser = ISO8601DateFormatter()
    parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    let date = parser.date(from: value) ?? ISO8601DateFormatter().date(from: value)
    guard let date else { return value }
    return date.formatted(date: .abbreviated, time: .shortened)
}
