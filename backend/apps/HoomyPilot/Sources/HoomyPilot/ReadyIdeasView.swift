import SwiftUI

struct PreparedIdea: Decodable, Identifiable {
    let id: String
    let projectId: String
    let projectName: String?
    let version: Int
    let revision: Int
    let candidate: ActivityCandidate
    let state: String
    let effectiveState: String?
    let confirmedAt: String?
    let validUntil: String?
    let snoozedUntil: String?
    let actor: String?
    let stateOwner: String?
    let poolActive: Bool?
    let questionReview: PreparedQuestionReview?
}

struct PreparedQuestionReview: Decodable {
    let context: ReadyContext?
}

struct PreparationStatus: Decodable {
    let id: String
    let status: String
    let stage: String?
    let sources: [ResearchSource]?
    var label: String {
        switch stage {
        case "retrieve": return "Repérage des conversations pertinentes…"
        case "read": return "Lecture des éléments récents…"
        case "orient": return "Compréhension de ton objectif pour ce projet…"
        case "suggest": return "Recherche d’activités utiles pour toi…"
        case "verify": return "Vérification de l’état par Hoomy…"
        case "reconsider": return "Vérification des pistes retenues…"
        default: return "Recherche prise en charge…"
        }
    }
}

struct ReadyContext: Decodable {
    let instruction: String
    let attentionBudget: Int
    let projectId: String?
    let questionsAnswered: Int
}

struct ReadySelection: Decodable, Identifiable {
    let id: String
    let kind: String
    let status: String
    let message: String
    let activity: ActivityRequest?
    let questions: [PreparedIdea]
    let elapsedSeconds: Double?
    let selectedIdeaId: String?
    let context: ReadyContext?
    let preparationId: String?
    let researchReport: ResearchReport?
    let contextQuestion: ProjectIntentQuestion?
    let contextAnswer: ProjectIntentAnswer?
    var isPending: Bool { status == "queued" || status == "running" }
    var isPreparing: Bool { status == "preparing" }
}

struct ReadyIdeasState: Decodable {
    let ideas: [PreparedIdea]
    let selections: [ReadySelection]
    let preparation: PreparationStatus?
    let preparationError: String?
    let policy: String
    let questionPool: QuestionPool?
    let readyQuestions: [PreparedIdea]?
}

struct QuestionPool: Decodable {
    let active: Int
    let ready: Int
    let checking: Int
    let maximum: Int
    let target: Int
}

struct ReadyIdeasView: View {
    @EnvironmentObject private var store: DiscoveryStore
    @AppStorage("readyIdeasInstruction") private var instruction = ""
    @AppStorage("readyIdeasProject") private var project = ""
    @AppStorage("readyIdeasBudget") private var budget = 5
    @State private var options = false
    @State private var data: ReadyIdeasState?
    @State private var history: ActState?
    @AppStorage("readySelectionID") private var selectionID = ""
    @State private var answered = 0
    @State private var busy = false
    @State private var error: String?
    @State private var notice: String?
    @State private var feedback: ActivityRequest?
    @State private var showHistory = false
    @State private var poolQuestionID: String?
    @State private var intentAnswer = ""
    @State private var explorer: ResearchExplorerTarget?
    @FocusState private var editingRequest: Bool

