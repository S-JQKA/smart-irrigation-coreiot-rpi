#include "protocol.h"
#if SMARTFARM_ROLE_BRIDGE
namespace smartfarm {
void forwardSerialCommand() {
  if (!Serial.available()) return;
  const String line = Serial.readStringUntil('\n');
  if (line.length() > ESP_NOW_MAX_DATA_LEN) return;
  JsonDocument command;
  if (deserializeJson(command, line) || !validFrame(command) || command["type"] != "SET_ZONE") {
    Serial.println("BRIDGE_REJECT reason=INVALID_UART_FRAME");
    return;
  }
  const bool queued = ensurePeer(kCentralMac) &&
                      esp_now_send(kCentralMac, reinterpret_cast<const uint8_t *>(line.c_str()),
                                   line.length()) == ESP_OK;
  if (!queued) Serial.println("BRIDGE_REJECT reason=CENTRAL_NOT_CONFIGURED");
}

void forwardRadioFrame() {
  uint8_t source[6]{};
  uint8_t data[ESP_NOW_MAX_DATA_LEN]{};
  int length = 0;
  if (!takeRx(source, data, length)) return;
  JsonDocument message;
  if (deserializeJson(message, data, static_cast<size_t>(length)) || !validFrame(message)) {
    Serial.println("BRIDGE_REJECT reason=INVALID_ESPNOW_FRAME");
    return;
  }
  Serial.write(data, static_cast<size_t>(length));
  Serial.write('\n');
}


}
#endif
