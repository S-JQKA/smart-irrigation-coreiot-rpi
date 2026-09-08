#include "protocol.h"
using namespace smartfarm;
bool radioReady = false;
void setup() {
#if SMARTFARM_ROLE_CENTRAL
  centralSetup();
#endif
  Serial.begin(115200);
  Serial.setTimeout(50);
#if SMARTFARM_ROLE_SENSOR
  sensorSetup();
#endif
  radioReady = initializeEspNow();
  printMac();
  Serial.println(radioReady ? "BOOT_OK origin=PHYSICAL" : "BOOT_FAIL reason=ESPNOW_INIT");
}
void loop() {
#if SMARTFARM_ROLE_CENTRAL
  // Local interlocks and lease expiry still run even if radio startup failed.
  runCentral();
#elif SMARTFARM_ROLE_SENSOR
  if (radioReady) runSensor();
#elif SMARTFARM_ROLE_BRIDGE
  if (radioReady) { forwardSerialCommand(); forwardRadioFrame(); }
#endif
  delay(2);
}
