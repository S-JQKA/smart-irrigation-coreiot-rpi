#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include "board_config.h"
#include "peer_config.h"
namespace smartfarm {
inline constexpr uint8_t kChannel = 1;
inline constexpr uint8_t kVersion = 1;
inline constexpr uint32_t kLeaseMaxMs = 15000;
inline constexpr uint32_t kHeartbeatPeriodMs = 2000;
inline constexpr uint32_t kSensorPeriodMs = 2000;
inline constexpr size_t kFrameCapacity = 768;
bool ensurePeer(const uint8_t *mac);
bool initializeEspNow();
bool takeRx(uint8_t *mac, uint8_t *data, int &length);
bool validFrame(JsonDocument &document);
bool sendDocument(const uint8_t *peer, JsonDocument &document);
String canonicalPayload(JsonDocument &source);
void printMac();
void sensorSetup();
void runSensor();
void forwardSerialCommand();
void forwardRadioFrame();
void centralSetup();
void runCentral();
}
