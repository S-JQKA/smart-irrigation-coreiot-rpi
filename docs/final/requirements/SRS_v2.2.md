# Phân tích yêu cầu hệ thống tưới tiêu thông minh trên CoreIoT

**Phiên bản:** v2.2 — cập nhật topology nước/flow theo zone, Central/Manifold Controller, WaterloggingRisk và điều chỉnh phạm vi pH

**Mã đề tài:** HK53-DATN-008 · **GVHD:** TS. Lê Trọng Nhân · **Sinh viên:** Võ Lê Sinh – MSSV 2212927 · **Bộ môn:** Kỹ thuật Máy tính · **Đợt:** GD2-ĐATN/LVTN HK253 · **Chương trình:** Tiếng Việt · **Hướng:** Ứng dụng

> Tài liệu này là **đặc tả yêu cầu hệ thống (SRS)** theo cấu trúc IEEE 830-1998 / ISO/IEC/IEEE 29148:2018, sử dụng mô hình chất lượng **ISO/IEC 25010:2023** (9 đặc trưng, bao gồm Safety) và phân loại ưu tiên **MoSCoW**. Tài liệu viết theo định hướng triển khai, không phải báo cáo học thuật thuần. Mọi quyết định kiến trúc quan trọng đều kèm phân tích đánh đổi.
>
> **Đồng bộ với phương án thiết kế v2.2 đang thảo luận.** Bản SRS này cập nhật các điều chỉnh quan trọng so với v2.1: topology nước chuyển sang **một bơm chính + van từng zone + flow sensor riêng từng nhánh**, có **Central/Manifold Controller ESP32** đặt gần cụm bơm/van/flow thay vì kéo dây relay xa từ Raspberry Pi; `waterLevel` được tách thành **phao cạn hard interlock (Must)** và đo mức nước liên tục (Should); bổ sung **Irrigation Scheduler** để tưới nhiều zone đồng thời trong giới hạn cấu hình; bổ sung **WaterloggingRisk** và khóa tưới zone quá ẩm như phần tối thiểu của “tiêu”; `pH` được đưa ra khỏi lõi hiện tại, chỉ giữ như hướng mở rộng. Bản này vẫn giữ các điểm v2.1: **Anomaly Detector**, **NORMAL/DEGRADED/SAFE-IDLE**, **khóa liên động cứng**, **cổng kiểm tra dữ liệu nhiều tầng**, và nguyên tắc mất cloud chuyển **DEGRADED** thay vì ép OFF vô điều kiện.

---

## 0. Ghi chú cập nhật v2.2

Các điều chỉnh chính đưa vào bản này:

1. Chuyển topology nước sang **một bơm chính + manifold + valve từng zone + flow sensor riêng từng nhánh** để tránh hạn chế chỉ tưới một zone tại một thời điểm.
2. Thêm **Irrigation Scheduler** với `maxConcurrentZones`, ưu tiên zone theo độ khô/VPD/lần tưới gần nhất, đóng từng zone độc lập khi đạt điều kiện dừng.
3. Tách phần actuator xa Pi bằng **ESP32 Central/Manifold Controller** đặt gần trạm nước; Pi không mặc định kéo dây GPIO/relay xa.
4. Tách `waterLevel`: **phao cạn/low-water switch là Must hard interlock**, đo mức nước liên tục là Should.
5. Chỉnh `pulseCounter`: mỗi zone có `pulseCounter_Zi` riêng; SIM mode cũng mô phỏng từng branch độc lập để test dry-run/leak đúng hơn.
6. Thêm **WaterloggingRisk** và khóa tưới zone quá ẩm như phần tối thiểu của “tiêu”; Drain Pump/Valve Controller giữ mức Should/Future.
7. Đưa **pH ra khỏi lõi hiện tại**, chỉ giữ như hướng mở rộng cho nutrient/fertigation.
8. Chỉnh Data Quality: giá trị rác không publish vào key chính, nhưng publish quality/status/last-invalid để dashboard vẫn giải thích được cảnh báo.
9. Tách **Control mode** (`MANUAL/AUTO/DISABLED`) khỏi **System operation mode** (`NORMAL/DEGRADED/SAFE-IDLE`); bổ sung manual ON TTL, manual OFF latch và command metadata.
10. Sửa NFR/test target quá “ảo”: không yêu cầu 0 mất gói tuyệt đối, dùng tỷ lệ mẫu hợp lệ + sequence/log.

---


## 1. Bối cảnh và vấn đề cần giải quyết

### 1.1 Bối cảnh nông nghiệp thông minh và CoreIoT tại Việt Nam

Nông nghiệp Việt Nam đang chịu sức ép đồng thời từ biến đổi khí hậu, suy giảm nguồn nước ngọt, chi phí nhân công tăng và yêu cầu truy xuất nguồn gốc theo tiêu chuẩn xuất khẩu. **IoT trong canh tác** (Smart Farming) là hướng tiếp cận để giảm lượng nước tưới, giảm phân thuốc, tăng năng suất nhờ vận hành theo dữ liệu thay vì theo lịch cố định. Kiến trúc **ba tầng "perception – edge – cloud"** là chuẩn mực cho smart farming, trong đó tầng edge gánh phần xử lý cục bộ và logic an toàn khi mất kết nối.

**CoreIoT** (`app.coreiot.io`) là nền tảng IoT do OhStem Education phát triển, tùy biến trên ThingsBoard. CoreIoT giữ nguyên giao thức MQTT chuẩn ThingsBoard (port 1883, xác thực bằng access token, các topic `v1/devices/me/telemetry`, `v1/devices/me/rpc/request/+`...), nên hệ thiết kế cho CoreIoT có thể chuyển sang ThingsBoard CE/PE/Cloud mà không phá vỡ hợp đồng giao tiếp. Trong tenant hiện tại, Smart Irrigation có thể được tạo từ **Solution Templates** của CoreIoT; nếu triển khai trên tenant không có template sẵn hoặc chuyển sang môi trường ThingsBoard khác thì cần import/tái dựng thủ công các thực thể (device profile, asset profile, rule chain, dashboard) theo cùng hợp đồng dữ liệu.

### 1.2 Vấn đề thực tế: vì sao tưới thông minh cần đa biến, không chỉ độ ẩm đất

Đề bài thực tế của đồ án là **một hệ Smart Farm đa cảm biến – đa cơ cấu chấp hành**, tích hợp nhiệt độ, độ ẩm không khí, ánh sáng, độ ẩm đất, mực nước và lưu lượng theo zone, đồng thời điều khiển nhiều loại actuator (bơm chính, van zone, đèn chiếu sáng, quạt thông gió). `pH` không nằm trong lõi tưới tiêu hiện tại vì liên quan nhiều hơn đến dinh dưỡng/fertigation, chi phí và hiệu chuẩn; chỉ giữ như hướng mở rộng.

Cơ sở nông học nằm ở **mô hình cân bằng nước cây trồng FAO-56 Penman–Monteith**. Lượng bốc thoát hơi nước tham chiếu **ET₀** phụ thuộc đồng thời nhiệt độ, độ ẩm tương đối, bức xạ mặt trời và tốc độ gió. Đại lượng **Vapor Pressure Deficit – VPD = SVP·(1 − RH/100)** với **SVP = 0.61078·exp[17.27·T/(T+237.3)]** (kPa) là chỉ thị tức thời cho sức hút hơi nước của không khí. Cùng một mức ẩm đất 25 % VWC, ngày 35 °C/RH 40 %/nắng gắt có VPD ≈ 3.4 kPa, cây mất nước nhanh gấp nhiều lần ngày 25 °C/RH 80 %/VPD ≈ 0.6 kPa. **Tưới chỉ theo ngưỡng độ ẩm đất sẽ vừa thừa nước ngày mát, vừa thiếu nước ngày khô nóng** — đó là lý do bài toán phải xử lý đa biến.

### 1.3 Kiến trúc nhiều cụm cảm biến không dây phản ánh farm thực tế

Một farm/nhà kính thực tế trải dài hàng chục đến vài trăm mét, chia thành nhiều **zone** vi khí hậu khác nhau. Mỗi zone cần một cụm cảm biến riêng. **Phương án "một dây cảm biến nối thẳng vào Raspberry Pi" là sai về kiến trúc** vì: (i) GPIO của Pi giới hạn 3.3 V, 16 mA/chân, ~50 mA tổng; (ii) không thể kéo dây cảm biến hàng chục mét qua ruộng (nhiễu, sụt áp, sự cố cơ học); (iii) farm cần nhiều điểm đo đồng thời.

Kiến trúc đúng: **nhiều sensor node không dây phân tán + Central/Manifold Controller tại trạm nước → ESP32 ESP-NOW bridge/receiver → Raspberry Pi gateway → CoreIoT**. Mỗi zone có một ESP32 sensor node đo môi trường; cụm bơm/van/flow đặt gần trạm nước và được điều khiển bởi ESP32 Central/Manifold Controller. Pi là **edge gateway** đảm nhiệm chuẩn hóa, ánh xạ telemetry, đẩy lên cloud, điều phối RPC/scheduler, thực thi safety cấp hệ thống và phát hiện bất thường; Pi không mặc định kéo dây GPIO/relay xa tới trạm bơm.

### 1.4 Khoảng trống giữa template lý tưởng và phần cứng thật

Smart Irrigation Template quy định ba **device profile** lõi (`SI Soil Moisture Sensor`, `SI Water Meter`, `SI Smart Valve`) với telemetry key đóng (`moisture`, `pulseCounter`, `battery`) và RPC method (`TURN_ON`, `TURN_OFF`). Phần cứng thực tế không trùng khít: một sensor node có thể mang nhiều cảm biến; một trạm nước có thể quản lý nhiều valve/flow sensor; reference design dùng flow sensor riêng từng nhánh nhưng implementation có thể mô phỏng từng `pulseCounter` độc lập trong giai đoạn tiết kiệm linh kiện. Bài toán cốt lõi vì thế là **ánh xạ (mapping)**: dịch dữ liệu vật lý sang đúng hợp đồng dữ liệu của template ở phần lõi, đồng thời **mở rộng có kỷ luật** template cho đại lượng/actuator ngoài lõi.