    private var minutes: Int { explicitAttentionMinutes(instruction) ?? budget }
    private var selection: ReadySelection? { data?.selections.first { $0.id == selectionID } }
    private var scopedIdeas: [PreparedIdea] {
        (data?.ideas ?? []).filter { (project.isEmpty || $0.projectId == project) &&
            $0.candidate.expectedAttentionMinutes <= Double(minutes) &&
            ($0.snoozedUntil == nil || ($0.snoozedUntil ?? "") <= ISO8601DateFormatter().string(from: Date())) }
    }
    private var question: PreparedIdea? {
        if selection?.kind == "question" { return selection?.questions.first }
        return preparedQuestions.first { $0.id == poolQuestionID }
    }
    private var preparedQuestions: [PreparedIdea] {
        Array((data?.readyQuestions ?? []).filter {
            guard let context = $0.questionReview?.context else { return false }
            return context.instruction == instruction && context.attentionBudget == minutes && (context.projectId ?? "") == project
        }.prefix(selection?.kind == "context_question" ? 4 : 5))
    }
    private var preparedQuestionCount: Int { preparedQuestions.count + (selection?.kind == "context_question" ? 1 : 0) }
    private var readyCount: Int { scopedIdeas.filter { ($0.effectiveState ?? $0.state) == "pending" && $0.actor == "user" && $0.poolActive != false }.count }
    private var allowed: Bool { store.state?.control.inferenceEnabled == true && store.state?.control.scopeConfirmed == true }
    private var explorableRequest: String? {
        selection?.preparationId ?? data?.preparation?.id ?? history?.requests.first {
            $0.purpose == "prepare_ideas" && ($0.projectScope ?? "") == project
        }?.id
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("Qu’est-ce qui serait utile maintenant ?").font(.title2.bold())
                Text("Dis-moi ce qui compte maintenant. Je vérifie ce que je peux et te pose seulement les questions qui te concernent.").foregroundStyle(.secondary)
                TextField("Par exemple : j’ai 5 minutes pour avancer sur un projet…", text: $instruction)
                    .textFieldStyle(.roundedBorder).focused($editingRequest)
                HStack {
                    Text("\(minutes) minutes · \(readyCount) idée(s) prête(s)").foregroundStyle(.secondary)
                    Spacer()
                    Button(options ? "Masquer les précisions" : "Préciser ma demande") { options.toggle() }
                }
                if let pool = data?.questionPool {
                    Text("\(preparedQuestionCount) question(s) préparée(s) pour toi · \(pool.active) piste(s) active(s), maximum \(pool.maximum)")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if options {
                    HStack {
                        Picker("Projet", selection: $project) {
                            Text("Mes projets actifs").tag("")
                            ForEach((store.state?.projects ?? []).filter { !$0.dismissed && !$0.isInactive }) { p in
                                Text(p.name).tag(p.id)
                            }
                        }.frame(maxWidth: 420)
                        if explicitAttentionMinutes(instruction) == nil {
                            Stepper("\(budget) min", value: $budget, in: 1...480)
                        }
                    }
                }
                HStack {
                    Button("Trouver une activité") { Task { await newSearch() } }
                        .buttonStyle(.borderedProminent)
                        .disabled(busy || selection?.isPending == true || selection?.isPreparing == true || !allowed || !(1...480).contains(minutes))
                    if selection?.isPending == true {
                        ProgressView().controlSize(.small)
                        Text(selection?.kind == "checking_questions" ? "Je prépare les questions qui te concernent…" : "Choix rapide…").foregroundStyle(.secondary)
                    }
                    if let id = explorableRequest {
                        Button("Explorer cette recherche") { explorer = ResearchExplorerTarget(id: id) }
                    }
                }
                if let error { Text(error).foregroundStyle(.red).textSelection(.enabled) }
                if let notice { Text(notice).foregroundStyle(.secondary) }
                if !allowed { Text("Active le traitement dans Confidentialité pour préparer et choisir des idées.") }
                Divider()
                if let answer = selection?.contextAnswer, answer.response == "answer" {
                    Label("Enregistré pour ce projet : \(answer.answer)", systemImage: "checkmark.circle")
                        .font(.callout).foregroundStyle(.secondary)
                }
                if let report = selection?.researchReport {
                    researchCard(report)
                }
                if let activity = selection?.activity, selection?.kind == "activity" {
                    activityCard(activity)
                } else if let question = selection?.contextQuestion, selection?.kind == "context_question" {
                    intentCard(question)
                } else if let question {
                    questionCard(question)
                } else if selection?.isPreparing == true {
                    Text("Je prends le relais.").font(.headline)
                    Text(selection?.message ?? "").foregroundStyle(.secondary)
                } else if let selection, !selection.isPending {
                    recoveryCard(selection.message)
                } else if selection == nil {
                    Text(readyCount > 0 ? "Des idées sont prêtes. Lance le choix pour ton créneau." : "Je chercherai des pistes et vérifierai leur état dès que tu lances la recherche.")
                        .font(.headline)
                    Button("Choisir un projet pour commencer") { options = true }
                }
                if let preparation = data?.preparation {
                Divider()
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 5) {
                        ProgressView(preparation.label)
                        Text("Cela peut prendre une à deux minutes. La suite apparaîtra ici automatiquement ; tu peux aussi changer de projet ou préciser ta demande.")
                            .font(.callout).foregroundStyle(.secondary)
                        if let sources = preparation.sources, !sources.isEmpty {
                            DisclosureGroup("\(sources.filter(\.read).count) conversation(s) consultée(s)") {
                                ForEach(sources) { source in Text(source.title).font(.caption) }
                            }
                        }
                    }
                    Spacer()
                    Button("Arrêter la recherche") { Task { await stopPreparation(preparation.id) } }
                }
                }
                if selection == nil, let failure = data?.preparationError {
                    recoveryCard(failure)
                }
                if question == nil && !preparedQuestions.isEmpty && selection?.activity == nil {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Questions prêtes pour toi").font(.headline)
                        Text("Déjà vérifiées pour ta demande. Réponds à celles que tu veux.").font(.callout).foregroundStyle(.secondary)
                        ForEach(preparedQuestions) { idea in
                            HStack {
                                VStack(alignment: .leading) {
                                    Text(idea.candidate.stateQuestion ?? idea.candidate.title)
                                    Text(idea.projectName ?? "Projet").font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                Button("Répondre") { resetChoice(); poolQuestionID = idea.id }
                            }
                        }
                    }.padding(18).background(Color.blue.opacity(0.04)).clipShape(RoundedRectangle(cornerRadius: 12))
                }
                if let history {
                    Text("\(history.metrics.completed) activité(s) réalisée(s) évaluée(s)").font(.caption).foregroundStyle(.secondary)
                    DisclosureGroup("Historique des expériences", isExpanded: $showHistory) {
                        VStack(alignment: .leading, spacing: 12) {
                            ForEach(history.requests.filter { $0.status == "ready" && $0.purpose != "verify_idea" }) { request in
                                HStack(alignment: .top) {
                                    VStack(alignment: .leading) {
                                        if let report = request.researchReport {
                                            DisclosureGroup(request.label) { researchCard(report) }
                                        } else { Text(request.label) }
                                        Text("\(request.policy) · \(request.elapsedSeconds ?? 0, specifier: "%.1f") s").font(.caption).foregroundStyle(.secondary)
                                        if let f = request.feedback { Text(f.reason).font(.caption).foregroundStyle(.secondary) }
                                    }
                                    Spacer()
                                    if request.purpose == "prepare_ideas" {
                                        Button("Explorer") { explorer = ResearchExplorerTarget(id: request.id) }
                                    }
                                    if request.policy.hasPrefix("ready-ideas-") {
                                        Button("Évaluer") { feedback = request }
                                    }
                                }
                            }
                        }.padding(.vertical, 8)
                    }
                }
            }.padding(28).frame(maxWidth: .infinity, alignment: .leading)
        }
        .onChange(of: instruction) { _, _ in resetChoice() }
        .onChange(of: project) { _, _ in resetChoice() }
        .onChange(of: budget) { _, _ in resetChoice() }
        .task {
            while !Task.isCancelled {
                await refresh()
                do { try await Task.sleep(for: .milliseconds(selection?.isPending == true ? 150 : 1500)) } catch { return }
            }
        }
        .sheet(item: $feedback) { activity in
            ActivityFeedbackView(request: activity) { body in
                do {
                    _ = try await store.client.request("/api/act/feedback", body: body)
                    await refresh(); return true
                } catch { self.error = error.localizedDescription; return false }
            }
        }
        .sheet(item: $explorer) { target in ResearchExplorerView(requestId: target.id) }
    }

    private func resetChoice() { selectionID = ""; poolQuestionID = nil; answered = 0; notice = nil; intentAnswer = "" }

    @ViewBuilder private func researchCard(_ report: ResearchReport) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Ce que j’ai regardé").font(.headline)
            Text(report.summary).textSelection(.enabled)
            DisclosureGroup("Sources · \(report.sources.filter(\.read).count) conversation(s), extraits partiels") {
                ForEach(report.sources) { source in
                    Text("\(source.title)\(source.read ? "" : " · non lue")").font(.caption)
                }
            }
            ForEach(report.checks, id: \.self) { Text($0).font(.callout).foregroundStyle(.secondary) }
            if !report.unresolved.isEmpty {
                Text("Ce que je n’ai pas pu trancher").font(.headline)
                ForEach(report.unresolved, id: \.self) { Text($0).font(.callout) }
            }
        }.padding(16).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.gray.opacity(0.06)).clipShape(RoundedRectangle(cornerRadius: 12))
    }

    @ViewBuilder private func intentCard(_ question: ProjectIntentQuestion) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Pour avancer sur \(question.projectName)").font(.headline)
            Text(question.prompt).font(.title3)
            Text(question.readableWhy).foregroundStyle(.secondary)
            Text(question.changesDecision).font(.callout)
            ForEach(question.options, id: \.self) { option in
                Button(option) { intentAnswer = option }.disabled(busy)
            }
            TextField("Ta réponse, avec tes mots…", text: $intentAnswer, axis: .vertical)
                .lineLimit(2...5).textFieldStyle(.roundedBorder)
            HStack {
                Button("Enregistrer et continuer") { Task { await answerIntent("answer") } }
                    .buttonStyle(.borderedProminent)
                    .disabled(busy || !allowed || intentAnswer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                Button("Je ne sais pas encore") { Task { await answerIntent("unknown") } }.disabled(busy || !allowed)
                Button("Passer") { Task { await answerIntent("skip") } }.disabled(busy || !allowed)
            }
            Text("Ta réponse précise le contexte du projet. Elle ne confirme pas qu’une tâche reste à faire.")
                .font(.caption).foregroundStyle(.secondary)
        }.padding(18).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.blue.opacity(0.04)).clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private func answerIntent(_ response: String) async {
        guard !busy else { return }
        busy = true; error = nil
        defer { busy = false }
        do {
            _ = try await store.client.request("/api/now/context-answer", body: ["id": selectionID, "response": response, "answer": intentAnswer])
            intentAnswer = ""
            await refresh()
        } catch { self.error = error.localizedDescription }
    }

    private func newSearch() async {
        resetChoice()
        await choose()
    }

    @ViewBuilder private func recoveryCard(_ message: String) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Orientons la suite").font(.headline)
            Text(message)
            HStack {
                Button("Chercher d’autres pistes") { Task { await newSearch() } }
                    .buttonStyle(.borderedProminent).disabled(busy || !allowed || data?.preparation != nil)
                Button("Choisir un projet") { options = true }
                Button("Préciser mon objectif") { editingRequest = true }
            }
            Text("Tes réponses restent enregistrées. Les points inconnus ne deviennent pas des tâches à faire.")
                .font(.caption).foregroundStyle(.secondary)
        }.padding(18).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.orange.opacity(0.06)).clipShape(RoundedRectangle(cornerRadius: 12))
    }

    @ViewBuilder private func questionCard(_ idea: PreparedIdea) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Une précision pour toi · \(answered + 1) / 2 maximum", systemImage: "questionmark.circle").font(.headline)
            Text(idea.projectName ?? "Projet").foregroundStyle(.secondary)
            Text(idea.candidate.stateQuestion ?? idea.candidate.completionClaim ?? idea.candidate.title).font(.title3)
            if let reason = idea.candidate.questionReason { Text(reason).foregroundStyle(.secondary) }
            HStack {
                Button("Déjà fait") { Task { await answer(idea, "already_done") } }
                Button("À faire et possible") { Task { await answer(idea, "todo") } }.buttonStyle(.borderedProminent)
            }.disabled(busy || !allowed)
            HStack {
                Button("Je ne sais pas") { Task { await answer(idea, "unknown") } }
                Button("Dépend d’un tiers") { Task { await answer(idea, "external") } }
                Button("À vérifier par Hoomy") { Task { await answer(idea, "agent") } }
                Button("Passer") { Task { await answer(idea, "skip") } }
            }.disabled(busy || !allowed)
            Text("Tu n’as pas à faire de recherches pour répondre. Si tu ne sais pas, Hoomy prend le relais ou cherche une autre piste.")
                .font(.caption).foregroundStyle(.secondary)
            DisclosureGroup("Voir l’idée concernée") { Text(idea.candidate.action).font(.callout) }
        }.padding(18).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.blue.opacity(0.06)).clipShape(RoundedRectangle(cornerRadius: 12))
    }

    @ViewBuilder private func activityCard(_ activity: ActivityRequest) -> some View {
        if let candidate = activity.selected {
            VStack(alignment: .leading, spacing: 12) {
                Label("Prête · état confirmé", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                Text(candidate.title).font(.title2.bold())
                if let idea = data?.ideas.first(where: { $0.id == selection?.selectedIdeaId }) {
                    Text(idea.projectName ?? "Projet").foregroundStyle(.secondary)
                }
                Text(candidate.action)
                Text("\(candidate.expectedAttentionMinutes, specifier: "%.0f") min · impact attendu \(candidate.expectedImpact)/5").foregroundStyle(.secondary)
                Text(candidate.whyNow)
                if let uncertainty = activity.uncertainty { Text(uncertainty).font(.caption).foregroundStyle(.secondary) }
                Text("Choix rapide : \(selection?.elapsedSeconds ?? 0, specifier: "%.2f") s").font(.caption).foregroundStyle(.secondary)
                HStack {
                    Button("Donner mon retour") { feedback = activity }.buttonStyle(.borderedProminent)
                    if let id = selection?.selectedIdeaId, let idea = data?.ideas.first(where: { $0.id == id }) {
                        Button("C’est maintenant terminé") { Task { await answer(idea, "already_done") } }
                        Button("L’état a changé") { Task { await answer(idea, "unknown") } }
                    }
                }.disabled(busy)
            }.padding(18).frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.green.opacity(0.06)).clipShape(RoundedRectangle(cornerRadius: 12))
        }
    }

    private func context() -> [String: Any] {
        var result: [String: Any] = ["instruction": instruction, "attention_budget": minutes, "questions_answered": answered, "recover": true]
        if !selectionID.isEmpty { result["continuation_id"] = selectionID }
        if !project.isEmpty { result["project_id"] = project }
        return result
    }

    private func refresh() async {
        do {
            data = try store.client.decode(ReadyIdeasState.self, from: await store.client.request("/api/now"))
            if let context = selection?.context {
                if context.instruction == instruction && context.attentionBudget == minutes && (context.projectId ?? "") == project {
                    answered = max(answered, context.questionsAnswered)
                } else { selectionID = "" }
            }
            if selection?.isPending != true {
                history = try store.client.decode(ActState.self, from: await store.client.request("/api/act"))
            }
        } catch { self.error = error.localizedDescription }
    }

    private func choose() async {
        guard !busy else { return }
        busy = true; error = nil; notice = nil
        defer { busy = false }
        do {
            let response = try store.client.decode(ReadySelection.self, from: await store.client.request("/api/now/choose", body: context()))
            selectionID = response.id
            await refresh()
            for _ in 0..<22 {
                guard selection?.isPending == true else { break }
                try await Task.sleep(for: .milliseconds(150))
                await refresh()
            }
        } catch { self.error = error.localizedDescription }
    }

    private func answer(_ idea: PreparedIdea, _ answer: String) async {
        guard !busy else { return }
        busy = true; error = nil
        do {
            var body = context()
            body.merge(["id": idea.id, "version": idea.version, "revision": idea.revision, "answer": answer]) { _, new in new }
            _ = try await store.client.request("/api/now/answer", body: body)
            answered = min(2, answered + 1)
            busy = false
            await choose()
        } catch { self.error = error.localizedDescription; busy = false }
    }

    private func stopPreparation(_ id: String) async {
        do { _ = try await store.client.request("/api/act/cancel", body: ["id": id]); await refresh() }
        catch { self.error = error.localizedDescription }
    }
}
