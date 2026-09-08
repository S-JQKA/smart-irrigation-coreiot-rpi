#include "protocol.h"
#if SMARTFARM_ROLE_CENTRAL
namespace smartfarm {
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
  return SMARTFARM_TANK_LOW_PIN >= 0 && digitalRead(SMARTFARM_TANK_LOW_PIN) == SMARTFARM_TANK_LOW_LEVEL;
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
  heartbeat["origin"] = "P";
  heartbeat["peerId"] = "central";
  heartbeat["role"] = "CENTRAL";
  heartbeat["tankLow"] = tankLow();
  heartbeat["type"] = "PEER";
  heartbeat["version"] = kVersion;
  sendDocument(kBridgeMac, heartbeat);
}

volatile uint32_t gFlowPulses[2] = {0, 0};
portMUX_TYPE gFlowMux = portMUX_INITIALIZER_UNLOCKED;
void IRAM_ATTR flow1() { portENTER_CRITICAL_ISR(&gFlowMux); ++gFlowPulses[0]; portEXIT_CRITICAL_ISR(&gFlowMux); }
void IRAM_ATTR flow2() { portENTER_CRITICAL_ISR(&gFlowMux); ++gFlowPulses[1]; portEXIT_CRITICAL_ISR(&gFlowMux); }
String gBootId;
void centralSetup() {
  // Preload OFF before enabling each output, before serial/radio initialization.
  for (int pin : {SMARTFARM_VALVE1_PIN, SMARTFARM_VALVE2_PIN, SMARTFARM_PUMP_PIN}) {
    digitalWrite(pin, !SMARTFARM_ACTIVE_LEVEL);
    pinMode(pin, OUTPUT);
  }
  pinMode(SMARTFARM_TANK_LOW_PIN, INPUT_PULLUP);
  forceAllOff();
  gBootId = String(esp_random(), HEX);
  for (int pin : kFlowPins) pinMode(pin, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(kFlowPins[0]), flow1, RISING);
  attachInterrupt(digitalPinToInterrupt(kFlowPins[1]), flow2, RISING);
}
void sendFlow() {
  static uint32_t last = 0, previous[2] = {0, 0}, sequence = 0;
  const uint32_t now = millis(), elapsed = now - last;
  if (elapsed < 2000) return;
  last = now;
  uint32_t counts[2];
  portENTER_CRITICAL(&gFlowMux);
  counts[0] = gFlowPulses[0]; counts[1] = gFlowPulses[1];
  portEXIT_CRITICAL(&gFlowMux);
  for (int i = 0; i < 2; ++i) {
    JsonDocument doc;
    doc["boot"] = gBootId;
    doc["flowMilliLpm"] = static_cast<uint32_t>(round((counts[i] - previous[i]) * 60000000.0 / (kPulsesPerLiter[i] * elapsed)));
    doc["nodeId"] = "central";
    doc["origin"] = "P";
    doc["pulseCounter"] = counts[i];
    doc["sequence"] = ++sequence;
    doc["type"] = "FLOW";
    doc["version"] = kVersion;
    doc["zoneId"] = gZones[i].zoneId;
    sendDocument(kBridgeMac, doc);
    previous[i] = counts[i];
    delay(5);
  }
}
void runCentral() {
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
  sendFlow();
}

}
#endif
