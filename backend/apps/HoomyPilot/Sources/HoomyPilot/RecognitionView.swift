import SwiftUI

struct RecognitionView: View {
    @EnvironmentObject private var store: DiscoveryStore
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var selectedProject: String?
    @State private var workspace = "act"
    @State private var selectedSession: SessionSummary?
    @State private var editingOutline: ProjectOutline?
    @State private var showSettings = false
    @State private var showDiagnostics = false
    @State private var showCatalog = false
    @State private var attachingOutline: ProjectOutline?
    @State private var showClarification = false
    @State private var showCompletion = false

    var body: some View {
        Group {
            if let state = store.state {
                    VStack(spacing: 0) {
                        HStack {
                            if selectedProject != nil {
                                Button { selectedProject = nil } label: { Label("Projets", systemImage: "chevron.left") }
                            } else { Text("Hoomy").font(.headline) }
                            Picker("Espace", selection: $workspace) {
                                Text("Mes projets").tag("discover")
                                Text("Pour maintenant").tag("act")
                            }.pickerStyle(.segmented).frame(width: 290)
                            Spacer()
                            if workspace == "discover" {
                            Menu("Outils de découverte") {
                                Button("Bilan des conversations") { showCatalog = true }
                                Button("Journal et diagnostic") { showDiagnostics = true }
                                if state.control.scopeConfirmed && state.control.inferenceEnabled {
                                    Divider()
                                    Button("Actualiser les nouveaux titres") { Task { await store.mutate("/api/overview/start", body: [:]) } }
                                        .disabled(state.control.running || store.isWorking)
                                    if !state.projects.isEmpty {
                                        Button("Compléter avec des extraits…") { showCompletion = true }
                                            .disabled(state.control.running)
                                    }
                                    Button("Choisir une lecture détaillée…") { showClarification = true }
                                        .disabled(state.control.running)
                                }
                            }.fixedSize()
                            }
                            Button("Confidentialité") { showSettings = true }
                        }.padding(.horizontal, 28).padding(.vertical, 14)
                        Divider()
                        if let error = store.connectionError {
                            Label(error, systemImage: "wifi.slash").font(.callout).padding(12)
                        }
                        if workspace == "act" {
                            ReadyIdeasView()
                        } else if let project = state.projects.first(where: { $0.id == selectedProject }) {
                            ProjectDetailView(project: project, state: state, openSession: { selectedSession = $0 }).id(project.id)
                        } else {
                            DiscoveryWizard(state: state, openProject: { selectedProject = $0 },
                                edit: { editingOutline = $0 }, attach: { attachingOutline = $0 },
                                inspect: { showCatalog = true })
                                .id(state.control.generation)
                        }
                    }
            } else {
                ContentUnavailableView {
                    Label("Connexion à Hoomy", systemImage: "desktopcomputer")
                } description: { Text(store.connectionError ?? "Ouverture du service local…") }
                actions: { Button("Réessayer") { Task { await store.refresh() } } }
            }
        }
        .navigationTitle("Hoomy")
        .task { await store.poll() }
        .onChange(of: store.state?.control.generation) { _, _ in
            selectedProject = nil; selectedSession = nil; editingOutline = nil
            showSettings = false; showDiagnostics = false; showCatalog = false
            attachingOutline = nil; showClarification = false
            showCompletion = false
        }
        .alert("La modification n’a pas été enregistrée", isPresented: Binding(
            get: { store.actionError != nil }, set: { if !$0 { store.actionError = nil } }
        )) { Button("OK") { store.actionError = nil } }
        message: { Text(store.actionError ?? "Réessaie.") }
        .sheet(isPresented: $showSettings) { ScopeSummaryView().environmentObject(store) }
        .sheet(isPresented: $showDiagnostics) { DiscoveryDiagnostics().environmentObject(store) }
        .sheet(isPresented: $showCatalog) { CatalogReview().environmentObject(store) }
        .sheet(item: $selectedSession) { SessionReviewView(sessionID: $0.id).environmentObject(store) }
        .sheet(item: $editingOutline) { OutlineEditor(outline: $0).environmentObject(store) }
        .sheet(item: $attachingOutline) { AttachOutlineView(outline: $0).environmentObject(store) }
        .sheet(isPresented: $showClarification) { ClarificationPicker().environmentObject(store) }
        .sheet(isPresented: $showCompletion) { CompletionConfirmation().environmentObject(store) }
    }

}

