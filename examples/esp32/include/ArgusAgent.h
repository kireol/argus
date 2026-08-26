// Copy of agents/esp32/arduino/ArgusAgent.h for this example project (kept self-contained so
// examples/esp32/ builds standalone). Do not edit here — change agents/esp32/arduino/ArgusAgent.h
// and re-copy it, so the two stay identical.
//
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
        if (discarding_) {
          discarding_ = false;
        } else {
          line_[lineLen_] = '\0';
          handleLine(line_);
        }
        lineLen_ = 0;
      } else if (discarding_) {
        // mid-discard: skip until the terminating '\n'
      } else if (lineLen_ < kMaxLine - 1) {
        line_[lineLen_++] = (char)c;
      } else {
        // Overlong line: discard everything through the next '\n' rather than starting
        // a fresh line mid-stream, which could reparse the tail of a too-long line as if
        // it were a legitimate command.
        discarding_ = true;
        lineLen_ = 0;
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

  // Length of `s` once JSON-escaped (excluding the surrounding quotes):
  // `"` and `\` cost 2 bytes, control chars < 0x20 cost 6 (`\u00XX`), everything else 1.
  static size_t jsonEscapedLength(const char* s) {
    size_t n = 0;
    for (const unsigned char* p = (const unsigned char*)s; *p; ++p) {
      if (*p == '"' || *p == '\\') n += 2;
      else if (*p < 0x20) n += 6;
      else n += 1;
    }
    return n;
  }

  // Writes `"<escaped s>"` to the stream.
  void writeJsonString(const char* s) {
    stream_->print('"');
    for (const unsigned char* p = (const unsigned char*)s; *p; ++p) {
      if (*p == '"') {
        stream_->print("\\\"");
      } else if (*p == '\\') {
        stream_->print("\\\\");
      } else if (*p < 0x20) {
        char buf[7];
        snprintf(buf, sizeof(buf), "\\u%04x", (unsigned)*p);
        stream_->print(buf);
      } else {
        stream_->write(*p);
      }
    }
    stream_->print('"');
  }

  // Streams `{"k":v,...}` for `table`. Keys are always JSON-escaped strings; values are
  // escaped strings when `quoted`, else emitted verbatim (numbers/true/false, which never
  // need escaping). The total length is computed in a first pass so the "ok <len>" header
  // can be sent before the body, without building the whole JSON blob in memory.
  void sendJson(const char* cmd, const Entry* table, size_t count) {
    size_t len = 2;  // '{' + '}'
    for (size_t i = 0; i < count; ++i) {
      if (i) len += 1;  // ','
      len += 2 + jsonEscapedLength(table[i].key);  // "key"
      len += 1;                                    // ':'
      len += table[i].quoted ? 2 + jsonEscapedLength(table[i].value) : strlen(table[i].value);
    }
    stream_->print(prefix());
    stream_->print(cmd);
    stream_->print(" ok ");
    stream_->print((unsigned long)len);
    stream_->print('\n');
    stream_->print('{');
    for (size_t i = 0; i < count; ++i) {
      if (i) stream_->print(',');
      writeJsonString(table[i].key);
      stream_->print(':');
      if (table[i].quoted) {
        writeJsonString(table[i].value);
      } else {
        stream_->print(table[i].value);
      }
    }
    stream_->print('}');
    stream_->print('\n');
    stream_->flush();
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
  bool discarding_ = false;
  Entry status_[kMaxEntries];
  size_t statusCount_ = 0;
  Entry state_[kMaxEntries];
  size_t stateCount_ = 0;
};
