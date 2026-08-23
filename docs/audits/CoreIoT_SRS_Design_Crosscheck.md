# Đối chiếu CoreIoT Knowledge Base với SRS v2.2 và Thiết kế hệ thống v2.2

Ngày rà soát: 2026-06-28

Nguồn đối chiếu hiện được chốt trong workspace:

- CoreIoT/Smart Irrigation knowledge base: `docs/final/research/CoreIoT_Smart_Irrigation_Knowledge_Base.md`
- SRS: `docs/final/requirements/SRS_v2.2.md`
- Thiết kế hệ thống: `docs/final/system_design/RB Thiet_ke_he_thong_v2.2_SmartFarm_an_toan_rebuild.docx`

Trạng thái sau cập nhật ngày 2026-06-28: các điều chỉnh chính đã được áp dụng vào ba artifact chốt nêu trên. Các mục "cần sửa" bên dưới nên được hiểu là audit trail/nguồn quyết định, không còn là toàn bộ backlog pending.

Lưu ý phạm vi: file CoreIoT backup phản ánh template/tenant CoreIoT đã quan sát thực tế. SRS và thiết kế v2.2 không chỉ mô tả template gốc, mà còn mô tả hướng mở rộng thành hệ Smart Farm nhiều zone với Raspberry Pi gateway, ESP-NOW, Central/Manifold Controller, safety và local degraded mode.

Nguyên tắc quan trọng: Smart Irrigation Template là baseline/contract lõi, không phải giới hạn cuối của hệ thống. Những phần template chưa có như device profile mới, telemetry mở rộng, rule chain mới, dashboard widget mới, alarm mới hoặc scheduler logic mới đều có thể bổ sung trên CoreIoT. Vì vậy trong tài liệu này, "gap triển khai" không có nghĩa là phải bỏ khỏi SRS/design; nó chỉ có nghĩa là cần tạo thêm cấu hình/thành phần tương ứng khi đi vào P0/P1/P2.

## 1. Kết luận nhanh

Ba nguồn cơ bản đi cùng hướng: giữ Smart Irrigation Template làm lõi, giữ contract lõi `SI Field`, `SI Soil Moisture Sensor`, `SI Water Meter`, `SI Smart Valve`, telemetry `moisture`/`pulseCounter`/`battery`, RPC `TURN_ON`/`TURN_OFF`, rồi mở rộng có kiểm soát cho hệ nhiều zone.

Tuy nhiên có một số điểm cần chỉnh để tránh sai khi triển khai:

1. SRS/design đang dùng tên logic `averageMoisture`, trong khi template thực tế dùng `avgMoisture` và `latestAvgMoisture`.
2. SRS/design yêu cầu dừng tưới theo moisture/target, nhưng template thực tế chủ yếu dừng theo duration hoặc water consumption từ scheduler task.
3. SRS cố định Low Battery `< 30`, nhưng tenant thực tế có soil moisture sensor đang dùng `lowBatteryThreshold = 20`; valve/meter dùng 30.
4. SRS/design nói dashboard 2 state, còn dashboard export thực tế có 10 states. Có thể hiểu 2 state chính, nhưng nên ghi rõ.
5. SRS có câu template phải tái dựng thủ công theo ThingsBoard PE, trong khi CoreIoT tenant hiện tại có Solution Templates và template đã tạo entity/dashboard/rule chain.
6. SRS/design mở rộng nhiều module chưa tồn tại trong template thực tế: main pump, Central/Manifold Controller, WaterloggingRisk, tankLowSwitch, maxConcurrentZones, local scheduler, quality gate, env sensors, fan/light/drain, manual TTL/latch. Đây là backlog bổ sung trên CoreIoT/gateway, không phải lỗi thiết kế.

## 2. Những điểm khớp tốt

### 2.1 Contract lõi Smart Irrigation khớp

SRS/design và backup đều thống nhất phần lõi:

- Asset profile: `SI Field`.
- Device profiles lõi: `SI Soil Moisture Sensor`, `SI Water Meter`, `SI Smart Valve`.
- Telemetry lõi: `moisture`, `pulseCounter`, `battery`.
- RPC lõi: `TURN_ON`, `TURN_OFF`.
- Scheduler message type: `START_IRRIGATION`.
- Quan hệ field-device:
  - `FieldToMoistureSensor`
  - `FieldToSmartValve`
  - `FieldToWaterMeter`

Đây là nền đúng để giữ template không bị phá khi mở rộng phần cứng.

### 2.2 Mô hình "physical controller gom nhiều zone, CoreIoT tách logical device" là hợp lý

Thiết kế hệ thống nói Central/Manifold Controller vật lý có thể quản lý nhiều zone, nhưng lên CoreIoT vẫn nên publish theo logical device/zone. Điều này khớp với template thực tế: mỗi `SI Field` có sensor, valve, water meter riêng; nếu bên trong Pi có `pulseCounter_Zi` thì khi publish vào `SI Water Meter Zi` vẫn phải dùng key public `pulseCounter`.

Kết luận: không publish `pulseCounter_Zi` trực tiếp vào CoreIoT template lõi. `_Zi` chỉ nên là internal key trong gateway/firmware.

### 2.3 Edge/gateway hướng đi không sai, nhưng cần phân biệt thuật ngữ

Backup có CoreIoT Edge/Remote Farm R1 và edge rule chain templates. SRS/design có Raspberry Pi "edge gateway". Hai khái niệm này có thể phối hợp, nhưng không tự động là một:

- CoreIoT Edge: thành phần platform sync rule chain/entity/data giữa edge và cloud.
- Raspberry Pi gateway: ứng dụng gateway/mapper/safety/scheduler do đề tài xây dựng.

Cần ghi rõ Pi có chạy CoreIoT Edge thật hay chỉ là gateway MQTT/ESP-NOW tự viết. Nếu không ghi, người đọc có thể hiểu nhầm "edge" trong hai lớp này là cùng một thứ.

## 3. Mâu thuẫn hoặc lệch tên cần sửa

### 3.1 `averageMoisture` vs `avgMoisture` / `latestAvgMoisture`

SRS và design dùng `averageMoisture` như calculated field cấp field. Template thực tế trong backup/dashboard/rule chain dùng:

- `avgMoisture`
- `latestAvgMoisture`

Ảnh hưởng:

- Firmware/gateway nếu chờ đọc hoặc publish `averageMoisture` sẽ không khớp dashboard hiện tại.
- Test case nếu assert `averageMoisture` có thể fail dù template đang chạy đúng.
- Dashboard field table/history hiện đang dựa vào `avgMoisture`, không phải `averageMoisture`.

Khuyến nghị:

- Trong SRS/design, sửa thành: "logical concept average moisture, actual CoreIoT key: `avgMoisture`/`latestAvgMoisture`".
- Hoặc nếu muốn chuẩn hóa theo `averageMoisture`, phải sửa rule chain + dashboard export đồng bộ. Không nên làm nếu mục tiêu là tận dụng template sẵn.

### 3.2 Dừng tưới theo moisture chưa có trong template thực tế

SRS/design yêu cầu:

- Nếu moisture/average moisture vượt target/max thì dừng tưới.
- WaterloggingRisk thì khóa tưới zone và đóng valve nếu đang tưới.

Template thực tế trong backup cho thấy `SI Field` start irrigation bằng `START_IRRIGATION`, tạo `irrigationTask`, bật valve bằng `TURN_ON`, rồi dừng chủ yếu khi:

- đạt `durationInMinutes`, hoặc
- `waterConsumption >= volumeInLitters`.

Moisture hiện được aggregate và dùng cho dashboard/alarm, nhưng chưa thấy nó là điều kiện stop irrigation trực tiếp trong rule chain template gốc.

Ảnh hưởng:

- FR-22 trong SRS chưa đúng với tenant hiện tại nếu hiểu là "đã có sẵn".
- Nếu demo chỉ dùng template gốc, tưới có thể không tự dừng vì moisture đã đủ, trừ khi có duration/volume threshold.

Khuyến nghị:

