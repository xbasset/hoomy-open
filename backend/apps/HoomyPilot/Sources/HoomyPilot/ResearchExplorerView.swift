import SwiftUI

struct ResearchExplorerTarget: Identifiable { let id: String }
struct ResearchGraph: Decodable {
    let requestId: String
    let status: String
    let background: String
    let nextStep: String
    let nodes: [ResearchNode]
    let edges: [ResearchEdge]
    let projects: [ResearchProject]
    let feedback: [ResearchFeedback]
}
struct ResearchNode: Decodable, Identifiable {
    let id: String
    let column: Int
    let title: String
    let status: String
    let detail: String
    var color: Color { status == "running" ? .blue : status == "blocked" ? .orange : status == "done" ? .green : .gray }
    var statusLabel: String {
        switch status {
        case "running": return "En cours"
        case "blocked": return "Point ouvert / écarté"
        case "done": return "Étape traitée"
        default: return "En attente"
        }
    }
}
struct ResearchEdge: Decodable { let source: String; let target: String }
struct ResearchProject: Decodable, Identifiable { let id: String; let name: String }
struct ResearchFeedback: Decodable, Identifiable { let id: String; let nodeId: String; let text: String; let at: String }

struct ResearchExplorerView: View {
    let requestId: String
    @EnvironmentObject private var store: DiscoveryStore
    @Environment(\.dismiss) private var dismiss
    @State private var graph: ResearchGraph?
    @State private var selectedId = "outcome"
    @State private var project = ""
    @State private var note = ""
    @State private var error: String?
    @State private var saved = false
    @State private var saving = false
    private let columns = ["Demande", "Conversations", "Pistes", "État et vérifications", "Décisions et questions"]
    private var selected: ResearchNode? { graph?.nodes.first { $0.id == selectedId } }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                VStack(alignment: .leading) {
                    Text("Explorer la recherche").font(.title2.bold())
                    Text(graph?.background ?? "Lecture du journal…").foregroundStyle(.secondary)
                }
                Spacer()
                Button("Fermer") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            if let graph {
                Text(graph.nextStep).font(.callout)
                diagram(graph)
                Text("Clique une carte. Les liens montrent les sources et les résultats enregistrés. Une étape traitée ne signifie pas que l’activité est terminée.")
                    .font(.caption).foregroundStyle(.secondary)
                if let selected {
                    HStack {
                        Circle().fill(selected.color).frame(width: 8, height: 8)
                        Text(selected.title).font(.headline)
                        Text(selected.statusLabel).font(.caption).foregroundStyle(.secondary)
                    }
                    ScrollView { Text(selected.detail).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }
                        .frame(minHeight: 80, maxHeight: 160)
                    HStack {
                        Text("Ton retour sur cette étape").font(.headline)
                        Picker("Projet", selection: $project) {
                            Text("Choisir le projet concerné").tag("")
                            ForEach(graph.projects) { Text($0.name).tag($0.id) }
                        }.frame(maxWidth: 300)
                    }
                    TextField("Ce qui manque, ce qui est incorrect, ou ce qui compte pour toi…", text: $note, axis: .vertical)
                        .lineLimit(2...4).textFieldStyle(.roundedBorder)
                    HStack {
                        Button("Enregistrer mon retour") { Task { await save() } }
                            .buttonStyle(.borderedProminent)
                            .disabled(saving || project.isEmpty || note.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        if saved { Text("Enregistré pour les prochaines recherches du projet.").foregroundStyle(.secondary) }
                    }
                    ForEach(graph.feedback.filter { $0.nodeId == selectedId }.prefix(3)) { feedback in
                        Text("Ton retour : \(feedback.text)").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            if let error { Text(error).foregroundStyle(.red) }
        }.padding(24).frame(minWidth: 1000, minHeight: 720)
            .task {
                while !Task.isCancelled {
                    await refresh()
                    do { try await Task.sleep(for: .seconds(2)) } catch { return }
                }
            }
    }

    private func position(_ node: ResearchNode, in graph: ResearchGraph) -> CGPoint {
        let index = graph.nodes.filter { $0.column == node.column }.firstIndex { $0.id == node.id } ?? 0
        return CGPoint(x: CGFloat(node.column) * 235 + 105, y: CGFloat(index) * 108 + 76)
    }

    private func diagram(_ graph: ResearchGraph) -> some View {
        let height = CGFloat((0..<5).map { col in graph.nodes.filter { $0.column == col }.count }.max() ?? 1) * 108 + 40
        return ScrollView([.horizontal, .vertical]) {
            ZStack(alignment: .topLeading) {
                Canvas { context, _ in
                    for edge in graph.edges {
                        guard let a = graph.nodes.first(where: { $0.id == edge.source }),
                              let b = graph.nodes.first(where: { $0.id == edge.target }) else { continue }
                        let from = position(a, in: graph), to = position(b, in: graph)
                        var path = Path()
                        if a.column == b.column {
                            path.move(to: CGPoint(x: from.x, y: from.y + 44))
                            path.addLine(to: CGPoint(x: to.x, y: to.y - 44))
                        } else {
                            path.move(to: CGPoint(x: from.x + 100, y: from.y))
                            path.addCurve(to: CGPoint(x: to.x - 100, y: to.y),
                                      control1: CGPoint(x: from.x + 125, y: from.y),
                                      control2: CGPoint(x: to.x - 125, y: to.y))
                        }
                        let active = selectedId == edge.source || selectedId == edge.target
                        context.stroke(path, with: .color(active ? .blue.opacity(0.8) : .gray.opacity(0.25)), lineWidth: active ? 2 : 1)
                    }
                }
                ForEach(Array(columns.enumerated()), id: \.offset) { index, title in
                    Text(title).font(.caption.bold()).position(x: CGFloat(index) * 235 + 105, y: 16)
                }
                ForEach(graph.nodes) { node in
                    Button {
                        selectedId = node.id; note = ""; saved = false
                    } label: {
                        VStack(alignment: .leading, spacing: 6) {
                            Text(node.title).font(.callout.weight(.medium)).lineLimit(2)
                            Label(node.statusLabel, systemImage: node.status == "running" ? "arrow.triangle.2.circlepath" : "circle.fill")
                                .font(.caption2).foregroundStyle(node.color)
                        }.padding(10).frame(width: 200, height: 88, alignment: .leading)
                            .background(node.color.opacity(0.08)).background(.background)
                            .clipShape(RoundedRectangle(cornerRadius: 10))
                            .overlay(RoundedRectangle(cornerRadius: 10).stroke(selectedId == node.id ? Color.blue : node.color.opacity(0.3), lineWidth: selectedId == node.id ? 2 : 1))
                    }.buttonStyle(.plain).position(position(node, in: graph))
                }
            }.frame(width: 1150, height: max(250, height))
        }.frame(height: 310).background(Color.gray.opacity(0.025))
    }

    private func refresh() async {
        do {
            graph = try store.client.decode(ResearchGraph.self, from: await store.client.request("/api/act/explore/\(store.client.safeID(requestId))"))
            if project.isEmpty, graph?.projects.count == 1 { project = graph?.projects.first?.id ?? "" }
        } catch { self.error = error.localizedDescription }
    }

    private func save() async {
        saving = true; error = nil
        defer { saving = false }
        do {
            _ = try await store.client.request("/api/act/research-feedback", body: ["request_id": requestId, "node_id": selectedId, "project_id": project, "text": note])
            note = ""; saved = true; await refresh()
        } catch { self.error = error.localizedDescription }
    }
}
