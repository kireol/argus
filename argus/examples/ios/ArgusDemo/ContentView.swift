import SwiftUI

/// Colours from the shared "Argus Demo" spec (see `examples/README.md`).
/// Declared in sRGB so a screenshot pixel reads back as the exact value the
/// `pixel_matches` conditions in `tests/demo.yaml` expect.
enum DemoColor {
    static let lightBackground = Color(.sRGB, red: 1, green: 1, blue: 1, opacity: 1)
    static let darkBackground = Color(.sRGB, red: 30 / 255, green: 30 / 255, blue: 46 / 255, opacity: 1)
    static let lightSwatch = Color(.sRGB, red: 46 / 255, green: 204 / 255, blue: 113 / 255, opacity: 1)
    static let darkSwatch = Color(.sRGB, red: 142 / 255, green: 68 / 255, blue: 173 / 255, opacity: 1)
    static let lightControl = Color(.sRGB, red: 226 / 255, green: 226 / 255, blue: 226 / 255, opacity: 1)
    static let darkControl = Color(.sRGB, red: 58 / 255, green: 58 / 255, blue: 82 / 255, opacity: 1)
}

/// Every control's centre, in points, on a 393 x 852 pt screen (iPhone 15).
///
/// `tests/demo.yaml` taps *screenshot pixels*, which on a 3x device are these
/// numbers multiplied by 3 — the README carries the conversion table.
enum Layout {
    static let swatch = CGPoint(x: 300, y: 120)
    static let swatchSize = CGSize(width: 120, height: 80)

    static let title = CGPoint(x: 196, y: 220)
    static let count = CGPoint(x: 196, y: 320)
    static let plus = CGPoint(x: 196, y: 420)
    static let settings = CGPoint(x: 196, y: 520)

    static let themeLabel = CGPoint(x: 196, y: 262)
    static let themeToggle = CGPoint(x: 196, y: 300)
    static let back = CGPoint(x: 196, y: 520)
}

/// The whole UI, laid out at fixed points.
///
/// Two deliberate choices, both in service of the tests:
///
/// * Controls are placed with `.position` on a safe-area-ignoring `ZStack`, so
///   a control's documented coordinate is also its coordinate in a screenshot.
/// * There is no `NavigationStack`. A navigation bar would push every control
///   down by its own height (and its height differs between screens and iOS
///   versions), which would invalidate every coordinate in `tests/demo.yaml`.
///   Screens are swapped on `AppModel.screen` instead, and the settings screen
///   carries its own `Back` control.
struct ContentView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        ZStack {
            background
            swatch
            switch model.screen {
            case .home:
                home
            case .settings:
                settings
            }
        }
        .ignoresSafeArea()
        // Pin the type size: Dynamic Type would move every control.
        .dynamicTypeSize(.large)
        .preferredColorScheme(model.theme == .dark ? .dark : .light)
    }

    // MARK: - Shared chrome

    private var background: some View {
        (model.theme == .dark ? DemoColor.darkBackground : DemoColor.lightBackground)
            .ignoresSafeArea()
    }

    /// The theme swatch: green in light theme, purple in dark theme. It is on
    /// both screens so `pixel_matches` can assert the theme from wherever the
    /// test happens to be.
    private var swatch: some View {
        Rectangle()
            .fill(model.theme == .dark ? DemoColor.darkSwatch : DemoColor.lightSwatch)
            .frame(width: Layout.swatchSize.width, height: Layout.swatchSize.height)
            .position(Layout.swatch)
    }

    // MARK: - Home

    private var home: some View {
        ZStack {
            Text("Argus Demo")
                .font(.system(size: 40, weight: .bold))
                .foregroundStyle(foreground)
                .position(Layout.title)

            Text("Count: \(model.counter)")
                .font(.system(size: 34, weight: .regular))
                .foregroundStyle(foreground)
                .position(Layout.count)

            control("+", width: 120, height: 80, fontSize: 44) {
                model.increment()
            }
            .position(Layout.plus)

            control("Settings", width: 220, height: 64, fontSize: 28) {
                model.show(.settings)
            }
            .position(Layout.settings)
        }
    }

    // MARK: - Settings

    private var settings: some View {
        ZStack {
            Text("Settings")
                .font(.system(size: 40, weight: .bold))
                .foregroundStyle(foreground)
                .position(Layout.title)

            Text("Dark theme")
                .font(.system(size: 26, weight: .regular))
                .foregroundStyle(foreground)
                .position(Layout.themeLabel)

            // The label is drawn separately so that the switch itself — the
            // only reliably tappable part of a Toggle — sits exactly on the
            // coordinate the tests tap.
            Toggle("Dark theme", isOn: model.darkThemeBinding)
                .labelsHidden()
                .position(Layout.themeToggle)

            control("Back", width: 220, height: 64, fontSize: 28) {
                model.show(.home)
            }
            .position(Layout.back)
        }
    }

    // MARK: - Building blocks

    private var foreground: Color {
        model.theme == .dark ? .white : .black
    }

    /// A fixed-size button. `.buttonStyle(.plain)` keeps the hit area equal to
    /// the frame, so the documented centre point really is what gets tapped.
    private func control(
        _ label: String,
        width: CGFloat,
        height: CGFloat,
        fontSize: CGFloat,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Text(label)
                .font(.system(size: fontSize, weight: .semibold))
                .foregroundStyle(foreground)
                .frame(width: width, height: height)
                .background(
                    RoundedRectangle(cornerRadius: 12)
                        .fill(model.theme == .dark ? DemoColor.darkControl : DemoColor.lightControl)
                )
        }
        .buttonStyle(.plain)
    }
}
