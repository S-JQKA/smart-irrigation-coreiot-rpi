#include <Arduino.h>
#include <BLEDevice.h>
#include <WiFi.h>
#include <esp_heap_caps.h>

namespace {

constexpr size_t kPsramTestBytes = 1024U * 1024U;
constexpr uint32_t kPatternSeed = 0x5A17C3E9U;

bool testPsram() {
  if (!psramFound()) {
    Serial.println("[FAIL] PSRAM_NOT_FOUND");
    return false;
  }

  auto *buffer = static_cast<uint32_t *>(
      heap_caps_malloc(kPsramTestBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (buffer == nullptr) {
    Serial.println("[FAIL] PSRAM_ALLOC_1MB");
    return false;
  }

  const size_t words = kPsramTestBytes / sizeof(uint32_t);
  for (size_t i = 0; i < words; ++i) {
    buffer[i] = kPatternSeed ^ static_cast<uint32_t>(i * 2654435761U);
  }

  bool valid = true;
  for (size_t i = 0; i < words; ++i) {
    const uint32_t expected =
        kPatternSeed ^ static_cast<uint32_t>(i * 2654435761U);
    if (buffer[i] != expected) {
      valid = false;
      break;
    }
  }

  heap_caps_free(buffer);
  Serial.println(valid ? "[PASS] PSRAM_PATTERN_1MB"
                       : "[FAIL] PSRAM_PATTERN_1MB");
  return valid;
}

bool testWifiScan() {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(false, true);
  delay(200);

  const int networkCount = WiFi.scanNetworks(false, true);
  if (networkCount < 0) {
    Serial.printf("[FAIL] WIFI_SCAN code=%d\n", networkCount);
    WiFi.mode(WIFI_OFF);
    return false;
  }

  int strongestRssi = -127;
  for (int i = 0; i < networkCount; ++i) {
    strongestRssi = max(strongestRssi, static_cast<int>(WiFi.RSSI(i)));
  }
  Serial.printf("[PASS] WIFI_SCAN networks=%d strongest_rssi=%d_dBm\n",
                networkCount, strongestRssi);
  WiFi.scanDelete();
  WiFi.mode(WIFI_OFF);
  return true;
}

bool testBleInit() {
  BLEDevice::init("ESP32S3_SMOKE_TEST");
  BLEServer *server = BLEDevice::createServer();
  const bool valid = server != nullptr;
  BLEDevice::deinit(true);
  Serial.println(valid ? "[PASS] BLE_STACK_INIT" : "[FAIL] BLE_STACK_INIT");
  return valid;
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(1500);

  Serial.println();
  Serial.println("=== ESP32-S3 BASIC SMOKE TEST ===");
  Serial.printf("chip=%s revision=%u cores=%u cpu_mhz=%u\n",
                ESP.getChipModel(), ESP.getChipRevision(), ESP.getChipCores(),
                ESP.getCpuFreqMHz());
  Serial.printf("flash_bytes=%u flash_speed_hz=%u\n", ESP.getFlashChipSize(),
                ESP.getFlashChipSpeed());
  Serial.printf("heap_bytes=%u psram_bytes=%u free_psram_bytes=%u\n",
                ESP.getHeapSize(), ESP.getPsramSize(), ESP.getFreePsram());

  const bool chipOk = String(ESP.getChipModel()).indexOf("ESP32-S3") >= 0;
  const bool flashOk = ESP.getFlashChipSize() == 8U * 1024U * 1024U;
  Serial.println(chipOk ? "[PASS] CHIP_ESP32_S3" : "[FAIL] CHIP_ESP32_S3");
  Serial.println(flashOk ? "[PASS] FLASH_SIZE_8MB"
                         : "[FAIL] FLASH_SIZE_8MB");

  const bool psramOk = testPsram();
  const bool wifiOk = testWifiScan();
  const bool bleOk = testBleInit();
  const bool allOk = chipOk && flashOk && psramOk && wifiOk && bleOk;

  Serial.printf("SUMMARY chip=%s flash=%s psram=%s wifi=%s ble=%s\n",
                chipOk ? "PASS" : "FAIL", flashOk ? "PASS" : "FAIL",
                psramOk ? "PASS" : "FAIL", wifiOk ? "PASS" : "FAIL",
                bleOk ? "PASS" : "FAIL");
  Serial.println(allOk ? "RESULT=PASS" : "RESULT=FAIL");
  Serial.println("Heartbeat follows every 2 seconds.");
}

void loop() {
  static uint32_t heartbeat = 0;
  Serial.printf("HEARTBEAT %lu uptime_ms=%lu free_heap=%u\n",
                static_cast<unsigned long>(++heartbeat),
                static_cast<unsigned long>(millis()), ESP.getFreeHeap());
  delay(2000);
}
