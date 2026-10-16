import SwiftUI

struct LiveAssociationsView: View {
    let state: DiscoveryState
    private var rows: [AutomaticAssociation] { state.overview?.automaticAssociations ?? [] }
    private var ambiguous: [AutomaticAssociation] { rows.filter { ($0.alternativeId != nil || !$0.ambiguityReason.isEmpty) && $0.corrected == 0 } }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if state.overview?.completionPhase == "agent" && state.overview?.agentContinuous == true {
                Text("L’exploration se poursuit · \(state.overview?.agentRemaining ?? 0) conversations restantes")
                    .font(.headline)
                Text("Les rattachements aux projets validés se font automatiquement. Tu peux corriger maintenant ; seuls les nouveaux projets demandent ton approbation.")
                    .font(.callout).foregroundStyle(.secondary)
            }
            if !ambiguous.isEmpty {
                Text("Rapprochements ambigus · \(ambiguous.count)").font(.title2.weight(.semibold))
                Text("L’autre projet plausible est déjà sélectionné : un clic pour réaffecter. Tu peux aussi choisir un autre projet dans la liste.")
                    .font(.caption).foregroundStyle(.secondary)
                ForEach(ambiguous) { row in
                    LiveAssociationRow(row: row, state: state)
                    Divider()
                }
            }
            if !rows.isEmpty {
                DisclosureGroup("Tous les rattachements automatiques · \(rows.count)") {
                    LazyVStack(alignment: .leading, spacing: 14) {
                        ForEach(rows) { row in
                            LiveAssociationRow(row: row, state: state)
                            Divider()
                        }
                    }.padding(.top, 10)
                }
            }
        }
    }
}

private struct LiveAssociationRow: View {
    @EnvironmentObject private var store: DiscoveryStore
    let row: AutomaticAssociation
    let state: DiscoveryState
    @State private var selected: String?
    private var target: String { selected ?? row.alternativeId ?? row.currentProjectId ?? row.projectId }
    private func name(_ id: String?) -> String {
        state.projects.first { $0.id == id }?.name ?? "Aucun projet"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(row.title).font(.headline)
            Text("\(row.corrected == 0 ? "Rattachée automatiquement" : "Corrigée par toi") → \(name(row.currentProjectId))")
                .font(.callout).foregroundStyle(.secondary)
            Text(row.rationale).font(.callout).textSelection(.enabled)
            if row.corrected == 0 && !row.ambiguityReason.isEmpty {
                Text("Ambiguïté : \(row.ambiguityReason)").font(.callout).foregroundStyle(.orange)
            }
            HStack {
                Picker("Autre projet", selection: Binding(get: { target }, set: { selected = $0 })) {
                    ForEach(state.projects.filter { !$0.dismissed }) { project in
                        Text(project.name).tag(project.id)
                    }
                }.labelsHidden().frame(maxWidth: 280)
                Button(target == row.currentProjectId ? "Confirmer" : "Réaffecter") { assign(target) }
                    .buttonStyle(.borderedProminent)
                if row.alternativeId != nil && row.corrected == 0, let current = row.currentProjectId {
                    Button("Conserver \(name(current))") { assign(current) }
                }
            }.disabled(store.isWorking || state.reviewBlocked || store.connectionError != nil)
        }
    }

    private func assign(_ project: String) {
        Task {
            await store.mutate("/api/catalog/\(store.client.safeID(row.id))/assign", body: ["decision": "project", "project_id": project])
        }
    }
}
