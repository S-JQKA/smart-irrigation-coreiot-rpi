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
#ifndef SMARTFARM_VALVE_PIN
#define SMARTFARM_VALVE_PIN LED_BUILTIN
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
    canonical["airHumidity"] = source["airHumidity"];
    canonical["airTemp"] = source["airTemp"];
    canonical["lightLux"] = source["lightLux"];
    canonical["nodeId"] = source["nodeId"];
    canonical["sequence"] = source["sequence"];
    canonical["soilMoisture"] = source["soilMoisture"];
    canonical["type"] = source["type"];
    canonical["version"] = source["version"];
    canonical["zoneId"] = source["zoneId"];
  } else if (type == "PEER") {
    canonical["online"] = source["online"];
    canonical["peerId"] = source["peerId"];
    canonical["role"] = source["role"];
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
  uint8_t buffer[ESP_NOW_MAX_DATA_LEN]{};
  const size_t length = serializeJson(document, buffer, sizeof(buffer));
  if (length == 0 || length > ESP_NOW_MAX_DATA_LEN) return false;
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
  sample["airHumidity"] = 60.0;
  sample["airTemp"] = 29.0;
  sample["lightLux"] = 18000.0;
  sample["nodeId"] = "sensor-field-1";
  sample["sequence"] = ++sequence;
  JsonArray soil = sample["soilMoisture"].to<JsonArray>();
  soil.add(25.0F + static_cast<float>(sequence % 10) / 10.0F);
  sample["type"] = "SENSOR";
  sample["version"] = kVersion;
  sample["zoneId"] = "field-1";
  const bool queued = sendDocument(kBridgeMac, sample);
  Serial.printf("SENSOR seq=%lu synthetic=true queued=%s\n",
                static_cast<unsigned long>(sequence), queued ? "yes" : "no");
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

bool gValveOn = false;
bool gPumpOn = false;
uint32_t gLeaseUntil = 0;
String gLastCommandId;
JsonDocument gLastAck;

void writeOutput(uint8_t pin, bool on) {
  digitalWrite(pin, on ? SMARTFARM_ACTIVE_LEVEL : !SMARTFARM_ACTIVE_LEVEL);
}

void forceOff() {
  writeOutput(SMARTFARM_PUMP_PIN, false);
  gPumpOn = false;
  delay(50);
  writeOutput(SMARTFARM_VALVE_PIN, false);
  gValveOn = false;
  gLeaseUntil = 0;
}

bool tankLow() {
  return SMARTFARM_TANK_LOW_PIN >= 0 && digitalRead(SMARTFARM_TANK_LOW_PIN) == LOW;
}

void fillAck(JsonDocument &ack, JsonDocument &command, bool accepted, const char *reason) {
  ack.clear();
  ack["accepted"] = accepted;
  ack["commandId"] = command["commandId"];
  ack["pumpOutput"] = gPumpOn ? "ON" : "OFF";
  ack["reason"] = reason;
  ack["sequence"] = command["sequence"];
  ack["type"] = "ACK";
  ack["valveOutput"] = gValveOn ? "ON" : "OFF";
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
  if (commandId.isEmpty() || command["zoneId"] != "field-1") {
    fillAck(gLastAck, command, false, "INVALID_COMMAND");
    sendDocument(source, gLastAck);
    return;
  }
  if (commandId == gLastCommandId) {
    const String duplicateState = command["state"] | "";
    if (duplicateState == "ON") {
      if (tankLow()) {
        forceOff();
        fillAck(gLastAck, command, false, "TANK_LOW");
      } else if (gPumpOn && gValveOn) {
        const uint32_t requestedLease = command["leaseMs"] | kLeaseMaxMs;
        gLeaseUntil = millis() + min(requestedLease, kLeaseMaxMs);
        fillAck(gLastAck, command, true, "EXECUTED");
      } else {
        fillAck(gLastAck, command, false, "LEASE_EXPIRED");
      }
    }
    sendDocument(source, gLastAck);
    return;
  }
  gLastCommandId = commandId;
  const String desired = command["state"] | "";
  if (desired == "ON") {
    if (tankLow()) {
      forceOff();
      fillAck(gLastAck, command, false, "TANK_LOW");
    } else {
      writeOutput(SMARTFARM_VALVE_PIN, true);
      gValveOn = true;
      delay(100);
      writeOutput(SMARTFARM_PUMP_PIN, true);
      gPumpOn = true;
      const uint32_t requestedLease = command["leaseMs"] | kLeaseMaxMs;
      gLeaseUntil = millis() + min(requestedLease, kLeaseMaxMs);
      fillAck(gLastAck, command, true, "EXECUTED");
    }
  } else if (desired == "OFF") {
    forceOff();
    fillAck(gLastAck, command, true, "EXECUTED");
  } else {
    forceOff();
    fillAck(gLastAck, command, false, "INVALID_COMMAND");
  }
  sendDocument(source, gLastAck);
}

void sendHeartbeat() {
  static uint32_t lastSent = 0;
  if (millis() - lastSent < kHeartbeatPeriodMs) return;
  lastSent = millis();
  JsonDocument heartbeat;
  heartbeat["online"] = true;
  heartbeat["peerId"] = "central";
  heartbeat["role"] = "CENTRAL";
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
  pinMode(SMARTFARM_VALVE_PIN, OUTPUT);
  pinMode(SMARTFARM_PUMP_PIN, OUTPUT);
  if (SMARTFARM_TANK_LOW_PIN >= 0) pinMode(SMARTFARM_TANK_LOW_PIN, INPUT_PULLUP);
  forceOff();
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
  if (gPumpOn && tankLow()) {
    forceOff();
    Serial.println("TANK_LOW outputs=OFF");
  } else if (gPumpOn && static_cast<int32_t>(millis() - gLeaseUntil) >= 0) {
    forceOff();
    Serial.println("LEASE_EXPIRED outputs=OFF");
  }
#endif
  delay(2);
}
