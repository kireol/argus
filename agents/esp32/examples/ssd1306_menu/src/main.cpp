// Two-item menu on a 128x64 SSD1306, driven by Argus keys (BTN_UP/BTN_DOWN/BTN_OK).
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Wire.h>

#include "ArgusAgent.h"

static Adafruit_SSD1306 display(128, 64, &Wire, -1);
static ArgusAgent argus;
static const char* kItems[] = {"Play", "Settings"};
static int selected = 0;

static void render() {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.print("ARGUS DEMO");
  for (int i = 0; i < 2; ++i) {
    display.setCursor(8, 20 + i * 14);
    display.print(i == selected ? "> " : "  ");
    display.print(kItems[i]);
  }
  // Solid marker block in the bottom-right corner: rows 56-63, columns 120-127 (pixel probe).
  display.fillRect(120, 56, 8, 8, SSD1306_WHITE);
  display.display();
  argus.setStatus("screen", "menu");
  argus.setState("selected", selected);
  Serial.printf("menu: selected=%s\n", kItems[selected]);
}

static void onKey(const char* key) {
  if (strcmp(key, "BTN_DOWN") == 0 && selected < 1) selected++;
  else if (strcmp(key, "BTN_UP") == 0 && selected > 0) selected--;
  else if (strcmp(key, "BTN_OK") == 0) Serial.printf("menu: activated=%s\n", kItems[selected]);
  render();
}

void setup() {
  Serial.begin(115200);
  Wire.begin(21, 22);
  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println("SSD1306 init failed");
  }
  argus.begin(Serial, display.getBuffer(), 128, 64, ARGUS_MONO_VLSB, "ssd1306_menu", "1.0");
  argus.onKey(onKey);
  render();
  Serial.println("ready");
}

void loop() {
  argus.poll();
  delay(2);
}
