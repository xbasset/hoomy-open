import SwiftUI

func relatedNames(_ lhs: String, _ rhs: String) -> Bool {
    func words(_ value: String) -> Set<String> {
        Set(value.folding(options: [.diacriticInsensitive, .caseInsensitive], locale: .current)
            .components(separatedBy: CharacterSet.alphanumerics.inverted)
            .filter { $0.count >= 4 && !["projet", "project", "plateforme", "application"].contains($0) })
    }
    return !words(lhs).intersection(words(rhs)).isEmpty
}

struct RecognitionCoverageView: View {
    let state: DiscoveryState
    let clarify: () -> Void

    private var segments: [(String, Int, Color)] {
        guard let o = state.overview else { return [] }
        return [("Rattachées", o.linked ?? 0, .blue),
                ("Propositions à valider", o.pendingSources ?? 0, .cyan),
                ("Sans projet identifié", o.noProject ?? 0, .orange),
                ("À clarifier", o.ambiguous ?? 0, .purple),
                ("Sans projet · ton choix", o.humanNoProject ?? 0, .gray),
                ("À décider plus tard", o.deferred ?? 0, .brown),
                ("Titres non examinés", o.unexamined ?? 0, .secondary)].filter { $0.1 > 0 }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Ce que Hoomy a pu comprendre").font(.title2.weight(.semibold))
            HStack(alignment: .center, spacing: 26) {
                ZStack {
                    Circle().stroke(Color.secondary.opacity(0.12), lineWidth: 20)
                    let total = max(1, segments.reduce(0) { $0 + $1.1 })
                    ForEach(segments.indices, id: \.self) { index in
                        let start = Double(segments.prefix(index).reduce(0) { $0 + $1.1 }) / Double(total)
                        let end = start + Double(segments[index].1) / Double(total)
                        Circle().trim(from: start, to: end).stroke(segments[index].2, lineWidth: 20).rotationEffect(.degrees(-90))
                    }
                    VStack { Text("\(state.overview?.indexed ?? 0)").font(.title.bold()); Text("titres").font(.caption) }
                }.frame(width: 140, height: 140).padding(12).accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 7) {
                    ForEach(segments.indices, id: \.self) { index in
                        HStack {
                            Circle().fill(segments[index].2).frame(width: 9, height: 9).accessibilityHidden(true)
                            Text("\(segments[index].1) · \(segments[index].0)")
                        }
                    }
                }.font(.callout)
            }
            Text("1 · Classement des titres et dossiers. Ce bilan n’est pas un score de fiabilité.").font(.callout)
            Text("2 · Compléter avec tes projets validés, puis des extraits ciblés. \(state.overview?.sampled ?? 0) conversations examinées par extraits · \(state.coverage.analyzed) résumés détaillés.").font(.callout).foregroundStyle(.secondary)
            Button("Compléter la découverte…", action: clarify)
                .buttonStyle(.borderedProminent).disabled(state.control.running)
            Text("Hoomy trie les conversations restantes avec tes projets validés. Tu arbitres les résultats, sans choisir les conversations une par une. Une absence de correspondance n’est pas une preuve d’absence de projet.")
                .font(.caption).foregroundStyle(.secondary)
        }.padding(.vertical, 10)
    }
}

struct CompletionConfirmation: View {
    @EnvironmentObject private var store: DiscoveryStore
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Compléter la découverte").font(.title2.weight(.semibold))
            Text("1 · Transmettre à l’agent Codex Luna les titres restants et les projets et exemples que tu as validés. La première passe n’est pas relancée.")
            Text("2 · Hoomy prépare de courts extraits et classe les conversations en parallèle. L’agent approfondit ensuite les cas incertains. Les phases, les résultats et les recherches apparaissent en direct.")
            Text("3 · Lecture locale des conversations restantes, en priorité tes demandes et les résumés explicites. Classement par groupes, avec trois appels simultanés maximum, puis investigation des cas difficiles. Budget : environ quatre minutes après validation ; les résultats incomplets restent visibles. Le classement transmet au plus 600 caractères par conversation, l’investigation jusqu’à 3 000 caractères supplémentaires.")
            Text("Les associations aux projets validés sont faites automatiquement, avec une justification et la possibilité de corriger en direct. Les nouveaux projets attendent ton approbation. Aucun accès libre aux fichiers.")
            Text("Tes choix explicites sont conservés. Les fichiers locaux lus peuvent être conservés comme sources privées dans Hoomy. Les fichiers de plus de 16 Mio sont écartés. La lecture peut être mise en pause.")
                .font(.callout).foregroundStyle(.secondary)
            Text("Après une pause, les rattachements et les étapes terminées sont conservés. Un nouveau tour repart sur le travail restant avec tes corrections ; le fil interne de l’agent n’est pas conservé. La limite globale court depuis le lancement, pause comprise.").font(.caption).foregroundStyle(.secondary)
            HStack {
                Button("Annuler") { dismiss() }
                Spacer()
                Button("Lancer avec ce budget") {
                    Task {
                        if await store.mutate("/api/overview/complete", body: ["include_content": true, "automatic_associations": true, "bounded_discovery": true]) { dismiss() }
                    }
                }.buttonStyle(.borderedProminent).disabled(store.isWorking || store.state?.control.running == true)
            }
        }.padding(26).frame(width: 650)
    }
}

