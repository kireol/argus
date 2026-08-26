// Argus Demo firmware for a 128x64 SSD1306 OLED (I2C).
//
// Home screen:   "ARGUS" (text size 2, top-left) + "Count: N" (size 1, y=30).
// Settings:      "Settings" + a "Dark theme" toggle + "Back".
// Theme swatch:  filled rect at (100,50) 24x10 (light theme) / hollow outline
//                (dark theme), drawn on every screen so pixel (112,55) reads
//                white in light theme and black in dark theme regardless of
//                which screen is on display.
//
// Keys (delivered by the Argus agent's "input" command -> onKey callback):
//   BTN_OK    - home: increment the counter / settings: toggle the theme
//   BTN_RIGHT - home: open Settings
//   BTN_BACK  - settings: return to Home (counter and theme are preserved)
//
// This mirrors the wiring of agents/esp32/examples/ssd1306_menu/src/main.cpp
// (same I2C pins, same Adafruit SSD1306/GFX libraries, same ArgusAgent.h
// usage) but implements the shared "Argus Demo" app instead of the menu demo.
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Wire.h>

#include "ArgusAgent.h"

enum Screen { SCREEN_HOME, SCREEN_SETTINGS };

static Adafruit_SSD1306 display(128, 64, &Wire, -1);
static ArgusAgent argus;
static Screen screen = SCREEN_HOME;
static int counter = 0;
static bool darkTheme = false;

static void render() {
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);

  if (screen == SCREEN_HOME) {
    display.setTextSize(2);
    display.setCursor(0, 0);
    display.print("ARGUS");
    display.setTextSize(1);
    display.setCursor(0, 30);
    display.print("Count: ");
    display.print(counter);
  } else {
    display.setTextSize(1);
    display.setCursor(0, 0);
    display.print("Settings");
    display.setCursor(0, 16);
    display.print("Dark theme: ");
    display.print(darkTheme ? "ON" : "OFF");
    display.setCursor(0, 32);
    display.print("Back");
  }

  // Theme swatch, drawn on every screen so it can be checked without
  // navigating back to Home first: filled (light) vs. hollow (dark).
  // Pixel (112,55) sits in the middle of this rect.
  if (darkTheme) {
    display.drawRect(100, 50, 24, 10, SSD1306_WHITE);
  } else {
    display.fillRect(100, 50, 24, 10, SSD1306_WHITE);
  }

  display.display();

  const char* screenName = screen == SCREEN_HOME ? "home" : "settings";
  argus.setStatus("screen", screenName);
  argus.setState("screen", screenName);
  argus.setState("counter", counter);
  argus.setState("theme", darkTheme ? "dark" : "light");
}

static void onKey(const char* key) {
  if (strcmp(key, "BTN_OK") == 0) {
    if (screen == SCREEN_HOME) {
      counter++;
      Serial.printf("Counter: %d\n", counter);
    } else {
      darkTheme = !darkTheme;
      Serial.println(darkTheme ? "Theme: dark" : "Theme: light");
    }
  } else if (strcmp(key, "BTN_RIGHT") == 0) {
    if (screen == SCREEN_HOME) {
      screen = SCREEN_SETTINGS;
      Serial.println("Screen: settings");
    }
  } else if (strcmp(key, "BTN_BACK") == 0) {
    if (screen == SCREEN_SETTINGS) {
      screen = SCREEN_HOME;
      Serial.println("Screen: home");
    }
  }
  render();
}

void setup() {
  Serial.begin(115200);
  Wire.begin(21, 22);
  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println("SSD1306 init failed");
  }

  argus.begin(Serial, display.getBuffer(), 128, 64, ARGUS_MONO_VLSB, "esp32_demo", "1.0.0");
  argus.onKey(onKey);

  // Status/state must be set before the first poll() so the agent's "hello"
  // advertises the status/state capabilities (see docs/esp32.md).
  argus.setStatus("application", "ArgusDemo");
  argus.setStatus("version", "1.0.0");
  argus.setStatus("ready", true);
  render();  // also sets status.screen / state.{screen,counter,theme}

  Serial.println("Screen: home");
  Serial.println("App ready");
}

void loop() {
  argus.poll();
  delay(2);
}
