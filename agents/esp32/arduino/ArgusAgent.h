// Argus test agent for Arduino / PlatformIO (ESP32).
//
//   #include "ArgusAgent.h"
//   Adafruit_SSD1306 display(128, 64, &Wire);
//   ArgusAgent argus;
//   void onKey(const char* key) { /* treat like a button */ }
//   void setup() {
//     Serial.begin(115200);
//     argus.begin(Serial, display.getBuffer(), 128, 64, ARGUS_MONO_VLSB, "menu", "1.0");
//     argus.onKey(onKey);
//   }
//   void loop() { argus.poll(); }
//
// Protocol (shared with argus.adapters.esp32): lines prefixed with ESC[ARGUS]
// are commands; replies are "<cmd> ok <len>\n" + raw bytes + "\n" or "<cmd> err <msg>\n".
#pragma once

#include <Arduino.h>
#include <string.h>

enum ArgusFormat {
  ARGUS_MONO_HLSB,
  ARGUS_MONO_HMSB,
  ARGUS_MONO_VLSB,
  ARGUS_GS8,
  ARGUS_RGB565,
  ARGUS_RGB565_BE,
  ARGUS_RGB888,
  ARGUS_NO_FRAMEBUFFER
};

class ArgusAgent {
 public:
  typedef void (*KeyHandler)(const char* key);
  typedef void (*LineHandler)(const char* line);

  static const size_t kMaxLine = 128;
  static const size_t kMaxEntries = 16;
  static const size_t kValueLen = 32;

  void begin(Stream& stream, const uint8_t* framebuffer, uint16_t width, uint16_t height,
             ArgusFormat format, const char* name = "app", const char* version = "1") {
    stream_ = &stream;
    fb_ = framebuffer;
    width_ = width;
    height_ = height;
    format_ = framebuffer ? format : ARGUS_NO_FRAMEBUFFER;
    name_ = name;
    version_ = version;
    lineLen_ = 0;
  }

  void begin(Stream& stream, const char* name = "app", const char* version = "1") {
    begin(stream, nullptr, 0, 0, ARGUS_NO_FRAMEBUFFER, name, version);
  }

  void onKey(KeyHandler handler) { keyHandler_ = handler; }
  void onSerialLine(LineHandler handler) { lineHandler_ = handler; }

  void setStatus(const char* key, const char* value) { set(status_, statusCount_, key, value, true); }
  void setStatus(const char* key, int value) { setNumber(status_, statusCount_, key, value); }
  void setStatus(const char* key, bool value) { set(status_, statusCount_, key, value ? "true" : "false", false); }
  void setState(const char* key, const char* value) { set(state_, stateCount_, key, value, true); }
  void setState(const char* key, int value) { setNumber(state_, stateCount_, key, value); }
  void setState(const char* key, bool value) { set(state_, stateCount_, key, value ? "true" : "false", false); }

  // Call from loop(); non-blocking.
  void poll() {
    if (!stream_) return;
    while (stream_->available() > 0) {
      int c = stream_->read();
      if (c < 0) break;
      if (c == '\n') {
        line_[lineLen_] = '\0';
        handleLine(line_);
        lineLen_ = 0;
      } else if (lineLen_ < kMaxLine - 1) {
        line_[lineLen_++] = (char)c;
      } else {
        lineLen_ = 0;  // overlong line: discard
      }
    }
  }

 private:
  struct Entry {
    char key[kValueLen];
    char value[kValueLen];
    bool quoted;
  };

  static const char* prefix() { return "\x1b[ARGUS] "; }

  void set(Entry* table, size_t& count, const char* key, const char* value, bool quoted) {
    for (size_t i = 0; i < count; ++i) {
      if (strcmp(table[i].key, key) == 0) {
        strncpy(table[i].value, value, kValueLen - 1);
        table[i].value[kValueLen - 1] = '\0';
        table[i].quoted = quoted;
        return;
      }
    }
    if (count >= kMaxEntries) return;
    strncpy(table[count].key, key, kValueLen - 1);
    table[count].key[kValueLen - 1] = '\0';
    strncpy(table[count].value, value, kValueLen - 1);
    table[count].value[kValueLen - 1] = '\0';
    table[count].quoted = quoted;
    ++count;
  }

  void setNumber(Entry* table, size_t& count, const char* key, int value) {
    char buf[16];
    snprintf(buf, sizeof(buf), "%d", value);
    set(table, count, key, buf, false);
  }

  size_t framebufferLength() const {
    size_t rowBytes = (width_ + 7) / 8;
    size_t pages = (height_ + 7) / 8;
    switch (format_) {
      case ARGUS_MONO_HLSB:
      case ARGUS_MONO_HMSB: return rowBytes * height_;
      case ARGUS_MONO_VLSB: return (size_t)width_ * pages;
      case ARGUS_GS8: return (size_t)width_ * height_;
      case ARGUS_RGB565:
      case ARGUS_RGB565_BE: return (size_t)width_ * height_ * 2;
      case ARGUS_RGB888: return (size_t)width_ * height_ * 3;
      default: return 0;
    }
  }