struct AttachOutlineView: View {
    @EnvironmentObject private var store: DiscoveryStore
    @Environment(\.dismiss) private var dismiss
    let outline: ProjectOutline
    @State private var target = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Regrouper « \(outline.name) »").font(.title2)
            Text("Les \(outline.sources.count) conversations rejoindront le projet choisi. Son nom, sa description et son état actif ou inactif seront conservés.")
            Picker("Projet à conserver", selection: $target) {
                Text("Choisir…").tag("")
                ForEach(store.state?.projects.filter { !$0.dismissed } ?? []) { project in
                    Text(project.name + (project.isInactive ? " · inactif" : "")).tag(project.id)
                }
            }
            if let project = store.state?.projects.first(where: { $0.id == target }) {
                Text(project.purpose).foregroundStyle(.secondary)
            }
            HStack {
                Button("Annuler") { dismiss() }
                Spacer()
                Button("Confirmer le regroupement") {
                    Task {
                        if await store.mutate("/api/outlines/\(store.client.safeID(outline.id))/review",
                            body: ["decision": "keep", "project_id": target]) { dismiss() }
                    }
                }.buttonStyle(.borderedProminent).disabled(target.isEmpty || store.isWorking || store.state?.reviewBlocked == true)
            }
        }.padding(26).frame(width: 580)
    }
}

struct ClarificationPicker: View {
    @EnvironmentObject private var store: DiscoveryStore
    @Environment(\.dismiss) private var dismiss
    @State private var rows: [CatalogConversation] = []
    @State private var selected: Set<String> = []
    @State private var error: String?
    @State private var query = ""
    @State private var session: SessionSummary?
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Seconde lecture · contenu des conversations").font(.title2)
            Text("Hoomy lira les échanges sélectionnés et les transmettra à OpenAI via Codex pour produire des résumés sourcés et rechercher des projets. Jusqu’à 3 conversations, sans démarrer la lecture des autres.")
            TextField("Rechercher un titre ou un dossier", text: $query)
            Text("\(rows.count) conversations éligibles · \(selected.count) sélectionnées sur 3").font(.callout)
            if let error { Text(error).foregroundStyle(.red) }
            List {
                ForEach(rows.filter { query.isEmpty || $0.title.localizedCaseInsensitiveContains(query) || $0.directory.localizedCaseInsensitiveContains(query) }) { row in
                    Toggle(isOn: Binding(get: { selected.contains(row.id) }, set: { enabled in
                        if enabled { selected.insert(row.id) } else { selected.remove(row.id) }
                    })) {
                        VStack(alignment: .leading) {
                            Text(row.title)
                            Text(row.directory).font(.caption).foregroundStyle(.secondary)
                            Text(row.outcome == "ambiguous" ? "Titre à clarifier" : "Aucun projet identifié dans le titre").font(.caption)
                        }
                    }.disabled(selected.count >= 3 && !selected.contains(row.id))
                }
                Section("Lectures terminées · revoir les résultats") {
                    ForEach(store.state?.sessions.filter { $0.status == "analyzed" } ?? []) { item in
                        Button(item.title) { session = item }
                    }
                }
            }
            Text("Les conversations déjà résumées à jour et tes choix « aucun projet » ou « à décider plus tard » ne seront pas relus ici.").font(.caption).foregroundStyle(.secondary)
            HStack {
                Button("Fermer") { dismiss() }
                Spacer()
                Button("Lire le contenu des \(selected.count) conversations") {
                    Task {
                        if await store.mutate("/api/overview/clarify", body: ["session_ids": rows.filter { selected.contains($0.id) }.map(\.id)]) { dismiss() }
                    }
                }.buttonStyle(.borderedProminent).disabled(selected.isEmpty || store.isWorking || store.state?.control.running == true)
            }
        }.padding(24).frame(width: 760, height: 620)
            .task {
                do { rows = try store.client.decode([CatalogConversation].self, from: await store.client.request("/api/clarification-inputs")) }
                catch { self.error = error.localizedDescription }
            }
            .sheet(item: $session) { SessionReviewView(sessionID: $0.id).environmentObject(store) }
    }
}