> **Lưu ý về tính tùy biến của template.** Các con số mặc định của template (3 device profile, 2 state vận hành chính trong dashboard, 5 rule chain SI lõi) **không phải ràng buộc cố định** - chúng chỉ là điểm xuất phát do template sinh ra khi cài. Dashboard export thực tế có thêm các sub-state để xem chi tiết, chỉnh sửa vị trí/polygon và xem alarm. Hệ thống được phép thêm/bớt device profile, rule chain, alarm, telemetry key, widget; và thiết kế này thực tế đã mở rộng vượt các con số gốc. Cái phải giữ đúng là **hợp đồng dữ liệu phần lõi**, không phải số lượng.

### 1.5 Vì sao Raspberry Pi gateway và vì sao ESP-NOW

Ba phương án tổ chức tầng edge: (a) ESP32 vừa node vừa gateway; (b) Pi cắm cảm biến/relay trực tiếp; (c) Pi làm gateway nhiều module, ESP32 làm sensor node và Central/Manifold Controller tại hiện trường. (a) bị giới hạn RAM (520 KB SRAM), khó chạy MQTT QoS 1 ổn định + log + watchdog + ML. (b) không phản ánh farm thực và vi phạm giới hạn điện. **Phương án (c) tối ưu** và được chọn, vì tách đúng vai trò: Pi xử lý/mapping/safety cấp hệ thống; ESP32 gần trạm nước thực thi valve/pump/flow và hard interlock cục bộ. Về truyền không dây, đề tài kế thừa giai đoạn 1 (ESP-NOW) và khẳng định lại sau khi đánh giá trade-off đầy đủ với Wi-Fi/MQTT và LoRa (§3.7).

---

## 2. Mục tiêu tổng quát và mục tiêu cụ thể

### 2.1 Mục tiêu tổng quát

Xây dựng và vận hành thử nghiệm một hệ Smart Farm đa cảm biến – đa cơ cấu chấp hành trên CoreIoT, lấy Smart Irrigation Template làm lõi, mở rộng có kiểm soát cho các đại lượng môi trường và actuator bổ sung, với Raspberry Pi 4 đóng vai edge gateway đa module và **mạng cảm biến không dây ESP-NOW** kết nối nhiều cụm cảm biến phân tán theo zone, **đảm bảo an toàn cục bộ và khả năng tự chủ khi mất kết nối cloud**.

### 2.2 Mục tiêu cụ thể (SMART)

| # | Mục tiêu cụ thể | Tiêu chí đo lường |
|---|---|---|
| MT-01 | Triển khai phần **lõi template** trên CoreIoT | Đủ 3 device profile lõi, asset `SI Field`, average moisture theo key thực tế `avgMoisture`/`latestAvgMoisture`, `waterConsumption`, propagate ngưỡng, 2 state vận hành chính trong dashboard kèm sub-state, 5 rule chain SI, alarm low/high moisture và low battery |
| MT-02 | Mở rộng device profile **đa cảm biến môi trường** | Tạo `Env Sensor Cluster` chứa `airTemp`, `airHumidity`, `lightLux`, tùy chọn `waterLevel`; `pH` chỉ là future work, không nằm trong lõi hiện tại |
| MT-03 | Triển khai **ESP-NOW nhiều node ↔ bridge/receiver** | ≥ 2 sensor node và 1 Central/Manifold Controller gửi telemetry về bridge/receiver; gateway phân biệt node theo MAC, ánh xạ đúng logical device |
| MT-04 | Điều khiển **đa actuator** qua RPC | Bơm chính, valve từng zone, quạt, đèn bật/tắt được từ dashboard, độ trễ end-to-end ≤ 2 s (P95) |
| MT-05 | **Logic điều khiển đa biến** cấp field | Tưới theo độ ẩm + nhiệt độ + ánh sáng/VPD; quạt theo nhiệt độ và RH; đèn theo ánh sáng và lịch — cấu hình qua attribute |
| MT-06 | **An toàn cục bộ nhiều lớp và tự chủ giảm cấp** | Watchdog tưới, dry-run, khóa cứng bể cạn, hạn mức nước, relay thường-hở; **ba chế độ NORMAL/DEGRADED/SAFE-IDLE** với Local Control Engine chạy khi mất cloud |
| MT-07 | **Water meter theo từng zone** | Reference design: mỗi nhánh có flow sensor riêng tạo `pulseCounter` riêng; SIM mode mô phỏng độc lập từng `pulseCounter`; thay bằng YF-S201 thật mà không phá hợp đồng dữ liệu |
| MT-08 | Kiểm thử end-to-end | Vượt đủ bộ test case lõi + mở rộng; bản v2.2 có 45 test case bao phủ topology flow theo zone, scheduler, safety và anomaly |
| MT-09 | Tài liệu kỹ thuật bàn giao | SRS, thiết kế hệ thống, sơ đồ kiến trúc/dây, README triển khai, kịch bản test |
| MT-10 | **Thiết kế mở cho OTA** dù OTA out-of-scope | Bật OTA partition trên ESP32, phiên bản hóa firmware, mô tả lộ trình hybrid OTA |
| MT-11 | **Phát hiện bất thường mức cơ bản (ML)** | Module Anomaly Detector bắt được cảm biến lỗi/trôi (`SensorAnomaly`) và nghi rò rỉ (`LeakSuspected`) bằng bất thường tiêm có kiểm soát; chạy ngoài vòng điều khiển an toàn |
| MT-12 | **Cổng kiểm tra dữ liệu nhiều tầng** | Giá trị rác/CRC lỗi/NaN/out-of-range không publish vào key chính; vẫn publish quality/status/last-invalid để dashboard giải thích được alarm |
| MT-13 | **Topology nước scale nhiều zone** | Một bơm chính + manifold + valve từng zone + flow sensor riêng từng nhánh; hỗ trợ nhiều zone tưới đồng thời qua `maxConcurrentZones` |
| MT-14 | **Waterlogging protection / tiêu tối thiểu** | Đất quá ẩm/nguy cơ úng sinh `WaterloggingRisk`, khóa tưới zone bị ảnh hưởng; Drain Pump/Valve Controller giữ ở mức Should/Future |

---

## 3. Phân tích phạm vi hệ thống

### 3.1 Bốn lớp phạm vi

1. **Phần dùng nguyên Smart Irrigation Template** (giữ nguyên hợp đồng).
2. **Phần mở rộng trên template** (thêm có kỷ luật, không phá lõi).
3. **Phần do Raspberry Pi / ESP32 đảm nhiệm** (firmware + dịch vụ phần mềm edge).
4. **Phần cảm biến và thiết bị chấp hành** (phần cứng vật lý).

Số lượng device profile / rule chain / dashboard state nêu dưới đây là **cấu hình mặc định** của template — được phép tùy biến, thêm bớt; thiết kế đã mở rộng vượt các con số gốc.

### 3.2 Phần giữ nguyên từ Smart Irrigation Template

| Thành phần template | Giữ nguyên | Ghi chú |
|---|---|---|
| Device profile `SI Soil Moisture Sensor` | ✅ | Telemetry: `moisture` (0-100), `battery`. Alarm low battery Warning theo `lowBatteryThreshold` của device; default mong muốn 30 nhưng tenant có thể cấu hình khác. |
| Device profile `SI Water Meter` | ✅ | Telemetry: `pulseCounter` (tích lũy), `battery`. |
| Device profile `SI Smart Valve` | ✅ | RPC `TURN_ON` / `TURN_OFF`. Telemetry `battery`. |
| Asset profile `SI Field` | ✅ | Mỗi field = một zone vật lý. |
| Average moisture cấp field | ✅ | Khái niệm trung bình `moisture` từ sensor con; key thực tế trong template CoreIoT là `avgMoisture` và `latestAvgMoisture`, không dùng nhầm thành key `averageMoisture`. |
| Calculated field `waterConsumption` | ✅ | Tính từ delta `pulseCounter`. |
| Propagate `minMoistureThreshold` / `maxMoistureThreshold` | ✅ | Propagation calculated field từ asset xuống child sensor. |
| Dashboard "Irrigation Management" | ✅ | Có 2 state vận hành chính (main/default và field) kèm các sub-state chi tiết/setup/alarm trong dashboard export. |
| 5 rule chain SI | ✅ | Tên chính xác lấy từ CoreIoT Solution Template/Solution Instructions hoặc bản import tương đương. |
| Alarm low/high moisture, low battery | ✅ | Severity theo template gốc. |
| Inclusion schedule | ✅ | Lịch tưới trong Field state. |

### 3.3 Phần mở rộng trên template

| Mở rộng | Mục đích | Tích hợp ngược |
|---|---|---|
| Device profile `Env Sensor Cluster` | Telemetry: `airTemp`, `airHumidity`, `lightLux`, optional `waterLevel`, `battery`, `rssi`; `pH` là future work | Quan hệ Contains với `SI Field`; tham gia calculated field VPD ở cấp asset |
| Device profile `Pump Controller`, `Valve Controller`, `Fan Controller`, `Light Controller` | Actuator ngoài Smart Valve; RPC `TURN_ON`/`TURN_OFF`, mở rộng `SET_PWM` cho quạt/đèn | Cùng pattern RPC như SI Smart Valve; valve là actuator chính theo zone |
| Calculated field `VPD`, `etoIndex` (đơn giản hóa) ở `SI Field` | Lượng hóa stress khí quyển | Đầu vào: airTemp, airHumidity (latest), bức xạ ước lượng từ lightLux |
| Rule chain mở rộng `SI Field Multivar Control` | Logic tưới đa biến, quạt theo VPD/RH, đèn theo light + lịch | Gọi RPC tới các device profile mới |
| Alarm mở rộng | High temperature, low water level, node offline, `WaterloggingRisk`, `SensorAnomaly`, `LeakSuspected`, `ZoneFlowLow`, `ValveLeak` | Định tuyến qua `SI Count Alarms` cho phần đếm alarm lõi và/hoặc rule chain alarm routing mở rộng cần tạo thêm |
| Widget dashboard mới | Biểu đồ T/RH/Lux/VPD; thẻ vận hành 3 actuator/field; trạng thái online node; alarm bất thường | Bố trí trong cùng dashboard "Irrigation Management" |

### 3.4 Phần do Raspberry Pi / ESP32 Central-Station đảm nhiệm

Raspberry Pi 4 Model B chạy Raspberry Pi OS 64-bit, đóng vai **edge gateway đa module** (không phải trung tâm nghiệp vụ và không mặc định kéo dây relay xa). Pi nhận dữ liệu từ ESP32 ESP-NOW bridge/receiver, ánh xạ thành logical device trên CoreIoT, nhận RPC và điều phối lệnh xuống Central/Manifold Controller. Các relay bơm/van/flow sensor đặt tại trạm nước do ESP32 Central/Manifold Controller thực thi cục bộ.

