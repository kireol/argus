//
//  DemoModel.swift — Argus Demo (tvOS)
//
//  The whole application state: a counter, a theme and the current screen.
//  Every state change emits one exact log line (see README) and refreshes the
//  snapshot the instrumentation server serves from its own queue.
//

import Foundation
import SwiftUI
import os

enum DemoScreen: String {
    case home
    case settings
}

/// Immutable view of the demo state, safe to read from any thread.
///
/// The instrumentation listener answers on a background queue, so it never
/// touches `DemoModel` (which is main-actor bound) directly.
struct DemoSnapshot: Sendable {
    var counter: Int = 0
    var theme: String = "light"
    var screen: String = "home"
}

/// Lock-protected box holding the latest `DemoSnapshot`.
final class DemoSnapshotBox: @unchecked Sendable {
    static let shared = DemoSnapshotBox()

    private let lock = NSLock()
    private var value = DemoSnapshot()

    var snapshot: DemoSnapshot {
        get {
            lock.lock()
            defer { lock.unlock() }
            return value
        }
        set {
            lock.lock()
            value = newValue
            lock.unlock()
        }
    }
}

/// The demo's log lines.
///
/// `Logger.notice` is the *default* log level, which `log stream` shows
/// without `--level info` — that matters because the Argus tvOS simulator
/// adapter streams with `log stream --style compact --predicate
/// 'process == "ArgusDemoTV"'`. The same line also goes to stdout so it shows
/// up when the app is launched with `simctl launch --console`.
enum DemoLog {
    private static let logger = Logger(subsystem: "com.argus.demo.tv", category: "demo")

    static func emit(_ line: String) {
        logger.notice("\(line, privacy: .public)")
        print(line)
        fflush(stdout)
    }
}

@MainActor
final class DemoModel: ObservableObject {
    @Published private(set) var counter: Int = 0
    @Published private(set) var screen: DemoScreen = .home

    /// Bound to the `Dark theme` toggle on the settings screen.
    @Published var darkTheme: Bool = false {
        didSet {
            guard darkTheme != oldValue else { return }
            publish()
            DemoLog.emit("Theme: \(darkTheme ? "dark" : "light")")
        }
    }

    func start() {
        publish()
        DemoLog.emit("App ready")
        DemoLog.emit("Screen: \(screen.rawValue)")
    }

    func increment() {
        counter += 1
        publish()
        DemoLog.emit("Counter: \(counter)")
    }

    func show(_ next: DemoScreen) {
        guard screen != next else { return }
        screen = next
        publish()
        DemoLog.emit("Screen: \(next.rawValue)")
    }

    private func publish() {
        DemoSnapshotBox.shared.snapshot = DemoSnapshot(
            counter: counter,
            theme: darkTheme ? "dark" : "light",
            screen: screen.rawValue
        )
    }
}

/// The exact colours the pixel assertions look for (see the README).
enum DemoPalette {
    /// `#ffffff`
    static let lightBackground = Color(.sRGB, red: 1.0, green: 1.0, blue: 1.0, opacity: 1.0)
    /// `#1e1e2e`
    static let darkBackground = Color(
        .sRGB, red: 30.0 / 255.0, green: 30.0 / 255.0, blue: 46.0 / 255.0, opacity: 1.0
    )
    /// `#2ecc71`
    static let lightSwatch = Color(
        .sRGB, red: 46.0 / 255.0, green: 204.0 / 255.0, blue: 113.0 / 255.0, opacity: 1.0
    )
    /// `#8e44ad`
    static let darkSwatch = Color(
        .sRGB, red: 142.0 / 255.0, green: 68.0 / 255.0, blue: 173.0 / 255.0, opacity: 1.0
    )
    static let lightText = Color(.sRGB, red: 0.0, green: 0.0, blue: 0.0, opacity: 1.0)
    static let darkText = Color(.sRGB, red: 1.0, green: 1.0, blue: 1.0, opacity: 1.0)
}