- Ghi rõ FR-22 là phần mở rộng cần implement trong `SI Field Multivar Control` hoặc Local Control Engine trên Pi.
- Giữ template gốc làm P0/P1: stop theo volume/duration.
- P2/P4 mới thêm stop theo target/max moisture, waterlogging, quota, watchdog.

### 3.3 Low Battery threshold không thống nhất

SRS ghi Low Battery Warning `< 30`. Solution/template instruction cũng có xu hướng nói ngưỡng 30. Nhưng backup tenant thực tế ghi:

- Soil moisture sensor: có trường hợp `lowBatteryThreshold = 20`.
- Smart valve: `lowBatteryThreshold = 30`.
- Water meter: `lowBatteryThreshold = 30`.

Quan trọng hơn: alarm rule thực tế không hardcode 30, mà đọc `Current device.lowBatteryThreshold`.

Ảnh hưởng:

- Test `battery < 30` có thể không sinh alarm với soil sensor nếu attr đang là 20.
- SRS "Warning <30" chỉ đúng nếu ta set attr của tất cả device về 30.

Khuyến nghị:

- SRS nên viết: "Low Battery tạo theo `lowBatteryThreshold`; acceptance mặc định đặt 30 trừ khi profile/device có cấu hình khác".
- Trước demo/test, đồng bộ server attribute soil sensors về 30 nếu muốn đúng SRS.
- Nếu giữ 20 cho soil sensor, test case phải đọc threshold từ device attr thay vì hardcode.

### 3.4 Dashboard "2 state" là cách nói chưa đủ chính xác

SRS/design nói dashboard 2 state: main state và field state. Backup từ dashboard export cho thấy dashboard thực tế có 10 states:

- `default`
- `field`
- `setup_field_polygon`
- `setup_sensor_location`
- `moisture_sensor_details`
- `moisture_sensor_details_brief`
- `field_alarms`
- `moisture_sensor_details_moisture`
- `moisture_sensor_details_alarms`
- `sensor_alarms`

Ảnh hưởng:

- Nếu chấm tài liệu dựa trên export thật, câu "2 state" có thể bị xem là thiếu.

Khuyến nghị:

- Sửa thành: "2 luồng/state vận hành chính (`default`, `field`), kèm các sub-states chi tiết/chỉnh sửa/alarm trong dashboard export".

### 3.5 Câu "template phải tái dựng thủ công" đã lệch với trạng thái tenant hiện tại

SRS viết Smart Irrigation Template cần được tái dựng thủ công dựa trên đặc tả ThingsBoard PE. Nhưng CoreIoT tenant hiện tại có tab Solution Templates, có Smart Irrigation instructions, và template đã tạo dashboard/entity/rule chain/devices mẫu.

Ảnh hưởng:

- Làm tài liệu có vẻ không cập nhật với môi trường CoreIoT đang dùng.
- Dễ khiến kế hoạch triển khai bị nặng hơn thực tế.

Khuyến nghị:

- Sửa thành: "Trong tenant hiện tại có thể tạo/import từ CoreIoT Solution Templates; nếu triển khai trên tenant không có template hoặc chuyển sang ThingsBoard CE/PE khác thì cần tái dựng/import thủ công".

### 3.6 `SI Customer Alarm Routing` không thấy trong template thực tế

SRS nhắc alarm mở rộng định tuyến theo rule chain `SI Customer Alarm Routing`. Trong backup thực tế, chain đã quan sát là:

- `SI Count Alarms`
- `SI Soil Moisture`
- `SI Smart Valve`
- `SI Water Meter`
- `SI Field`
- Root/Edge root chains

Không thấy `SI Customer Alarm Routing` trong template backup.

Ảnh hưởng:

- Nếu tài liệu nói "kế thừa" chain này thì không có bằng chứng từ tenant hiện tại.

Khuyến nghị:

- Nếu chỉ nói lõi template, dùng `SI Count Alarms`.
- Nếu muốn có alarm routing mở rộng, ghi là "rule chain mở rộng cần tạo mới", không gọi là kế thừa nếu chưa có.

### 3.7 `maxConcurrentZones` vs `maxConcurrentGroup`

SRS dùng `maxConcurrentZones` ở nhiều nơi, nhưng FR-48 có `maxConcurrentGroup`. Design cũng có chỗ dùng `maxConcurrentZones` hoặc `maxConcurrentGroup`.

