import SwiftUI

struct RecognitionProgress: View {
    let state: DiscoveryState

    var body: some View {
        if let overview = state.overview {
            VStack(alignment: .leading, spacing: 8) {
                Text(overview.completionPhase == "excerpts" && state.control.running ? "Seconde passe · tri local et extraits partiels" : state.control.mode == "deep" && state.control.running
                     ? "Approfondissement · contenu des conversations"
                     : "Repérage · titres et dossiers uniquement").font(.headline)
                if let examined = overview.examined {
                    Text("\(examined) / \(overview.indexed) titres examinés · \(overview.unexamined ?? 0) à examiner")
                    Text("\(overview.linked ?? 0) conversations rattachées · \(overview.pendingSources ?? 0) à valider")
                    if (overview.humanNoProject ?? 0) + (overview.deferred ?? 0) > 0 {
                        Text("Tes choix : \(overview.humanNoProject ?? 0) sans projet · \(overview.deferred ?? 0) à décider plus tard")
                    }
                    Text("\(overview.noProject ?? 0) sans projet identifié · \(overview.ambiguous ?? 0) à clarifier")
                }
                Text("\(state.coverage.analyzed) résumés détaillés · \(overview.sampled ?? 0) conversations examinées par extraits partiels")
                if let note = overview.coverageNote, !note.isEmpty {
                    Text(note).font(.caption)
                }
                if state.control.running && overview.completionPhase == "excerpts" {
                    Text("Les titres ont été reclassés avec tes validations. Hoomy complète maintenant les cas incertains dans le budget de 12 conversations.")
                } else if state.control.running && state.control.mode != "deep" {
                    if overview.stage == "inventory" {
                        Text("Actualisation de l’inventaire…")
                    } else {
                        if overview.batchesTotal == 1 {
                            Text("Vue d’ensemble · \(overview.passTotal ?? 0) titres dans un seul appel")
                            Text("Le modèle les compare ensemble. Le nombre de titres effectivement examinés sera confirmé à réception du résultat complet.")
                        } else {
                            Text("Cette découverte : \(overview.passDone ?? 0) / \(overview.passTotal ?? 0) titres · lot \(min((overview.batchesDone ?? 0) + 1, overview.batchesTotal ?? 0)) / \(overview.batchesTotal ?? 0)")
                            Text("Les lots s’enchaînent automatiquement ; les résultats terminés sont conservés.")
                        }
                        Text("\(overview.passNew ?? 0) nouveaux ou sans suivi antérieur · \(overview.passReexamined ?? 0) réexaminés.")
                    }
                } else if overview.considered > 0 {
                    Text("Dernière découverte terminée : \(overview.considered) titres. Ce nombre n’est pas le cumul.")
                }
            }.font(.callout).foregroundStyle(.secondary)
        }
    }
}

struct ConversationAssignment: View {
    @EnvironmentObject private var store: DiscoveryStore
    let sessionID: String

    var body: some View {
        Menu("Choisir le projet…") {
            ForEach(store.state?.projects.filter { !$0.dismissed } ?? []) { project in
                Button(project.name + (project.isInactive ? " · inactif" : "")) {
                    assign("project", project.id)
                }
            }
            Divider()
            Button("Aucun projet") { assign("none") }
            Button("À décider plus tard") { assign("defer") }
        }.disabled(store.isWorking || store.state?.reviewBlocked == true || store.connectionError != nil)
    }

    private func assign(_ decision: String, _ project: String? = nil) {
        Task {
            var body: [String: Any] = ["decision": decision]
            if let project { body["project_id"] = project }
            await store.mutate("/api/catalog/\(store.client.safeID(sessionID))/assign", body: body)
        }
    }
}

struct CatalogReview: View {
    @EnvironmentObject private var store: DiscoveryStore
    @Environment(\.dismiss) private var dismiss
    @State private var rows: [CatalogConversation] = []
    @State private var query = ""
    @State private var filter = "all"
    @State private var error: String?

