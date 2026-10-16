import SwiftUI

struct ActLearnView: View {
    @EnvironmentObject private var store: DiscoveryStore
    @State private var activityState: ActState?
    @State private var selectedProject = ""
    @State private var instruction = ""
    @State private var budget = 30
    @State private var selectedRequest: String?
    @State private var error: String?
    @State private var loadError: String?
    @State private var busy = false
    @State private var feedbackRequest: ActivityRequest?
    @State private var inspecting: String?
    @State private var showOptions = false

    private var requests: [ActivityRequest] { activityState?.requests ?? [] }
    private var current: ActivityRequest? { requests.first { $0.id == selectedRequest } ?? requests.first }

    private var effectiveBudget: Int { explicitAttentionMinutes(instruction) ?? budget }
    private var projectLabel: String { store.state?.projects.first { $0.id == selectedProject }?.name ?? "Tous mes projets actifs" }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 5) {
                    Text("Qu’est-ce qui serait utile maintenant ?").font(.title2.bold())
                    Text("Dis-moi le temps que tu as et ce qui compte pour toi aujourd’hui.")
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("Actualiser") { Task { await refresh() } }
            }
            if let message = error ?? loadError { Text(message).foregroundStyle(.red).textSelection(.enabled) }
            TextField("Par exemple : dimanche soir, j’ai 15 minutes devant moi…", text: $instruction, axis: .vertical)
                .textFieldStyle(.roundedBorder).lineLimit(2...4).font(.body)
            HStack {
                Text("\(projectLabel) · \(effectiveBudget) minutes")
                    .font(.callout).foregroundStyle(.secondary)
                if explicitAttentionMinutes(instruction) != nil {
                    Text("Durée reprise de ta demande").font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Button(showOptions ? "Masquer les précisions" : "Préciser ma demande") { showOptions.toggle() }
            }
            if showOptions {
                HStack {
                    Picker("Projet", selection: $selectedProject) {
                        Text("Tous mes projets actifs").tag("")
                        ForEach((store.state?.projects ?? []).filter { !$0.dismissed }) { project in
                            Text(project.name + (project.isInactive ? " · inactif" : "")).tag(project.id)
                        }
                    }.frame(maxWidth: 420)
                    if explicitAttentionMinutes(instruction) == nil {
                        Stepper("\(budget) min disponibles", value: $budget, in: 5...480, step: 5)
                    } else {
                        Text("Pour changer la durée, modifie ta demande.").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            HStack {
                Button { Task { await request() } } label: { Label("Trouver une activité", systemImage: "sparkles") }
                    .buttonStyle(.borderedProminent)
                    .disabled(busy || !(1...480).contains(effectiveBudget) || requests.contains { $0.isPending } || store.state?.control.inferenceEnabled != true)
                Text("Je consulte tes conversations et vérifie si possible. Sinon, je te demande une précision.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if store.state?.control.inferenceEnabled != true {
                Text("Active le traitement Codex dans Confidentialité pour demander une suggestion.").font(.callout)
            }
            Divider()
            HStack(alignment: .top, spacing: 22) {
                VStack(alignment: .leading, spacing: 10) {
                    Text("Tes demandes").font(.headline)
                    ScrollView {
                        VStack(alignment: .leading, spacing: 8) {
                            ForEach(requests) { request in
                                Button { selectedRequest = request.id } label: {
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text(request.label).lineLimit(3)
                                        Text(String(request.createdAt.prefix(16)).replacingOccurrences(of: "T", with: " · "))
                                            .font(.caption).foregroundStyle(.secondary)
                                        if let confirmation = request.confirmation {
                                            Text(confirmation.label).font(.caption).foregroundStyle(.secondary)
                                        }
                                        if let feedback = request.feedback {
                                            Text(feedback.decision == "done" ? "Réalisée · évaluée" : feedback.decision == "deferred" ? "Reportée" : "Refusée")
                                                .font(.caption).foregroundStyle(.secondary)
                                        }
                                    }.frame(maxWidth: .infinity, alignment: .leading).padding(10)
                                        .background(current?.id == request.id ? Color.blue.opacity(0.09) : Color.clear)
                                        .clipShape(RoundedRectangle(cornerRadius: 8))
                                }.buttonStyle(.plain)
                            }
                        }
                    }
                }.frame(width: 245)
                Divider()
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        if let current { detail(current) }
                        else {
                            Text("Une suggestion, puis un retour concret.").font(.title3)
                            Text("Écris simplement ta situation, puis clique sur Trouver une activité. Tu pourras confirmer ce qui est déjà fait et évaluer ce qui t’a été utile.")
                                .foregroundStyle(.secondary)
                        }
                        if let metrics = activityState?.metrics { metricsView(metrics) }
                    }.frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
                }
            }
        }.padding(26)
        .task {
            while !Task.isCancelled {
                await refresh()
                do { try await Task.sleep(for: .seconds(3)) } catch { return }
            }
        }
        .sheet(item: $feedbackRequest) { request in
            ActivityFeedbackView(request: request) { body in
                await mutate("/api/act/feedback", body: body)
            }
        }
        .sheet(isPresented: Binding(get: { inspecting != nil }, set: { if !$0 { inspecting = nil } })) {
            VStack(alignment: .leading) {
                HStack { Text("Contexte et trace enregistrés").font(.headline); Spacer(); Button("Fermer") { inspecting = nil } }
                ScrollView { Text(inspecting ?? "").font(.system(.caption, design: .monospaced)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }
            }.padding(24).frame(width: 780, height: 620)
        }
    }

    @ViewBuilder private func detail(_ request: ActivityRequest) -> some View {
        if request.isPending {
            ProgressView(request.label)
            Text("Recherche en cours : conversations, vérification si possible, puis suggestion. Tu peux quitter cet écran ; le service local poursuit la demande.")
            Button("Arrêter cette demande") { Task { _ = await mutate("/api/act/cancel", body: ["id": request.id]) } }.disabled(busy)
        } else if let failure = request.error {
            Text(failure).foregroundStyle(.secondary)
        } else if request.status == "ready" {
            if let candidate = request.selected {
                Text(candidate.title).font(.title2.bold())
                if let project = store.state?.projects.first(where: { $0.id == candidate.projectId }) {
                    Text(project.name).font(.headline).foregroundStyle(.secondary)
                }
                confirmationView(request, candidate: candidate)
                Text(candidate.action)
                HStack(spacing: 20) {
                    Label("Impact attendu : \(candidate.expectedImpact)/5", systemImage: "target")
                    Label("Attention : \(candidate.expectedAttentionMinutes, specifier: "%.0f") min", systemImage: "clock")
                }.font(.callout)
                Text("Pourquoi maintenant").font(.headline)
                Text(candidate.whyNow)
                Button(request.feedback == nil ? "Donner mon retour" : "Revoir mon évaluation") { feedbackRequest = request }
                    .buttonStyle(.borderedProminent)
                if let feedback = request.feedback {
                    if let impact = feedback.impact, let minutes = feedback.attentionMinutes {
                        Text("Observé : impact \(impact)/5 · \(minutes, specifier: "%.0f") min d’attention")
                    }
                    if !feedback.reason.isEmpty { Text(feedback.reason).foregroundStyle(.secondary) }
                }
            } else { Text("Pas de suggestion suffisamment étayée.").font(.headline) }
            Text(request.rationale)
            if let uncertainty = request.uncertainty, !uncertainty.isEmpty {
                Label(uncertainty, systemImage: "questionmark.circle").foregroundStyle(.secondary)
            }
            DisclosureGroup("Sources lues : \(request.sources.filter { $0.key != "agent_verification" && !$0.messages.isEmpty }.count) / \(request.sources.filter { $0.key != "agent_verification" }.count) conversations · vérifications incluses ci-dessous") {
                ForEach(request.sources) { source in
                    VStack(alignment: .leading, spacing: 6) {
                        Text(source.title).font(.headline)
                        Text(source.modifiedAt).font(.caption).foregroundStyle(.secondary)
                        if let warning = source.warning { Text(warning).font(.caption).foregroundStyle(.secondary) }
                        ForEach(source.messages) { message in
                            Text("\(message.role) · \(message.text)").font(.caption).padding(.vertical, 4)
                        }
                    }.padding(.vertical, 8)
                }
            }
            DisclosureGroup("Autres activités envisagées") {
                ForEach(request.candidates.filter { $0.id != request.selectedId }) { candidate in
                    VStack(alignment: .leading) {
                        Text(candidate.title).font(.headline)
                        Text(candidate.action)
                        Text("Impact \(candidate.expectedImpact)/5 · \(candidate.expectedAttentionMinutes, specifier: "%.0f") min")
                    }.padding(.vertical, 8)
                }
            }
            Text("\(request.model ?? "Codex") · \(request.policy) · \(request.elapsedSeconds ?? 0, specifier: "%.0f") s")
                .font(.caption).foregroundStyle(.secondary)
            Button("Voir le contexte et les retours utilisés") { Task { await inspect(request.id) } }
        } else { Text("Demande arrêtée. Tu peux en formuler une nouvelle.") }
    }

    @ViewBuilder private func confirmationView(_ request: ActivityRequest, candidate: ActivityCandidate) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            if let fact = request.confirmation {
                Label(fact.label, systemImage: fact.answer == "already_done" ? "checkmark.circle" : "info.circle")
                Text(fact.claim).font(.callout)
                Text("Enregistré le " + String(fact.confirmedAt.prefix(10))).font(.caption).foregroundStyle(.secondary)
                if fact.answer != "todo" {
                    Button("Trouver une autre activité") { Task { await next(request) } }
                        .disabled(busy || requests.contains { $0.isPending })
                }
                DisclosureGroup("Corriger ma réponse") { confirmationButtons(request) }
            } else if candidate.needsConfirmation == false {
                DisclosureGroup("Déjà fait ou état à préciser ?") { confirmationButtons(request) }
            } else {
                Text(candidate.needsConfirmation == true ? "Une précision avant de commencer" : "Cette activité est-elle encore utile ?").font(.headline)
                Text(candidate.completionClaim ?? "Activité déjà réalisée : " + candidate.title).font(.callout)
                Text("Est-ce déjà fait ? Ta réponse mettra à jour ce que je sais du projet.")
                    .font(.caption).foregroundStyle(.secondary)
                confirmationButtons(request)
            }
        }.padding(14).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.blue.opacity(0.06)).clipShape(RoundedRectangle(cornerRadius: 10))
    }

    private func confirmationButtons(_ request: ActivityRequest) -> some View {
        HStack {
            Button("Déjà fait → autre idée") { Task { await confirm(request, answer: "already_done", advance: true) } }
            Button("Reste à faire") { Task { await confirm(request, answer: "todo", advance: false) } }
            Button("Je ne sais pas") { Task { await confirm(request, answer: "unknown", advance: false) } }
        }.disabled(busy || requests.contains { $0.isPending })
    }

    private func confirm(_ request: ActivityRequest, answer: String, advance: Bool) async {
        if await mutate("/api/act/confirm", body: ["id": request.id, "answer": answer]), advance { await next(request) }
    }

    private func next(_ request: ActivityRequest) async {
        if await mutate("/api/act/next", body: ["id": request.id]) { selectedRequest = activityState?.requests.first?.id }
    }

    private func metricsView(_ metrics: ActivityMetrics) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Divider()
            Text("Observation · \(metrics.completed) activité(s) réalisée(s) évaluée(s)").font(.headline)
            if let impact = metrics.impact, let attention = metrics.attention,
               let impactError = metrics.impactError, let attentionError = metrics.attentionError {
                Text("Impact moyen \(impact, specifier: "%.1f")/5 · attention moyenne \(attention, specifier: "%.1f") min")
                Text("Erreur moyenne : \(impactError, specifier: "%.1f") point(s) d’impact · \(attentionError, specifier: "%.1f") min")
            }
            Text("Mesures déclarées sur les activités réalisées. Elles ne prouvent pas encore une amélioration de l’apprentissage.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private func refresh() async {
        do {
            activityState = try store.client.decode(ActState.self, from: await store.client.request("/api/act"))
            loadError = nil
        }
        catch { loadError = "Le suivi Act & Learn est indisponible. Vérifie que le service local a été mis à jour." }
    }

    private func request() async {
        var body: [String: Any] = ["instruction": instruction, "attention_budget": effectiveBudget]
        if !selectedProject.isEmpty { body["project_id"] = selectedProject }
        if await mutate("/api/act/request", body: body) { selectedRequest = activityState?.requests.first?.id }
    }

    private func mutate(_ path: String, body: [String: Any]) async -> Bool {
        guard !busy else { return false }
        busy = true; error = nil
        defer { busy = false }
        do { _ = try await store.client.request(path, body: body); await refresh(); return true }
        catch { self.error = error.localizedDescription; return false }
    }

    private func inspect(_ id: String) async {
        do {
            let data = try await store.client.request("/api/act")
            let object = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            let rows = object?["requests"] as? [[String: Any]]
            if let row = rows?.first(where: { $0["id"] as? String == id }) {
                let pretty = try JSONSerialization.data(withJSONObject: row, options: [.prettyPrinted, .sortedKeys])
                inspecting = String(data: pretty, encoding: .utf8)
            }
        } catch { self.error = error.localizedDescription }
    }
}

