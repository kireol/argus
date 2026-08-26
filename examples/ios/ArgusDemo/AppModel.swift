import Foundation
import SwiftUI
import os

/// The screen the demo is showing. Raw values match the strings Argus asserts
/// on (`Screen: home`, `application_state screen == settings`).
enum Screen: String {
    case home
    case settings
}

/// The two themes the demo can render.
enum Theme: String {
    case light
    case dark
}

/// The entire application state: a counter, a theme and the current screen.
///
/// Every mutation does three things so that Argus can observe it three ways:
/// it updates the published state (pixels/OCR), writes one line to the log
/// (`log_contains`) and refreshes the snapshot the instrumentation server
/// serves (`instrumentation_value` / `application_state`).
///
/// Deliberately not `@MainActor`: SwiftUI only ever mutates it from the
/// main thread, and staying isolation-free keeps the example compiling in
/// both the Swift 5 and Swift 6 language modes.
final class AppModel: ObservableObject {
    @Published private(set) var counter: Int = 0
    @Published private(set) var theme: Theme = .light
    @Published private(set) var screen: Screen = .home

    private let logger = Logger(subsystem: "com.argus.demo", category: "app")

    /// Called once when the UI appears.
    func start() {
        publishState()
        log("App ready")
    }

    func increment() {
        counter += 1
        publishState()
        log("Counter: \(counter)")
    }

    func show(_ screen: Screen) {
        guard screen != self.screen else { return }
        self.screen = screen
        publishState()
        log("Screen: \(screen.rawValue)")
    }

    func setDarkTheme(_ enabled: Bool) {
        let theme: Theme = enabled ? .dark : .light
        guard theme != self.theme else { return }
        self.theme = theme
        publishState()
        log("Theme: \(theme.rawValue)")
    }

    /// A binding for the `Dark theme` toggle.
    var darkThemeBinding: Binding<Bool> {
        Binding(get: { self.theme == .dark }, set: { self.setDarkTheme($0) })
    }

    // MARK: - Observability

    /// One line per action, on both stdout (Xcode console) and the unified log
    /// (`xcrun simctl spawn booted log stream --predicate 'process == "ArgusDemo"'`).
    ///
    /// `privacy: .public` matters: without it the unified log redacts every
    /// interpolated value to `<private>` and `log_contains "Counter: 3"` never
    /// matches.
    private func log(_ line: String) {
        print(line)
        logger.info("\(line, privacy: .public)")
    }

    private func publishState() {
        #if DEBUG
        InstrumentationState.shared.update(
            counter: counter,
            theme: theme.rawValue,
            screen: screen.rawValue
        )
        #endif
    }
}