**Mười một module phần mềm chính trên Pi**:

1. **Sensor Data Receiver** – đọc khung từ ESP32 bridge/receiver qua UART, kiểm CRC, đưa vào hàng đợi.
2. **Sensor Reader / Parser** – tách MAC nguồn, loại node, loại cảm biến, sequence và giá trị thô.
3. **Flow Counter Aggregator** – nhận `pulseCounter` từng nhánh từ Central/Manifold Controller hoặc SIM mode; tính delta, flow rate, daily volume theo từng zone.
4. **Data Mapper + Quality Gate** – ánh xạ sang key lõi/mở rộng; kiểm dải, kiểu, rate-of-change, dedup; không publish giá trị rác vào key chính, nhưng publish quality/status để giải thích cảnh báo.
5. **CoreIoT Telemetry Client** – MQTT 3.1.1, QoS 1, keep-alive, Last Will; mỗi logical device dùng token riêng (Direct Device API), sẵn đường chuyển Gateway API.
6. **RPC Listener** – subscribe RPC, whitelist method, kiểm `commandId`/timestamp/source, trả response.
7. **Command Dispatcher / Actuator Coordinator** – chuyển lệnh hợp lệ xuống ESP32 Central/Manifold Controller qua ESP-NOW; direct GPIO relay chỉ dùng cho lab/demo gần Pi, không phải reference topology.
8. **Irrigation Scheduler** – chọn nhóm zone cần tưới, giới hạn `maxConcurrentZones`, ưu tiên theo độ khô/VPD/lần tưới gần nhất, đóng zone đạt ngưỡng hoặc vượt hạn mức.
9. **Local Safety Handler & Mode Manager** – watchdog, dry-run, khóa cứng bể cạn, hạn mức nước, waterlogging lock, relay thường-hở, NORMAL/DEGRADED/SAFE-IDLE, cache cấu hình.
10. **Anomaly Detector** – module Python độc lập, đọc telemetry sạch + quality events, phát alarm `SensorAnomaly`/`LeakSuspected`/`ValveLeak`; ngoài vòng điều khiển an toàn.
11. **Logger / Replay Buffer** – ghi log xoay vòng, lưu telemetry có timestamp gốc tại Pi, replay khi MQTT phục hồi.

**ESP32 Central/Manifold Controller** đặt gần trạm nước hoặc cụm manifold, quản lý 4–6 zone/cụm trong reference design: output valve, đọc flow sensor nhánh, đọc phao cạn/tank status, thực thi interlock cục bộ trước khi bật bơm/valve, gửi telemetry trạng thái về Pi. Với hệ 10–20 zone, mở rộng bằng nhiều Manifold Controller thay vì dồn toàn bộ GPIO/interrupt lên một board.

### 3.5 Phần cảm biến và thiết bị chấp hành

| Vai trò | Linh kiện | Lý do chọn |
|---|---|---|
| Độ ẩm đất | **Capacitive Soil Moisture v1.2/v2.0**, đọc qua ADC ESP32 (hoặc ADS1115) | Không điện phân/ăn mòn như loại điện trở, bền, rẻ |
| Nhiệt độ + độ ẩm KK | **SHT3x (SHT31)** I²C 0x44 — ưu tiên; **DHT22** dự phòng | SHT31 ±0.3 °C / ±2 % RH, có CRC, đọc nhanh |
| Ánh sáng | **BH1750FVI** I²C 0x23 | Đo lux trực tiếp 1–65535, không cần hiệu chuẩn từng module |
| Mực nước / bể cạn | **Phao cạn / low-water switch (Must)** + tùy chọn HC-SR04/analog level (Should) | Phao cạn là hard interlock chống cháy bơm; đo mức liên tục chỉ để hiển thị/ước lượng |
| Lưu lượng từng nhánh | **YF-S201** hoặc flow sensor tương đương trên từng zone branch | Mỗi zone có `pulseCounter` riêng để thống kê nước và phát hiện flow thấp/rò rỉ theo zone; SIM mode mô phỏng độc lập từng nhánh |
| Lưu lượng tổng (tùy chọn) | Flow sensor sau bơm chính | Cross-check `sum(flow_Zi)` với tổng, phát hiện rò manifold/cảm biến lỗi |
| pH (future work) | Probe pH analog | Không nằm trong lõi tưới tiêu hiện tại; liên quan nhiều hơn đến dinh dưỡng/fertigation, cần hiệu chuẩn và chi phí cao |
| Bơm / van / quạt / đèn | Một bơm chính, solenoid valve từng zone, quạt DC, LED/grow light qua relay/driver | Valve là actuator chính theo zone; bơm chính cấp áp cho manifold; tải cảm cần relay/driver, diode flyback/snubber |
| Gateway | **Raspberry Pi 4 Model B** | GPIO 3.3 V, UART/MQTT; chạy 11 module + systemd; không mặc định kéo dây relay xa |
| Tầng đệm | Relay module **opto-isolated** (PC817), **tách JD-VCC**, **thường-hở** | Cách ly logic; nguồn cuộn relay riêng; mất điện = tải OFF |
| ADC ngoài | **ADS1115** 16-bit I²C | Cho cảm biến analog khi ADC nội nhiễu |
| Sensor node MCU | **ESP32-WROOM-32** | Dual-core, ESP-NOW, deep-sleep, rẻ |
| Bộ thu/bridge | 1× ESP32 **ESP-NOW bridge/receiver**, nối Pi qua UART | Nhận uplink từ sensor/Central node và gửi downlink command tới Central/Manifold Controller; giữ channel cố định |
| Central/Manifold Controller | ESP32 đặt gần bơm/manifold, quản lý 4–6 zone/cụm | Đọc flow branch, đọc low-water switch, điều khiển valve/pump, enforce hard interlock cục bộ |

### 3.6 Bảng phân định in-scope / out-of-scope

| Hạng mục | In-scope | Out-of-scope (thiết kế mở) |
|---|---|---|
| Lõi template (device + asset + rule chain + dashboard) | ✅ | – |
| Mở rộng đa cảm biến (T/RH/Lux/Water level) | ✅ | – |
| Đa actuator (bơm chính/valve zone/quạt/đèn) | ✅ | – |
| Logic điều khiển đa biến cấp field | ✅ | – |
| **An toàn nhiều lớp + 3 chế độ vận hành (tự chủ giảm cấp)** | ✅ | – |
| **Khóa cứng (bể cạn, hạn mức nước, relay thường-hở)** | ✅ | – |
| **Cổng kiểm tra dữ liệu nhiều tầng (node + gateway)** | ✅ | – |
| **Phát hiện bất thường mức B (thống kê / Isolation Forest)** | ✅ | – |
| **WaterloggingRisk và khóa tưới zone quá ẩm** | ✅ | Bơm/van thoát nước thật là Should/Could |
| **Drain Pump/Drain Valve Controller** | Should (thiết kế mở) | Giải thuật tiêu nước đầy đủ / mô hình sump phức tạp để sau |
| Cảm biến pH liên tục dài ngày | – | Out/Future work (calibration + drift + chi phí); chỉ mở rộng khi chuyển sang fertigation/nutrient monitoring |
| Water meter theo zone | ✅ reference design: flow sensor riêng từng nhánh; SIM mode có thể mô phỏng từng `pulseCounter` độc lập | Flow tổng/cảm biến công nghiệp chính xác cao là mở rộng |
| **OTA firmware node** | – | **Out**; bật OTA partition, ghi version, để ngỏ hybrid |
| **Dự báo độ ẩm ngắn hạn / RL (ML mức C, D)** | – | Out; thiếu dữ liệu lịch sử thật → để ngỏ hướng phát triển |
| Multi-tenant, customer mgmt | – | Out; demo trên 1 tenant |
| Cellular fallback (4G/LTE) | – | Out; Pi gắn dongle về sau |
| TLS đến CoreIoT (port 8883) | Tùy chọn | Mặc định 1883 + token |

### 3.7 Đánh đổi phương án truyền không dây giữa node và gateway

| Tiêu chí | **ESP-NOW (chọn)** | Wi-Fi/MQTT trực tiếp mỗi node | LoRa (SX1276/78) |
|---|---|---|---|
| Băng tần | 2.4 GHz Wi-Fi | 2.4/5 GHz Wi-Fi | sub-GHz 433/868/915 MHz |
| Tầm xa thực nghiệm | ~100–220 m LoS; có chế độ Long-Range | ~25–100 m LoS, phụ thuộc AP | 2–5 km đô thị, 10–15 km nông thôn |
| Payload tối đa | **250 byte (v1)** | Tùy MTU, lớn | 51–242 byte tùy SF |
| Độ trễ điều khiển | **~ms** | 50–500 ms | 1–5 s (duty cycle) |
| Hạ tầng yêu cầu | Không cần AP | Cần AP + router + Internet | Cần LoRaWAN gateway + server |
| Tiêu thụ pin node | Thấp | Cao (keep-alive) | Thấp nhất |
| Khả năng OTA | Khó (§3.8) | Dễ (`esp_https_ota`) | Gần như không |
| Bảo mật | CCMP/AES-128 + PMK/LMK | WPA2/3 + TLS | AES-128 |
| Ổn định khi rớt AP | Bình thường (P2P) | Treo đến khi reconnect (3–12 s) | Không phụ thuộc AP |

**Kết luận:** **ESP-NOW thắng** cho farm vài chục đến ~100 m vì dùng chung radio 2.4 GHz nên tầm xa tương đương Wi-Fi, nhưng peer-to-peer không bám AP nên chịu tín hiệu yếu tốt hơn, độ trễ ms, không cần hạ tầng Wi-Fi, kế thừa kinh nghiệm giai đoạn 1. Wi-Fi/MQTT trực tiếp kém thực tế ở farm thưa AP (rớt/reconnect, tốn pin). **LoRa giữ làm nhánh tùy chọn** cho zone xa > 100 m (telemetry thưa), không dùng cho điều khiển thời gian thực hay OTA.

### 3.8 Đánh đổi OTA — vì sao out-of-scope nhưng thiết kế mở

