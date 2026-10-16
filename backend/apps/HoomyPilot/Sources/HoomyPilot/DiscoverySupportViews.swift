import SwiftUI

struct ScopeView: View {
    @EnvironmentObject private var store: DiscoveryStore
    @State private var exclusions = ""
    @State private var consent = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                Label("Hoomy · Découverte privée", systemImage: "sparkle.magnifyingglass").foregroundStyle(.blue)
                Text("Retrouve les projets\nderrière tes conversations.")
                    .font(.system(size: 32, weight: .semibold, design: .rounded))
                Text("Hoomy examine les titres et dossiers de tout ton historique Codex autorisé sur ce Mac, sans limite de projets proposés. Tu choisis ensuite lesquels approfondir.")
                    .font(.title3).foregroundStyle(.secondary)
                Divider()
                VStack(alignment: .leading, spacing: 14) {
                    Label("Les copies, résumés et corrections sont conservés localement.", systemImage: "externaldrive")
                    Label("Les titres et dossiers, puis les échanges choisis pour approfondissement, sont traités par OpenAI via ton compte Codex. Les limites de ton abonnement s’appliquent.", systemImage: "network")
                    Label("Aucune action sur tes projets, publication ou télémétrie. Tu peux mettre en pause à tout moment.", systemImage: "hand.raised")
                }
                VStack(alignment: .leading, spacing: 7) {
                    Text("Dossiers à exclure").font(.headline)
                    Text("Facultatif : un chemin absolu par ligne. Exclus les dossiers que tu n’es pas autorisé à faire traiter. Hoomy compare ces préfixes au dossier enregistré pour chaque conversation.")
                        .font(.callout).foregroundStyle(.secondary)
                    TextEditor(text: $exclusions)
                        .font(.system(.body, design: .monospaced))
                        .frame(height: 80).padding(6)
                        .overlay(RoundedRectangle(cornerRadius: 8).stroke(.quaternary))
                        .accessibilityLabel("Chemins des dossiers exclus")
                    Text("Ces exclusions ne détectent pas les sujets sensibles dans les autres conversations. Modifier ce périmètre après le repérage nécessite une réinitialisation des données Hoomy.")
                        .font(.caption).foregroundStyle(.secondary)
                    Text("Avec des exclusions, les conversations sans dossier identifiable sont mises à l’écart.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Toggle("Je suis autorisé à faire traiter ces conversations et comprends que les données sélectionnées sont envoyées à OpenAI.", isOn: $consent)
                HStack {
                    Button(store.isWorking ? "Démarrage…" : "Repérer mes projets") {
                        let paths = exclusions.split(separator: "\n").map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
                        Task {
                            await store.mutate("/api/control", body: ["running": true, "inference_enabled": true, "exclusions": paths, "consent": true])
                        }
                    }
                    .buttonStyle(.borderedProminent).controlSize(.large)
                    .disabled(!consent || store.isWorking || store.connectionError != nil || !validPaths)
                    if !validPaths { Text("Utilise des chemins absolus commençant par /.").font(.caption).foregroundStyle(.red) }
                }
            }
            .padding(38).frame(maxWidth: 760, alignment: .leading).frame(maxWidth: .infinity)
        }
    }

    private var validPaths: Bool {
        exclusions.split(separator: "\n").allSatisfy { line in
            let value = line.trimmingCharacters(in: .whitespacesAndNewlines)
            return value.isEmpty || value.hasPrefix("/")
        }
    }
}

struct ScopeSummaryView: View {
    @EnvironmentObject private var store: DiscoveryStore
    @Environment(\.dismiss) private var dismiss
    @State private var confirmingReset = false

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Ton périmètre de découverte").font(.title2.bold())
            Text("Historique Codex sur ce Mac. Le stockage privé reste local. L’envoi de titres, puis d’extraits à OpenAI fait l’objet d’accords séparés dans le parcours.")
            Text("Dossiers exclus").font(.headline)
            if let paths = store.state?.control.exclusions, !paths.isEmpty {
                ForEach(paths, id: \.self) { Text($0).font(.system(.body, design: .monospaced)).textSelection(.enabled) }
            } else { Text("Aucun").foregroundStyle(.secondary) }
            Text("Conserver un projet comme inactif n’exclut pas ses conversations. Pour modifier le périmètre après lecture, il faut réinitialiser Hoomy. La suppression sélective n’est pas encore disponible.")
                .foregroundStyle(.secondary)
            HStack {
                Button("Réinitialiser Hoomy…", role: .destructive) { confirmingReset = true }
                    .disabled(store.isWorking || store.connectionError != nil)
                Spacer()
                Button("Fermer") { dismiss() }.keyboardShortcut(.defaultAction)
            }
        }.padding(28).frame(width: 560)
        .confirmationDialog("Réinitialiser cette expérimentation Hoomy ?", isPresented: $confirmingReset, titleVisibility: .visible) {
            Button("Supprimer les données Hoomy et recommencer", role: .destructive) {
                Task {
                    if await store.mutate("/api/reset", body: ["confirm": "DELETE_HOOMY_DATA"]) { dismiss() }
                }
            }
            Button("Annuler", role: .cancel) {}
        } message: {
            Text("La découverte s’arrêtera. Les copies de conversations, synthèses, projets et corrections seront supprimées du stockage local de Hoomy. Les fichiers Codex d’origine resteront intacts. Cette action est irréversible. Tu confirmeras de nouveau le périmètre avant toute découverte.")
        }
    }
}

struct ChangesView: View {
    let changes: [UnderstandingChange]
    @State private var selectedSnapshot: SnapshotSelection?

    var body: some View {
        Group {
            if changes.isEmpty {
                ContentUnavailableView("No changes yet", systemImage: "clock.arrow.circlepath", description: Text("Meaningful revisions and your corrections will appear here."))
            } else {
                List(changes) { change in
                    VStack(alignment: .leading, spacing: 9) {
                        Text(change.summary).font(.headline).textSelection(.enabled)
                        Text("\(change.kind.replacingOccurrences(of: "_", with: " ").capitalized) · \(displayDate(change.createdAt))")
                            .font(.caption).foregroundStyle(.secondary)
                        DisclosureGroup("Inspect the change") {
                            VStack(alignment: .leading, spacing: 12) {
                                if let before = change.before {
                                    Text("Before").font(.caption.bold())
                                    Text(before.readable).textSelection(.enabled)
                                }
                                if let after = change.after {
                                    Text("After").font(.caption.bold())
                                    Text(after.readable).textSelection(.enabled)
                                }
                                if let evidence = change.evidence, !evidence.readable.isEmpty, evidence.readable != "—" {
                                    Text("Supporting evidence").font(.caption.bold())
                                    Text(evidence.readable).textSelection(.enabled)
                                }
                                ForEach(Array(change.snapshotIds.enumerated()), id: \.element) { index, snapshotID in
                                    Button("Open source snapshot \(index + 1)") {
                                        selectedSnapshot = SnapshotSelection(id: snapshotID)
                                    }
                                }
                            }.font(.callout).frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, 8)
                        }
                    }.padding(.vertical, 10)
                }
            }
        }.navigationTitle("What changed")
            .sheet(item: $selectedSnapshot) { selection in
                SessionReviewView(sessionID: "", snapshotID: selection.id)
            }
    }
}

struct SnapshotSelection: Identifiable {
    let id: String
}
