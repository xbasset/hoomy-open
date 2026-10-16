import Foundation
import SwiftUI

struct APIError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

struct APIClient {
    // No remote endpoint override while this pilot has no authentication.
    let baseURL: URL = {
        // Isolated QA can select a loopback port; never a remote host.
        let args = ProcessInfo.processInfo.arguments
        if let index = args.firstIndex(of: "--api-port"), args.indices.contains(index + 1),
           let port = Int(args[index + 1]), (1024...65535).contains(port) {
            return URL(string: "http://127.0.0.1:\(port)")!
        }
        return URL(string: "http://127.0.0.1:8765")!
    }()

    func request(_ path: String, body: [String: Any]? = nil) async throws -> Data {
        guard let url = URL(string: path, relativeTo: baseURL) else {
            throw APIError(message: "This local request could not be prepared.")
        }
        var request = URLRequest(url: url)
        request.timeoutInterval = 12
        if let body {
            request.httpMethod = "POST"
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let response = response as? HTTPURLResponse else {
            throw APIError(message: "The local service sent an unreadable response.")
        }
        guard (200..<300).contains(response.statusCode) else {
            let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            throw APIError(message: value?["error"] as? String ?? "The local service could not complete that request (\(response.statusCode)).")
        }
        return data
    }

    func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(type, from: data)
    }

    func session(_ id: String) async throws -> SessionDetail {
        try decode(SessionDetail.self, from: await request("/api/sessions/\(safeID(id))"))
    }

    func snapshot(_ id: String) async throws -> SessionDetail {
        try decode(SessionDetail.self, from: await request("/api/snapshots/\(safeID(id))"))
    }

    func safeID(_ value: String) -> String {
        value.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? value
    }
}

@MainActor
final class DiscoveryStore: ObservableObject {
    @Published var state: DiscoveryState?
    @Published var connectionError: String?
    @Published var actionError: String?
    @Published var isWorking = false
    @Published var isRefreshing = false
    let client = APIClient()

    func refresh() async {
        guard !isRefreshing else { return }
        isRefreshing = true
        defer { isRefreshing = false }
        do {
            state = try client.decode(DiscoveryState.self, from: await client.request("/api/state"))
            connectionError = nil
        } catch is DecodingError {
            connectionError = "The local service responded, but its data format does not match this app. Restart matching versions of the app and backend."
        } catch {
            connectionError = "Le service local de Hoomy est indisponible. Relance-le pour continuer. Tes derniers résultats restent affichés."
        }
    }

    @discardableResult
    func mutate(_ path: String, body: [String: Any]) async -> Bool {
        guard !isWorking else { return false }
        isWorking = true
        actionError = nil
        defer { isWorking = false }
        do {
            _ = try await client.request(path, body: body)
            await refresh()
            return true
        } catch {
            actionError = error.localizedDescription
            return false
        }
    }

    func poll() async {
        while !Task.isCancelled {
            await refresh()
            do { try await Task.sleep(for: .seconds(state?.overview?.completionPhase == "agent" && state?.control.running == true ? 1 : 3)) }
            catch { return }
        }
    }
}
