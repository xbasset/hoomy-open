import SwiftUI

struct ProjectDetailView: View {
    @EnvironmentObject private var store: DiscoveryStore
    let project: Project
    let state: DiscoveryState
    let openSession: (SessionSummary) -> Void
    @State private var editing = false
    @State private var merging = false
    @State private var splitting = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 25) {
                VStack(alignment: .leading, spacing: 12) {
                    Text(project.name).font(.largeTitle.weight(.semibold)).textSelection(.enabled)
                    Text(project.purpose.isEmpty ? "The purpose is not clear yet." : project.purpose)
                        .font(.title3).foregroundStyle(.secondary).textSelection(.enabled)
                    let ready = state.sessions.filter { project.sessionIds.contains($0.id) && $0.status == "analyzed" }.count
                    Text("\(project.sessionIds.count) conversations liées · \(ready) résumés prêts")
                        .font(.callout).foregroundStyle(.secondary)
                    Text(project.isInactive ? "Inactif · historique conservé, sans approfondissement" : "Actif")
                        .font(.callout).foregroundStyle(.secondary)
                }
                if let note = project.note, !note.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Ton contexte").font(.headline)
                        Text(note).textSelection(.enabled)
                    }
                }
                HStack {
                    if !["name", "purpose"].allSatisfy({ project.confirmedFields.contains($0) }) {
                        Button("Garder ce projet") {
                            Task {
                                await store.mutate("/api/projects/\(store.client.safeID(project.id))/correct", body: ["name": project.name, "purpose": project.purpose])
                            }
                        }.buttonStyle(.borderedProminent).disabled(store.isWorking)
                    } else {
                        Button("Approfondir · jusqu’à 3 conversations") {
                            Task { await store.mutate("/api/projects/\(store.client.safeID(project.id))/deepen", body: [:]) }
                        }.buttonStyle(.borderedProminent)
                            .disabled(project.isInactive || store.isWorking || state.control.running || !state.sessions.contains { project.sessionIds.contains($0.id) && $0.status != "analyzed" })
                    }
                    Button("Corriger") { editing = true }
                    Button("Regrouper avec un autre projet…") { merging = true }
                        .disabled(store.isWorking || state.control.running || state.projects.filter { !$0.dismissed && $0.id != project.id }.isEmpty)
                    Button(project.isInactive ? "Réactiver" : "Rendre inactif") {
                        Task { await store.mutate("/api/projects/\(store.client.safeID(project.id))/correct", body: ["activity": project.isInactive ? "active" : "inactive"]) }
                    }.disabled(store.isWorking || state.control.running)
                }
                if state.control.running, state.control.mode == "deep" {
                    HStack {
                        ProgressView().controlSize(.small)
                        Text("Approfondissement · contenu des conversations · \(state.overview?.deepDone ?? 0) / \(state.overview?.deepTotal ?? 0)").font(.callout)
                        Spacer()
                        Button("Pause") { Task { await store.mutate("/api/control", body: ["running": false]) } }
                    }
                }
                Divider()
                VStack(alignment: .leading, spacing: 14) {
                    Text("Conversations liées").font(.title2.weight(.semibold))
                    ForEach(state.sessions.filter { project.sessionIds.contains($0.id) }) { session in
                        Button { openSession(session) } label: {
                            HStack(alignment: .top) {
                                Image(systemName: "text.bubble").foregroundStyle(.blue)
                                VStack(alignment: .leading, spacing: 5) {
                                    Text(session.title).foregroundStyle(.primary)
                                    Text("\(session.status == "indexed" ? "Titre repéré · échanges non lus" : session.status == "analyzed" ? "Résumé prêt" : session.status == "failed" ? "Lecture à reprendre" : "Non lu") · \(displayDate(session.modifiedAt))")
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                Image(systemName: "chevron.right").foregroundStyle(.tertiary)
                            }.padding(.vertical, 8).contentShape(Rectangle())
                        }.buttonStyle(.plain)
                        ConversationAssignment(sessionID: session.id)
                    }
                    if project.sessionIds.isEmpty { Text("Aucune conversation liée.").foregroundStyle(.secondary) }
                }
                ProjectRecognitionMemory(project: project)
                DisclosureGroup("Ajuster le regroupement") {
                    VStack(alignment: .leading, spacing: 12) {
                        Button("C’est le même qu’un autre projet…") { merging = true }
                            .disabled(state.projects.filter { !$0.dismissed && $0.id != project.id }.isEmpty)
                        Button("Certaines conversations appartiennent à un autre projet…") { splitting = true }
                            .disabled(project.sessionIds.isEmpty)
                        Button(project.dismissed ? "Remettre dans mes projets" : "Ce n’est pas un projet à garder") {
                            Task { await store.mutate("/api/projects/\(store.client.safeID(project.id))/correct", body: ["dismissed": !project.dismissed]) }
                        }
                    }
                    .padding(.top, 12)
                }.font(.callout)
            }.padding(34).frame(maxWidth: 860, alignment: .leading).frame(maxWidth: .infinity, alignment: .leading)
        }
        .navigationTitle(project.name)
        .sheet(isPresented: $editing) { ProjectEditor(project: project).environmentObject(store) }
        .sheet(isPresented: $merging) { MergeProjectView(project: project, projects: state.projects).environmentObject(store) }
        .sheet(isPresented: $splitting) {
            SplitProjectView(project: project, sessions: state.sessions.filter { project.sessionIds.contains($0.id) }).environmentObject(store)
        }
    }
}