OTA-over-ESP-NOW khó vì: frame ≤ 250 byte (firmware 0.5–2 MB ⇒ hàng nghìn gói); ESP-NOW connectionless, không TCP/HTTP, phải tự xây sequence + ACK + retry + ghi OTA partition + rollback; ESP-NOW và Wi-Fi STA phải cùng channel nên muốn `esp_https_ota` phải tạm chuyển STA mode (dễ lỗi). Tự làm là một tiểu dự án riêng.

**Quyết định:** OTA không dây **out-of-scope**; giai đoạn này cập nhật firmware **qua cáp USB**. Nhưng **thiết kế mở**: (1) phiên bản hóa firmware (`fw_version`); (2) bật OTA partition; (3) app rollback + anti-rollback; (4) hai lộ trình tương lai — node lai hybrid (tạm bật Wi-Fi OTA rồi về ESP-NOW) hoặc chuyển hẳn sang Wi-Fi/MQTT khi farm có hạ tầng Wi-Fi.



### 3.9 Topology nước, flow sensor và khả năng mở rộng nhiều zone

Reference topology của hệ thống dùng **một bơm chính + manifold + valve từng zone + flow sensor riêng từng nhánh**:

```text
Tank → low-water switch → main pump → optional total flow → manifold
                                      ├── Valve Z1 → Flow Z1 → Zone 1
                                      ├── Valve Z2 → Flow Z2 → Zone 2
                                      └── Valve Zn → Flow Zn → Zone n
```

Quyết định này thay cho phương án một flow sensor tổng và chỉ tưới một zone tại một thời điểm. Với flow sensor riêng từng nhánh, hệ thống có thể tưới nhiều zone đồng thời mà vẫn thống kê được `waterConsumption` theo từng `SI Field`. Mỗi nhánh tạo một `pulseCounter_Zi`, ánh xạ vào `SI Water Meter` của zone tương ứng.

Hệ thống vẫn phải giới hạn số zone tưới đồng thời bằng cấu hình `maxConcurrentZones`, vì ràng buộc lúc này là năng lực thủy lực của bơm/áp lực đường ống, không phải khả năng đo nước. `Irrigation Scheduler` gom các zone cần tưới thành nhóm, ưu tiên zone khô hơn hoặc có stress cao hơn, không vượt `maxConcurrentZones`, đóng từng valve khi zone đạt target hoặc vượt hạn mức nước.

Flow sensor tổng sau bơm là **Should**, không phải Must. Nó dùng để cross-check `sum(flow_Zi)` với tổng nhằm phát hiện rò manifold, cảm biến nhánh lỗi hoặc sai lệch thủy lực. Với implementation tiết kiệm linh kiện, SIM mode được phép mô phỏng từng flow branch độc lập, nhưng interface vẫn giữ như thiết kế thật.

### 3.10 Tiêu nước tối thiểu và drainage hook

Trong phạm vi hiện tại, “tiêu” được xử lý ở mức tối thiểu nhưng rõ ràng: hệ thống phải phát hiện **WaterloggingRisk** khi zone quá ẩm/nguy cơ úng và **khóa tưới zone đó**. Điều kiện cơ bản: `moisture > floodMoistureThreshold` trong N mẫu liên tiếp, hoặc `moisture > maxMoistureThreshold` kéo dài sau khi đã dừng tưới.

Drain Pump/Drain Valve Controller được giữ ở mức **Should**: thiết kế profile/RPC/telemetry mở để sau này gắn bơm thoát/van thoát nếu có mô hình vật lý phù hợp. Giải thuật tiêu nước đầy đủ cần thêm tín hiệu như `fieldFloodSwitch`, `sumpWaterLevel`, trạng thái hố gom/sump và safety chống drain pump chạy khô; do đó không đưa vào lõi bắt buộc của giai đoạn này.

---

## 4. Yêu cầu chức năng (Functional Requirements)

### 4.1 Luồng thu thập đa cảm biến tại sensor node

Mỗi node chu kỳ **mặc định 30 s** (cấu hình qua `samplingPeriod`) đọc các cảm biến đã gắn: capacitive soil qua ADC, SHT31 và BH1750 qua I²C, optional water level GPIO. Node tính độ ẩm đất thang 0–100 (calibration khô/ướt lưu NVS), **kiểm tra hợp lệ dữ liệu tại node** (xem §4.14), đóng gói cấu trúc nhị phân ≤ 250 byte: header (MAC, version, sequence) + payload (`key:value` rút gọn) + trailer (battery, RSSI, CRC16).

### 4.2 Luồng truyền ESP-NOW node → receiver

Node và receiver pair trước theo MAC (gateway whitelist). Callback `esp_now_send_cb` cho thử lại tối đa **3 lần** với backoff nếu không `ESP_NOW_SEND_SUCCESS`; sau 3 lần fail thì đệm RTC RAM, thử chu kỳ sau. Tùy chọn mã hóa **PMK + LMK** (CCMP/AES-128). Receiver lắng nghe trên channel cố định, không nối Wi-Fi.

### 4.3 Luồng receiver → Raspberry Pi qua UART/SPI

Khung: `0xAA 0x55 | len | mac[6] | payload | crc16`. Pi đọc nonblocking, parse, kiểm CRC, đưa lên hàng đợi nội bộ. UART mức 3.3 V (hoặc USB qua CP2102/CH340).

### 4.4 Luồng Pi ánh xạ đa cảm biến và publish telemetry

Mỗi MAC → một bộ logical device (cấu hình `nodeMap.yaml`): soil moisture → `SI Soil Moisture Sensor`; T/RH/Lux → `Env Sensor Cluster`; trạng thái actuator → device tương ứng. QoS 1, JSON theo chuẩn ThingsBoard. Hỗ trợ batch `[{"ts":…,"values":{…}}]` để replay khi mất mạng.

### 4.5 Luồng water meter theo từng zone

Reference design: mỗi nhánh zone có một flow sensor riêng. Central/Manifold Controller đếm xung từng nhánh và gửi `pulseCounter_Zi` lên Pi. Pi ánh xạ mỗi `pulseCounter_Zi` vào `SI Water Meter` của `SI Field Zi`. `waterConsumption_Zi` (L) = delta `pulseCounter_Zi` / `pulsesPerLiter`.

Chế độ `flowSource`:
- **`simulated_branch`**: SIM mode mô phỏng độc lập từng flow branch; `pump_state` và `flow_pulse_source` tách nhau để inject dry-run/leak.
- **`branch_yfs201`**: mỗi nhánh dùng YF-S201 hoặc sensor Hall tương đương.
- **`total_flow_optional`**: flow tổng sau bơm chỉ dùng cross-check, không thay thế branch flow.

Nguyên tắc: không dùng một `pulseCounter` tổng để phân bổ ảo cho nhiều zone nếu hệ thống cần tưới song song. Mỗi zone phải có `pulseCounter` riêng ở logical layer, dù implementation hiện tại là SIM.

### 4.6 Luồng RPC điều khiển đa actuator

Mỗi logical device (Pump/Valve/Fan/Light/Drain tùy chọn) subscribe RPC. Method chính: `TURN_ON`, `TURN_OFF`; tùy chọn `SET_PWM` cho quạt/đèn. RPC Listener xác thực whitelist, kiểm `commandId`, `timestamp`, `source`, loại bỏ lệnh trùng hoặc quá cũ, rồi chuyển cho Command Dispatcher.

Reference topology không yêu cầu Pi kéo dây relay xa. Pi gửi command qua ESP-NOW tới Central/Manifold Controller đặt gần trạm bơm/van. Central/Manifold Controller kiểm hard interlock cục bộ lần cuối, thực thi relay/driver, gửi ACK và telemetry trạng thái. Nếu Local Safety Handler hoặc Central Controller chặn → trả `{"success": false, "reason": "SAFETY_BLOCK"}`.

### 4.7 Luồng logic điều khiển đa biến cấp field

Rule chain `SI Field Multivar Control` hoặc Local Control Engine chạy khi field cập nhật average moisture (key thực tế trên CoreIoT: `avgMoisture`/`latestAvgMoisture`) hoặc cụm môi trường báo telemetry mới. Độ ẩm đất vẫn là biến chính; nhiệt độ/VPD/ánh sáng là biến điều chỉnh ưu tiên, không phải lúc nào cũng là điều kiện bắt buộc.

Logic đề xuất:
- Nếu `avgMoisture`/`latestAvgMoisture < criticalMoisture` → đưa zone vào hàng đợi tưới tối thiểu, miễn qua safety/interlock.
- Nếu `avgMoisture`/`latestAvgMoisture < minMoistureThreshold` và (`airTemp > 28°C` hoặc `VPD > 1.5 kPa`) → tăng ưu tiên tưới hoặc tăng target water trong giới hạn.
- Nếu `avgMoisture`/`latestAvgMoisture > maxMoistureThreshold` → dừng tưới và khóa auto tạm thời cho zone đó.
- Nếu `avgMoisture`/`latestAvgMoisture > floodMoistureThreshold` trong N mẫu → `WaterloggingRisk`, khóa tưới zone đó.
- **Quạt**: `airHumidity > 80 %` hoặc `airTemp > 32 °C` → `TURN_ON`; hysteresis ±3 %/±2 °C.
- **Đèn**: `lightLux < 200` trong khung giờ canh tác → `TURN_ON`.

`Irrigation Scheduler` gom các zone cần tưới, không vượt `maxConcurrentZones`, mở valve từng zone, bật main pump nếu có ít nhất một valve active, đóng từng zone khi đạt target moisture/maxWaterPerCycle/maxDuration. Mỗi quy tắc có hysteresis và debounce.

### 4.8 Luồng cảnh báo

Bốn lớp:
- **Lõi template**: `Low/Critical Low Moisture`, `High Moisture`, `Low Battery` Warning theo `lowBatteryThreshold` của device.
- **Vận hành mở rộng**: `High Temperature` (airTemp > 35 °C kéo dài), `Low Water Level`, `WaterloggingRisk`, `Node Offline` (Device Inactivity > 5 phút).
- **An toàn (edge)**: dry-run, tưới quá thời lượng, mất cloud — do Local Safety Handler phát, xử lý cục bộ và báo lên cloud.
- **Bất thường (ML)**: `SensorAnomaly` (cảm biến lỗi/trôi), `LeakSuspected`/`ValveLeak` (nghi rò rỉ hoặc valve rò), `ZoneFlowLow` (valve mở nhưng flow thấp) — do Anomaly Detector hoặc Safety Handler phát (§4.15).

Cảnh báo an toàn có **debounce + clear condition + gộp trùng theo entity** để tránh bão alarm.

