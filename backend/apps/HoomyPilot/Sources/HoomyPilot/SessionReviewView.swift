import SwiftUI
import Combine

struct SessionReviewView: View {
    @EnvironmentObject private var store: DiscoveryStore
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    let sessionID: String
    var snapshotID: String? = nil
    @State private var session: SessionDetail?
    @State private var error: String?
    @State private var edits: [String: String] = [:]
    @State private var removed = Set<String>()
    @State private var accepted = Set<String>()
    @State private var verdict = ""
    @State private var evidenceChoice = ""
    @State private var seconds: Double = 0
    @State private var timerRunning = true
    @State private var lastTick = Date()
    @State private var saved = false
    @State private var reviewError: String?
    @State private var showReview = false
    private let timer = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Résumé de conversation").font(.headline)
                Spacer()
                if session?.brief != nil && snapshotID == nil {
                    Button(showReview ? "Lire le résumé" : "Review détaillée") {
                        showReview.toggle(); lastTick = Date()
                    }
                }
                Button("Fermer") { dismiss() }.keyboardShortcut(.cancelAction)
            }.padding(20)
            Divider()
            if let session {
                reviewContent(session)
            } else if let error {
                ContentUnavailableView {
                    Label("Couldn’t open this conversation", systemImage: "exclamationmark.bubble")
                } description: { Text(error) } actions: {
                    Button("Try again") { Task { await load() } }
                }
            } else {
                ProgressView("Loading the private evidence…").frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .frame(minWidth: 640, idealWidth: 760, maxWidth: 900, minHeight: 520, idealHeight: 680)
        .task { await load() }
        .onReceive(timer) { now in
            if showReview && snapshotID == nil && session?.brief != nil && timerRunning && scenePhase == .active && !saved {
                seconds += min(2, max(0, now.timeIntervalSince(lastTick)))
            }
            lastTick = now
        }
        .onChange(of: scenePhase) { _, _ in lastTick = Date() }
        .onDisappear { timerRunning = false }
    }

    private func reviewContent(_ session: SessionDetail) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                VStack(alignment: .leading, spacing: 9) {
                    Text(session.title).font(.title.bold()).textSelection(.enabled)
                    Text("\(session.status.capitalized) · \(displayDate(session.modifiedAt))").font(.caption).foregroundStyle(.secondary)
                    if snapshotID == nil {
                        ConversationAssignment(sessionID: sessionID)
                    }
                    if snapshotID != nil {
                        Label("Source snapshot · read-only historical evidence", systemImage: "clock.arrow.circlepath")
                            .font(.callout).foregroundStyle(.secondary)
                    }
                    if let warning = session.warning, !warning.isEmpty {
                        Label(warning, systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
                    }
                    if showReview, let previous = session.review {
                        Text("Previous development review: \(previous.verdict.replacingOccurrences(of: "_", with: " ")) · \(Int(previous.seconds)) seconds. Saving again replaces that review; this is not a frozen pilot result.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                if let brief = session.brief {
                    Text("Lecture de la conversation · les sources sont consultables sous chaque point.")
                        .font(.callout).foregroundStyle(.secondary)
                    ForEach(brief.sections, id: \.key) { section in
                        VStack(alignment: .leading, spacing: 14) {
                            Text(section.title).font(.title2.bold())
                            if section.statements.isEmpty {
                                Text("Nothing identified.").foregroundStyle(.secondary)
                            }
                            ForEach(Array(section.statements.enumerated()), id: \.offset) { index, statement in
                                statementView(statement, section: section.key, index: index, session: session)
                            }
                        }
                        Divider()
                    }
                    if snapshotID == nil && showReview { humanReview(brief) }
                } else {
                    VStack(alignment: .leading, spacing: 14) {
                        Text(session.status == "indexed" ? "Seul le titre a été repéré" : session.status == "failed" ? "Cette lecture est à reprendre" : "Le résumé n’est pas encore prêt").font(.title2.weight(.semibold))
                        Text(session.status == "indexed" ? "Pour lire les échanges, retourne au projet et choisis Approfondir." : "Les résultats déjà obtenus sont conservés. La lecture détaillée se lance depuis le projet.")
                            .foregroundStyle(.secondary)
                        if session.status == "failed" && snapshotID == nil {
                            Button("Retry analysis") {
                                Task {
                                    if await store.mutate("/api/sessions/\(store.client.safeID(sessionID))/retry", body: [:]) { await load() }
                                }
                            }.disabled(store.isWorking)
                        }
                        if snapshotID == nil && session.status != "indexed" { Button("Actualiser le résumé") { Task { await load() } } }
                    }
                }
                if !session.messages.isEmpty { DisclosureGroup("Conversation complète · \(session.messages.count) messages") {
                    LazyVStack(alignment: .leading, spacing: 18) {
                        ForEach(session.messages) { message in
                            VStack(alignment: .leading, spacing: 7) {
                                Text("\(message.role.capitalized) · source lines \(message.lineStart)–\(message.lineEnd)").font(.caption.bold())
                                Text(message.text).font(.callout).textSelection(.enabled)
                            }.frame(maxWidth: .infinity, alignment: .leading)
                            Divider()
                        }
                    }.padding(.top, 14)
                } }
            }.padding(28)
        }
    }

    private func statementView(_ statement: Statement, section: String, index: Int, session: SessionDetail) -> some View {
        let key = "\(section):\(index)"
        return VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(statement.kind.capitalized).font(.caption.bold()).foregroundStyle(.secondary)
                Spacer()
                if removed.contains(key) { Text("Removed in your review").font(.caption).foregroundStyle(.secondary) }
                else if accepted.contains(key) { Label("Kept by you", systemImage: "checkmark").font(.caption).foregroundStyle(.blue) }
                else if edits[key] != nil { Text("Edited by you").font(.caption).foregroundStyle(.blue) }
            }
            if showReview && edits[key] != nil && !removed.contains(key) {
                TextField("Your corrected statement", text: Binding(get: { edits[key] ?? statement.text }, set: { edits[key] = $0; saved = false }), axis: .vertical)
                    .lineLimit(2...10).textFieldStyle(.roundedBorder)
            } else {
                Text(edits[key] ?? statement.text).strikethrough(removed.contains(key)).foregroundStyle(removed.contains(key) ? .secondary : .primary).textSelection(.enabled)
            }
            DisclosureGroup("Sources (\(statement.evidence.count))") {
                VStack(alignment: .leading, spacing: 14) {
                    ForEach(Array(statement.evidence.enumerated()), id: \.offset) { _, evidence in
                        VStack(alignment: .leading, spacing: 7) {
                            if let source = session.messages.first(where: { $0.id == evidence.messageId }) {
                                Text("\(source.role.capitalized) · source lines \(source.lineStart)–\(source.lineEnd)").font(.caption.bold())
                                Text(evidence.quote).textSelection(.enabled)
                                    .padding(.leading, 10)
                                    .overlay(alignment: .leading) { Rectangle().fill(.blue.opacity(0.5)).frame(width: 2) }
                                DisclosureGroup("Read the source message") {
                                    Text(source.text).font(.callout).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding(.top, 8)
                                }
                            } else {
                                Label("Source message is unavailable. Do not mark this evidence valid.", systemImage: "exclamationmark.triangle")
                                    .foregroundStyle(.orange)
                            }
                        }
                    }
                }.padding(.top, 10)
            }.font(.callout)
            if snapshotID == nil && showReview { HStack(spacing: 14) {
                Button("Keep") { accepted.insert(key); edits[key] = nil; removed.remove(key); saved = false }
                Button("Edit") { accepted.remove(key); removed.remove(key); edits[key] = edits[key] ?? statement.text; saved = false }
                Button(removed.contains(key) ? "Restore" : "Remove") {
                    if removed.contains(key) { removed.remove(key) }
                    else { removed.insert(key); accepted.remove(key); edits[key] = nil }
                    saved = false
                }
            }.buttonStyle(.link).font(.callout) }
        }.padding(.vertical, 10)
    }

    private func humanReview(_ brief: ConversationBrief) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Your review").font(.title2.bold())
            Text("This records your judgment, not an automatic quality score. Development reviews do not count toward a frozen ten-session pilot.")
                .font(.callout).foregroundStyle(.secondary)
            if let reviewError {
                Label(reviewError, systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
                Button("Discard unsaved edits and reload latest brief") { Task { await load() } }
            }
            HStack {
                Label("\(Int(seconds)) seconds of review", systemImage: "timer")
                    .monospacedDigit()
                Button(timerRunning ? "Pause timer" : "Resume timer") {
                    timerRunning.toggle(); lastTick = Date()
                }
                Text("Only while this review is visible and the app is active.").font(.caption).foregroundStyle(.secondary)
            }
            Picker("Overall quality", selection: $verdict) {
                Text("Choose your verdict").tag("")
                Text("Accept as is").tag("accept")
                Text("Minor edits").tag("minor_edit")
                Text("Major edits").tag("major_edit")
                Text("Reject").tag("reject")
            }
            Picker("Does the source evidence support the statements?", selection: $evidenceChoice) {
                Text("Not assessed yet").tag("")
                Text("Yes, I checked").tag("valid")
                Text("No, I found an evidence problem").tag("invalid")
            }
            HStack {
                Button(saved ? "Review saved" : "Save my review") {
                    var changes: [[String: Any]] = []
                    for section in brief.sections {
                        for (index, statement) in section.statements.enumerated() {
                            let key = "\(section.key):\(index)"
                            if removed.contains(key) {
                                changes.append(["section": section.key, "index": index, "text": NSNull()])
                            } else if let text = edits[key] {
                                changes.append(["section": section.key, "index": index, "text": text])
                            } else if accepted.contains(key) {
                                changes.append(["section": section.key, "index": index, "text": statement.text])
                            }
                        }
                    }
                    Task {
                        if await store.mutate("/api/sessions/\(store.client.safeID(sessionID))/review", body: ["snapshot_id": session?.snapshotId ?? "", "verdict": verdict, "seconds": seconds, "evidence_valid": evidenceChoice == "valid", "edits": changes]) {
                            saved = true
                            timerRunning = false
                            reviewError = nil
                        } else {
                            reviewError = store.actionError ?? "The review was not saved. Please try again."
                        }
                    }
                }.buttonStyle(.borderedProminent)
                    .disabled(verdict.isEmpty || evidenceChoice.isEmpty || saved || store.isWorking)
                if saved { Label("Stored privately on this Mac", systemImage: "checkmark").font(.callout).foregroundStyle(.secondary) }
            }
        }
        .onChange(of: verdict) { _, _ in saved = false }
        .onChange(of: evidenceChoice) { _, _ in saved = false }
    }

    private func load() async {
        do {
            let detail: SessionDetail
            if let snapshotID { detail = try await store.client.snapshot(snapshotID) }
            else { detail = try await store.client.session(sessionID) }
            session = detail
            error = nil
            reviewError = nil
            edits = [:]
            removed = []
            accepted = []
            verdict = ""
            evidenceChoice = ""
            seconds = 0
            saved = false
            timerRunning = true
            // Existing edits are visible, but a new human verdict is never preselected.
            for edit in detail.review?.edits ?? [] {
                let key = "\(edit.section):\(edit.index)"
                if let text = edit.text { edits[key] = text }
                else { removed.insert(key) }
            }
            lastTick = Date()
        } catch { self.error = error.localizedDescription }
    }
}