  static const char* formatName(ArgusFormat f) {
    switch (f) {
      case ARGUS_MONO_HLSB: return "MONO_HLSB";
      case ARGUS_MONO_HMSB: return "MONO_HMSB";
      case ARGUS_MONO_VLSB: return "MONO_VLSB";
      case ARGUS_GS8: return "GS8";
      case ARGUS_RGB565: return "RGB565";
      case ARGUS_RGB565_BE: return "RGB565_BE";
      case ARGUS_RGB888: return "RGB888";
      default: return "none";
    }
  }

  void ok(const char* cmd, const uint8_t* payload, size_t len) {
    stream_->print(prefix());
    stream_->print(cmd);
    stream_->print(" ok ");
    stream_->print((unsigned long)len);
    stream_->print('\n');
    if (len) stream_->write(payload, len);
    stream_->print('\n');
    stream_->flush();
  }

  void err(const char* cmd, const char* message) {
    stream_->print(prefix());
    stream_->print(cmd);
    stream_->print(" err ");
    stream_->print(message);
    stream_->print('\n');
    stream_->flush();
  }

  void sendJson(const char* cmd, const Entry* table, size_t count) {
    // Built in a small buffer: 16 entries * (32+32+6) fits in 1200 bytes.
    static char json[kMaxEntries * (2 * kValueLen + 6) + 2];
    size_t pos = 0;
    json[pos++] = '{';
    for (size_t i = 0; i < count; ++i) {
      if (i) json[pos++] = ',';
      pos += snprintf(json + pos, sizeof(json) - pos, "\"%s\":", table[i].key);
      if (table[i].quoted) {
        pos += snprintf(json + pos, sizeof(json) - pos, "\"%s\"", table[i].value);
      } else {
        pos += snprintf(json + pos, sizeof(json) - pos, "%s", table[i].value);
      }
    }
    json[pos++] = '}';
    ok(cmd, (const uint8_t*)json, pos);
  }

  void handleLine(char* line) {
    size_t plen = strlen(prefix());
    if (strncmp(line, prefix(), plen) != 0) {
      if (lineHandler_) lineHandler_(line);
      return;
    }
    char* body = line + plen;
    char* arg = strchr(body, ' ');
    if (arg) { *arg = '\0'; ++arg; } else { arg = body + strlen(body); }
    if (strcmp(body, "hello") == 0) {
      char hello[160];
      if (format_ == ARGUS_NO_FRAMEBUFFER) {
        snprintf(hello, sizeof(hello), "name=%s version=%s fb=none caps=%s%s%s%s",
                 name_, version_, "", keyHandler_ ? "input," : "",
                 statusCount_ ? "status," : "", stateCount_ ? "state," : "");
      } else {
        snprintf(hello, sizeof(hello), "name=%s version=%s fb=%s,%u,%u caps=screen,%s%s%s",
                 name_, version_, formatName(format_), (unsigned)width_, (unsigned)height_,
                 keyHandler_ ? "input," : "", statusCount_ ? "status," : "",
                 stateCount_ ? "state," : "");
      }
      size_t n = strlen(hello);
      if (n && hello[n - 1] == ',') hello[n - 1] = '\0';
      ok("hello", (const uint8_t*)hello, strlen(hello));
    } else if (strcmp(body, "screenshot") == 0) {
      if (format_ == ARGUS_NO_FRAMEBUFFER) err("screenshot", "no framebuffer registered");
      else ok("screenshot", fb_, framebufferLength());
    } else if (strcmp(body, "input") == 0) {
      if (!keyHandler_) { err("input", "no key handler"); return; }
      keyHandler_(arg);
      ok("input", nullptr, 0);
    } else if (strcmp(body, "status") == 0) {
      sendJson("status", status_, statusCount_);
    } else if (strcmp(body, "state") == 0) {
      sendJson("state", state_, stateCount_);
    } else {
      err(body, "unknown command");
    }
  }

  Stream* stream_ = nullptr;
  const uint8_t* fb_ = nullptr;
  uint16_t width_ = 0, height_ = 0;
  ArgusFormat format_ = ARGUS_NO_FRAMEBUFFER;
  const char* name_ = "app";
  const char* version_ = "1";
  KeyHandler keyHandler_ = nullptr;
  LineHandler lineHandler_ = nullptr;
  char line_[kMaxLine];
  size_t lineLen_ = 0;
  Entry status_[kMaxEntries];
  size_t statusCount_ = 0;
  Entry state_[kMaxEntries];
  size_t stateCount_ = 0;
};
