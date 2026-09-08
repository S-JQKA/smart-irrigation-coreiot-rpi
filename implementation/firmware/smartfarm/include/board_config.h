#pragma once
#include <Arduino.h>
#ifndef SMARTFARM_ROLE_SENSOR
#define SMARTFARM_ROLE_SENSOR 0
#endif
#ifndef SMARTFARM_ROLE_BRIDGE
#define SMARTFARM_ROLE_BRIDGE 0
#endif
#ifndef SMARTFARM_ROLE_CENTRAL
#define SMARTFARM_ROLE_CENTRAL 0
#endif
#ifndef SMARTFARM_SENSOR_FIELD
#define SMARTFARM_SENSOR_FIELD 1
#endif
#define SMARTFARM_VALVE1_PIN 11
#define SMARTFARM_VALVE2_PIN 12
#define SMARTFARM_PUMP_PIN 13
#define SMARTFARM_TANK_LOW_PIN 10
// Reference driver is active HIGH. Use an external pull-down on each input.
#ifndef SMARTFARM_ACTIVE_LEVEL
#define SMARTFARM_ACTIVE_LEVEL HIGH
#endif
#define SMARTFARM_MAX_CONCURRENT_ZONES 2
// Closed contact means water available. Open wire also means tank low.
#define SMARTFARM_TANK_LOW_LEVEL HIGH
constexpr int kSoilPins[4] = {1, 2, 4, 5};
constexpr int kSdaPin = 8;
constexpr int kSclPin = 9;
constexpr int kFlowPins[2] = {6, 7};
constexpr float kPulsesPerLiter[2] = {450.0f, 450.0f};
static_assert(SMARTFARM_SENSOR_FIELD == 1 || SMARTFARM_SENSOR_FIELD == 2, "Unknown Field");
