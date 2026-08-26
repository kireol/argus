#if DEBUG
import Foundation
import Network
import os

/// A thread-safe snapshot of the app state.
///
/// `AppModel` writes it on the main thread; the instrumentation server reads
/// it on its own queue, so the two are separated by a lock rather than by a
/// direct reference to the model.
final class InstrumentationState: @unchecked Sendable {
    static let shared = InstrumentationState()

    private let lock = NSLock()
    private var counter = 0
    private var theme = Theme.light.rawValue
    private var screen = Screen.home.rawValue

    func update(counter: Int, theme: String, screen: String) {
        lock.lock()
        defer { lock.unlock() }
        self.counter = counter
        self.theme = theme
        self.screen = screen
    }

    /// `GET /test/status` — the standard Argus status document.
    var status: [String: Any] {
        lock.lock()
        defer { lock.unlock() }
        return [
            "application": "ArgusDemo",
            "version": "1.0.0",
            "ready": true,
            "screen": screen,
            "capabilities": ["status", "state"],
        ]
    }

    /// `GET /test/state` — free-form application state.
    var state: [String: Any] {
        lock.lock()
        defer { lock.unlock() }
        return ["counter": counter, "theme": theme, "screen": screen]
    }
}

/// A ~100-line HTTP server serving the three Argus instrumentation endpoints
/// on port 8085.
///
/// The iOS Simulator shares the Mac's network stack, so a listener bound here
/// is reachable from the host at `http://127.0.0.1:8085` with no forwarding.
/// On a physical device use the device's Wi-Fi address instead (see the
/// README).
///
/// Debug builds only — the whole file is inside `#if DEBUG`.
///
/// `@unchecked Sendable`: `listener` is written once, from `start()` on the
/// main thread; everything else runs on `queue`.
final class InstrumentationServer: @unchecked Sendable {
    static let shared = InstrumentationServer(port: 8085)

    private let port: NWEndpoint.Port
    private let queue = DispatchQueue(label: "com.argus.demo.instrumentation")
    private let logger = Logger(subsystem: "com.argus.demo", category: "app")
    private var listener: NWListener?

    init(port: UInt16) {
        self.port = NWEndpoint.Port(rawValue: port) ?? .any
    }

    func start() {
        guard listener == nil else { return }
        let parameters = NWParameters.tcp
        parameters.allowLocalEndpointReuse = true
        guard let listener = try? NWListener(using: parameters, on: port) else {
            logger.error("Instrumentation could not bind port \(self.port.rawValue, privacy: .public)")
            return
        }
        listener.stateUpdateHandler = { [weak self] state in
            guard let self else { return }
            switch state {
            case .ready:
                self.logger.info("Instrumentation on port \(self.port.rawValue, privacy: .public)")
            case .failed(let error):
                self.logger.error("Instrumentation failed: \(error.localizedDescription, privacy: .public)")
            default:
                break
            }
        }
        listener.newConnectionHandler = { [weak self] connection in
            guard let self else {
                connection.cancel()
                return
            }
            connection.start(queue: self.queue)
            self.receive(connection, buffer: Data())
        }
        listener.start(queue: queue)
        self.listener = listener
    }

    // MARK: - One request per connection

    private func receive(_ connection: NWConnection, buffer: Data) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 4096) {
            [weak self] data, _, isComplete, error in
            guard let self else {
                connection.cancel()
                return
            }
            var buffer = buffer
            if let data {
                buffer.append(data)
            }
            if let range = buffer.range(of: Data("\r\n\r\n".utf8)) {
                let head = String(decoding: buffer[..<range.lowerBound], as: UTF8.self)
                self.respond(to: head, on: connection)
                return
            }
            if isComplete || error != nil {
                connection.cancel()
                return
            }
            self.receive(connection, buffer: buffer)
        }
    }

    private func respond(to head: String, on connection: NWConnection) {
        let requestLine = head.components(separatedBy: "\r\n").first ?? ""
        let fields = requestLine.split(separator: " ")
        let target = fields.count > 1 ? String(fields[1]) : "/"
        let path = target.components(separatedBy: "?").first ?? target

        let status: Int
        let body: [String: Any]
        switch path {
        case "/test/status":
            status = 200
            body = InstrumentationState.shared.status
        case "/test/state":
            status = 200
            body = InstrumentationState.shared.state
        case "/test/health":
            status = 200
            body = ["ok": true]
        default:
            status = 404
            body = ["error": "not found"]
        }

        let json = (try? JSONSerialization.data(withJSONObject: body)) ?? Data("{}".utf8)
        connection.send(
            content: Self.httpResponse(status: status, body: json),
            completion: .contentProcessed { _ in connection.cancel() }
        )
    }

    private static func httpResponse(status: Int, body: Data) -> Data {
        var headers = "HTTP/1.1 \(status) \(status == 200 ? "OK" : "Not Found")\r\n"
        headers += "Content-Type: application/json\r\n"
        headers += "Content-Length: \(body.count)\r\n"
        headers += "Connection: close\r\n\r\n"
        return Data(headers.utf8) + body
    }
}
#endif
