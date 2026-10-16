import SwiftUI

struct AgentExplorationProgressView: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let progress: AgentProgress
    let running: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                if running { ProgressView().controlSize(.small).accessibilityLabel("Traitement en cours") }
                Text(running ? "Exploration en direct" : "Dernière exploration").font(.headline)
                Spacer()
                Text("\(Int(progress.elapsedSeconds)) s").monospacedDigit().foregroundStyle(.secondary)
            }
            ForEach(progress.plan.indices, id: \.self) { index in
                let step = progress.plan[index]
                Label(step.step, systemImage: step.status == "completed" ? "checkmark.circle.fill" : step.status == "pending" ? "circle" : "circle.dotted")
                    .font(.callout)
            }
            ScrollViewReader { reader in
                ScrollView {
                    VStack(alignment: .leading, spacing: 10) {
                        ForEach(progress.events.indices, id: \.self) { index in
                            let event = progress.events[index]
                            HStack(alignment: .top, spacing: 12) {
                                Text("\(Int(event.elapsedSeconds)) s").font(.caption.monospacedDigit()).foregroundStyle(.secondary).frame(width: 45, alignment: .trailing)
                                Text(event.text).font(.callout).textSelection(.enabled)
                            }
                        }
                        Color.clear.frame(height: 1).id("latest")
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }.frame(maxHeight: 240)
                    .onChange(of: progress.events.last?.text) { _, _ in
                        if running {
                            withAnimation(reduceMotion ? nil : .easeOut(duration: 0.25)) {
                                reader.scrollTo("latest", anchor: .bottom)
                            }
                        }
                    }
            }
            if let usage = progress.usage {
                Text("Tokens SDK · entrée : \(usage.inputTokens.map(String.init) ?? "—") · sortie : \(usage.outputTokens.map(String.init) ?? "—")")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Text("Activité et résumés publics du SDK · aucun raisonnement interne brut. Les extraits restent partiels.\(running ? "" : " Les résultats publiés sont conservés ; une reprise repart sur le travail restant.")")
                .font(.caption).foregroundStyle(.secondary)
        }.padding(16).background(Color.secondary.opacity(0.06), in: RoundedRectangle(cornerRadius: 12))
    }
}
