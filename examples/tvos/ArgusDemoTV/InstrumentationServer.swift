//
//  InstrumentationServer.swift — Argus Demo (tvOS)
//
//  A ~100 line HTTP listener implementing the Argus instrumentation protocol
//  (docs/instrumentation.md):
//
//      GET /test/status  -> {"application":"ArgusDemo","version":"1.0.0",
//                            "ready":true,"screen":"home"|"settings",
//                            "capabilities":["status","state"]}
//      GET /test/state   -> {"counter":N,"theme":"light"|"dark",
//                            "screen":"home"|"settings"}
//      GET /test/health  -> 200 {"ok":true}
//
//  DEBUG builds only — instrumentation must never ship in a release build.
//  The simulator shares the Mac's network stack, so the host reaches this at
//  http://127.0.0.1:8085.
//

#if DEBUG

import Foundation
import Network

final class InstrumentationServer {
    static let shared = InstrumentationServer(port: 8085)

    private let port: NWEndpoint.Port
    private let queue = DispatchQueue(label: "com.argus.demo.tv.instrumentation")
    private var listener: NWListener?

    init(port: UInt16) {
        self.port = NWEndpoint.Port(rawValue: port) ?? .any
    }

    func start() {
        guard listener == nil else { return }
        let parameters = NWParameters.tcp
        parameters.allowLocalEndpointReuse = true
        do {
            let listener = try NWListener(using: parameters, on: port)
            listener.newConnectionHandler = { [weak self] connection in
                self?.accept(connection)
            }
            listener.start(queue: queue)
            self.listener = listener
            DemoLog.emit("Instrumentation: listening on \(port.rawValue)")
        } catch {
            DemoLog.emit("Instrumentation: failed to listen on \(port.rawValue): \(error)")
        }
    }

    // MARK: - Connection handling

    private func accept(_ connection: NWConnection) {
        connection.start(queue: queue)
        connection.receive(minimumIncompleteLength: 1, maximumLength: 16 * 1024) {
            [weak self] data, _, _, _ in
            guard let self else {
                connection.cancel()
                return
            }
            let request = data.flatMap { String(data: $0, encoding: .utf8) } ?? ""
            let payload = self.response(forPath: Self.requestPath(request))
            connection.send(
                content: payload,
                completion: .contentProcessed { _ in connection.cancel() }
            )
        }
    }

    /// "GET /test/state?x=1 HTTP/1.1" -> "/test/state"
    private static func requestPath(_ request: String) -> String {
        guard let line = request.split(separator: "\r\n", maxSplits: 1).first else { return "" }
        let fields = line.split(separator: " ")
        guard fields.count >= 2 else { return "" }
        return String(fields[1].split(separator: "?", maxSplits: 1)[0])
    }

    // MARK: - Documents

    private func response(forPath path: String) -> Data {
        let state = DemoSnapshotBox.shared.snapshot
        switch path {
        case "/test/status":
            return Self.ok([
                "application": "ArgusDemo",
                "version": "1.0.0",
                "ready": true,
                "screen": state.screen,
                "capabilities": ["status", "state"],
            ])
        case "/test/state":
            return Self.ok([
                "counter": state.counter,
                "theme": state.theme,
                "screen": state.screen,
            ])
        case "/test/health":
            return Self.ok(["ok": true])
        default:
            return Self.http(status: "404 Not Found", body: Data(#"{"error":"not found"}"#.utf8))
        }
    }

    private static func ok(_ document: [String: Any]) -> Data {
        let body =
            (try? JSONSerialization.data(withJSONObject: document, options: [.sortedKeys]))
            ?? Data("{}".utf8)
        return http(status: "200 OK", body: body)
    }

    private static func http(status: String, body: Data) -> Data {
        var header = "HTTP/1.1 \(status)\r\n"
        header += "Content-Type: application/json\r\n"
        header += "Content-Length: \(body.count)\r\n"
        header += "Connection: close\r\n"
        header += "\r\n"
        return Data(header.utf8) + body
    }
}

#endif
