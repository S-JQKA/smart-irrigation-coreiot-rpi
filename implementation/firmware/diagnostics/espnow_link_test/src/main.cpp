#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>

#ifndef LINK_ROLE_SENDER
#define LINK_ROLE_SENDER 0
#endif

namespace {

constexpr uint16_t kMagic = 0x5346;  // "SF"
constexpr uint8_t kProtocolVersion = 1;
constexpr uint8_t kTelemetryType = 1;
constexpr uint8_t kAckType = 2;
constexpr uint8_t kChannel = 1;
constexpr uint8_t kMaxAttempts = 3;
constexpr uint32_t kAckTimeoutMs = 350;
constexpr uint32_t kSendPeriodMs = 1500;
constexpr uint32_t kTargetPackets = 20;

// Board 2 ESP32-S3-A, measured before this run.
constexpr uint8_t kReceiverMac[6] = {0xF4, 0x12, 0xFA,
                                     0xE6, 0xF9, 0x48};

struct __attribute__((packed)) TelemetryPacket {
  uint16_t magic;
  uint8_t version;
  uint8_t type;
  uint32_t sequence;
  uint32_t uptimeMs;
  int16_t soilCentiPct;
  int16_t temperatureCentiC;
  uint16_t crc16;
};

struct __attribute__((packed)) AckPacket {
  uint16_t magic;
  uint8_t version;
  uint8_t type;
  uint32_t sequence;
  uint32_t receiverUptimeMs;
  uint8_t accepted;
  uint16_t crc16;
};

static_assert(sizeof(TelemetryPacket) <= ESP_NOW_MAX_DATA_LEN,
              "Telemetry packet exceeds ESP-NOW payload limit");
static_assert(sizeof(AckPacket) <= ESP_NOW_MAX_DATA_LEN,
              "ACK packet exceeds ESP-NOW payload limit");

portMUX_TYPE gMux = portMUX_INITIALIZER_UNLOCKED;
volatile bool gAckReady = false;
volatile AckPacket gAck{};

volatile bool gRxReady = false;
volatile int gRxLength = 0;
volatile uint8_t gRxMac[6]{};
volatile uint8_t gRxData[ESP_NOW_MAX_DATA_LEN]{};

volatile bool gSendStatusReady = false;
volatile esp_now_send_status_t gSendStatus = ESP_NOW_SEND_FAIL;

uint16_t crc16Ccitt(const uint8_t *data, size_t length) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < length; ++i) {
    crc ^= static_cast<uint16_t>(data[i]) << 8;
    for (uint8_t bit = 0; bit < 8; ++bit) {
      crc = (crc & 0x8000U) != 0U ? static_cast<uint16_t>((crc << 1) ^ 0x1021U)
                                  : static_cast<uint16_t>(crc << 1);
    }
  }
  return crc;
}

template <typename T>
uint16_t packetCrc(const T &packet) {
  return crc16Ccitt(reinterpret_cast<const uint8_t *>(&packet),
                    sizeof(T) - sizeof(packet.crc16));
}

void formatMac(const uint8_t *mac, char *buffer, size_t size) {
  snprintf(buffer, size, "%02X:%02X:%02X:%02X:%02X:%02X", mac[0], mac[1],
           mac[2], mac[3], mac[4], mac[5]);
}

bool ensurePeer(const uint8_t *mac) {
  if (esp_now_is_peer_exist(mac)) {
    return true;
  }
  esp_now_peer_info_t peer{};
  memcpy(peer.peer_addr, mac, sizeof(peer.peer_addr));
  peer.channel = kChannel;
  peer.ifidx = WIFI_IF_STA;
  peer.encrypt = false;
  return esp_now_add_peer(&peer) == ESP_OK;
}

void onDataSent(const uint8_t *, esp_now_send_status_t status) {
  portENTER_CRITICAL(&gMux);
  gSendStatus = status;
  gSendStatusReady = true;
  portEXIT_CRITICAL(&gMux);
}

