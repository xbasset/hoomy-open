import SwiftUI

enum WizardStage: String {
    case access, inventory, permission, working, paused, review, deepen, ready, empty

    static func resolve(scope: Bool, inference: Bool, running: Bool, stage: String,
                        indexed: Int, proposals: Int, projects: Int, sampled: Int, finished: Bool) -> Self {
        if !scope { return .access }
        if !inference { return running ? .inventory : .permission }
        if running { return proposals > 0 && stage != "inventory" ? .review : .working }
        if ["inventory", "classifying", "deepening", "error"].contains(stage) { return .paused }
        if proposals > 0 && !finished { return .review }
        if projects == 0 { return .empty }
        return sampled > 0 || finished ? .ready : .deepen
    }
}

struct DiscoveryWizard: View {
    @EnvironmentObject private var store: DiscoveryStore
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let state: DiscoveryState
    let openProject: (String) -> Void
    let edit: (ProjectOutline) -> Void
    let attach: (ProjectOutline) -> Void
    let inspect: () -> Void
    @AppStorage("hoomy.wizard.finishedGeneration") private var finishedGeneration = ""
    @State private var exclusions = ""
    @State private var showExclusions = false
    @State private var selectedID: String?
    @State private var deferred: Set<String> = []
    @State private var contentConsent = false
    @State private var confirmSkip = false

