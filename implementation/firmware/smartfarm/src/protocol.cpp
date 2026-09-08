#include "protocol.h"
namespace smartfarm {
portMUX_TYPE gMux = portMUX_INITIALIZER_UNLOCKED;
volatile bool gRxReady = false;
volatile int gRxLength = 0;
volatile uint8_t gRxMac[6]{};
volatile uint8_t gRxData[ESP_NOW_MAX_DATA_LEN]{};

uint16_t crc16Ccitt(const uint8_t *data, size_t length) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < length; ++i) {
    crc ^= static_cast<uint16_t>(data[i]) << 8;
    for (uint8_t bit = 0; bit < 8; ++bit) {
      crc = (crc & 0x8000U) != 0U
                ? static_cast<uint16_t>((crc << 1U) ^ 0x1021U)
                : static_cast<uint16_t>(crc << 1U);
    }
  }
  return crc;
}

bool isConfiguredMac(const uint8_t *mac) {
  for (size_t i = 0; i < 6; ++i) {
    if (mac[i] != 0) return true;
  }
  return false;
}

bool ensurePeer(const uint8_t *mac) {
  if (!isConfiguredMac(mac)) return false;
  if (esp_now_is_peer_exist(mac)) return true;
  esp_now_peer_info_t peer{};
  memcpy(peer.peer_addr, mac, 6);
  peer.channel = kChannel;
  peer.ifidx = WIFI_IF_STA;
  peer.encrypt = false;
  return esp_now_add_peer(&peer) == ESP_OK;
}

void onDataReceived(const uint8_t *mac, const uint8_t *data, int length) {
  if (length <= 0 || length > ESP_NOW_MAX_DATA_LEN) return;
  portENTER_CRITICAL(&gMux);
  memcpy(const_cast<uint8_t *>(gRxMac), mac, 6);
  memcpy(const_cast<uint8_t *>(gRxData), data, static_cast<size_t>(length));
  gRxLength = length;
  gRxReady = true;
  portEXIT_CRITICAL(&gMux);
}

bool takeRx(uint8_t *mac, uint8_t *data, int &length) {
  bool ready = false;
  portENTER_CRITICAL(&gMux);
  if (gRxReady) {
    memcpy(mac, const_cast<const uint8_t *>(gRxMac), 6);
    length = gRxLength;
    memcpy(data, const_cast<const uint8_t *>(gRxData), static_cast<size_t>(length));
    gRxReady = false;
    ready = true;
  }
  portEXIT_CRITICAL(&gMux);
  return ready;
}

bool initializeEspNow() {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(false, false);
  if (esp_wifi_set_channel(kChannel, WIFI_SECOND_CHAN_NONE) != ESP_OK) return false;
  if (esp_now_init() != ESP_OK) return false;
  return esp_now_register_recv_cb(onDataReceived) == ESP_OK;
}

String canonicalPayload(JsonDocument &source) {
  // Alphabetical order matches Python json.dumps(sort_keys=True). Only keys
  // actually transmitted participate, including null slots for failed probes.
  static const char *keys[] = {
    "accepted", "airHumidity", "airHumidityCentiPct", "airTemp", "airTempCentiC",
    "boot", "commandId", "flowMilliLpm", "flowRateLpm", "leaseMs", "lightLux",
    "nodeId", "online", "origin", "peerId", "pulseCounter", "pumpOutput",
    "reason", "rh", "role", "sequence", "soil", "soilCentiPct", "soilMoisture", "state",
    "tC", "tankLow", "type", "valveOutput", "version", "zoneId"
  };
  JsonDocument canonical;
  for (const auto *key : keys) {
    if (source.containsKey(key)) canonical[key] = source[key];
  }
  // Reject unrecognized keys rather than silently removing them from the CRC.
  if (canonical.size() + (source.containsKey("crc16") ? 1 : 0) != source.size()) return "";
  String result;
  serializeJson(canonical, result);
  return result;
}

bool validFrame(JsonDocument &document) {
  if ((document["version"] | 0) != kVersion || !document["crc16"].is<uint16_t>()) return false;
  const String canonical = canonicalPayload(document);
  if (canonical.isEmpty()) return false;
  return document["crc16"].as<uint16_t>() ==
         crc16Ccitt(reinterpret_cast<const uint8_t *>(canonical.c_str()), canonical.length());
}

bool sendDocument(const uint8_t *peer, JsonDocument &document) {
  const String canonical = canonicalPayload(document);
  if (canonical.isEmpty() || !ensurePeer(peer)) return false;
  document["crc16"] = crc16Ccitt(
      reinterpret_cast<const uint8_t *>(canonical.c_str()), canonical.length());
  const size_t requiredLength = measureJson(document);
  if (requiredLength == 0 || requiredLength > ESP_NOW_MAX_DATA_LEN) return false;
  uint8_t buffer[ESP_NOW_MAX_DATA_LEN]{};
  const size_t length = serializeJson(document, buffer, sizeof(buffer));
  if (length != requiredLength) return false;
  return esp_now_send(peer, buffer, length) == ESP_OK;
}

void printMac() {
  uint8_t mac[6]{};
  esp_wifi_get_mac(WIFI_IF_STA, mac);
  Serial.printf("STA_MAC=%02X:%02X:%02X:%02X:%02X:%02X\n", mac[0], mac[1], mac[2],
                mac[3], mac[4], mac[5]);
}


}