struct ActivityFeedbackView: View {
    @Environment(\.dismiss) private var dismiss
    let request: ActivityRequest
    let save: ([String: Any]) async -> Bool
    @State private var decision = "done"
    @State private var impact = -1
    @State private var minutes = ""
    @State private var reason = ""
    @State private var busy = false
    @State private var failed = false
    private var validMinutes: Double? {
        guard let number = Double(minutes.replacingOccurrences(of: ",", with: ".")), number.isFinite, (0...1440).contains(number) else { return nil }
        return number
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Qu’est-ce que cette activité t’a apporté ?").font(.title2.bold())
            Text(request.label)
            Picker("Décision", selection: $decision) {
                Text("Réalisée").tag("done"); Text("Reportée").tag("deferred"); Text("Refusée").tag("rejected")
            }.pickerStyle(.segmented)
            if decision == "done" {
                Picker("Impact réel", selection: $impact) {
                    Text("Choisir…").tag(-1)
                    ForEach(0...5, id: \.self) { value in Text("\(value) / 5").tag(value) }
                }
                Text("0 aucun bénéfice · 2 utile · 3 progrès net · 4 déblocage majeur · 5 décisif")
                    .font(.caption).foregroundStyle(.secondary)
                TextField("Minutes d’attention active réellement consommées", text: $minutes)
                    .textFieldStyle(.roundedBorder)
                Text("Le temps actif, pas l’attente des machines. Zéro signifie réellement zéro ; inconnu reste vide.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            TextField("Pourquoi / ce qu’il faudrait changer (facultatif)", text: $reason, axis: .vertical)
                .textFieldStyle(.roundedBorder).lineLimit(3...6)
            if failed { Text("Le retour n’a pas été enregistré. Tes saisies sont conservées ; réessaie.").foregroundStyle(.red) }
            HStack {
                Button("Annuler") { dismiss() }.disabled(busy)
                Spacer()
                Button("Enregistrer mon retour") {
                    Task {
                        busy = true; failed = false
                        var body: [String: Any] = ["id": request.id, "decision": decision, "reason": reason]
                        if decision == "done" { body["impact"] = impact; body["attention_minutes"] = validMinutes }
                        if await save(body) { dismiss() } else { failed = true }
                        busy = false
                    }
                }.buttonStyle(.borderedProminent)
                    .disabled(busy || (decision == "done" && (impact < 0 || validMinutes == nil)))
            }
        }.padding(28).frame(width: 570)
        .onAppear {
            if let feedback = request.feedback {
                decision = feedback.decision; impact = feedback.impact ?? -1
                minutes = feedback.attentionMinutes.map { String($0) } ?? ""; reason = feedback.reason
            }
        }
    }
}
