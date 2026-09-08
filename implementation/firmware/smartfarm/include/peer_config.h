#pragma once

#include <stdint.h>

// Replace these with the STA MAC printed by each board at boot.
// All-zero values deliberately prevent radio transmission instead of sending
// actuator commands to an unknown peer.
constexpr uint8_t kBridgeMac[6] = {0, 0, 0, 0, 0, 0};
constexpr uint8_t kCentralMac[6] = {0, 0, 0, 0, 0, 0};