    private var overview: ProjectOverview? { state.overview }
    private var projects: [Project] { state.projects.filter { !$0.dismissed } }
    private var candidates: [ProjectOutline] { overview?.candidates ?? [] }
    private var finished: Bool { finishedGeneration == state.control.generation && state.control.generation != nil }
    private var phase: WizardStage {
        if !state.control.running, state.control.inferenceEnabled, candidates.isEmpty, !projects.isEmpty,
           (overview?.ambiguous ?? 0) + (overview?.noProject ?? 0) + (overview?.unexamined ?? 0) == 0 { return .ready }
        return WizardStage.resolve(scope: state.control.scopeConfirmed, inference: state.control.inferenceEnabled,
            running: state.control.running, stage: overview?.stage ?? "idle", indexed: overview?.indexed ?? 0,
            proposals: candidates.count, projects: projects.count, sampled: overview?.sampled ?? 0, finished: finished)
    }
    private var candidate: ProjectOutline? {
        candidates.first { $0.id == selectedID } ?? candidates.first { !deferred.contains($0.id) }
    }
    private var disabled: Bool { store.isWorking || store.connectionError != nil }
    private var step: Int {
        switch phase {
        case .access, .inventory: return 0
        case .permission: return 1
        case .working, .paused: return overview?.completionPhase == "agent" ? 3 : 1
        case .review: return 2
        case .deepen, .ready, .empty: return 3
        }
    }
    private var title: String {
        switch phase {
        case .access: return "Tes projets sont déjà là."
        case .inventory: return "Repérons ton historique."
        case .permission: return "Prêt à repérer tes projets ?"
        case .working: return overview?.completionPhase == "agent" ? "Tes projets prennent forme." : "Les premiers projets se dessinent."
        case .paused: return "On reprend quand tu veux."
        case .review: return candidate == nil ? "Les autres projets peuvent attendre." : "Est-ce bien un de tes projets ?"
        case .deepen: return "Relions le reste à tes projets."
        case .ready: return "Voici ta base de projets."
        case .empty: return "Il reste à trouver les bons indices."
        }
    }
    private var subtitle: String {
        switch phase {
        case .access: return "Retrouvons-les dans ton historique Codex, sans rien modifier dans tes fichiers."
        case .inventory: return "Lecture locale des titres et dossiers. Aucun contenu de conversation n’est lu ou envoyé."
        case .permission: return "\(overview?.indexed ?? 0) conversations repérées sur ce Mac. Leur contenu n’a pas été lu."
        case .review: return "Confirme ce qui compte pour toi. Tu pourras toujours corriger ou regrouper ensuite."
        case .deepen: return "Tu as reconnu \(projects.count) projets. Hoomy peut maintenant chercher les conversations qui leur correspondent."
        case .ready: return "\(projects.filter { !$0.isInactive }.count) projets actifs · \(projects.filter(\.isInactive).count) inactifs · \(overview?.linked ?? 0) conversations rattachées."
        case .empty: return (overview?.indexed ?? 0) == 0 ? "Aucun historique exploitable trouvé. Vérifie que Codex a déjà été utilisé sur ce Mac et consulte les détails d’accès." : "Les titres n’ont pas suffi à identifier un projet que tu souhaites garder. Le bilan reste disponible."
        case .paused: return "Les résultats enregistrés sont conservés. Aucune nouvelle analyse n’est en cours."
        case .working: return "Les résultats apparaissent au fil de l’analyse. Tu peux mettre en pause à tout moment."
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 22) {
                ForEach(Array(["Accès", "Repérage", "Tes choix", "Ta base"].enumerated()), id: \.offset) { index, label in
                    HStack(spacing: 7) {
                        Image(systemName: index < step ? "checkmark.circle.fill" : "\(index + 1).circle\(index == step ? ".fill" : "")")
                        Text(label).fontWeight(index == step ? .semibold : .regular)
                    }.foregroundStyle(index == step ? Color.accentColor : Color.secondary)
                        .accessibilityLabel("Étape \(index + 1) : \(label)\(index == step ? ", en cours" : "")")
                }
                Spacer()
            }.font(.callout).padding(.horizontal, 40).padding(.vertical, 22)
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    VStack(alignment: .leading, spacing: 10) {
                        Text(title).font(.system(size: 32, weight: .semibold, design: .rounded)).accessibilityAddTraits(.isHeader)
                        Text(subtitle).font(.title3).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    }
                    content
                }.padding(.horizontal, 40).padding(.vertical, 18)
                    .frame(maxWidth: 900, alignment: .leading).frame(maxWidth: .infinity, alignment: .leading)
            }
            Divider()
            VStack(alignment: .leading, spacing: 16) {
                HStack(alignment: .top, spacing: 10) {
                    if state.control.running { ProgressView().controlSize(.small) }
                    else { Image(systemName: store.connectionError == nil ? "info.circle" : "wifi.slash").foregroundStyle(.secondary) }
                    Text(store.connectionError ?? feedback).font(.callout).foregroundStyle(.secondary)
                        .lineLimit(3).frame(maxWidth: .infinity, minHeight: 38, alignment: .topLeading)
                    if let progress = overview?.agentProgress, state.control.running {
                        Text("\(Int(progress.elapsedSeconds)) s").monospacedDigit().foregroundStyle(.secondary)
                    }
                }
                HStack(spacing: 12) { actions }
                    .controlSize(.large).frame(minHeight: 38)
            }.padding(.horizontal, 40).padding(.vertical, 20).background(.background)
        }
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.2), value: phase)
        .onAppear { selectNext() }
        .onChange(of: candidates.map(\.id)) { _, _ in selectNext() }
        .confirmationDialog("Écarter cette proposition ?", isPresented: $confirmSkip, titleVisibility: .visible) {
            Button("Ce n’est pas un projet", role: .destructive) { decide("skip") }
            Button("Annuler", role: .cancel) {}
        } message: { Text("Ses conversations seront marquées sans projet par ton choix. Utilise « Plus tard » si tu hésites.") }
    }

    @ViewBuilder private var content: some View {
        switch phase {
        case .access:
            VStack(alignment: .leading, spacing: 18) {
                permissionRow("externaldrive", "Sur ce Mac uniquement", "Hoomy repère les titres et les dossiers de l’historique Codex. Tes fichiers d’origine restent intacts.")
                permissionRow("hand.raised", "Rien envoyé pour l’instant", "L’envoi à OpenAI fera l’objet d’une autorisation séparée, après cet inventaire.")
                DisclosureGroup("Exclure des dossiers avant de commencer", isExpanded: $showExclusions) {
                    Text("Un chemin absolu par ligne. Ces exclusions définissent le périmètre ; les changer après lecture nécessite une réinitialisation.")
                        .font(.caption).foregroundStyle(.secondary)
                    TextEditor(text: $exclusions).font(.system(.body, design: .monospaced)).frame(height: 80)
                        .accessibilityLabel("Dossiers exclus")
                    if !validPaths { Text("Chaque chemin doit commencer par /.").foregroundStyle(.red) }
                }
            }.padding(.top, 12)
        case .permission:
            if (overview?.indexed ?? 0) > 0 {
                permissionRow("network", "Classer avec OpenAI", "Seuls les titres et les noms de dossiers seront envoyés via ton compte Codex. Les limites de ton abonnement s’appliquent.")
                permissionRow("text.bubble", "Le contenu attendra ton accord", "Hoomy te proposera des projets à reconnaître. Aucun échange complet ni extrait n’est envoyé à cette étape.")
                Text("Un compte Codex connecté sur ce Mac est nécessaire. Si la connexion échoue, tes résultats locaux seront conservés.").font(.callout).foregroundStyle(.secondary)
            } else {
                Text("Aucune conversation disponible. Vérifie l’historique Codex et les éventuelles exclusions dans les détails.")
            }
        case .review:
            if let c = candidate {
                proposal(c)
            } else {
                Text("\(candidates.count) propositions mises de côté. Elles restent disponibles, sans bloquer la suite.")
                Button("Revoir les propositions") { deferred = []; selectedID = nil }
            }
        case .deepen:
            projectList
            if contentConsent {
                Divider()
                permissionRow("text.magnifyingglass", "Autoriser des extraits ciblés", "Lecture locale des conversations restantes, en priorité tes demandes et les résumés explicites. De courts extraits sont transmis à OpenAI ; des copies privées peuvent être conservées sur ce Mac.")
                Text("Environ 4 minutes maximum de traitement prévu. Jusqu’à 600 caractères par conversation pour le classement, puis 3 000 supplémentaires pour les cas investigués. Les fichiers de plus de 16 Mio sont écartés.")
                    .font(.callout).foregroundStyle(.secondary)
                Text("Les associations aux projets reconnus se font automatiquement et restent corrigeables. Les nouveaux projets attendent ton accord. Une lecture partielle ne garantit pas de tout classer.")
                    .font(.callout)
            }
        case .ready:
            projectList
            if (overview?.ambiguous ?? 0) + (overview?.noProject ?? 0) > 0 {
                Divider()
                Text("\((overview?.ambiguous ?? 0) + (overview?.noProject ?? 0)) conversations restent sans décision définitive. Elles ne t’empêchent pas de travailler avec tes projets.")
                    .foregroundStyle(.secondary)
                Button("Examiner les conversations restantes", action: inspect).buttonStyle(.link)
            }
            if !candidates.isEmpty {
                Button("Revoir les \(candidates.count) propositions en attente") { finishedGeneration = ""; deferred = [] }
            }
        case .working, .inventory:
            if projects.isEmpty {
                VStack(alignment: .leading, spacing: 22) {
                    permissionRow("magnifyingglass", "Repérer les sujets", "Les titres et dossiers donnent une première vue de ton activité.")
                    permissionRow("rectangle.3.group", "Préparer des regroupements", "Tu reconnaîtras ensuite les projets et ceux que tu ne souhaites plus suivre.")
                }.padding(.top, 16)
            } else { projectList }
        case .paused:
            if let message = overview?.message { Text(message).textSelection(.enabled) }
            Text("Si un accès ou une connexion est nécessaire, ouvre les détails pour identifier le blocage. Reprendre ne supprime pas tes choix.").foregroundStyle(.secondary)
        case .empty:
            Text("Tu peux inspecter le bilan, vérifier l’accès à ton historique, puis réessayer. Rien n’a été supprimé.").foregroundStyle(.secondary)
        }
    }

    private func permissionRow(_ symbol: String, _ heading: String, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 16) {
            Image(systemName: symbol).font(.title2).foregroundStyle(Color.accentColor).frame(width: 28)
            VStack(alignment: .leading, spacing: 6) {
                Text(heading).font(.headline)
                Text(text).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func proposal(_ c: ProjectOutline) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text(c.name).font(.title.weight(.semibold))
                Spacer()
                Text("\(candidates.count) à examiner").font(.caption).foregroundStyle(.secondary)
            }
            Text(c.purpose).font(.title3)
            Text(c.rationale).foregroundStyle(.secondary)
            ForEach(projects.filter { relatedNames(c.name, $0.name) }) { p in
                Button("Peut-être le même projet que « \(p.name) » · Regrouper…") { attach(c) }
                    .disabled(state.reviewBlocked || disabled)
            }
            DisclosureGroup("Pourquoi ce regroupement ? · \(c.sources.count) conversations") {
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(c.sources) { source in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(source.title)
                            Text(source.directory).font(.caption).foregroundStyle(.secondary)
                            if let text = source.excerptText { Text(text).font(.callout).lineLimit(8).textSelection(.enabled) }
                        }
                    }
                }.padding(.top, 12)
            }
        }.padding(.vertical, 12)
    }

    private var projectList: some View {
        VStack(alignment: .leading, spacing: 16) {
            ForEach([false, true], id: \.self) { inactive in
                let group = projects.filter { $0.isInactive == inactive }
                if !group.isEmpty {
                    Text(inactive ? "CONSERVÉS POUR PLUS TARD" : "ACTIFS").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                    ForEach(group) { p in
                        Button { openProject(p.id) } label: {
                            HStack {
                                Text(p.name).foregroundStyle(.primary)
                                Spacer()
                                Text("\(p.sessionIds.count) conversations").foregroundStyle(.secondary).monospacedDigit()
                                Image(systemName: "chevron.right").font(.caption).foregroundStyle(.secondary)
                            }.padding(.vertical, 8).contentShape(Rectangle())
                        }.buttonStyle(.plain)
                    }
                }
            }
        }
    }

    private var feedback: String {
        if state.control.running {
            return overview?.agentProgress?.events.last?.text ?? state.worker.message ?? "Analyse en cours. Les résultats sont conservés au fil de l’eau."
        }
        switch phase {
        case .access: return "L’accès concerne uniquement ton historique Codex. Tu choisis ensuite, séparément, ce qui peut être envoyé à OpenAI."
        case .permission: return "Inventaire local terminé. Rien n’a été envoyé à OpenAI."
        case .review: return "Garder confirme le projet comme actif. Les autres choix permettent de corriger, regrouper ou conserver pour plus tard."
        case .deepen: return contentConsent ? "Cet accord autorise la lecture d’extraits et les rattachements automatiques aux projets validés." : "L’approfondissement est facultatif. Ta base de projets peut déjà être utilisée."
        case .ready: return "Ta base est enregistrée sur ce Mac. Tes décisions priment sur les prochaines analyses."
        case .paused: return "En pause. Aucun nouvel appel n’est lancé."
        default: return "Tes choix sont conservés. Les détails restent accessibles en haut de la fenêtre."
        }
    }

    @ViewBuilder private var actions: some View {
        if phase == .access {
            Text("1 · Autorisation locale").font(.caption).foregroundStyle(.secondary)
            Spacer()
            Button("Autoriser le repérage local") {
                Task {
                    let paths = exclusions.split(separator: "\n").map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
                    if await store.mutate("/api/control", body: ["consent": true, "running": false, "inference_enabled": false, "exclusions": paths]) {
                        await store.mutate("/api/overview/start", body: ["local_only": true])
                    }
                }
            }.buttonStyle(.borderedProminent).disabled(disabled || !validPaths)
        } else if phase == .permission {
            Button("Vérifier le bilan", action: inspect)
            Spacer()
            if (overview?.indexed ?? 0) > 0 {
                Button("Autoriser l’envoi des titres") {
                    Task { await store.mutate("/api/control", body: ["consent": true, "inference_enabled": true, "running": true]) }
                }.buttonStyle(.borderedProminent).disabled(disabled)
            } else {
                Button("Réessayer le repérage") { Task { await store.mutate("/api/overview/start", body: ["local_only": true]) } }
                    .buttonStyle(.borderedProminent).disabled(disabled)
            }
        } else if phase == .review, let c = candidate {
            Button("Plus tard") { deferred.insert(c.id); selectedID = nil; selectNext() }
            Menu("Autres choix") {
                Button("Garder comme inactif") { decide("inactive") }
                Button("Modifier le nom ou l’objectif…") { edit(c) }
                Button("Regrouper avec un projet…") { attach(c) }.disabled(projects.isEmpty)
                Button("Ce n’est pas un projet…", role: .destructive) { confirmSkip = true }
            }.disabled(disabled || state.reviewBlocked)
            Spacer()
            if state.control.running { pauseButton }
            Button(c.existingProjectId == nil ? "Garder ce projet" : "Rattacher au projet") { decide("keep") }
                .buttonStyle(.borderedProminent).disabled(disabled || state.reviewBlocked)
        } else if state.control.running {
            Text("Tu peux changer d’app ; Hoomy reste à l’œuvre.").font(.caption).foregroundStyle(.secondary)
            Spacer()
            pauseButton
        } else if phase == .paused {
            Button("Voir les détails", action: inspect)
            Spacer()
            Button("Reprendre") { Task { await store.mutate("/api/control", body: ["running": true]) } }
                .buttonStyle(.borderedProminent).disabled(disabled)
        } else if phase == .deepen {
            Button(contentConsent ? "Retour" : "Utiliser mes projets maintenant") {
                if contentConsent { contentConsent = false } else { finish() }
            }
            Spacer()
            Button(contentConsent ? "Autoriser les extraits et continuer" : "Compléter les rattachements") {
                if contentConsent {
                    Task { await store.mutate("/api/overview/complete", body: ["include_content": true, "automatic_associations": true, "bounded_discovery": true]) }
                } else { contentConsent = true }
            }.buttonStyle(.borderedProminent).disabled(disabled)
        } else if phase == .ready {
            Button("Vérifier les rattachements", action: inspect)
            Spacer()
            Text("Base enregistrée").foregroundStyle(.secondary)
        } else {
            Button("Voir le bilan", action: inspect)
            Spacer()
            if !projects.isEmpty {
                Button("Continuer avec mes projets") { finish() }.buttonStyle(.borderedProminent)
            }
        }
    }

    private var pauseButton: some View {
        Button("Mettre en pause") { Task { await store.mutate("/api/control", body: ["running": false]) } }.disabled(disabled)
    }
    private var validPaths: Bool { exclusions.split(separator: "\n").allSatisfy { $0.trimmingCharacters(in: .whitespaces).hasPrefix("/") } }
    private func finish() { finishedGeneration = state.control.generation ?? "" }
    private func selectNext() {
        if !candidates.contains(where: { $0.id == selectedID }) {
            selectedID = candidates.first { !deferred.contains($0.id) }?.id
        }
    }
    private func decide(_ decision: String) {
        guard let c = candidate else { return }
        Task {
            if await store.mutate("/api/outlines/\(store.client.safeID(c.id))/review", body: ["decision": decision]) { selectedID = nil }
        }
    }
}
