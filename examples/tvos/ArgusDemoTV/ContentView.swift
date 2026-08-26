//
//  ContentView.swift — Argus Demo (tvOS)
//
//  tvOS has no pointer, so the whole UI is driven by the focus engine:
//
//    Home      [ + ]  →(DPAD_RIGHT)→  [ Settings ]      default focus: +
//    Settings  [ Dark theme ]                            default focus: toggle
//              [ Back ]              ←(DPAD_DOWN)
//              MENU also returns home (onExitCommand)
//
//  The colour swatch is drawn in absolute screen coordinates —
//  x 1500…1700, y 100…200 on a 1920×1080 (1x) tvOS simulator — so
//  `pixel_matches` can assert the theme without OCR.
//

import SwiftUI

private enum FocusTarget: Hashable {
    case plus
    case settings
    case darkTheme
    case back
}

struct ContentView: View {
    @StateObject private var model = DemoModel()
    @FocusState private var focus: FocusTarget?

    private var background: Color {
        model.darkTheme ? DemoPalette.darkBackground : DemoPalette.lightBackground
    }

    private var swatch: Color {
        model.darkTheme ? DemoPalette.darkSwatch : DemoPalette.lightSwatch
    }

    private var text: Color {
        model.darkTheme ? DemoPalette.darkText : DemoPalette.lightText
    }

    var body: some View {
        ZStack(alignment: .topLeading) {
            background

            Group {
                if model.screen == .home {
                    homeScreen
                } else {
                    settingsScreen
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .center)

            // Absolute-position swatch: 200×100 with its top-left at (1500, 100).
            Rectangle()
                .fill(swatch)
                .frame(width: 200, height: 100)
                .offset(x: 1500, y: 100)
        }
        .ignoresSafeArea()
        .onAppear { model.start() }
    }

    // MARK: - Home

    private var homeScreen: some View {
        VStack(spacing: 60) {
            Text("Argus Demo")
                .font(.system(size: 96, weight: .bold, design: .default))
                .foregroundColor(text)

            Text("Count: \(model.counter)")
                .font(.system(size: 72, weight: .semibold, design: .default))
                .foregroundColor(text)

            HStack(spacing: 80) {
                Button("+") { model.increment() }
                    .focused($focus, equals: .plus)

                Button("Settings") { model.show(.settings) }
                    .focused($focus, equals: .settings)
            }
            .font(.system(size: 48, weight: .semibold, design: .default))
        }
        .defaultFocus($focus, .plus)
    }

    // MARK: - Settings

    private var settingsScreen: some View {
        VStack(spacing: 60) {
            Text("Settings")
                .font(.system(size: 96, weight: .bold, design: .default))
                .foregroundColor(text)

            Toggle("Dark theme", isOn: $model.darkTheme)
                .focused($focus, equals: .darkTheme)
                .frame(width: 900)

            Button("Back") { model.show(.home) }
                .focused($focus, equals: .back)
        }
        .font(.system(size: 48, weight: .semibold, design: .default))
        .defaultFocus($focus, .darkTheme)
        // The Apple TV remote's MENU button (Escape in the Simulator).
        .onExitCommand { model.show(.home) }
    }
}
