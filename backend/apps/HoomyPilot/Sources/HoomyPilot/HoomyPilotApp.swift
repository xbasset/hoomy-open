import SwiftUI
#if os(macOS)
import AppKit
#endif

@main
struct HoomyPilotApp: App {
    @StateObject private var store = DiscoveryStore()

    var body: some Scene {
        WindowGroup("Hoomy · Discover / Act & Learn") {
            RecognitionView()
                .environmentObject(store)
                .tint(.blue)
                .frame(minWidth: 850, minHeight: 620)
                .onAppear {
                    #if os(macOS)
                    NSApplication.shared.setActivationPolicy(.regular)
                    NSApplication.shared.activate(ignoringOtherApps: true)
                    #endif
                }
        }
        #if os(macOS)
        .defaultSize(width: 1100, height: 780)
        .commands { CommandGroup(replacing: .newItem) {} }
        #endif
    }
}