    private var filtered: [CatalogConversation] {
        rows.filter { row in
            (filter == "all" || (filter == "unassigned" ? row.projectIds.isEmpty : row.outcome == filter)) &&
            (query.isEmpty || row.title.localizedCaseInsensitiveContains(query) || row.directory.localizedCaseInsensitiveContains(query))
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text("Bilan des conversations").font(.title2)
                Spacer()
                Button("Fermer") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            Text("Examiner un titre n’est pas lire son contenu. Tes décisions de rattachement priment sur les classements suivants.")
                .foregroundStyle(.secondary)
            TextField("Rechercher un titre ou un dossier", text: $query)
            Picker("Afficher", selection: $filter) {
                Text("Toutes").tag("all")
                Text("Sans rattachement").tag("unassigned")
                Text("À examiner").tag("unexamined")
                Text("Ambiguës").tag("ambiguous")
                Text("Sans projet identifié").tag("no_project")
                Text("À décider").tag("defer")
            }
            if let error { Text(error).foregroundStyle(.red) }
            Text("\(filtered.count) conversations").font(.caption)
            List(filtered) { row in
                VStack(alignment: .leading, spacing: 7) {
                    Text(row.title).font(.headline).textSelection(.enabled)
                    Text(row.directory).font(.caption).foregroundStyle(.secondary)
                    Text(row.reason).font(.callout).foregroundStyle(.secondary)
                    if let excerpts = row.excerptText {
                        DisclosureGroup("Extraits utilisés · pas une lecture intégrale") {
                            Text(excerpts).font(.callout).textSelection(.enabled)
                        }
                    }
                    HStack {
                        Text(label(row)).font(.callout)
                        Spacer()
                        ConversationAssignment(sessionID: row.id)
                    }
                }.padding(.vertical, 8)
            }
        }.padding(24).frame(minWidth: 780, minHeight: 580)
            .task { await load() }
            .onChange(of: store.state?.changes.first?.id) { _, _ in Task { await load() } }
            .onChange(of: store.state?.overview?.examined) { _, _ in Task { await load() } }
    }

    private func label(_ row: CatalogConversation) -> String {
        let names = (store.state?.projects ?? []).filter { row.projectIds.contains($0.id) }.map(\.name)
        if !names.isEmpty { return names.joined(separator: ", ") }
        return ["none": "Aucun projet · ton choix", "defer": "À décider · ton choix",
                "unexamined": "Titre à examiner", "proposed": "Proposition à valider",
                "no_project": "Sans projet identifié", "ambiguous": "À clarifier"][row.outcome] ?? "Examiné"
    }

    private func load() async {
        do {
            rows = try store.client.decode([CatalogConversation].self, from: await store.client.request("/api/catalog-details"))
            error = nil
        } catch { self.error = error.localizedDescription }
    }
}

struct ProjectRecognitionMemory: View {
    @EnvironmentObject private var store: DiscoveryStore
    let project: Project
    @State private var memory: RecognitionMemory?
    @State private var rules = ""
    @State private var error: String?

    var body: some View {
        DisclosureGroup("Ce qui aide Hoomy à reconnaître ce projet") {
            VStack(alignment: .leading, spacing: 12) {
                Text("Au plus 10 exemples validés et diversifiés, sans contenu de conversation. Les motifs déduits sont des indices, pas des décisions.")
                    .font(.callout).foregroundStyle(.secondary)
                TextField("Tes règles : alias, sujets caractéristiques, distinctions…", text: $rules, axis: .vertical).lineLimit(3...6)
                Button("Enregistrer les règles") {
                    Task { await store.mutate("/api/projects/\(store.client.safeID(project.id))/correct", body: ["recognition_rules": rules]) }
                }.disabled(store.isWorking || rules.count > 2000)
                if let error { Text(error).foregroundStyle(.red) }
                if let memory {
                    if !memory.patternHints.isEmpty {
                        Text("Indices récurrents à vérifier : " + memory.patternHints.joined(separator: ", "))
                    }
                    ForEach(Array(memory.examples.enumerated()), id: \.offset) { _, example in
                        Text("\(example.belongs ? "Exemple validé" : "Contre-exemple") · \(example.title) · \(example.folder)").font(.callout)
                    }
                }
            }.padding(.top, 12)
        }.task {
            rules = project.recognitionRules ?? ""
            do {
                let items = try store.client.decode([RecognitionMemory].self, from: await store.client.request("/api/recognition-context"))
                memory = items.first { $0.id == project.id }
            } catch { self.error = error.localizedDescription }
        }
    }
}