void onDataReceived(const uint8_t *mac, const uint8_t *data, int length) {
#if LINK_ROLE_SENDER
  if (length != static_cast<int>(sizeof(AckPacket))) {
    return;
  }
  AckPacket ack{};
  memcpy(&ack, data, sizeof(ack));
  if (ack.magic != kMagic || ack.version != kProtocolVersion ||
      ack.type != kAckType || ack.crc16 != packetCrc(ack)) {
    return;
  }
  portENTER_CRITICAL(&gMux);
  memcpy(const_cast<AckPacket *>(&gAck), &ack, sizeof(ack));
  gAckReady = true;
  portEXIT_CRITICAL(&gMux);
#else
  if (length <= 0 || length > ESP_NOW_MAX_DATA_LEN) {
    return;
  }
  portENTER_CRITICAL(&gMux);
  memcpy(const_cast<uint8_t *>(gRxMac), mac, sizeof(gRxMac));
  memcpy(const_cast<uint8_t *>(gRxData), data, static_cast<size_t>(length));
  gRxLength = length;
  gRxReady = true;
  portEXIT_CRITICAL(&gMux);
#endif
}

bool initializeEspNow() {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(false, false);
  if (esp_wifi_set_channel(kChannel, WIFI_SECOND_CHAN_NONE) != ESP_OK) {
    Serial.println("[FAIL] SET_CHANNEL");
    return false;
  }
  if (esp_now_init() != ESP_OK) {
    Serial.println("[FAIL] ESPNOW_INIT");
    return false;
  }
  if (esp_now_register_send_cb(onDataSent) != ESP_OK ||
      esp_now_register_recv_cb(onDataReceived) != ESP_OK) {
    Serial.println("[FAIL] ESPNOW_CALLBACKS");
    return false;
  }
  return true;
}

#if LINK_ROLE_SENDER

bool takeAck(uint32_t sequence, AckPacket &ack) {
  bool ready = false;
  portENTER_CRITICAL(&gMux);
  if (gAckReady && gAck.sequence == sequence) {
    memcpy(&ack, const_cast<const AckPacket *>(&gAck), sizeof(ack));
    gAckReady = false;
    ready = true;
  }
  portEXIT_CRITICAL(&gMux);
  return ready;
}

bool sendWithRetry(TelemetryPacket &packet, uint32_t &rttMs,
                   uint8_t &attemptsUsed) {
  for (uint8_t attempt = 1; attempt <= kMaxAttempts; ++attempt) {
    attemptsUsed = attempt;
    portENTER_CRITICAL(&gMux);
    gAckReady = false;
    gSendStatusReady = false;
    portEXIT_CRITICAL(&gMux);

    const uint32_t startedAt = millis();
    const esp_err_t queued = esp_now_send(kReceiverMac,
                                           reinterpret_cast<uint8_t *>(&packet),
                                           sizeof(packet));
    if (queued != ESP_OK) {
      Serial.printf("TX_QUEUE_FAIL seq=%lu attempt=%u err=%d\n",
                    static_cast<unsigned long>(packet.sequence), attempt,
                    static_cast<int>(queued));
    } else {
      while (millis() - startedAt < kAckTimeoutMs) {
        AckPacket ack{};
        if (takeAck(packet.sequence, ack) && ack.accepted == 1U) {
          rttMs = millis() - startedAt;
          return true;
        }
        delay(2);
      }
    }
    delay(40U * attempt);
  }
  return false;
}

void runSender() {
  static uint32_t sequence = 0;
  static uint32_t passed = 0;
  static uint32_t failed = 0;
  static uint32_t lastSendAt = 0;
  static bool summaryPrinted = false;

  if (sequence >= kTargetPackets) {
    if (!summaryPrinted) {
      Serial.printf("SUMMARY sent=%lu ack_pass=%lu ack_fail=%lu result=%s\n",
                    static_cast<unsigned long>(sequence),
                    static_cast<unsigned long>(passed),
                    static_cast<unsigned long>(failed),
                    failed == 0 ? "PASS" : "FAIL");
      summaryPrinted = true;
    }
    delay(100);
    return;
  }
  if (millis() - lastSendAt < kSendPeriodMs) {
    delay(5);
    return;
  }
  lastSendAt = millis();

  TelemetryPacket packet{};
  packet.magic = kMagic;
  packet.version = kProtocolVersion;
  packet.type = kTelemetryType;
  packet.sequence = ++sequence;
  packet.uptimeMs = millis();
  packet.soilCentiPct = static_cast<int16_t>(4200 + sequence * 7);
  packet.temperatureCentiC = static_cast<int16_t>(2850 + sequence * 3);
  packet.crc16 = packetCrc(packet);

  uint32_t rttMs = 0;
  uint8_t attempts = 0;
  const bool acknowledged = sendWithRetry(packet, rttMs, attempts);
  if (acknowledged) {
    ++passed;
    Serial.printf("[PASS] ACK seq=%lu attempts=%u rtt_ms=%lu\n",
                  static_cast<unsigned long>(sequence), attempts,
                  static_cast<unsigned long>(rttMs));
  } else {
    ++failed;
    Serial.printf("[FAIL] ACK_TIMEOUT seq=%lu attempts=%u\n",
                  static_cast<unsigned long>(sequence), attempts);
  }
}

