#include <Arduino.h>
#include <ArduinoJson.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>

#include "peer_config.h"

#ifndef SMARTFARM_ROLE_SENSOR
#define SMARTFARM_ROLE_SENSOR 0
#endif
#ifndef SMARTFARM_ROLE_BRIDGE
#define SMARTFARM_ROLE_BRIDGE 0
#endif
#ifndef SMARTFARM_ROLE_CENTRAL
#define SMARTFARM_ROLE_CENTRAL 0
#endif
#ifndef SMARTFARM_VALVE1_PIN
#define SMARTFARM_VALVE1_PIN LED_BUILTIN
#endif
#ifndef SMARTFARM_VALVE2_PIN
#define SMARTFARM_VALVE2_PIN -1
#endif
#ifndef SMARTFARM_PUMP_PIN
#define SMARTFARM_PUMP_PIN 47
#endif
#ifndef SMARTFARM_ACTIVE_LEVEL
#define SMARTFARM_ACTIVE_LEVEL HIGH
#endif
#ifndef SMARTFARM_TANK_LOW_PIN
#define SMARTFARM_TANK_LOW_PIN -1
#endif
#ifndef SMARTFARM_SENSOR_FIELD
#define SMARTFARM_SENSOR_FIELD 1
#endif
#ifndef SMARTFARM_MAX_CONCURRENT_ZONES
#define SMARTFARM_MAX_CONCURRENT_ZONES 1
#endif

static_assert(SMARTFARM_SENSOR_FIELD == 1 || SMARTFARM_SENSOR_FIELD == 2,
              "SMARTFARM_SENSOR_FIELD must be 1 or 2");
static_assert(SMARTFARM_MAX_CONCURRENT_ZONES >= 1 &&
                  SMARTFARM_MAX_CONCURRENT_ZONES <= 2,
              "SMARTFARM_MAX_CONCURRENT_ZONES must be 1 or 2");

