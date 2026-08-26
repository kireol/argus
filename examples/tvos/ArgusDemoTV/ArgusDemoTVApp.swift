//
//  ArgusDemoTVApp.swift — Argus Demo (tvOS)
//
//  SwiftUI lifecycle. The instrumentation listener starts before the first
//  frame so `wait_until instrumentation_value ready == true` can be the very
//  first thing a test does after `device.reset`.
//

import SwiftUI

@main
struct ArgusDemoTVApp: App {
    init() {
        #if DEBUG
        InstrumentationServer.shared.start()
        #endif
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}