#else

bool takeRx(uint8_t *mac, uint8_t *data, int &length) {
  bool ready = false;
  portENTER_CRITICAL(&gMux);
  if (gRxReady) {
    length = gRxLength;
    memcpy(mac, const_cast<const uint8_t *>(gRxMac), sizeof(gRxMac));
    memcpy(data, const_cast<const uint8_t *>(gRxData),
           static_cast<size_t>(length));
    gRxReady = false;
    ready = true;
  }
  portEXIT_CRITICAL(&gMux);
  return ready;
}

void runReceiver() {
  uint8_t sourceMac[6]{};
  uint8_t data[ESP_NOW_MAX_DATA_LEN]{};
  int length = 0;
  if (!takeRx(sourceMac, data, length)) {
    delay(5);
    return;
  }

  char macText[18]{};
  formatMac(sourceMac, macText, sizeof(macText));
  if (length != static_cast<int>(sizeof(TelemetryPacket))) {
    Serial.printf("[FAIL] RX_LENGTH from=%s length=%d\n", macText, length);
    return;
  }

  TelemetryPacket packet{};
  memcpy(&packet, data, sizeof(packet));
  const bool valid = packet.magic == kMagic &&
                     packet.version == kProtocolVersion &&
                     packet.type == kTelemetryType &&
                     packet.crc16 == packetCrc(packet);
  Serial.printf("[%s] RX seq=%lu from=%s crc=%s soil=%.2f temp=%.2f\n",
                valid ? "PASS" : "FAIL",
                static_cast<unsigned long>(packet.sequence), macText,
                valid ? "PASS" : "FAIL", packet.soilCentiPct / 100.0F,
                packet.temperatureCentiC / 100.0F);
  if (!valid || !ensurePeer(sourceMac)) {
    Serial.println("[FAIL] ACK_PEER");
    return;
  }

  AckPacket ack{};
  ack.magic = kMagic;
  ack.version = kProtocolVersion;
  ack.type = kAckType;
  ack.sequence = packet.sequence;
  ack.receiverUptimeMs = millis();
  ack.accepted = 1;
  ack.crc16 = packetCrc(ack);
  const esp_err_t result =
      esp_now_send(sourceMac, reinterpret_cast<uint8_t *>(&ack), sizeof(ack));
  Serial.printf("[%s] ACK_QUEUED seq=%lu err=%d\n",
                result == ESP_OK ? "PASS" : "FAIL",
                static_cast<unsigned long>(packet.sequence),
                static_cast<int>(result));
}

#endif

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(1500);

  Serial.println();
  Serial.println("=== SMARTFARM ESP-NOW LINK TEST ===");
  Serial.printf("role=%s channel=%u packet_bytes=%u ack_bytes=%u\n",
                LINK_ROLE_SENDER ? "SENDER" : "RECEIVER", kChannel,
                static_cast<unsigned>(sizeof(TelemetryPacket)),
                static_cast<unsigned>(sizeof(AckPacket)));
  if (!initializeEspNow()) {
    Serial.println("RESULT=FAIL_INIT");
    return;
  }
  Serial.printf("local_mac=%s\n", WiFi.macAddress().c_str());

#if LINK_ROLE_SENDER
  char peerText[18]{};
  formatMac(kReceiverMac, peerText, sizeof(peerText));
  if (!ensurePeer(kReceiverMac)) {
    Serial.println("RESULT=FAIL_ADD_PEER");
    return;
  }
  Serial.printf("[PASS] READY peer=%s\n", peerText);
#else
  Serial.println("[PASS] READY dynamic_peer_ack=true");
#endif
}

void loop() {
#if LINK_ROLE_SENDER
  runSender();
#else
  runReceiver();
#endif
}