Ảnh hưởng:

- Dễ sai key config khi code scheduler.

Khuyến nghị:

- Chọn một key chuẩn: `maxConcurrentZones`.
- Nếu cần group thủy lực, dùng key khác rõ nghĩa, ví dụ `hydraulicGroupId` hoặc `maxConcurrentZonesPerGroup`.

## 4. Gap triển khai mở rộng so với template hiện tại

Các mục dưới đây không phải mâu thuẫn nếu ta xem SRS/design là target v2.2. Chúng chỉ chưa có sẵn trong template CoreIoT backup, nên phải được đưa vào kế hoạch implement bằng cách thêm device profile, asset/device, relation, rule chain, dashboard widget, alarm rule, scheduler logic hoặc gateway module tương ứng.

### 4.1 Multi-zone scheduler và global arbitrator

Template gốc xử lý từng field/scheduler event riêng. SRS/design yêu cầu:

- chọn nhiều zone cần tưới;
- mở tối đa `maxConcurrentZones`;
- bật main pump khi có ít nhất một valve active;
- đóng riêng từng zone khi đạt target/hạn mức/lỗi flow.

Gap:

- Template chưa có global scheduler/arbitrator nhìn toàn bộ zone và pump chung.
- CoreIoT Scheduler hiện gửi `START_IRRIGATION` vào từng field, không tự quản lý năng lực bơm/áp lực toàn hệ.

Nơi nên implement:

- Pi Local Irrigation Scheduler là chính.
- Cloud/CoreIoT chỉ giữ dashboard, config, event đề xuất/lệnh start.
- Nếu muốn làm trên CoreIoT cloud, cần rule chain mới có state toàn cục, tránh race condition giữa nhiều field.

### 4.2 Main pump và Central/Manifold Controller

Template gốc có `SI Smart Valve` nhưng không có main pump/controller vật lý trung tâm.

SRS/design yêu cầu:

- Pump Controller.
- Central/Manifold Controller.
- valve state từng zone.
- flow từng branch.
- ACK command từ Central.
- low-water hard interlock tại hiện trường.

Gap:

- Cần tạo logical devices/profiles mở rộng hoặc gom telemetry trong một Central Controller.
- Cần RPC dispatcher trên Pi để chuyển lệnh CoreIoT xuống ESP-NOW.

### 4.3 Safety layer chưa có trong template gốc

Template gốc có alarm low/high moisture và low battery. SRS/design yêu cầu thêm:

- `tankLowSwitch`
- hard interlock bể cạn
- watchdog tưới
- dry-run
- `ZoneFlowLow`
- `ValveLeak` / `LeakSuspected`
- quota nước theo cycle/day
- `WaterloggingRisk`
- `SAFE-IDLE` / `DEGRADED`

Gap:

- Các khóa cứng không nên chỉ nằm ở cloud rule chain; phải có ở Pi/Central.
- CoreIoT chỉ nên nhận alarm/status và hiển thị.

### 4.4 Data quality gate và alarm giải thích được

SRS/design yêu cầu giá trị rác không publish vào key chính, nhưng vẫn publish quality/status/last-invalid để dashboard giải thích alarm.

Template hiện tại chưa có các key/quality panel này.

Gap:

- Gateway cần quyết định drop/clamp/flag trước khi publish `moisture`, `pulseCounter`, `battery`.
- Dashboard cần thêm widget hoặc table để xem `quality_*`, `lastInvalid_*`, `dropReason`, `seqGap`.

### 4.5 Env Sensor Cluster, VPD, fan/light/drain là mở rộng hoàn toàn

Template gốc không có:

- `Env Sensor Cluster`
- `airTemp`
- `airHumidity`
- `lightLux`
- VPD/etoIndex
- Fan Controller
- Light Controller
- Drain Controller

Gap:

- Cần profiles, telemetry contract, dashboard widgets, alarm rules và mapping trên Pi.
- Không ghi đè key lõi của Smart Irrigation; nên tách namespace/key mở rộng.