### 4.9 Luồng dashboard và lập lịch

Dashboard giữ 2 state vận hành chính của template (main/default và field) nhưng có thể bổ sung các sub-state/panel như safety, quality, scheduler reason và central controller. Main state bổ sung lớp marker trạng thái node online/offline và pin trung bình. Field state bổ sung tab "Môi trường" (T/RH/Lux + VPD tức thời) và tab "Vận hành" (nút bật/tắt bơm/quạt/đèn). Lịch tưới giữ cơ chế event `START_IRRIGATION` của template; scheduler đa zone/local scheduler là phần mở rộng trên gateway/rule chain.

### 4.10 Luồng an toàn cục bộ tại edge

Local Safety Handler là tuyến phòng thủ độc lập rule chain cloud. Thiết kế tách hai lớp mode: **Control mode theo field** (`MANUAL`/`AUTO`/`DISABLED`) và **System operation mode** (`NORMAL`/`DEGRADED`/`SAFE-IDLE`). Thứ tự ưu tiên cố định: (1) hard interlock — không gì ghi đè; (2) emergency/manual OFF; (3) manual ON có TTL; (4) auto rule từ cloud; (5) local rule khi DEGRADED; (6) mặc định an toàn → OFF.

- **Watchdog tưới**: bơm ON quá thời lượng tối đa (mặc định 600 s) → ép OFF + alarm.
- **Dry-run cấp bơm**: main pump ON nhưng tổng flow branch gần 0 trong X giây → ép OFF + alarm `PumpDryRun`.
- **Flow thấp theo zone**: valve Zi ON nhưng `flow_Zi` thấp/0 trong X giây → đóng valve Zi + alarm `ZoneFlowLow`.
- **Valve/rò nhánh**: valve Zi OFF nhưng `flow_Zi` vẫn tăng → alarm `ValveLeak`/`LeakSuspected`.
- **Mất cloud → DEGRADED**: mất MQTT > T₁ (mặc định 60 s) → chuyển chế độ DEGRADED (§4.12), **không ép OFF vô điều kiện**; chỉ OFF khi vào SAFE-IDLE.
- **Boot safety**: sau reboot, mọi actuator khởi động OFF, đợi đồng bộ attribute mới vận hành.
- **RPC whitelist + command metadata**: chỉ chấp nhận method đăng ký; RPC lạ, command trùng hoặc timestamp quá cũ bị từ chối có log.
- **Manual TTL/latch**: Manual ON có thời lượng/lượng nước tối đa; Manual OFF có latch để Auto không bật lại ngay.

### 4.11 Luồng cấu hình quản lý node/field/token/MAC

Thêm node mới = (i) nạp firmware qua cáp gắn MAC; (ii) tạo logical device trên CoreIoT, lấy token; (iii) cập nhật `nodeMap.yaml` (MAC → token + field id + key); (iv) gateway reload không cần restart.

### 4.12 Luồng ba chế độ vận hành — tự chủ giảm cấp khi mất cloud

Local Safety Handler quản lý máy trạng thái ba chế độ:
- **NORMAL** (online, MQTT ổn định): cloud rule chain điều phối; Pi thực thi RPC; logic đa biến đầy đủ trên cloud; vẫn đệm telemetry.
- **DEGRADED** (mất MQTT > 60 s, dữ liệu cảm biến hợp lệ, bể còn nước): **Local Control Engine** chạy vòng tưới tối giản theo **ngưỡng cache** (bơm theo min/max moisture; quạt theo ngưỡng đơn; đèn theo lịch cache), **bị chặn bởi mọi khóa cứng**; tiếp tục đệm telemetry để replay.
- **SAFE-IDLE** (bể cạn / dữ liệu field lỗi / lỗi phần cứng / waterlogging nghiêm trọng): tắt actuator của field bị ảnh hưởng; chờ hồi phục; vẫn ghi log + alarm.

Pi **cache** ngưỡng độ ẩm mỗi field, lịch, hạn mức nước, cấu hình an toàn — cập nhật mỗi khi nhận từ cloud, lưu xuống file để sống sót qua reboot. Khi nối lại cloud: resync + replay telemetry + gửi log đã làm trong lúc mất mạng.

### 4.13 Luồng khóa liên động cứng (hard interlock)

Thực thi tại edge, ưu tiên cao nhất, không phụ thuộc cloud, không lệnh nào ghi đè:
- **Bể cạn → cấm bơm**: phao cạn/low-water switch báo cạn là tín hiệu Must → **cấm bật main pump tuyệt đối** + ép OFF nếu đang chạy, hoạt động cả khi mất mạng. Đo `waterLevel` liên tục là Should để hiển thị/ước lượng, không thay thế phao cạn hard interlock.
- **Hạn mức nước**: tổng nước mỗi zone mỗi chu kỳ/ngày vượt trần → khóa tưới zone đó + alarm (Pi tính từ `pulseCounter_Zi`).
- **Waterlogging lock**: `moisture > floodMoistureThreshold` hoặc high moisture kéo dài → khóa tưới zone đó + alarm `WaterloggingRisk`.
- **Watchdog tưới** và **dry-run**: như §4.10.
- **Relay thường-hở**: mất điện Pi / đứt nguồn → cuộn relay nhả → tải OFF (mức phần cứng).

### 4.14 Luồng cổng kiểm tra dữ liệu nhiều tầng

Lọc dữ liệu rác là việc **xác định (deterministic)**, tách khỏi phát hiện bất thường ML:
- **Tầng node ESP**: kiểm dải vật lý/điện của giá trị thô; đọc cờ CRC/status của cảm biến I²C (SHT31, BH1750); phát hiện giá trị đơ lặp (flatline) → đọc lỗi/CRC sai thì bỏ, không gửi rác; clamp/bỏ giá trị bất khả thi; gắn cờ chất lượng. (Tiết kiệm băng thông ESP-NOW.)
- **Tầng gateway (Pi, trong Data Mapper)**: kiểm lại dải/kiểu (không tin node mù quáng); kiểm tốc độ thay đổi (rate-of-change) phi vật lý; chống trùng / giới hạn tần suất → quyết định publish / clamp / drop / gắn cờ. Giá trị rác không publish vào key chính (`moisture`, `airTemp`...), nhưng publish quality telemetry như `<key>_quality`, `<key>_invalid_count`, `<key>_last_invalid_raw`, `last_valid_<key>` để dashboard giải thích được cảnh báo. **Cổng cuối trước khi vào time-series cloud.**

Quy tắc theo khóa (ví dụ): `moisture` 0–100 (nhảy ≤ ~30/mẫu); `airTemp` −10…60 °C; `airHumidity` 0–100 %; `lightLux` 0–~100000; `pulseCounter_Zi` chỉ tăng (monotonic); `battery` 0–100. Flatline không áp dụng chung mọi khóa; chỉ bật theo config cho một số key analog như `airTemp`, `airHumidity`, `moisture`, và tắt/giãn cửa sổ cho `battery`, `lightLux` ban đêm, phao cạn.

### 4.15 Luồng phát hiện bất thường (Anomaly Detector)

Module Python độc lập đọc telemetry từ hàng đợi nội bộ (sau Data Mapper), duy trì thống kê trượt cho từng khóa của từng node, so với baseline và ngưỡng, phát alarm mở rộng lên CoreIoT. **Ngoài vòng điều khiển an toàn** (không tự đóng/mở actuator). Ba loại bất thường:
- **Cảm biến lỗi/trôi**: giá trị ngoài dải, đơ (stuck), spike → z-score/EWMA cửa sổ trượt → `SensorAnomaly`.
- **Node mất kết nối**: kế thừa inactivity timeout → alarm offline.
- **Bất thường thủy lực/rò rỉ**: tương quan trạng thái pump/valve với `pulseCounter_Zi` từng nhánh; valve OFF mà xung tăng → `ValveLeak`/`LeakSuspected`; valve ON mà flow thấp → `ZoneFlowLow`.

Phương pháp: thống kê nhẹ (z-score / EWMA), tùy chọn nâng **Isolation Forest** cho đa biến; tránh deep learning. **Không cần dữ liệu nhãn lớn** — học "bình thường" từ dữ liệu vận hành; chứng minh bằng **bất thường tiêm có kiểm soát** (rút cảm biến, làm chập, đơ giá trị, cho xung branch khi valve OFF, hoặc cho pump ON nhưng branch flow không tăng).

---

## 5. Yêu cầu phi chức năng (Non-Functional Requirements)

Phân loại theo **ISO/IEC 25010:2023** (9 đặc trưng; 2023 đổi Usability → **Interaction Capability**, Portability → **Flexibility**, bổ sung **Safety**).

### 5.1 Functional Suitability

Tuân thủ **đúng hợp đồng** template ở phần lõi: tên device profile, telemetry key (`moisture`, `pulseCounter`, `battery`), RPC method (`TURN_ON`/`TURN_OFF`), asset profile (`SI Field`). Mọi mở rộng nằm trong namespace bổ sung, không ghi đè key lõi. **Dữ liệu vào time-series phải qua cổng kiểm tra** — giá trị ngoài dải/đơ bị loại trước khi publish.

### 5.2 Performance Efficiency

| Đặc tính | Ngưỡng yêu cầu |
|---|---|
| Độ trễ node → Pi qua ESP-NOW | ≤ 200 ms (P95) |
| Độ trễ RPC end-to-end | ≤ 2 000 ms (P95) |
| Chu kỳ telemetry mặc định / tối thiểu | 30 s / 10 s |
| Throughput đồng thời | ≥ 5 node/cụm × 4 key × 1 mẫu/30 s với tỷ lệ mẫu hợp lệ lên cloud ≥ 99% trong điều kiện test; gói mất có sequence/log |
| CPU Pi | < 30 % trung bình, < 70 % đỉnh |
| RAM Pi | < 600 MB cho toàn bộ 11 module |

### 5.3 Reliability

Fault tolerance ở cả ba tầng. Node: retry ESP-NOW 3 lần, buffer khi rớt. Pi: reconnect MQTT backoff lũy thừa, đệm telemetry có timestamp gốc tại Pi ≤ 15 phút và replay khi nối lại; **khi mất cloud vẫn duy trì vận hành ở chế độ DEGRADED** (tự chủ giảm cấp) thay vì dừng hẳn. Cloud: QoS 1; alarm có clear condition. **Recoverability**: sau mọi sự cố, hệ tự khôi phục với actuator khởi động OFF; cấu hình điều khiển cache xuống file để sống qua reboot.

