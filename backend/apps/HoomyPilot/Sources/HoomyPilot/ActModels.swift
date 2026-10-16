import Foundation

struct ActState: Decodable {
    let requests: [ActivityRequest]
    let metrics: ActivityMetrics
}

struct ActivityMetrics: Decodable {
    let completed: Int
    let impact: Double?
    let attention: Double?
    let impactError: Double?
    let attentionError: Double?
}

struct ActivityCandidate: Decodable, Identifiable {
    let id: String
    let projectId: String
    let title: String
    let action: String
    let whyNow: String
    let expectedImpact: Int
    let expectedAttentionMinutes: Double
    let sourceRefs: [String]
    let completionClaim: String?
    let needsConfirmation: Bool?
    let actor: String?
    let stateOwner: String?
    let stateQuestion: String?
    let questionReason: String?
}

struct ActivityFeedback: Decodable {
    let decision: String
    let impact: Int?
    let attentionMinutes: Double?
    let reason: String
    let updatedAt: String
}

struct ActivitySource: Decodable, Identifiable {
    var id: String { key }
    let key: String
    let title: String
    let modifiedAt: String
    let warning: String?
    let messages: [ActivityMessage]
}

struct ActivityMessage: Decodable, Identifiable {
    let id: String
    let role: String
    let text: String
    let truncated: Bool
}

struct ActivityRequest: Decodable, Identifiable {
    let id: String
    let createdAt: String
    let status: String
    let instruction: String
    let attentionBudget: Int
    let candidates: [ActivityCandidate]
    let selectedId: String?
    let rationale: String
    let uncertainty: String?
    let error: String?
    let model: String?
    let policy: String
    let elapsedSeconds: Double?
    let sources: [ActivitySource]
    let feedback: ActivityFeedback?
    let confirmation: ActivityConfirmation?
    let purpose: String?
    let projectScope: String?
    let researchReport: ResearchReport?
    var selected: ActivityCandidate? { candidates.first { $0.id == selectedId } }
    var isPending: Bool { status == "queued" || status == "running" }
    var label: String {
        if let selected { return selected.title }
        switch status {
        case "queued": return "En attente du service local"
        case "running": return "Recherche dans les conversations…"
        case "failed": return "Recherche interrompue"
        case "cancelled": return "Demande arrêtée"
        default: return researchReport == nil ? "Aucune activité suffisamment étayée" : "Bilan de la recherche"
        }
    }
}

struct ResearchSource: Decodable, Identifiable {
    var id: String { key }
    let key: String
    let title: String
    let read: Bool
    let modifiedAt: String?
}

struct ResearchReport: Decodable {
    let requestId: String
    let summary: String
    let unresolved: [String]
    let sources: [ResearchSource]
    let checks: [String]
    let elapsedSeconds: Double?
}

struct ProjectIntentQuestion: Decodable {
    let requestId: String
    let projectId: String
    let projectName: String
    let dimension: String
    let prompt: String
    let why: String
    let changesDecision: String
    let options: [String]
    let sourceRefs: [String]?
    var readableWhy: String {
        (sourceRefs ?? []).reduce(why) { text, reference in text.replacingOccurrences(of: reference, with: "consultée") }
    }
}

struct ProjectIntentAnswer: Decodable {
    let answer: String
    let response: String
    let answeredAt: String
}


struct ActivityConfirmation: Decodable {
    let claim: String
    let answer: String
    let confirmedAt: String
    var label: String {
        switch answer {
        case "already_done": return "Déjà fait · confirmé par toi"
        case "todo": return "Reste à faire · confirmé par toi"
        default: return "État inconnu · aucune conclusion déduite"
        }
    }
}

func explicitAttentionMinutes(_ text: String) -> Int? {
    let expression = try! NSRegularExpression(pattern: #"(?<![\d.,-])(\d+)\s*(minutes?|mins?|heures?|h)(?!\w)"#, options: .caseInsensitive)
    let ns = text as NSString
    guard let match = expression.firstMatch(in: text, range: NSRange(location: 0, length: ns.length)),
          let value = Int(ns.substring(with: match.range(at: 1))) else { return nil }
    let unit = ns.substring(with: match.range(at: 2)).lowercased()
    guard value <= 10_000 else { return 10_000 }
    return unit.hasPrefix("h") ? value * 60 : value
}