### 4.6 Manual TTL, Manual OFF latch, Emergency OFF

Dashboard template hiện tại có workflow scheduler và edit field/sensor, nhưng chưa có đầy đủ:

- Manual ON TTL.
- Manual OFF latch.
- Emergency OFF.
- mode `MANUAL/AUTO/DISABLED`.

Gap:

- Cần thêm UI control và Local Safety Handler enforce cuối cùng.

## 5. Thiếu trong file CoreIoT backup hiện tại

File backup hiện khá đầy đủ về entity/profile/rule chain/dashboard/scheduler/alarm. Tuy nhiên so với những gì đã đọc thêm ở Solution Template và Edge tab, nên bổ sung:

1. Payload mẫu chính thức:
   - Soil moisture: `battery`, `moisture`.
   - Water meter: `battery`, `pulseCounter`.
   - Smart valve: `battery`, RPC `TURN_ON`/`TURN_OFF`.
2. Ghi chú Solution Template tạo sẵn:
   - dashboard `Irrigation Management`;
   - 2 field assets;
   - 12 devices mô phỏng;
   - rule chains core và remote farm.
3. Edge instance `Remote Farm R1`:
   - các tab Details/Attributes/Latest telemetry/Alarms/Events/Downlinks/Relations/Audit logs;
   - các action sync/manage groups/rule chains/scheduler events;
   - không lưu/chép Edge key/secret.
4. Ghi chú lệch threshold:
   - template/SRS nói 30;
   - tenant soil sensor đang thấy 20;
   - rule alarm đọc attribute, không hardcode.

## 6. Việc nên sửa trước khi triển khai/code

Ưu tiên P0 - sửa tài liệu để không sai key:

1. Sửa `averageMoisture` thành "actual keys `avgMoisture`/`latestAvgMoisture`" trong SRS/design.
2. Sửa dashboard "2 state" thành "2 state chính + sub-states".
3. Sửa Low Battery thành threshold cấu hình, default target 30.
4. Sửa câu template tái dựng thủ công thành có hai đường: dùng CoreIoT Solution Templates hoặc tái dựng/import nếu môi trường không có.
5. Chuẩn hóa `maxConcurrentZones`, bỏ/định nghĩa lại `maxConcurrentGroup`.

Ưu tiên P1/P2 - chốt ranh giới template vs mở rộng:

1. Ghi rõ template gốc dừng theo `durationInMinutes`/`volumeInLitters`; dừng theo moisture/WaterloggingRisk là extension.
2. Ghi rõ CoreIoT Scheduler event và Pi Local Irrigation Scheduler là hai lớp khác nhau.
3. Ghi rõ CoreIoT Edge instance và Raspberry Pi gateway là hai khái niệm khác nhau, chỉ trùng nếu ta cài CoreIoT Edge thật trên Pi.

Ưu tiên P2+ - backlog triển khai:

1. Pi gateway mapping `pulseCounter_Zi` -> per-zone logical `SI Water Meter` key `pulseCounter`.
2. Local safety: `tankLowSwitch`, watchdog, dry-run, quota, waterlogging lock.
3. Multi-zone scheduler with `maxConcurrentZones`.
4. Central/Manifold Controller command ACK and state telemetry.
5. Dashboard extension: safety panel, scheduler reason, quality panel, central controller panel, manual TTL/latch.

## 7. Kết luận

Không có mâu thuẫn nền tảng làm hỏng hướng thiết kế. Vấn đề chính là SRS/design đang trộn ba lớp:

1. Template CoreIoT thực tế đã có.
2. Khái niệm lõi Smart Irrigation phải giữ đúng.
3. Mục tiêu mở rộng Smart Farm v2.2 chưa có sẵn và phải implement thêm.

Nếu chỉnh các key/tên gọi và ghi rõ ranh giới "template có sẵn" vs "extension cần thêm", bộ tài liệu sẽ nhất quán hơn nhiều và đủ tốt để chuyển sang roadmap triển khai. Các extension chưa có trong template hiện tại vẫn hoàn toàn có thể thêm vào CoreIoT, miễn giữ đúng contract lõi để dashboard/rule chain gốc không bị phá.