### 5.4 Security

Access token riêng từng device, lưu `0600`, không commit. Whitelist RPC. Khuyến nghị mã hóa ESP-NOW **PMK + LMK** (AES-128-CCMP). Tùy chọn TLS MQTT 8883. Pi tắt SSH password, đổi default password, cập nhật apt.

### 5.5 Maintainability

Kiến trúc **11 module độc lập** có giao diện rõ ràng, thay riêng từng module mà không phá phần còn lại (vd Flow Counter mô phỏng theo branch → flow sensor thật từng nhánh chỉ đổi driver). **Anomaly Detector là module tách rời**, có thể bật/tắt độc lập. Cấu hình tách khỏi mã (yaml/.env). Mã có docstring, README, sơ đồ tuần tự.

### 5.6 Compatibility

Tuân thủ **MQTT 3.1.1** (OASIS): QoS 0/1, keep-alive 60 s, Last Will. Tuân thủ data contract template. Sẵn đường chuyển **Gateway API**. ESP-NOW theo Espressif IDF stable, payload 250 byte, mode `WIFI_STA` cố định channel.

### 5.7 Interaction Capability

Người vận hành chỉ thao tác trên dashboard để xem trạng thái field, đặt ngưỡng, bật/tắt bơm/quạt/đèn, xem cảnh báo. Alarm có severity và mô tả tiếng Việt. Dashboard hiển thị tốt trên desktop và mobile CoreIoT App.

### 5.8 Flexibility (Portability)

**Adaptability**: chạy Pi 4 (chính), thử Pi 3B+. **Scalability**: ≥ 10 sensor/actuator node; 10–20 zone bằng cách chia nhiều Manifold Controller 4–6 zone/cụm; ESP-NOW paired/broadcast và Gateway API là đường mở rộng. **Installability**: script bash/Ansible. **Replaceability**: thay CoreIoT bằng ThingsBoard CE/PE/Cloud chỉ đổi `MQTT_HOST` + import template.

### 5.9 Safety (đặc trưng ISO 25010:2023)

Ngang hàng chức năng tưới. Lượng hóa thành sub-characteristic:
- **Operational constraint**: bơm không ON liên tục quá 600 s; số zone tưới đồng thời không vượt `maxConcurrentZones`; tổng nước mỗi zone/chu kỳ không vượt trần.
- **Risk identification**: dry-run, flow thấp theo zone, valve/rò nhánh, ngập úng (`WaterloggingRisk`), **bể cạn**, mất cloud — phát hiện được.
- **Fail safe**: **mất điện Pi → relay thường-hở → mọi tải OFF**; mất cloud → DEGRADED có giới hạn cứng (không phải tắt mù); bể cạn → cấm bơm tuyệt đối.
- **Hard interlock priority**: thứ tự ưu tiên cố định — hard interlock > lệnh TẮT > lệnh BẬT > rule cục bộ > mặc định OFF; không lệnh nào vượt khóa cứng.
- **Hazard warning**: alarm Critical/Major; chống bão alarm (debounce/clear/dedup).
- **Safe integration**: tách nguồn logic/tải qua JD-VCC; opto PC817; diode flyback cho tải cảm; relay thường-hở; không nối motor trực tiếp GPIO.

### 5.10 Ràng buộc phần cứng/điện

GPIO Pi 3.3 V, không 5 V tolerant; tín hiệu 5 V qua level-shifter. 16 mA/chân, ~50 mA tổng ⇒ không drive tải trực tiếp. Reference topology không kéo dây relay xa từ Pi; relay/driver đặt gần Central/Manifold Controller. ESP-NOW cùng channel receiver. Payload ESP-NOW ≤ 250 byte. Relay **thường-hở** để mất điện = OFF.

### 5.11 NFR về OTA (out-of-scope, thiết kế mở)

NFR-OTA-01: cập nhật firmware node **qua cáp USB**. NFR-OTA-02: bật OTA partition + app rollback. NFR-OTA-03: mỗi node gắn `fw_version`.

---

## 6. Bảng yêu cầu hệ thống FR/NFR

### 6.1 Bảng Functional Requirements

| Mã | Mô tả | Tiêu chí chấp nhận | Thực thể liên quan | MoSCoW | Test case |
|---|---|---|---|---|---|
| FR-01 | Node đọc độ ẩm đất theo chu kỳ | Telemetry `moisture` ≤ 30 s, sai số calibration ≤ ±5 % | Capacitive + ESP32 + `SI Soil Moisture Sensor` | Must | TC-01 |
| FR-02 | Node đọc nhiệt độ + độ ẩm KK | `airTemp`/`airHumidity` ≤ 30 s, lệch ≤ ±0.5 °C / ±3 % RH | SHT31/DHT22 + `Env Sensor Cluster` | Must | TC-10 |
| FR-03 | Node đọc ánh sáng | `lightLux` ≤ 30 s, sai số ≤ ±20 % | BH1750 + `Env Sensor Cluster` | Must | TC-11 |
| FR-04a | Phao cạn hard interlock | `tankLowSwitch` cập nhật ≤ 5 s; khi cạn thì main pump bị cấm bật tuyệt đối | Low-water switch + Central/Manifold Controller + Safety Handler | Must | TC-12, TC-21 |
| FR-04b | Đo mức nước liên tục | `waterLevel`/`tankLevelPct` ≤ 30 s để hiển thị/ước lượng, không thay thế phao cạn | HC-SR04/analog level + `Env Sensor Cluster` | Should | TC-12 |
| FR-05 | Truyền ESP-NOW node/Central → bridge/receiver | Gói từ sensor node và Central/Manifold Controller nhận được, có sequence và retry; chỉ tiêu range là mục tiêu thiết kế trong môi trường giả định | ESP32 node + bridge/receiver | Must | TC-13 |
| FR-06 | Receiver → Pi qua UART | Parse đúng 100 %, CRC fail < 0.1 % | Receiver + Sensor Data Receiver | Must | TC-14 |
| FR-07 | Pi ánh xạ MAC → logical device | Đúng `nodeMap.yaml`; MAC lạ bị từ chối | Data Mapper | Must | TC-15 |
| FR-08 | Publish telemetry lõi | Hiện dashboard Main state ≤ 2 s | Telemetry Client + 3 device lõi | Must | TC-01, TC-02 |
| FR-09 | Publish telemetry mở rộng | Hiện dashboard Field state | Telemetry Client + `Env Sensor Cluster` | Must | TC-10..12 |
| FR-10 | Mô phỏng `pulseCounter` từng nhánh | SIM mode sinh `pulseCounter_Zi` độc lập theo từng branch; có thể inject dry-run/leak bằng cách tách `pump_state` và `flow_pulse_source` | Flow Counter sim + `SI Water Meter` | Must | TC-02, TC-26, TC-32 |
| FR-11 | Flow sensor riêng từng nhánh | Mỗi zone branch có `pulseCounter_Zi` riêng; `waterConsumption_Zi` = delta pulse/pulsesPerLiter | YF-S201/flow sensor branch + Central/Manifold Controller | Must (reference design), SIM allowed | TC-16 |
| FR-12 | RPC TURN_ON/OFF van | Relay đổi ≤ 2 s, telemetry trạng thái cập nhật | `SI Smart Valve` + RPC Listener | Must | TC-03 |
| FR-13 | RPC TURN_ON/OFF main pump | Main pump ON khi có zone active và qua interlock; OFF khi không còn zone active hoặc safety block | `Pump Controller` + Central/Manifold Controller | Must | TC-17 |
| FR-13b | RPC TURN_ON/OFF valve từng zone | Valve Zi mở/đóng theo scheduler/manual; trạng thái cập nhật về dashboard | `Valve Controller` / `SI Smart Valve` | Must | TC-17 |
| FR-14 | RPC TURN_ON/OFF quạt | Như FR-12 | `Fan Controller` | Must | TC-18 |
| FR-15 | RPC TURN_ON/OFF đèn | Như FR-12 | `Light Controller` | Should | TC-19 |
| FR-16 | Tính average moisture cấp field | Bằng trung bình `moisture` sensor con, ≤ 5 s; key CoreIoT dùng `avgMoisture`/`latestAvgMoisture` | `SI Field` + calculated field/rule chain aggregation | Must | TC-04 |
| FR-17 | Tính `waterConsumption` từ delta `pulseCounter_Zi` | Delta từng zone × 1/`pulsesPerLiter` (L) khớp; không dùng flow tổng để chia ảo khi tưới song song | Calculate Delta / Flow Aggregator | Must | TC-02 |
| FR-18 | Propagate ngưỡng độ ẩm | Sensor con nhận attribute đúng | Propagation calculated field | Must | TC-04 |
| FR-19 | Lịch sử độ ẩm | Biểu đồ Field state ≥ 7 ngày | Dashboard + Time-series DB | Must | TC-05 |
| FR-20 | Alarm low battery | `battery < lowBatteryThreshold` ⇒ alarm Warning ≤ 10 s; default nghiệm thu nên đặt 30 nếu muốn thống nhất | Alarm rule lõi | Must | TC-06 |
| FR-21 | Alarm low/high moisture | Theo ngưỡng propagate; có clear | Alarm rule lõi | Must | TC-07 |
| FR-22 | Dừng tưới khi đủ nước | Template gốc dừng theo `durationInMinutes`/`volumeInLitters`; extension v2.2 phải dừng thêm khi `avgMoisture`/`latestAvgMoisture > max` hoặc đạt target/quota ⇒ RPC TURN_OFF tự động | Rule chain Multivar Control / Local Control Engine | Must | TC-08 |
| FR-23 | Alarm high temperature | `airTemp > 35 °C` liên tục 5 phút ⇒ Major | Alarm rule mở rộng | Should | TC-20 |
| FR-24 | **Khóa cứng bể cạn** | `tankLowSwitch=true` ⇒ **cấm bật main pump tuyệt đối tại edge/Central** + ép OFF nếu đang chạy + alarm Critical; hoạt động cả khi mất mạng | Low-water switch + Local Safety Handler + Central/Manifold Controller | Must | TC-21 |
| FR-25 | Alarm node offline | Mất kết nối > 5 phút ⇒ Major (Device Inactivity) | Inactivity timeout | Should | TC-22 |
| FR-26 | Điều khiển quạt theo VPD/RH | Quạt ON khi RH > 80 % hoặc T > 32 °C | Rule chain | Should | TC-23 |
| FR-27 | Điều khiển đèn theo lux + lịch | Đèn ON khi `lightLux < 200` trong giờ canh tác | Rule chain | Could | TC-24 |
| FR-28 | Watchdog tưới | Bơm ON quá 600 s ⇒ tự OFF + log | Local Safety Handler | Must | TC-25 |
| FR-29 | Dry-run / flow thấp theo zone | Main pump ON nhưng tổng branch flow = 0 ⇒ OFF + `PumpDryRun`; valve Zi ON nhưng `flow_Zi` thấp ⇒ đóng Zi + `ZoneFlowLow` | Local Safety Handler + Central/Manifold Controller | Must | TC-26 |
| FR-30 | **Mất cloud → chế độ DEGRADED** | Mất MQTT > 60 s ⇒ chuyển DEGRADED (Local Control Engine theo ngưỡng cache, trong giới hạn cứng), **KHÔNG ép OFF vô điều kiện**; chỉ OFF khi SAFE-IDLE | Local Safety Handler | Must | TC-27 |
| FR-31 | Khôi phục an toàn sau reboot | Mọi actuator khởi động OFF, đồng bộ attribute trước khi vận hành | Pi startup script | Must | TC-28 |
| FR-32 | Cấu hình nodeMap động | Sửa `nodeMap.yaml` ⇒ reload không restart | Pi config watcher | Could | TC-29 |
| FR-33 | Phiên bản hóa firmware node | Mỗi node gửi `fw_version`; cloud lưu shared attribute | ESP32 firmware | Should | TC-30 |
| **FR-34** | **Phát hiện cảm biến lỗi/trôi** | Giá trị out-of-range / đơ / spike ⇒ alarm `SensorAnomaly` lên CoreIoT | Anomaly Detector | Should | TC-31 |
| **FR-35** | **Phát hiện nghi rò rỉ/valve rò** | Valve Zi OFF mà `pulseCounter_Zi` vẫn tăng ⇒ alarm `ValveLeak`/`LeakSuspected` | Anomaly Detector + branch flow | Should | TC-32 |
| **FR-36** | **Local Control Engine (chế độ DEGRADED)** | Khi mất cloud, Pi tưới theo ngưỡng cache (min/max moisture) trong giới hạn cứng; bật/tắt bơm cục bộ | Local Safety Handler / Control Engine | Must | TC-33 |
| **FR-37** | **Cache cấu hình điều khiển** | Pi cache ngưỡng/lịch/hạn mức/cấu hình an toàn, lưu file, sống qua reboot; replay log khi nối lại cloud | Local Safety Handler | Must | TC-33 |
| **FR-38** | **Hạn mức nước mỗi zone** | Tổng nước/zone/chu kỳ/ngày từ `pulseCounter_Zi` vượt trần ⇒ khóa tưới zone đó + alarm | Local Safety Handler | Should | TC-34 |
| **FR-39** | **Kiểm tra dữ liệu tại node** | Node kiểm dải vật lý + CRC/status cảm biến I²C + flatline; bỏ/clamp giá trị xấu, không gửi rác | ESP32 firmware | Should | TC-35 |
| **FR-40** | **Cổng kiểm tra dữ liệu tại gateway** | Pi kiểm lại dải/kiểu + rate-of-change + dedup; giá trị rác không publish vào key chính nhưng publish quality/status/last-invalid | Data Mapper | Must | TC-35 |
| **FR-41** | **Relay thường-hở fail-safe** | Mất điện Pi/đứt nguồn ⇒ relay nhả → tải OFF (mức phần cứng) | Phần cứng relay | Must | TC-36 |
| **FR-42** | **Chống bão alarm** | Alarm an toàn có debounce + clear condition + gộp trùng theo entity | Rule chain + Safety Handler | Should | TC-37 |
| **FR-43** | **Topology main pump + valve/flow theo zone** | Hệ hỗ trợ một main pump cấp manifold, mỗi zone có valve và flow sensor riêng ở logical/reference design | Pump/Valve/Water Meter profiles + Central/Manifold Controller | Must | TC-38 |
| **FR-44** | **Tưới nhiều zone đồng thời có giới hạn** | Scheduler mở tối đa `maxConcurrentZones`; không vượt năng lực cấu hình; zone nào đạt điều kiện dừng thì đóng riêng | Irrigation Scheduler | Must | TC-39 |
| **FR-45** | **Central/Manifold Controller đặt gần trạm nước** | Pi không cần kéo dây relay xa; lệnh actuator đi qua ESP-NOW tới controller; controller ACK và gửi telemetry | ESP32 Central/Manifold Controller | Should | TC-40 |
| **FR-46** | **WaterloggingRisk và khóa tưới zone quá ẩm** | `moisture > floodMoistureThreshold` hoặc high moisture kéo dài ⇒ alarm + khóa tưới zone; nếu đang tưới thì đóng valve zone đó | Safety Handler + Rule Chain | Must | TC-41 |
| **FR-47** | **Drainage actuator hook** | Có profile/telemetry/RPC cho Drain Pump/Drain Valve ở mức thiết kế mở; chưa bắt buộc giải thuật tiêu nước đầy đủ | Drain Controller | Should | TC-42 |
| **FR-48** | **Crop/soil profile dạng threshold preset** | Mỗi field có `criticalMoisture`, `min/target/max/floodMoisture`, `maxWaterPerCycle`, `maxWaterPerDay`, `maxConcurrentZones` theo preset hoặc attribute; nếu cần nhóm thủy lực thì dùng thêm `hydraulicGroupId` | Field attributes | Should | TC-43 |
| **FR-49** | **Phân tách Control mode và System mode** | Field có `MANUAL/AUTO/DISABLED`; hệ có `NORMAL/DEGRADED/SAFE-IDLE`; hai lớp mode không nhập nhằng | Mode Manager | Must | TC-44 |
| **FR-50** | **Manual TTL và Manual OFF latch** | Manual ON tự hết hạn theo thời gian/lượng nước; Manual OFF chặn Auto bật lại trong khoảng cấu hình | RPC Listener + Scheduler | Should | TC-45 |