namespace {

constexpr uint8_t kChannel = 1;
constexpr uint8_t kVersion = 1;
constexpr uint32_t kLeaseMaxMs = 15000;
constexpr uint32_t kHeartbeatPeriodMs = 2000;
constexpr uint32_t kSensorPeriodMs = 2000;
constexpr size_t kFrameCapacity = 768;

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
  JsonDocument canonical;
  const String type = source["type"] | "";
  if (type == "SET_ZONE") {
    canonical["commandId"] = source["commandId"];
    canonical["leaseMs"] = source["leaseMs"];
    canonical["sequence"] = source["sequence"];
    canonical["state"] = source["state"];
    canonical["type"] = source["type"];
    canonical["version"] = source["version"];
    canonical["zoneId"] = source["zoneId"];
  } else if (type == "ACK") {
    canonical["accepted"] = source["accepted"];
    canonical["commandId"] = source["commandId"];
    canonical["pumpOutput"] = source["pumpOutput"];
    canonical["reason"] = source["reason"];
    canonical["sequence"] = source["sequence"];
    canonical["type"] = source["type"];
    canonical["valveOutput"] = source["valveOutput"];
    canonical["version"] = source["version"];
    canonical["zoneId"] = source["zoneId"];
  } else if (type == "SENSOR") {
    if (!source["airHumidity"].isNull()) {
      canonical["airHumidity"] = source["airHumidity"];
    } else {
      canonical["airHumidityCentiPct"] = source["airHumidityCentiPct"];
    }
    if (!source["airTemp"].isNull()) {
      canonical["airTemp"] = source["airTemp"];
    } else {
      canonical["airTempCentiC"] = source["airTempCentiC"];
    }
    if (!source["flowRateLpm"].isNull()) {
      canonical["flowRateLpm"] = source["flowRateLpm"];
    } else {
      canonical["flowMilliLpm"] = source["flowMilliLpm"];
    }
    canonical["lightLux"] = source["lightLux"];
    canonical["nodeId"] = source["nodeId"];
    canonical["pulseCounter"] = source["pulseCounter"];
    canonical["sequence"] = source["sequence"];
    if (!source["soilMoisture"].isNull()) {
      canonical["soilMoisture"] = source["soilMoisture"];
    } else {
      canonical["soilCentiPct"] = source["soilCentiPct"];
    }
    if (!source["tankLow"].isNull()) canonical["tankLow"] = source["tankLow"];
    canonical["type"] = source["type"];
    canonical["version"] = source["version"];
    canonical["zoneId"] = source["zoneId"];
  } else if (type == "PEER") {
    canonical["online"] = source["online"];
    canonical["peerId"] = source["peerId"];
    canonical["role"] = source["role"];
    canonical["tankLow"] = source["tankLow"];
    canonical["type"] = source["type"];
    canonical["version"] = source["version"];
  } else {
    return "";
  }
  String output;
  serializeJson(canonical, output);
  return output;
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

#if SMARTFARM_ROLE_SENSOR

void runSensor() {
  static uint32_t sequence = 0;
  static uint32_t lastSent = 0;
  if (millis() - lastSent < kSensorPeriodMs) return;
  lastSent = millis();
  JsonDocument sample;
  sample["airHumidity"] = 60;
  sample["airTemp"] = 29;
  sample["flowRateLpm"] = 1;
  sample["lightLux"] = 18000;
  const String zoneId = String("field-") + SMARTFARM_SENSOR_FIELD;
  const String nodeId = String("sensor-") + zoneId;
  sample["nodeId"] = nodeId;
  sample["sequence"] = ++sequence;
  sample["pulseCounter"] = sequence * 5;
  JsonArray soil = sample["soilMoisture"].to<JsonArray>();
  const int soilBase = SMARTFARM_SENSOR_FIELD == 1 ? 2500 : 2200;
  for (int sensorIndex = 0; sensorIndex < 4; ++sensorIndex) {
    soil.add((soilBase + sensorIndex * 20 + static_cast<int>(sequence % 10) * 10) /
             100.0);
  }
  sample["type"] = "SENSOR";
  sample["version"] = kVersion;
  sample["zoneId"] = zoneId;
  const bool queued = sendDocument(kBridgeMac, sample);
  Serial.printf("SENSOR node=%s zone=%s seq=%lu synthetic=true queued=%s\n",
                nodeId.c_str(), zoneId.c_str(), static_cast<unsigned long>(sequence),
                queued ? "yes" : "no");
}

#elif SMARTFARM_ROLE_BRIDGE

void forwardSerialCommand() {
  if (!Serial.available()) return;
  const String line = Serial.readStringUntil('\n');
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

#elif SMARTFARM_ROLE_CENTRAL

struct ZoneOutput {
  const char *zoneId;
  int pin;
  bool on;
  uint32_t leaseUntil;
  String activeCommandId;
};

ZoneOutput gZones[] = {
    {"field-1", SMARTFARM_VALVE1_PIN, false, 0, ""},
    {"field-2", SMARTFARM_VALVE2_PIN, false, 0, ""},
};
bool gPumpOn = false;
constexpr size_t kDedupCapacity = 8;
String gSeenCommandIds[kDedupCapacity];
size_t gDedupCursor = 0;

void writeOutput(int pin, bool on) {
  if (pin < 0) return;
  digitalWrite(pin, on ? SMARTFARM_ACTIVE_LEVEL : !SMARTFARM_ACTIVE_LEVEL);
}

bool outputMatches(int pin, bool on) {
  if (pin < 0) return false;
  return digitalRead(pin) == (on ? SMARTFARM_ACTIVE_LEVEL : !SMARTFARM_ACTIVE_LEVEL);
}

ZoneOutput *findZone(const String &zoneId) {
  for (auto &zone : gZones) {
    if (zoneId == zone.zoneId) return &zone;
  }
  return nullptr;
}

bool anyValveOn() {
  for (const auto &zone : gZones) {
    if (zone.on) return true;
  }
  return false;
}

size_t activeValveCount() {
  size_t active = 0;
  for (const auto &zone : gZones) {
    if (zone.on) ++active;
  }
  return active;
}

void clearZoneLease(ZoneOutput &zone) {
  zone.leaseUntil = 0;
  zone.activeCommandId = "";
}

bool seenCommand(const String &commandId) {
  for (const auto &seen : gSeenCommandIds) {
    if (seen == commandId) return true;
  }
  return false;
}

void rememberCommand(const String &commandId) {
  gSeenCommandIds[gDedupCursor] = commandId;
  gDedupCursor = (gDedupCursor + 1) % kDedupCapacity;
}

void forceAllOff() {
  writeOutput(SMARTFARM_PUMP_PIN, false);
  delay(50);
  gPumpOn = !outputMatches(SMARTFARM_PUMP_PIN, false);
  for (auto &zone : gZones) {
    writeOutput(zone.pin, false);
    if (zone.pin >= 0) {
      delay(20);
      zone.on = !outputMatches(zone.pin, false);
    } else {
      zone.on = false;
    }
    clearZoneLease(zone);
  }
}

bool stopZone(ZoneOutput &zone) {
  if (!zone.on) {
    clearZoneLease(zone);
    return true;
  }
  const bool lastActiveZone = activeValveCount() == 1;
  if (lastActiveZone) {
    writeOutput(SMARTFARM_PUMP_PIN, false);
    delay(50);
    gPumpOn = !outputMatches(SMARTFARM_PUMP_PIN, false);
    if (gPumpOn) return false;
  }
  writeOutput(zone.pin, false);
  delay(50);
  zone.on = !outputMatches(zone.pin, false);
  clearZoneLease(zone);
  if (zone.on) return false;
  if (!lastActiveZone) {
    gPumpOn = outputMatches(SMARTFARM_PUMP_PIN, true);
    return gPumpOn;
  }
  return !gPumpOn;
}

bool tankLow() {
  return SMARTFARM_TANK_LOW_PIN >= 0 && digitalRead(SMARTFARM_TANK_LOW_PIN) == LOW;
}

void fillAck(JsonDocument &ack, JsonDocument &command, ZoneOutput *zone, bool accepted,
             const char *reason) {
  ack.clear();
  ack["accepted"] = accepted;
  ack["commandId"] = command["commandId"];
  ack["pumpOutput"] = gPumpOn ? "ON" : "OFF";
  ack["reason"] = reason;
  ack["sequence"] = command["sequence"];
  ack["type"] = "ACK";
  ack["valveOutput"] = zone != nullptr && zone->on ? "ON" : "OFF";
  ack["version"] = kVersion;
  ack["zoneId"] = command["zoneId"];
}

void handleCommand() {
  uint8_t source[6]{};
  uint8_t data[ESP_NOW_MAX_DATA_LEN]{};
  int length = 0;
  if (!takeRx(source, data, length)) return;
  JsonDocument command;
  if (deserializeJson(command, data, static_cast<size_t>(length)) || !validFrame(command) ||
      command["type"] != "SET_ZONE") return;
  ensurePeer(source);
  const String commandId = command["commandId"] | "";
  const String zoneId = command["zoneId"] | "";
  ZoneOutput *zone = findZone(zoneId);
  JsonDocument ack;
  if (commandId.isEmpty() || zone == nullptr || zone->pin < 0) {
    fillAck(ack, command, zone, false, "INVALID_COMMAND");
    sendDocument(source, ack);
    return;
  }
  const String desired = command["state"] | "";
  if (seenCommand(commandId)) {
    const String duplicateState = command["state"] | "";
    if (duplicateState == "ON" && commandId == zone->activeCommandId) {
      if (tankLow()) {
        forceAllOff();
        fillAck(ack, command, zone, false, "TANK_LOW");
      } else if (gPumpOn && zone->on) {
        const uint32_t requestedLease = command["leaseMs"] | kLeaseMaxMs;
        zone->leaseUntil = millis() + min(requestedLease, kLeaseMaxMs);
        fillAck(ack, command, zone, true, "EXECUTED");
      } else {
        fillAck(ack, command, zone, false, "LEASE_EXPIRED");
      }
    } else if (duplicateState == "OFF" && !zone->on) {
      fillAck(ack, command, zone, true, "EXECUTED");
    } else {
      fillAck(ack, command, zone, false, "DUPLICATE");
    }
    sendDocument(source, ack);
    return;
  }
  rememberCommand(commandId);
  if (desired == "ON") {
    if (tankLow()) {
      forceAllOff();
      fillAck(ack, command, zone, false, "TANK_LOW");
    } else if (zone->on) {
      fillAck(ack, command, zone, false, "ALREADY_ACTIVE");
    } else if (activeValveCount() >= SMARTFARM_MAX_CONCURRENT_ZONES) {
      fillAck(ack, command, zone, false, "MAX_CONCURRENT_ZONES");
    } else {
      writeOutput(zone->pin, true);
      delay(100);
      zone->on = outputMatches(zone->pin, true);
      if (!zone->on) {
        forceAllOff();
        fillAck(ack, command, zone, false, "OUTPUT_CONFIRM_FAILED");
      } else {
        if (!gPumpOn) {
          writeOutput(SMARTFARM_PUMP_PIN, true);
          delay(50);
          gPumpOn = outputMatches(SMARTFARM_PUMP_PIN, true);
        }
        if (!gPumpOn) {
          forceAllOff();
          fillAck(ack, command, zone, false, "OUTPUT_CONFIRM_FAILED");
        } else {
          const uint32_t requestedLease = command["leaseMs"] | kLeaseMaxMs;
          zone->leaseUntil = millis() + min(requestedLease, kLeaseMaxMs);
          zone->activeCommandId = commandId;
          fillAck(ack, command, zone, true, "EXECUTED");
        }
      }
    }
  } else if (desired == "OFF") {
    const bool confirmed = stopZone(*zone);
    if (!confirmed) forceAllOff();
    fillAck(ack, command, zone, confirmed, confirmed ? "EXECUTED" : "OUTPUT_CONFIRM_FAILED");
  } else {
    forceAllOff();
    fillAck(ack, command, zone, false, "INVALID_COMMAND");
  }
  sendDocument(source, ack);
}

void sendHeartbeat() {
  static uint32_t lastSent = 0;
  if (millis() - lastSent < kHeartbeatPeriodMs) return;
  lastSent = millis();
  JsonDocument heartbeat;
  heartbeat["online"] = true;
  heartbeat["peerId"] = "central";
  heartbeat["role"] = "CENTRAL";
  heartbeat["tankLow"] = tankLow();
  heartbeat["type"] = "PEER";
  heartbeat["version"] = kVersion;
  sendDocument(kBridgeMac, heartbeat);
}

#endif

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(1000);
#if SMARTFARM_ROLE_CENTRAL
  if (SMARTFARM_VALVE1_PIN >= 0) pinMode(SMARTFARM_VALVE1_PIN, OUTPUT);
  if (SMARTFARM_VALVE2_PIN >= 0) pinMode(SMARTFARM_VALVE2_PIN, OUTPUT);
  pinMode(SMARTFARM_PUMP_PIN, OUTPUT);
  if (SMARTFARM_TANK_LOW_PIN >= 0) pinMode(SMARTFARM_TANK_LOW_PIN, INPUT_PULLUP);
  forceAllOff();
#endif
  if (!initializeEspNow()) {
    Serial.println("BOOT_FAIL reason=ESPNOW_INIT");
    return;
  }
  printMac();
  Serial.println("BOOT_OK protocol=1");
}

void loop() {
#if SMARTFARM_ROLE_SENSOR
  runSensor();
#elif SMARTFARM_ROLE_BRIDGE
  forwardSerialCommand();
  forwardRadioFrame();
#elif SMARTFARM_ROLE_CENTRAL
  handleCommand();
  sendHeartbeat();
  if (gPumpOn && !anyValveOn()) {
    forceAllOff();
    Serial.println("INTERLOCK outputs=OFF reason=PUMP_WITHOUT_VALVE");
  } else if (!gPumpOn && anyValveOn()) {
    forceAllOff();
    Serial.println("INTERLOCK outputs=OFF reason=VALVE_WITHOUT_PUMP");
  } else if (gPumpOn && tankLow()) {
    forceAllOff();
    Serial.println("TANK_LOW outputs=OFF");
  } else {
    for (auto &zone : gZones) {
      if (!zone.on || zone.leaseUntil == 0 ||
          static_cast<int32_t>(millis() - zone.leaseUntil) < 0) {
        continue;
      }
      const bool stopped = stopZone(zone);
      if (!stopped) forceAllOff();
      Serial.printf("LEASE_EXPIRED zone=%s pump=%s result=%s\n", zone.zoneId,
                    gPumpOn ? "ON" : "OFF", stopped ? "EXECUTED" : "ALL_OFF");
    }
  }
#endif
  delay(2);
}