struct OutlineEditor: View {
    @EnvironmentObject private var store: DiscoveryStore
    @Environment(\.dismiss) private var dismiss
    let outline: ProjectOutline
    @State private var name: String
    @State private var purpose: String

    init(outline: ProjectOutline) {
        self.outline = outline
        _name = State(initialValue: outline.name)
        _purpose = State(initialValue: outline.purpose)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Comment décrirais-tu ce projet ?").font(.title2.weight(.semibold))
            TextField("Nom", text: $name)
            TextField("Ce que tu cherches à faire", text: $purpose, axis: .vertical).lineLimit(2...5)
            Text("Tu pourras ajuster les conversations liées dans le détail du projet.").font(.callout).foregroundStyle(.secondary)
            HStack {
                Button("Annuler") { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("Garder avec ces corrections") {
                    Task {
                        if await store.mutate("/api/outlines/\(store.client.safeID(outline.id))/review", body: ["decision": "keep", "name": name, "purpose": purpose]) { dismiss() }
                    }
                }.buttonStyle(.borderedProminent).disabled(store.isWorking || name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }.padding(28).frame(width: 540)
    }
}

struct DiscoveryDiagnostics: View {
    @EnvironmentObject private var store: DiscoveryStore
    @Environment(\.dismiss) private var dismiss
    @State private var tab = "conversations"
    @State private var selectedSession: SessionSummary?

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text("Détails de la découverte").font(.headline)
                Spacer()
                Button("Fermer") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            Picker("Afficher", selection: $tab) {
                Text("Conversations").tag("conversations")
                Text("Évolution").tag("changes")
                Text("Diagnostic").tag("status")
            }.pickerStyle(.segmented)
            if let state = store.state {
                if tab == "conversations" {
                    List(state.sessions) { session in
                        Button { selectedSession = session } label: {
                            HStack {
                                Text(session.title).foregroundStyle(.primary)
                                Spacer()
                                Text(session.status == "indexed" ? "Titre repéré" : session.status == "analyzed" ? "Résumé prêt" : session.status == "failed" ? "À reprendre" : "Non lu")
                                    .font(.caption).foregroundStyle(.secondary)
                            }.padding(.vertical, 5)
                        }.buttonStyle(.plain)
                    }
                } else if tab == "changes" { ChangesView(changes: state.changes) }
                else {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 14) {
                            if let progress = state.overview?.agentProgress {
                                AgentExplorationProgressView(progress: progress, running: state.control.running)
                            }
                            Text("\(state.coverage.analyzed) résumés prêts · \(state.coverage.failed) lectures à vérifier")
                            Text(state.worker.message ?? "En attente")
                            Text("Dernier contact : \(displayDate(state.worker.lastSeen))")
                            if let overview = state.overview {
                                Text("Inventaire : \(overview.indexed) conversations. Passe : \(overview.considered) titres.")
                                if let elapsed = overview.elapsedSeconds {
                                    Text("Dernier classement : \(Int(elapsed)) s · \(overview.model ?? "modèle inconnu")")
                                }
                                ForEach(Array(overview.failures.enumerated()), id: \.offset) { _, failure in Text(failure.reason) }
                                ForEach(Array((overview.deepFailures ?? []).enumerated()), id: \.offset) { _, failure in Text(failure.reason) }
                            }
                            ForEach(state.sessions.filter { $0.status == "failed" }) { session in
                                Text(session.warning ?? "Lecture à reprendre")
                            }
                            Text("Les citations restent vérifiées. Un échec ne valide aucun résultat. Les chronomètres et verdicts de recherche sont disponibles dans la review détaillée d’un résumé.")
                                .foregroundStyle(.secondary)
                        }.frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
        }.padding(24).frame(minWidth: 720, minHeight: 560)
            .sheet(item: $selectedSession) { SessionReviewView(sessionID: $0.id).environmentObject(store) }
    }
}