### 6.2 Bảng Non-Functional Requirements

| Mã | Đặc trưng ISO 25010:2023 | Mô tả | Ngưỡng/Tiêu chí đo | Ưu tiên |
|---|---|---|---|---|
| NFR-01 | Functional Suitability | Tuân thủ hợp đồng key + RPC template | 100 % key lõi đúng tên | Must |
| NFR-02 | Performance Efficiency | Độ trễ ESP-NOW node→Pi | ≤ 200 ms P95 | Must |
| NFR-03 | Performance Efficiency | Độ trễ RPC end-to-end | ≤ 2 000 ms P95 | Must |
| NFR-04 | Performance Efficiency | Throughput ≥ 5 node/cụm | Tỷ lệ mẫu hợp lệ lên cloud ≥ 99% trong điều kiện test; mất gói có sequence/log, không yêu cầu 0 tuyệt đối | Must |
| NFR-05 | Performance Efficiency | CPU/RAM Pi | < 30 % CPU TB, < 600 MB RAM (11 module) | Should |
| NFR-06 | Reliability | Tự reconnect MQTT | Backoff ≤ 60 s, replay buffer ≤ 15 phút | Must |
| NFR-07 | Reliability | ESP-NOW retry | ≤ 3 lần với backoff | Must |
| NFR-08 | Reliability | Recoverability sau mất điện | Phục hồi ≤ 60 s, actuator OFF | Must |
| NFR-09 | Security | Access token mỗi device | chmod 0600, không commit | Must |
| NFR-10 | Security | Whitelist RPC | 100 % method ngoài danh sách bị reject | Must |
| NFR-11 | Security | Mã hóa ESP-NOW | Bật PMK/LMK trong build production | Should |
| NFR-12 | Maintainability | 11 module độc lập | Module có interface rõ; unit test ưu tiên mapper/scheduler/safety/anomaly | Should |
| NFR-13 | Maintainability | Thay SIM branch flow bằng flow sensor thật không phá hợp đồng | Đổi `flowSource` không sửa device profile/CoreIoT mapping | Must |
| NFR-14 | Compatibility | MQTT 3.1.1 QoS 1 keep-alive 60 s LWT | Đúng spec OASIS | Must |
| NFR-15 | Compatibility | Tương thích Gateway API | Có code path `v1/gateway/...` sẵn sàng | Should |
| NFR-16 | Flexibility | Chạy Pi 4 là chính, Pi 3B+ là mục tiêu phụ | Ổn định theo kịch bản test; 24h là mục tiêu triển khai, không phải cam kết nếu chưa validate | Should |
| NFR-17 | Flexibility – Scalability | Hỗ trợ 10–20 zone ở mức kiến trúc | Chia nhiều Manifold Controller 4–6 zone/cụm; không phụ thuộc một board duy nhất | Should |
| NFR-18 | Interaction Capability | Dashboard tiếng Việt, có tooltip | 100 % widget có tooltip | Should |
| NFR-19 | Safety | Watchdog tưới | 600 s mặc định, cấu hình được | Must |
| NFR-20 | Safety | Tách nguồn relay JD-VCC | Dòng GPIO Pi < 10 mA khi 4 relay ON | Must |
| NFR-21 | Safety | Diode flyback cho tải cảm | Tắt motor không reset Pi/ESP32 | Must |
| NFR-22 | Safety | Fail-safe đa actuator | Vào SAFE-IDLE ⇒ tất cả OFF | Must |
| NFR-23 | OTA (thiết kế mở) | OTA partition + rollback + version | — | Must (thiết kế), Won't (triển khai OTA) |
| NFR-24 | Compliance | Đặt tên theo template lõi, log đầy đủ | 100 % alarm gắn type chuẩn | Should |
| **NFR-25** | **Reliability / Safety** | **Tự chủ giảm cấp khi mất cloud** | Mất cloud > 60 s vẫn duy trì tưới tối giản (DEGRADED); khôi phục NORMAL khi nối lại + replay | Must |
| **NFR-26** | **Safety** | **Khóa cứng bể cạn** | Bơm không bao giờ chạy khi bể cạn, kể cả khi mất mạng / cloud yêu cầu BẬT | Must |
| **NFR-27** | **Safety** | **Hạn mức nước mỗi zone** | Trần nước/zone/chu kỳ cấu hình được; vượt là khóa tưới zone | Should |
| **NFR-28** | **Safety** | **Relay thường-hở (fail de-energized)** | Mất điện = mọi tải OFF, không phụ thuộc phần mềm | Must |
| **NFR-29** | **Functional Suitability / Reliability** | **Cổng kiểm tra dữ liệu nhiều tầng** | Giá trị ngoài dải / đơ bị loại ở node + gateway, không vào time-series | Must |
| **NFR-30** | **Maintainability / Safety** | **Anomaly Detector tách rời** | Module ngoài vòng điều khiển an toàn — không tự đóng/mở actuator; bật/tắt độc lập | Must |
| **NFR-31** | **Safety** | **Thứ tự ưu tiên an toàn** | hard interlock > emergency/manual OFF > manual ON có TTL > auto cloud > local rule > mặc định OFF; không lệnh nào vượt khóa cứng | Must |
| **NFR-32** | **Safety / Scalability** | **Giới hạn zone tưới đồng thời** | `maxConcurrentZones` cấu hình được; scheduler không vượt giới hạn | Must |
| **NFR-33** | **Functional Suitability** | **Water statistic theo zone** | Mỗi `SI Field` có `pulseCounter`/`waterConsumption` riêng, không phụ thuộc phân bổ ảo từ flow tổng khi tưới song song | Must |
| **NFR-34** | **Interaction Capability** | **Cảnh báo có giá trị giải thích được** | Giá trị rác không làm bẩn chart chính nhưng dashboard có quality/status/last-invalid để hiểu alarm | Should |
| **NFR-35** | **Safety** | **Waterlogging protection** | Zone quá ẩm bị khóa tưới và có alarm; Drain actuator là mở rộng | Must |
| **NFR-36** | **Maintainability** | **pH out-of-core** | pH không chặn tiến độ lõi; chỉ thêm profile/telemetry khi chuyển sang nutrient/fertigation | Should |

