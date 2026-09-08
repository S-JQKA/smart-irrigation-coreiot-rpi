#include "protocol.h"
#if SMARTFARM_ROLE_SENSOR
#include <Wire.h>
#include <Preferences.h>
#include <algorithm>
#include <cmath>

namespace smartfarm {
namespace {
Preferences calibration;
int dryMv[4]{}, wetMv[4]{};
String bootId;
String consoleLine;

uint8_t shtCrc(const uint8_t *data) {
  uint8_t crc = 0xFF;
  for (int i = 0; i < 2; ++i) {
    crc ^= data[i];
    for (int b = 0; b < 8; ++b) crc = (crc & 0x80) ? (crc << 1) ^ 0x31 : crc << 1;
  }
  return crc;
}

int soilMillivolts(int index) {
  int samples[9];
  for (int &sample : samples) {
    sample = analogReadMilliVolts(kSoilPins[index]);
    delay(1);
  }
  std::sort(samples, samples + 9);
  return samples[4];
}

bool readSht31(int &temperature, int &humidity) {
  Wire.beginTransmission(0x44);
  Wire.write(0x24); Wire.write(0x00);  // Single shot, high repeatability, no stretch.
  if (Wire.endTransmission() != 0) return false;
  delay(20);
  if (Wire.requestFrom(uint8_t(0x44), uint8_t(6)) != 6) return false;
  uint8_t data[6];
  for (auto &byte : data) byte = Wire.read();
  if (shtCrc(data) != data[2] || shtCrc(data + 3) != data[5]) return false;
  temperature = lround((-45.0 + 175.0 * ((data[0] << 8) | data[1]) / 65535.0) * 100);
  humidity = lround(10000.0 * ((data[3] << 8) | data[4]) / 65535.0);
  return temperature >= -1000 && temperature <= 6000 && humidity >= 0 && humidity <= 10000;
}

bool readBh1750(uint32_t &lux) {
  Wire.beginTransmission(0x23);
  Wire.write(0x20);  // One-time high-resolution measurement.
  if (Wire.endTransmission() != 0) return false;
  delay(180);
  if (Wire.requestFrom(uint8_t(0x23), uint8_t(2)) != 2) return false;
  const uint8_t high = Wire.read();
  const uint8_t low = Wire.read();
  const uint16_t raw = (static_cast<uint16_t>(high) << 8) | low;
  if (raw == 0xFFFF) return false;  // Saturation is not a valid lux measurement.
  lux = lround(raw / 1.2);
  return true;
}

void calibrationConsole() {
  // Each endpoint is acquired from the actual ADC. No fabricated defaults.
  // Commands: CAL DRY 1 ... CAL DRY 4; CAL WET 1 ... CAL WET 4; CAL SHOW.
  while (Serial.available()) {
    const char c = Serial.read();
    if (c == '\r') continue;
    if (c != '\n') {
      if (consoleLine.length() < 48) consoleLine += c;
      else consoleLine = "";
      continue;
    }
    int channel = 0;
    char endpoint[5]{};
    if (sscanf(consoleLine.c_str(), "CAL %4s %d", endpoint, &channel) == 2 && channel >= 1 && channel <= 4) {
      const bool dry = strcmp(endpoint, "DRY") == 0;
      if (dry || strcmp(endpoint, "WET") == 0) {
        const int index = channel - 1, value = soilMillivolts(index);
        if (value > 50 && value < 3050) {
          (dry ? dryMv : wetMv)[index] = value;
          const String key = String(dry ? "dry" : "wet") + index;
          calibration.putInt(key.c_str(), value);
          Serial.printf("CAL_SAVED channel=%d endpoint=%s millivolts=%d\n", channel, endpoint, value);
        } else Serial.println("CAL_REJECT reason=ADC_RAIL");
      }
    } else if (consoleLine == "CAL SHOW") {
      for (int i = 0; i < 4; ++i) Serial.printf("CAL channel=%d dryMv=%d wetMv=%d rawMv=%d\n", i + 1, dryMv[i], wetMv[i], soilMillivolts(i));
    }
    consoleLine = "";
  }
}
}  // namespace

void sensorSetup() {
  Wire.begin(kSdaPin, kSclPin, 100000);
  Wire.setTimeOut(50);
  analogReadResolution(12);
  for (int pin : kSoilPins) {
    pinMode(pin, INPUT);
    analogSetPinAttenuation(pin, ADC_11db);
  }
  calibration.begin("soil-cal", false);
  for (int i = 0; i < 4; ++i) {
    dryMv[i] = calibration.getInt((String("dry") + i).c_str(), 0);
    wetMv[i] = calibration.getInt((String("wet") + i).c_str(), 0);
  }
  bootId = String(esp_random(), HEX);
}

void runSensor() {
  calibrationConsole();
  static uint32_t sequence = 0, last = 0;
  if (millis() - last < kSensorPeriodMs) return;
  last = millis();
  JsonDocument sample;
  int temperature, humidity;
  if (readSht31(temperature, humidity)) {
    // Integer wire fields make CRC representation identical on MCU and Python.
    sample["tC"] = temperature;
    sample["rh"] = humidity;
  }
  uint32_t lux;
  if (readBh1750(lux)) sample["lightLux"] = lux;
  sample["boot"] = bootId;
  sample["nodeId"] = String("sensor-field-") + SMARTFARM_SENSOR_FIELD;
  sample["origin"] = "P";
  sample["sequence"] = ++sequence;
  JsonArray soil = sample["soil"].to<JsonArray>();
  for (int i = 0; i < 4; ++i) {
    const int mv = soilMillivolts(i);
    if (dryMv[i] <= 0 || wetMv[i] <= 0 || abs(dryMv[i] - wetMv[i]) < 100 || mv <= 50 || mv >= 3050) {
      soil.add(nullptr);
    } else {
      const int percent = lround(10000.0 * (mv - dryMv[i]) / (wetMv[i] - dryMv[i]));
      soil.add(constrain(percent, 0, 10000));
    }
  }
  sample["type"] = "SENSOR";
  sample["version"] = kVersion;
  sample["zoneId"] = String("field-") + SMARTFARM_SENSOR_FIELD;
  if (!sendDocument(kBridgeMac, sample)) Serial.printf("SENSOR_NOT_SENT bytes=%u\n", static_cast<unsigned>(measureJson(sample)));
}
}  // namespace smartfarm
#endif
