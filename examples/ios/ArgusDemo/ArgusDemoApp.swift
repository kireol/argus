import SwiftUI

/// Argus Demo — the smallest SwiftUI app that is worth writing UI tests for.
///
/// See `examples/ios/README.md` for how to build it, install it on a
/// simulator, start WebDriverAgent and run `examples/ios/tests/demo.yaml`
/// against it.
@main
struct ArgusDemoApp: App {
    @StateObject private var model = AppModel()

    init() {
        #if DEBUG
        // Started before the first frame so the endpoints are already
        // answering by the time the UI is on screen — tests can query
        // instrumentation as soon as they can see the app.
        InstrumentationServer.shared.start()
        #endif
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
                .onAppear { model.start() }
        }
    }
}