### 6.3 Bộ test case

9 test case lõi (TC-01..TC-09) + mở rộng:
- **TC-10..TC-12**: T/RH, Lux, mực nước.
- **TC-13..TC-15**: ESP-NOW link budget, UART CRC, ánh xạ MAC.
- **TC-16**: Thay flow sim → YF-S201, kiểm `waterConsumption` = pulse/450.
- **TC-17..TC-19**: RPC bơm/quạt/đèn.
- **TC-20..TC-22**: Alarm high temp / low water / node offline.
- **TC-23..TC-24**: Quạt theo VPD, đèn theo lux + lịch.
- **TC-25..TC-28**: Watchdog tưới, dry-run, mất cloud, reboot safety.
- **TC-29..TC-30**: Cấu hình động, phiên bản firmware.
- **TC-31**: Rút/đơ/spike cảm biến → `SensorAnomaly`.
- **TC-32**: Valve Zi OFF mà cho xung `pulseCounter_Zi` tăng → `ValveLeak`/`LeakSuspected`.
- **TC-33**: Ngắt MQTT > 60 s → hệ vào DEGRADED, vẫn tưới theo ngưỡng cache; nối lại → NORMAL + replay.
- **TC-34**: Vượt hạn mức nước zone → khóa tưới + alarm.
- **TC-35**: Bơm giá trị rác (ngoài dải / đơ) ở node và gateway → bị loại, không lên dashboard.
- **TC-36**: Cắt nguồn Pi khi bơm đang chạy → relay nhả, tải OFF.
- **TC-37**: Tạo điều kiện alarm dao động → debounce, không bão alarm; hồi phục → tự clear.
- **TC-38**: Kiểm topology logical main pump + valve/flow theo zone; mỗi zone có `pulseCounter_Zi` riêng.
- **TC-39**: 5 zone cùng yêu cầu tưới, `maxConcurrentZones=3` → scheduler chỉ mở tối đa 3 zone, zone đạt target đóng riêng.
- **TC-40**: Pi gửi command qua ESP-NOW tới Central/Manifold Controller; controller ACK, relay/valve state cập nhật.
- **TC-41**: Ép `moisture > floodMoistureThreshold` nhiều mẫu → `WaterloggingRisk`, khóa tưới zone, đóng valve nếu đang tưới.
- **TC-42**: Drain Controller profile/RPC/telemetry tồn tại ở mức thiết kế mở; chưa bắt buộc chạy bơm thoát thật.
- **TC-43**: Đổi crop/soil profile hoặc field attributes → threshold/hạn mức/scheduler dùng cấu hình mới.
- **TC-44**: Kiểm tách `MANUAL/AUTO/DISABLED` và `NORMAL/DEGRADED/SAFE-IDLE`, không nhập nhằng khi mất cloud.
- **TC-45**: Manual ON hết TTL thì OFF; Manual OFF latch chặn Auto bật lại ngay.

---

## 7. Kết luận

Bản chất yêu cầu của đề tài là **dựng một hệ Smart Farm đa cảm biến – đa cơ cấu chấp hành trên nền Smart Irrigation Template**, không phải bài toán tưới đơn biến. Kiến trúc đúng là **nhiều cụm cảm biến không dây phân tán theo zone + Central/Manifold Controller tại trạm nước**, kết nối qua **ESP-NOW** về một **ESP32 bridge/receiver**, lên **Raspberry Pi 4 edge gateway** chạy 11 module, rồi đẩy lên CoreIoT theo đúng hợp đồng dữ liệu của template. Phương án này thắng so với nối dây cảm biến trực tiếp Pi, so với Wi-Fi/MQTT trực tiếp (kém ổn định AP thưa), và so với LoRa (datarate quá thấp cho điều khiển thời gian thực).

Template được **giữ làm lõi tùy biến** (số lượng device/rule chain/state là mặc định, được mở rộng), với phần lõi gồm ba device profile, `SI Field`, average moisture key thực tế `avgMoisture`/`latestAvgMoisture`, dashboard 2 state vận hành chính kèm sub-state, 5 rule chain SI, alarm độ ẩm/pin theo attribute. **Mở rộng có kỷ luật**: `Env Sensor Cluster`, các profile actuator, VPD, rule chain đa biến, alarm và widget mới. Pi đóng vai **nhiều logical device** qua Direct Device API, sẵn đường lên Gateway API.

Điểm được củng cố mạnh nhất ở bản này là **lớp an toàn và tự chủ**: hệ không chỉ "tắt khi mất mạng" mà **tự chủ giảm cấp** (ba chế độ NORMAL/DEGRADED/SAFE-IDLE) để cây không chết khi mất cloud, đồng thời **không bao giờ vượt khóa liên động cứng** (bể cạn cấm bơm, hạn mức nước, watchdog, relay thường-hở). **Cổng kiểm tra dữ liệu nhiều tầng** (node + gateway, xác định) tách bạch với **phát hiện bất thường bằng ML** (Anomaly Detector mức B — z-score/EWMA/Isolation Forest, ngoài vòng điều khiển an toàn, không cần dữ liệu nhãn lớn). **Water meter thiết kế theo zone** (flow sensor riêng từng nhánh ở reference design, SIM mode mô phỏng độc lập từng `pulseCounter_Zi`) giúp hệ thống tưới nhiều zone đồng thời mà vẫn thống kê nước riêng. `pH` được đưa khỏi lõi hiện tại; **WaterloggingRisk** và khóa tưới zone quá ẩm là phần tối thiểu của “tiêu”, còn Drain Pump/Valve giữ mức Should/Future. **OTA out-of-scope nhưng thiết kế mở**.

Bộ FR (50 mục) và NFR (36 mục) cùng bộ test case (45 trường hợp) truy vết được tới kiểm thử, gắn ưu tiên MoSCoW, đủ chi tiết để chuyển sang giai đoạn thiết kế chi tiết – cài đặt – kiểm thử, và **đã đồng bộ với phương án thiết kế v2.2 đang thảo luận**.

---

## Tài liệu tham khảo

**Smart Irrigation Template, ThingsBoard, CoreIoT**
- ThingsBoard PE — Smart Irrigation solution template: https://thingsboard.io/docs/pe/solution-templates/smart-irrigation/
- ThingsBoard — Calculated Fields (Aggregation, Propagation): https://thingsboard.io/docs/user-guide/calculated-fields/
- ThingsBoard — Device/Asset profiles, Rule Engine, Alarm Rules: https://thingsboard.io/docs/pe/user-guide/
- ThingsBoard — MQTT/HTTP Device API, Gateway API: https://thingsboard.io/docs/reference/
- CoreIoT — tài liệu nền tảng: https://coreiot.io/docs/

**ESP-NOW, ESP32, OTA**
- Espressif — ESP-NOW (giao thức, payload, peer, PMK/LMK): https://www.espressif.com/en/solutions/low-power-solutions/esp-now
- ESP-IDF — ESP-NOW API reference: https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/network/esp_now.html
- ESP-IDF — Over The Air Updates (OTA), app rollback: https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/system/ota.html
- Farm-Data-Relay-System (ESP-NOW + LoRa cho nông nghiệp): https://github.com/timmbogner/Farm-Data-Relay-System

**Cảm biến & phần cứng**
- Sensirion SHT3x datasheet: https://sensirion.com/products/catalog/SHT31-DIS-B
- Capacitive Soil Moisture Sensor: https://how2electronics.com/interface-capacitive-soil-moisture-sensor-arduino/
- YF-S201 Water Flow Sensor (F = 7.5·Q): https://www.tomsonelectronics.com/products/water-flow-sensor-yf-s201
- Raspberry Pi 4 — GPIO specs (3.3 V, 16 mA/chân): https://www.raspberrypi.com/documentation/computers/raspberry-pi.html
- Texas Instruments — ADS1115: https://www.ti.com/product/ADS1115

**Giao thức & chuẩn**
- OASIS — MQTT v3.1.1: https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html
- IEEE Std 830-1998 — SRS: https://standards.ieee.org/ieee/830/1222/
- ISO/IEC 25010:2023 — Product quality model (9 đặc trưng, có Safety): https://www.iso.org/standard/78176.html
- MoSCoW Prioritisation: https://www.agilebusiness.org/dsdm-project-framework/moscow-prioritisation.html

**Cơ sở nông học & phát hiện bất thường**
- FAO-56 Penman–Monteith / Evapotranspiration: https://www.fao.org/3/x0490e/x0490e00.htm
- VPD (Vapor Pressure Deficit): https://everythingcalculators.com/garden-calculators/vapor-pressure-deficit-vpd-calculator/
- scikit-learn — Isolation Forest (anomaly detection): https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html