struct ProjectEditor: View {
    @EnvironmentObject private var store: DiscoveryStore
    @Environment(\.dismiss) private var dismiss
    let project: Project
    @State private var name: String
    @State private var purpose: String
    @State private var status: String
    @State private var note: String

    init(project: Project) {
        self.project = project
        _name = State(initialValue: project.name)
        _purpose = State(initialValue: project.purpose)
        _status = State(initialValue: project.status)
        _note = State(initialValue: project.note ?? "")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Corriger le projet").font(.title2.weight(.semibold))
            Form {
                TextField("Nom", text: $name)
                TextField("Ce que tu cherches à faire", text: $purpose, axis: .vertical).lineLimit(2...5)
                DisclosureGroup("Contexte et état, si utile") {
                    Picker("État", selection: $status) {
                        Text("À déterminer").tag("unclear")
                        Text("En cours").tag("active")
                        Text("En pause").tag("paused")
                        Text("Terminé").tag("completed")
                    }
                    TextField("Ton contexte", text: $note, axis: .vertical).lineLimit(3...6)
                }
            }
            HStack {
                Button("Annuler") { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("Enregistrer") {
                    var body: [String: Any] = [:]
                    if name != project.name { body["name"] = name.trimmingCharacters(in: .whitespacesAndNewlines) }
                    if purpose != project.purpose { body["purpose"] = purpose }
                    if status != project.status { body["status"] = status }
                    if note != (project.note ?? "") { body["note"] = note }
                    Task {
                        if await store.mutate("/api/projects/\(store.client.safeID(project.id))/correct", body: body) { dismiss() }
                    }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(store.isWorking || name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !hasChanges)
            }
        }.padding(28).frame(width: 590)
    }

    private var hasChanges: Bool {
        name != project.name || purpose != project.purpose || status != project.status || note != (project.note ?? "")
    }
}

struct MergeProjectView: View {
    @EnvironmentObject private var store: DiscoveryStore
    @Environment(\.dismiss) private var dismiss
    let project: Project
    let projects: [Project]
    @State private var targetID = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Regrouper deux projets").font(.title2.bold())
            Text("Les conversations de « \(project.name) » rejoindront le projet choisi. Son nom, sa description et son état actif ou inactif seront conservés. Hoomy retiendra ce regroupement. Il n’y a pas d’annulation en un clic dans ce pilote.")
                .foregroundStyle(.secondary)
            Picker("Projet à conserver", selection: $targetID) {
                Text("Choisir un projet").tag("")
                ForEach(projects.filter { !$0.dismissed && $0.id != project.id }) { candidate in
                    Text(candidate.name).tag(candidate.id)
                }
            }
            HStack {
                Button("Annuler") { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("Confirmer le regroupement") {
                    Task {
                        if await store.mutate("/api/projects/merge", body: ["source_ids": [project.id], "target_id": targetID]) { dismiss() }
                    }
                }.disabled(targetID.isEmpty || store.isWorking || store.state?.control.running == true)
            }
        }.padding(28).frame(width: 560)
    }
}

struct SplitProjectView: View {
    @EnvironmentObject private var store: DiscoveryStore
    @Environment(\.dismiss) private var dismiss
    let project: Project
    let sessions: [SessionSummary]
    @State private var selected = Set<String>()
    @State private var name = ""
    @State private var purpose = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("These belong to a different project").font(.title2.bold())
            Text("Move the selected conversation links into a new project. Your grouping will be preserved. This first version separates whole conversations, not individual statements.")
                .foregroundStyle(.secondary)
            TextField("New project name", text: $name)
            TextField("Purpose", text: $purpose, axis: .vertical).lineLimit(2...4)
            List(sessions) { session in
                Toggle(session.title, isOn: Binding(
                    get: { selected.contains(session.id) },
                    set: { if $0 { selected.insert(session.id) } else { selected.remove(session.id) } }
                )).padding(.vertical, 4)
            }.frame(height: 230)
            HStack {
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("Create separate project") {
                    Task {
                        if await store.mutate("/api/projects/\(store.client.safeID(project.id))/split", body: ["session_ids": Array(selected), "name": name, "purpose": purpose]) { dismiss() }
                    }
                }.disabled(selected.isEmpty || name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || store.isWorking)
            }
        }.padding(28).frame(width: 610)
    }
}
