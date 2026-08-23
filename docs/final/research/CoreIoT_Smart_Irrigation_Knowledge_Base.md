# CoreIoT và Smart Irrigation - Knowledge Base

Cập nhật: 2026-06-28

Mục đích của tài liệu này là lưu lại hiểu biết đã rà soát về platform CoreIoT và template Smart Irrigation để dùng làm "backup bộ nhớ" khi cần hướng dẫn sử dụng, cấu hình, debug hoặc viết tài liệu kỹ thuật.

Lưu ý quan trọng: các giá trị telemetry/attribute động như độ ẩm, pin, water consumption, irrigation task trong tenant hiện tại phần lớn là dữ liệu khởi tạo/mẫu của template khi tạo solution. Không nên coi các số đó là dữ liệu đo thực tế ngoài hiện trường. Chỉ nên dùng chúng để hiểu key, luồng dữ liệu, quan hệ entity và cách dashboard/rule chain hoạt động.

## 1. CoreIoT - mô hình platform

CoreIoT là một IoT platform theo mô hình tenant, entity, telemetry, attributes, alarms, rule chains và dashboards. Qua UI và JSON export, rule engine dùng các node theo namespace `org.thingsboard.rule.engine...`, nên có thể hiểu cách vận hành rất gần với ThingsBoard rule engine.

Các nhóm chức năng chính thấy trong UI:

- Home
- Alarms
- Dashboards
- Solution templates
- Entities: Devices, Assets, Entity views, Gateways
- Profiles: Device profiles, Asset profiles
- Customers, Users
- Integrations center: Integrations, Data converters
- Rule chains
- Edge management: Instances, Rule chain templates, Integration templates, Converter templates
- Advanced features: OTA updates, Version control, Scheduler
- Resources: Widgets library, Image gallery, SCADA symbols, JavaScript library, Resources library
- Notification center, Mobile center, API usage
- White labeling, Security, Roles, Audit logs, OAuth 2.0

## 2. Các khái niệm nền cần nhớ

### 2.1 Entity

Entity là đối tượng được quản lý trên CoreIoT. Những loại entity quan trọng:

- Device: thiết bị gửi telemetry/attributes, nhận RPC.
- Asset: đối tượng logic/vật lý cấp cao hơn, thường đại diện khu vực, máy móc, field, farm.
- Entity group: nhóm entity để phân quyền, hiển thị, route message hoặc assign edge.
- Customer/User: người dùng và phạm vi truy cập.
- Edge: instance remote/edge để sync dữ liệu giữa edge và cloud.

Trong Smart Irrigation:

- `SI Field` là Asset.
- Soil moisture sensor, smart valve, water meter là Device.
- Field liên kết với sensor/valve/meter bằng relation.

### 2.2 Profile

Profile là cấu hình mặc định cho entity.

Device profile thường chứa:

- Default rule chain.
- Default edge rule chain.
- Transport type.
- Provisioning config.
- Alarm rules.

Asset profile thường chứa:

- Default rule chain.
- Default edge rule chain nếu có.
- Calculated fields nếu có.

Trong Smart Irrigation, device profile là nơi định nghĩa alarm Low Battery, High/Low Moisture, còn rule chain xử lý telemetry/RPC/aggregation.

### 2.3 Telemetry và Attributes

CoreIoT tách dữ liệu thành:

- Telemetry/timeseries: dữ liệu theo thời gian, ví dụ `moisture`, `battery`, `pulseCounter`, `avgMoisture`, `irrigationState`.
- Latest telemetry: giá trị telemetry mới nhất của từng key.
- Server attributes: attribute quản lý ở phía server, ví dụ threshold, location, active flag.
- Client attributes: attribute do device gửi lên.
- Shared attributes: attribute server có thể chia sẻ xuống device.

Trong template này, phần lớn cấu hình quan trọng nằm ở server attributes:

- Field: `cropType`, `minMoistureThreshold`, `maxMoistureThreshold`, `perimeter`.
- Sensor: `latitude`, `longitude`, `lowBatteryThreshold`, `lowBatteryAlarmEnabled`, `minMoistureThreshold`, `maxMoistureThreshold`.
- Valve/meter: `lowBatteryThreshold`, `lowBatteryAlarmEnabled`.

### 2.4 Relations

Relation là cạnh liên kết giữa các entity. Đây là phần rất quan trọng trong Smart Irrigation.

Hướng relation thực tế:

- Field là entity nguồn.
- Sensor/valve/meter là entity đích.

Các relation chính:

- `FieldToMoistureSensor`: Field -> Soil Moisture Sensor
- `FieldToSmartValve`: Field -> Smart Valve
- `FieldToWaterMeter`: Field -> Water Meter

Khi mở tab Relations ở phía device, Outbound thường trống. Phải đổi direction sang inbound/`To` mới thấy Field đang trỏ vào device. Khi đọc rule chain, các node query relation dùng `direction` theo ngữ cảnh originator:

- Từ sensor/meter tìm Field: direction `TO`, relation type tương ứng, entity type `ASSET`.
- Từ Field tìm device: direction `FROM`, relation type tương ứng, entity type `DEVICE`.

### 2.5 Rule chain

Rule chain xử lý message theo luồng:

- Message có `msg`, `metadata`, `msgType`, `originator`.
- Node có thể lưu telemetry/attributes, filter, transform, đổi originator, gọi RPC, aggregate, route sang rule chain khác.
- Device Profile Node kích hoạt alarm rules từ device profile.
- RuleChainInputNode chuyển message sang rule chain khác.

Rule chain là phần lõi để hiểu platform, vì dashboard/scheduler/device data cuối cùng đều đi qua rule chain.

### 2.6 Dashboard

Dashboard gồm:

- Entity aliases: định nghĩa tập entity widget sẽ query.
- States: các màn hình con trong dashboard.
- Widgets: table, map, chart, alarms, markdown/html card.
- Actions: row click, header button, custom dialog, open dashboard state.
- Custom JS/HTML/CSS: nhiều workflow được nhúng trong dashboard, ví dụ tạo field, thêm sensor, tạo scheduler event.

### 2.7 Scheduler

Scheduler tạo event theo thời gian. Event có:

- Name.
- Event/message type.
- Originator.
- Schedule: start time, repeat, timezone, end.
- Message body.
- Metadata nếu có.

Trong Smart Irrigation, scheduler dùng message type `START_IRRIGATION`.

### 2.8 Alarm

Alarm thường được tạo từ alarm rules trong device profile. Alarm có:

- Originator.
- Type.
- Severity.
- Status.
- Start time/duration.
- Assignee.
- Actions: acknowledge, clear.

Trong template này, Low Battery alarm là Warning. Warning được rule chain `SI Count Alarms` gom vào `majorAlarmsCount`.

### 2.9 Edge

Edge management có rule chain templates tương ứng với cloud/core rule chains. Edge Root Rule Chain có thêm node `Push to cloud`, dùng để đồng bộ dữ liệu từ edge lên cloud.

## 3. Smart Irrigation - inventory tổng quan

Template Smart Irrigation gồm:

- Khi tạo từ Solution Templates, CoreIoT sinh sẵn dashboard `Irrigation Management`, 2 field assets, 12 devices mô phỏng và các rule chain/profile liên quan. Các telemetry ban đầu trong tenant là dữ liệu mẫu/simulation của template, không phải dữ liệu đo thực tế.
- Asset profile: `SI Field`
- Asset: `SI Field 1`, `SI Field 2`
- Device profiles:
  - `SI Soil Moisture Sensor`
  - `SI Smart Valve`
  - `SI Water Meter`
- Devices:
  - `SI Soil Moisture 1` đến `SI Soil Moisture 8`
  - `SI Smart Valve 1`, `SI Smart Valve 2`
  - `SI Water Meter 1`, `SI Water Meter 2`
- Rule chains:
  - `SI Soil Moisture`
  - `SI Smart Valve`
  - `SI Water Meter`
  - `SI Field`
  - `SI Count Alarms`
  - `Root Rule Chain`
- Edge rule chain templates:
  - `SI Soil Moisture (Remote Farm)`
  - `SI Smart Valve (Remote Farm)`
  - `SI Water Meter (Remote Farm)`
  - `SI Field (Remote Farm)`
  - `SI Count Alarms (Remote Farm)`
  - `Edge Root Rule Chain`
- Dashboard:
  - `Irrigation Management`
- Scheduler:
  - event type `START_IRRIGATION`

Template này là baseline để bắt đầu, không phải giới hạn cuối. Có thể thêm device profile, devices, relations, rule chains, alarm rules, dashboard widgets/states và gateway logic mới nếu thiết kế v2.2 cần, miễn giữ đúng contract lõi của template.

## 4. Asset profile và assets

### 4.1 Asset profile `SI Field`

Thông tin đã xem:

- Name: `SI Field`
- Description: `Smart Irrigation Field`
- Default rule chain: `SI Field`
- Default edge rule chain: trống/không chọn trong UI.
- Calculated fields: không có.

Ý nghĩa: mọi Field asset dùng profile này sẽ đi vào rule chain `SI Field` khi nhận message.

### 4.2 Asset `SI Field 1`

Thông tin chính:

- Asset profile: `SI Field`
- Label: `Wheat`
- Group: `Smart Irrigation`
- Edge group: `[Edge] Remote Farm R1 All`
- Crop type mẫu: `wheat`
- Moisture thresholds mẫu:
  - `minMoistureThreshold = 25`
  - `maxMoistureThreshold = 75`
- Có polygon `perimeter` để hiển thị trên map.

Relations:

- `FieldToMoistureSensor` -> `SI Soil Moisture 1`, `SI Soil Moisture 2`, `SI Soil Moisture 3`, `SI Soil Moisture 4`
- `FieldToSmartValve` -> `SI Smart Valve 1`
- `FieldToWaterMeter` -> `SI Water Meter 1`

Telemetry mẫu từng thấy:

- `avgMoisture`
- `latestAvgMoisture`
- `battery`
- `irrigationState`
- `irrigationTask`
- `currentIrrigationWaterConsumption`
- `pulseCounter`
- `waterConsumption`

Các giá trị cụ thể của telemetry trong tenant hiện tại chỉ là dữ liệu mẫu khởi tạo, không dùng như số liệu thực.

### 4.3 Asset `SI Field 2`

Thông tin chính:

- Asset profile: `SI Field`
- Label: `Corn`
- Group: `Smart Irrigation`
- Crop type mẫu: `corn`
- Moisture thresholds mẫu:
  - `minMoistureThreshold = 23`
  - `maxMoistureThreshold = 80`
- Có polygon `perimeter` để hiển thị trên map.

Relations:

- `FieldToMoistureSensor` -> `SI Soil Moisture 5`, `SI Soil Moisture 6`, `SI Soil Moisture 7`, `SI Soil Moisture 8`
- `FieldToSmartValve` -> `SI Smart Valve 2`
- `FieldToWaterMeter` -> `SI Water Meter 2`

## 5. Device profiles

### 5.1 `SI Soil Moisture Sensor`

Cấu hình chính:

- Type: Default
- Transport: Default, hỗ trợ MQTT/HTTP/CoAP
- Provisioning: Disabled
- Default rule chain: `SI Soil Moisture`
- Default edge rule chain: `SI Soil Moisture (Remote Farm)`

Alarm rules:

1. `High Moisture Level`
   - Severity: Critical
   - Create condition: `moisture >= Current device.maxMoistureThreshold`
   - Clear condition: `moisture < Current device.maxMoistureThreshold`

2. `Low Moisture Level`
   - Severity: Critical
   - Create condition: `moisture <= Current device.minMoistureThreshold`
   - Clear condition: `moisture > Current device.minMoistureThreshold`

3. `Low Battery`
   - Severity: Warning
   - Create condition: `battery < Current device.lowBatteryThreshold` và alarm enabled flag đúng.
   - Clear condition: `battery > Current device.lowBatteryThreshold` và alarm enabled flag đúng.

Điểm quan trọng: alarm moisture rule đọc threshold từ chính device, nhưng thresholds này được copy từ Field xuống sensor thông qua rule chain/relation.

Lưu ý về Low Battery: Solution Template/SRS thường dùng default mong muốn 30, nhưng tenant thực tế đã thấy soil moisture sensor có `lowBatteryThreshold = 20`, trong khi valve/meter thường là 30. Alarm rule đọc `Current device.lowBatteryThreshold`, nên khi test không nên hardcode 30 nếu chưa đồng bộ attribute.

### 5.2 `SI Smart Valve`

Cấu hình chính:

- Default rule chain: `SI Smart Valve`
- Default edge rule chain: `SI Smart Valve (Remote Farm)`
- Transport: Default
- Provisioning: Disabled

Alarm rule:

- `Low Battery`
- Severity: Warning
- Dựa trên `battery`, `lowBatteryThreshold`, `lowBatteryAlarmEnabled`.

### 5.3 `SI Water Meter`

Cấu hình chính:

- Default rule chain: `SI Water Meter`
- Default edge rule chain: `SI Water Meter (Remote Farm)`
- Transport: Default

Alarm rule:

- `Low Battery`
- Severity: Warning
- Dựa trên `battery`, `lowBatteryThreshold`, `lowBatteryAlarmEnabled`.

### 5.4 Payload mẫu theo template

Các payload dưới đây dùng để hiểu contract thiết bị, không phải dữ liệu đo thật:

Soil moisture sensor:

```json
{
  "battery": 99,
  "moisture": 57
}
```

Water meter:

```json
{
  "battery": 99,
  "pulseCounter": 123000
}
```

Smart valve telemetry:

```json
{
  "battery": 99
}
```

Smart valve RPC từ server xuống device:

```json
{
  "method": "TURN_ON",
  "params": {}
}
```

Tương tự, valve có thể nhận `TURN_OFF`. Khi triển khai gateway/firmware thật, giữ đúng method name `TURN_ON`/`TURN_OFF` để không phá rule chain lõi.

## 6. Devices và dữ liệu cấu hình mẫu

### 6.1 Soil moisture sensors

Danh sách đã thấy:

- `SI Soil Moisture 1`: label `Wheat 1`
- `SI Soil Moisture 2`: label `Wheat 2`
- `SI Soil Moisture 3`: label `Wheat 3`
- `SI Soil Moisture 4`: label `Wheat 4`
- `SI Soil Moisture 5`: label `Corn 1`
- `SI Soil Moisture 6`: label `Corn 2`
- `SI Soil Moisture 7`: label `Corn 3`
- `SI Soil Moisture 8`: label `Corn 4`

Profile:

- Tất cả dùng `SI Soil Moisture Sensor`.

Groups:

- `Smart Irrigation`.
- Một số sensor Wheat đầu tiên có thêm `[Edge] Remote Farm R1 All`.

Server attributes thường có:

- `active = true`
- `criticalAlarmsCount`
- `majorAlarmsCount`
- `inactivityTimeout`
- `lastActivityTime`
- `latitude`
- `longitude`
- `lowBatteryAlarmEnabled = true`
- `lowBatteryThreshold = 20`
- `minMoistureThreshold`
- `maxMoistureThreshold`

Ví dụ đã xác nhận:

- `SI Soil Moisture 1` thuộc Wheat/Field 1:
  - `minMoistureThreshold = 25`
  - `maxMoistureThreshold = 75`
- `SI Soil Moisture 5` thuộc Corn/Field 2:
  - `minMoistureThreshold = 23`
  - `maxMoistureThreshold = 80`

Telemetry keys thường có:

- `moisture`
- `battery`

Các giá trị telemetry hiện có chỉ là dữ liệu mẫu.

### 6.2 Smart valves

Danh sách:

- `SI Smart Valve 1`
- `SI Smart Valve 2`

Profile:

- `SI Smart Valve`

Server attributes thường có:

- `active`
- `criticalAlarmsCount`
- `majorAlarmsCount`
- `inactivityTimeout`
- `lastActivityTime`
- `lowBatteryAlarmEnabled = true`
- `lowBatteryThreshold = 30`

Telemetry keys thường có:

- `battery`

Relation:

- `SI Smart Valve 1` nhận inbound relation `FieldToSmartValve` từ `SI Field 1`.
- `SI Smart Valve 2` nhận inbound relation `FieldToSmartValve` từ `SI Field 2`.

### 6.3 Water meters

Danh sách:

- `SI Water Meter 1`
- `SI Water Meter 2`

Profile:

- `SI Water Meter`

Server attributes thường có:

- `active`
- `criticalAlarmsCount`
- `majorAlarmsCount`
- `inactivityTimeout`
- `lastActivityTime`
- `lowBatteryAlarmEnabled = true`
- `lowBatteryThreshold = 30`

Telemetry keys thường có:

- `battery`
- `pulseCounter`
- `waterConsumption` sau khi rule chain tính delta

Relation:

- `SI Water Meter 1` nhận inbound relation `FieldToWaterMeter` từ `SI Field 1`.
- `SI Water Meter 2` nhận inbound relation `FieldToWaterMeter` từ `SI Field 2`.

## 7. Rule chains - Core

### 7.1 `Root Rule Chain`

Node chính:

- `Device Profile Node`
- `Is Entity Group`
- `Post attributes or RPC request`
- `Duplicate To Group Entities`
- `Message Type Switch`
- `Save Timeseries`
- `Save Attributes`
- `RPC Call Request`
- `Log RPC from Device`
- `Log Other`

Luồng chính:

- Nếu originator là `ENTITY_GROUP`, và msg type là `POST_ATTRIBUTES_REQUEST` hoặc `RPC_CALL_FROM_SERVER_TO_DEVICE`, message được duplicate xuống các entity trong group.
- Nếu không phải entity group, message đi vào switch chuẩn:
  - Post telemetry -> save timeseries
  - Post attributes -> save attributes
  - RPC request to device -> send RPC
  - RPC request from device -> log
  - Other -> log

### 7.2 `SI Soil Moisture`

Mục đích:

- Lưu telemetry/attributes từ soil moisture sensor.
- Kích hoạt alarm rules từ device profile.
- Khi sensor có relation tới field, fetch thresholds từ field và lưu lại vào sensor.
- Khi có telemetry `moisture`, đổi originator sang Field asset, aggregate moisture và gửi tiếp sang `SI Field`.
- Đưa alarm events sang `SI Count Alarms`.

Node quan trọng:

- `Device Profile Node`
- `Message Type Switch`
- `Save Timeseries`
- `Save Client Attributes`
- `Check Field relation`
- `Fetch Moisture Thresholds`
- `Change msg type`
- `Save Attributes`
- `Has Moisture?`
- `To Field Asset`
- `Aggregate Avg`
- `Aggregate Latest Moisture`
- `To Field Rule Chain`
- `Count Alarms`

Luồng thresholds:

1. Message type `Relation Added or Updated`.
2. `Check Field relation` kiểm tra `msg.type == "FieldToMoistureSensor"`.
3. `Fetch Moisture Thresholds` query related asset:
   - relation type `FieldToMoistureSensor`
   - direction `TO`
   - max level 1
   - entity type `ASSET`
4. Fetch:
   - `maxMoistureThreshold`
   - `minMoistureThreshold`
5. `Change msg type` tạo message:
   - `maxMoistureThreshold`
   - `minMoistureThreshold`
   - msgType `POST_ATTRIBUTES_REQUEST`
6. `Save Attributes` lưu thresholds vào sensor server attributes.

Luồng telemetry moisture:

1. Sensor post telemetry.
2. `Save Timeseries` lưu telemetry.
3. `Has Moisture?` kiểm tra key `moisture`.
4. `To Field Asset` đổi originator từ sensor sang Field asset qua `FieldToMoistureSensor`.
5. `Aggregate Avg`:
   - input: `moisture`
   - output: `avgMoisture`
   - function: AVG
   - interval: 5 minutes
   - out msg type: `POST_TELEMETRY_REQUEST`
6. `Aggregate Latest Moisture`:
   - relation: `FieldToMoistureSensor`
   - direction: `FROM`
   - source: latest telemetry `moisture`
   - target: `latestAvgMoisture`
   - aggregate: AVG
   - filter: chỉ sensor có server attribute `active = true`
7. Cả aggregate message đều đi sang `SI Field`.

Alarm routing:

- `Alarm Created`
- `Alarm Cleared`
- `Alarm Severity Updated`
- `Alarm Acknowledged`

Các event này đi sang `SI Count Alarms`.

### 7.3 `SI Field`

Mục đích:

- Là controller trung tâm cho một field.
- Nhận telemetry aggregate moisture từ sensors.
- Nhận message `START_IRRIGATION` từ scheduler/dashboard.
- Lưu irrigation state/task.
- Gửi RPC `TURN_ON`/`TURN_OFF` tới smart valve.
- Nhận water consumption từ water meter và quyết định khi nào dừng tưới.
- Khi field thresholds thay đổi, duplicate thresholds xuống sensors.

Node quan trọng:

- `SwitchEventType`
- `Save telemetry`
- `Start Irrigation Using Volume`
- `To Irrigation Start`
- `Save Timeseries`
- `To Smart Valve`
- `To Turn On RPC call`
- `Start Irrigation`
- `IsMsgFromWaterMeter`
- `Fetch Irrigation State`
- `Is Irrigation On?`
- `calculateIrrigationWaterConsumption`
- `Fetch Task`
- `Should Turn Off?`
- `To Smart Valve`
- `To Turn Off RPC call`
- `Stop Irrigation`
- `To stopped state`
- `Update state`
- `Has Thresholds?`
- `To Attributes Update`
- `To Moisture Sensor`
- `Save Thresholds`
- `Field 1 Water Consumption Simulator`
- `Field 2 Water Consumption Simulator`

#### Start irrigation

Input:

- `msgType = START_IRRIGATION`
- `msg` chứa một trong hai:
  - `volumeInLitters`
  - `durationInMinutes`

`To Irrigation Start` tạo telemetry:

```json
{
  "currentIrrigationWaterConsumption": 0,
  "irrigationState": "ON",
  "irrigationTask": {
    "startTs": "Date.now()",
    "durationThreshold": "durationInMinutes * 60 * 1000 hoặc 0",
    "consumptionThreshold": "volumeInLitters",
    "consumption": 0
  }
}
```

Sau đó:

1. Lưu telemetry lên Field.
2. Đổi originator sang Smart Valve qua relation `FieldToSmartValve`, direction `FROM`.
3. Transform sang RPC:

```json
{
  "method": "TURN_ON",
  "params": {}
}
```

Metadata RPC:

```json
{
  "expirationTime": "Date.now() + 300000",
  "oneway": true,
  "persistent": true
}
```

Msg type:

```text
RPC_CALL_FROM_SERVER_TO_DEVICE
```

#### Theo dõi water consumption và stop irrigation

Input từ water meter sang Field có key:

- `waterConsumption`

Luồng:

1. Field nhận post telemetry có `waterConsumption`.
2. Fetch latest telemetry/attribute `irrigationState`.
3. Nếu `irrigationState == "ON"` thì tiếp tục.
4. `calculateIrrigationWaterConsumption` cộng:
   - x = `msg.waterConsumption`
   - y = timeseries `currentIrrigationWaterConsumption`
   - result = `currentIrrigationWaterConsumption`
5. Fetch `irrigationTask`.
6. `Should Turn Off?`:
   - Nếu `durationThreshold > 0`: stop khi `startTs + durationThreshold < Date.now()`.
   - Nếu không: stop khi `currentIrrigationWaterConsumption >= consumptionThreshold`.
7. Nếu stop:
   - Đổi originator sang Smart Valve.
   - Gửi RPC `TURN_OFF`.
   - Update telemetry Field:

```json
{
  "irrigationTask": {
    "startTs": "...",
    "durationThreshold": "...",
    "consumptionThreshold": "...",
    "consumption": "currentIrrigationWaterConsumption",
    "duration": "Date.now() - startTs"
  },
  "irrigationState": "DONE"
}
```

8. Nếu chưa stop:
   - Chỉ update `irrigationTask.consumption` và `irrigationTask.duration`.

#### Field thresholds update

Khi Field có attributes updated:

1. `Has Thresholds?` kiểm tra message có `maxMoistureThreshold` hoặc `minMoistureThreshold`.
2. `To Attributes Update` tạo message chỉ gồm threshold có trong input.
3. `To Moisture Sensor` duplicate message tới related devices qua relation `FieldToMoistureSensor`, direction `FROM`.
4. `Save Thresholds` lưu vào sensor attributes.

Ý nghĩa: chỉnh threshold trên Field thì sensors trong field được đồng bộ threshold.

#### Water consumption simulator

Rule chain có generator nodes:

- `Field 1 Water Consumption Simulator`
- `Field 2 Water Consumption Simulator`

Mỗi node tạo message mẫu:

```json
{
  "waterConsumption": 60
}
```

Các node này phục vụ demo/template. Không nên coi đó là dữ liệu đo thực tế.

### 7.4 `SI Water Meter`

Mục đích:

- Lưu telemetry/attributes từ water meter.
- Tính delta từ `pulseCounter`.
- Tạo `waterConsumption`.
- Đổi originator sang Field qua relation `FieldToWaterMeter`.
- Gửi message sang `SI Field`.
- Đưa alarm events sang `SI Count Alarms`.

Node chính:

- `Device Profile Node`
- `Message Type Switch`
- `Save Timeseries`
- `Save Client Attributes`
- `Calculate Delta`
- `To Field`
- `To Field Rule Chain`
- `Count Alarms`

`Calculate Delta`:

- input: `pulseCounter`
- output: `waterConsumption`
- use cache: true
- tell failure if delta is negative: true
- exclude zero deltas: false

Luồng:

1. Water meter post telemetry `pulseCounter`.
2. Rule chain save timeseries.
3. Calculate delta -> `waterConsumption`.
4. Change originator to Field via `FieldToWaterMeter`.
5. Send to `SI Field`.

### 7.5 `SI Smart Valve`

Mục đích:

- Lưu telemetry/attributes của valve.
- Kích hoạt alarm rules từ device profile.
- Nhận RPC từ Field.
- Đưa alarm events sang `SI Count Alarms`.

Rule chain này khá chuẩn/đơn giản:

- `Device Profile Node`
- `Message Type Switch`
- `Save Timeseries`
- `Save Client Attributes`
- `RPC Call Request`
- `Log RPC from Device`
- `Log Other`
- `Count Alarms`

Điểm quan trọng: logic bật/tắt valve không nằm trong `SI Smart Valve`. Nó nằm ở `SI Field`, nơi tạo RPC `TURN_ON`/`TURN_OFF` rồi gửi xuống valve.

### 7.6 `SI Count Alarms`

Mục đích:

- Đếm alarm đang active.
- Lưu count thành attributes.
- Propagate count lên asset liên quan.

Node:

- `Count Alarms`
- `Save as attribute`

Mapping:

- `criticalAlarmsCount`
  - severity: `CRITICAL`
  - status: `ACTIVE_UNACK`, `ACTIVE_ACK`
- `majorAlarmsCount`
  - severity: `MAJOR`, `MINOR`, `WARNING`, `INDETERMINATE`
  - status: `ACTIVE_UNACK`, `ACTIVE_ACK`

Config:

- `countAlarmsForPropagationEntities = true`
- `propagationEntityTypes = ["ASSET"]`
- output msg type: `POST_ATTRIBUTES_REQUEST`

Ý nghĩa: alarm Low Battery của device là Warning, nên được tính vào `majorAlarmsCount`; nếu có propagation qua relation, Field cũng nhận count alarm.

## 8. Edge rule chain templates

Edge templates mirror gần như y nguyên core rule chains:

- `SI Soil Moisture (Remote Farm)` giống `SI Soil Moisture`
- `SI Smart Valve (Remote Farm)` giống `SI Smart Valve`
- `SI Water Meter (Remote Farm)` giống `SI Water Meter`
- `SI Field (Remote Farm)` giống `SI Field`
- `SI Count Alarms (Remote Farm)` giống `SI Count Alarms`

Khác biệt quan trọng nằm ở `Edge Root Rule Chain`.

### 8.1 `Edge Root Rule Chain`

Node chính:

- `Device Profile Node`
- `Is Entity Group`
- `Post attributes or RPC request`
- `Duplicate To Group Entities`
- `Message Type Switch`
- `Save Timeseries`
- `Save Client Attributes`
- `RPC Call Request`
- `Log RPC from Device`
- `Log Other`
- `Push to cloud`
- `Push to cloud`

Luồng khác core:

- Post telemetry -> Save Timeseries -> Push to cloud với scope `CLIENT_SCOPE`
- Post attributes -> Save Client Attributes -> Push to cloud với scope `CLIENT_SCOPE`
- Attributes Updated/Deleted -> Push to cloud với scope `SERVER_SCOPE`
- Entity group vẫn duplicate message xuống group entities cho:
  - `POST_ATTRIBUTES_REQUEST`
  - `RPC_CALL_FROM_SERVER_TO_DEVICE`

Ý nghĩa: Edge Root là cầu nối sync dữ liệu từ edge lên cloud. Phần logic irrigation vẫn nằm ở các remote farm rule chain templates tương ứng.

### 8.2 Edge instance `Remote Farm R1`

Trong Solution Template/tenant đã thấy Edge instance `Remote Farm R1` liên quan đến nhóm `[Edge] Remote Farm R1 All`.

Tabs chính trên trang Edge instance:

- Details
- Attributes
- Latest telemetry
- Alarms
- Events
- Downlinks
- Relations
- Audit logs

Những thao tác/nhóm cấu hình quan trọng:

- Manage user groups, asset groups, device groups, entity view groups, dashboard groups.
- Manage scheduler events, rule chains, integrations.
- Sync Edge để đồng bộ lại edge-cloud khi cần.
- Install & Connect Instructions để lấy hướng dẫn cài/kết nối edge.
- Có nút copy Edge Id/key/secret, nhưng không nên lưu lại key/secret trong tài liệu backup hoặc log trao đổi.

Phân biệt thuật ngữ: CoreIoT Edge là thành phần platform để sync giữa edge và cloud; Raspberry Pi gateway trong thiết kế đồ án là ứng dụng gateway/mapper/safety/scheduler do ta xây dựng. Hai lớp này có thể phối hợp, nhưng không được mặc định là một nếu chưa cài CoreIoT Edge thật trên Pi.

## 9. Dashboard `Irrigation Management`

Dashboard export đã đọc:

- Title/name: `Irrigation Management`
- Configuration gồm:
  - `widgets`
  - `states`
  - `entityAliases`
  - `filters`
  - `timewindow`
  - `settings`

### 9.1 Entity aliases

Dashboard có 6 aliases:

1. `Fields`
   - type: `assetType`
   - asset type: `SI Field`
   - resolve multiple: true

2. `Current field`
   - type: `stateEntity`
   - resolve multiple: false

3. `Irrigation schedule`
   - type: `schedulerEvent`
   - event type: `START_IRRIGATION`
   - originator state entity: true

4. `Moisture Sensors`
   - type: `deviceSearchQuery`
   - root state entity: true
   - direction: `FROM`
   - max level: 1
   - device type: `SI Soil Moisture Sensor`

5. `Moisture Sensor Field`
   - type: `assetSearchQuery`
   - root state entity: true
   - direction: `TO`
   - max level: 1
   - asset type: `SI Field`

6. `Current sensor`
   - type: `stateEntity`
   - resolve multiple: false

### 9.2 Dashboard states

Dashboard có 10 states:

1. `default`
   - Name: `Irrigation Management`
   - Root: true
   - Widgets:
     - Fields table
     - Moisture history
     - Map polygons

2. `field`
   - Name: `${entityName}`
   - Widgets:
     - Irrigation schedule table
     - Moisture sensors table
     - Alarm summary
     - Irrigation tasks
     - Irrigation state card
     - Avg moisture chart
     - Daily water consumption chart
     - Statistics
     - Map with sensors and field

3. `setup_field_polygon`
   - Map để tạo/sửa polygon field.

4. `setup_sensor_location`
   - Map để đặt/move sensor location.

5. `moisture_sensor_details`
   - Sensor details card/popover.

6. `moisture_sensor_details_brief`
   - Sensor details brief.

7. `field_alarms`
   - Field alarms table.

8. `moisture_sensor_details_moisture`
   - Sensor moisture statistics.

9. `moisture_sensor_details_alarms`
   - Sensor alarm summary.

10. `sensor_alarms`
   - Sensor alarms table.

### 9.3 Widgets chính

#### Fields table

Datasource: alias `Fields`.

Data keys:

- `name`
- `label`
- `cropType`
- `avgMoisture`
- `minMoistureThreshold`
- `maxMoistureThreshold`
- `irrigationState`

Actions:

- Row click -> open dashboard state `field`, set entity id.
- Header button `Create field`.
- Action buttons:
  - Edit field
  - Delete field

#### Moisture sensors table

Datasource: alias `Moisture Sensors`.

Data keys:

- `name`
- `label`
- `moisture`
- `active`
- `minMoistureThreshold`
- `maxMoistureThreshold`

Actions:

- Header button `Add moisture sensor`.
- Edit moisture sensor.
- Delete moisture sensor.
- Row click -> open `moisture_sensor_details`, set current sensor.

#### Irrigation schedule table

Datasource: alias `Irrigation schedule`.

Data keys:

- `name`
- `schedule` fields:
  - start day
  - start time
  - repeat
  - end day
- `configuration` fields:
  - duration
  - volume

Actions:

- Create irrigation schedule.
- Edit irrigation schedule.
- Delete irrigation schedule.

#### Irrigation Tasks widget

Datasource: alias `Current field`.

Data key:

- `irrigationTask`

Hiển thị:

- Start day
- Start time
- End rule
- Duration
- Volume
- Progress

#### Avg moisture chart

Datasource: alias `Current field`.

Data key:

- `avgMoisture`

#### Daily water consumption chart

Datasource: alias `Current field`.

Data key:

- `waterConsumption`

#### Alarm widgets

Field alarm summary:

- `criticalAlarmsCount`
- `majorAlarmsCount`

Sensor alarm summary:

- `criticalAlarmsCount`
- `majorAlarmsCount`

Actions mở state alarm tương ứng.

### 9.4 Map widgets

#### Main map in default state

Hiển thị field polygons từ alias `Fields`.

Polygon key:

- `perimeter`

Additional keys:

- `name`
- `cropType`
- `avgMoisture`
- `minMoistureThreshold`
- `maxMoistureThreshold`

Label function:

- Hiển thị `${entityName} (${cropType})`.
- Hiển thị `avgMoisture`.
- Nếu `avgMoisture` ngoài ngưỡng min/max thì đổi màu cảnh báo đỏ.

Click polygon:

- Open dashboard state `field`.
- Set current field entity.

#### Field detail map

Hiển thị:

- Sensor markers qua alias `Moisture Sensors`.
- Field polygon.

Sensor marker:

- x key: `latitude`
- y key: `longitude`
- additional keys: `type`, `moisture`, thresholds.
- Click marker mở sensor details popover/state.

#### Setup field polygon map

Cho phép edit polygon:

- Enabled actions: add, edit, move, remove
- Attribute scope: `SERVER_SCOPE`
- Attribute key: `perimeter`

#### Setup sensor location map

Cho phép move sensor marker:

- x key: `latitude`
- y key: `longitude`
- Attribute scope: `SERVER_SCOPE`

### 9.5 Dashboard custom actions

#### Create/Edit field

Dùng services:

- `assetService`
- `entityGroupService`
- `attributeService`

Field form gồm:

- name
- label
- cropType
- minMoistureThreshold
- maxMoistureThreshold

Lưu server attributes:

- `cropType`
- `minMoistureThreshold`
- `maxMoistureThreshold`

Polygon:

- Lưu vào server attribute `perimeter`.

Group:

- Thêm asset vào group `Smart Irrigation`.

#### Add moisture sensor

Dùng services:

- `deviceService`
- `entityRelationService`
- `entityGroupService`
- `attributeService`

Workflow:

1. Tạo device profile/type `SI Soil Moisture Sensor`.
2. Thêm device vào group `Smart Irrigation`.
3. Tạo relation:

```json
{
  "type": "FieldToMoistureSensor",
  "typeGroup": "COMMON",
  "from": "fieldId",
  "to": "sensorEntityId"
}
```

4. Nếu chưa đặt location, lấy `perimeter` của field và tính center polygon.
5. Lưu sensor server attributes:

```json
[
  { "key": "latitude", "value": "polygon center lat" },
  { "key": "longitude", "value": "polygon center lon" }
]
```

Sau khi relation được tạo/cập nhật, rule chain `SI Soil Moisture` sẽ fetch thresholds từ Field và lưu vào sensor.

#### Edit moisture sensor

Cho sửa:

- name
- label
- location

Nếu location thay đổi, lưu server attributes:

- `latitude`
- `longitude`

#### Create/Edit irrigation schedule

Dùng service:

- `schedulerEventService`

Form gồm:

- name
- start date
- start time
- stop irrigation condition:
  - consumption
  - duration
- volume in litters
- duration in minutes
- repeat
- repeat type
- end date

Tạo scheduler event:

```json
{
  "name": "...",
  "type": "START_IRRIGATION",
  "originatorId": "current field entity id",
  "schedule": {
    "timezone": "browser timezone",
    "startTime": "timestamp",
    "repeat": "optional"
  },
  "configuration": {
    "msgType": "START_IRRIGATION",
    "msgBody": {}
  }
}
```

Nếu condition là consumption:

```json
{
  "volumeInLitters": 1000
}
```

Nếu condition là duration:

```json
{
  "durationInMinutes": 30
}
```

Tên key trong template là `volumeInLitters`, giữ nguyên chính tả này vì rule chain đang đọc đúng key đó.

## 10. Scheduler thực tế đã quan sát

Ảnh UI Scheduler cho thấy có event:

- Name: `Evening`
- Event type: `START_IRRIGATION`
- Enable scheduler: enabled
- Originator:
  - mode: Single entity
  - Type: Asset
  - Asset: `SI Field 2`
- Message type: `START_IRRIGATION`
- Message body:

```json
{
  "volumeInLitters": 500
}
```

Ý nghĩa:

1. Đến lịch, Scheduler gửi message `START_IRRIGATION` vào originator `SI Field 2`.
2. Rule chain `SI Field` xử lý message này.
3. Field set `irrigationState = ON`, tạo `irrigationTask` với `consumptionThreshold = 500`.
4. Field gửi RPC `TURN_ON` tới `SI Smart Valve 2`.
5. Water meter gửi `pulseCounter`; rule chain `SI Water Meter` tính `waterConsumption`.
6. Field cộng dồn `currentIrrigationWaterConsumption`.
7. Khi consumption >= 500, Field gửi RPC `TURN_OFF` và set `irrigationState = DONE`.

## 11. Alarms thực tế đã quan sát

Alarm list đang filter Active và For all time.

Có 8 alarms active, đều:

- Type: `Low Battery`
- Severity: `Warning`
- Status: `Active Unacknowledged`
- Assignee: Unassigned

Originators thấy trong list:

- `SI Smart Valve 1`
- `SI Soil Moisture 1`
- `SI Soil Moisture 2`
- `SI Soil Moisture 7`
- `SI Water Meter 2`
- `SI Soil Moisture 5`
- `SI Smart Valve 2`
- `SI Water Meter 1`

Alarm detail đã xem cho `SI Smart Valve 1`:

- Originator: `SI Smart Valve 1`
- Type: `Low Battery`
- Severity: `Warning`
- Status: `Active Unacknowledged`
- Start time: `2026-06-16 17:18:02`
- Duration: hơn 11 ngày tại thời điểm xem
- Actions: `Acknowledge`, `Clear`

Ý nghĩa:

- Các Low Battery alarm này được tạo bởi device profile.
- Vì severity là Warning, chúng được `SI Count Alarms` gom vào `majorAlarmsCount`.
- Chúng không làm dashboard irrigation state thành ON/OFF; irrigation state là telemetry riêng trên Field.

## 12. Dữ liệu mẫu và cách đọc đúng

Nhiều giá trị thấy trong tenant là dữ liệu demo/template:

- `avgMoisture`
- `latestAvgMoisture`
- `battery`
- `pulseCounter`
- `waterConsumption`
- `currentIrrigationWaterConsumption`
- `irrigationTask`
- `irrigationState`
- alarm timestamps

Không nên dùng các số đó làm bằng chứng đo thực tế. Nên dùng chúng để xác định:

- Key telemetry/attribute nào tồn tại.
- Widget nào đọc key nào.
- Rule chain nào tạo/cập nhật key nào.
- Device/asset relation đúng chưa.
- Alarm rule hoạt động theo threshold nào.

## 13. Luồng vận hành tổng thể Smart Irrigation

### 13.1 Setup field

1. Tạo Asset `SI Field`.
2. Set label/crop type.
3. Set moisture thresholds.
4. Vẽ polygon `perimeter`.
5. Field vào group `Smart Irrigation`.

### 13.2 Setup sensors

1. Tạo soil moisture devices.
2. Set profile `SI Soil Moisture Sensor`.
3. Tạo relation Field -> Sensor bằng `FieldToMoistureSensor`.
4. Set location `latitude`, `longitude`.
5. Rule chain tự copy thresholds từ Field xuống sensor.

### 13.3 Setup valve và meter

1. Tạo Smart Valve device, profile `SI Smart Valve`.
2. Tạo Water Meter device, profile `SI Water Meter`.
3. Tạo relations:
   - Field -> Valve: `FieldToSmartValve`
   - Field -> Meter: `FieldToWaterMeter`

### 13.4 Runtime telemetry

Sensor:

- Gửi `moisture`, `battery`.
- Rule chain lưu telemetry, check alarms, aggregate moisture lên Field.

Water meter:

- Gửi `pulseCounter`, `battery`.
- Rule chain tính delta thành `waterConsumption`, đổi originator sang Field.

Valve:

- Gửi `battery`.
- Nhận RPC `TURN_ON`, `TURN_OFF`.

Field:

- Lưu `avgMoisture`, `latestAvgMoisture`.
- Lưu `irrigationState`, `irrigationTask`, `currentIrrigationWaterConsumption`.
- Điều khiển valve qua RPC.

### 13.5 Runtime scheduling

1. Scheduler event `START_IRRIGATION` được enable.
2. Đến giờ, Scheduler gửi message vào Field.
3. Field rule chain bật valve.
4. Meter/simulator tạo water consumption.
5. Field dừng theo volume hoặc duration.

### 13.6 Runtime alarms

1. Device Profile Node chạy alarm rules.
2. Alarm Created/Cleared/Severity Updated/Acknowledged được route sang `SI Count Alarms`.
3. Count được lưu vào attributes:
   - `criticalAlarmsCount`
   - `majorAlarmsCount`
4. Dashboard hiển thị alarm summary.

## 14. Hướng dẫn sử dụng thực dụng

### 14.1 Muốn thêm một field mới

Nên dùng dashboard `Irrigation Management` nếu có thể, vì dashboard đã tự:

- Tạo asset đúng profile/type.
- Thêm vào group `Smart Irrigation`.
- Lưu crop type và thresholds.
- Cho vẽ polygon.

Nếu làm thủ công:

1. Tạo asset profile `SI Field`.
2. Set server attributes:
   - `cropType`
   - `minMoistureThreshold`
   - `maxMoistureThreshold`
   - `perimeter`
3. Đảm bảo default rule chain là `SI Field`.
4. Tạo relations tới sensor/valve/meter.

### 14.2 Muốn thêm sensor vào field

Cần có:

- Device profile `SI Soil Moisture Sensor`.
- Relation từ Field tới Sensor:

```text
Field --FieldToMoistureSensor--> Sensor
```

- Server attributes location:
  - `latitude`
  - `longitude`

Sau khi relation đúng, threshold nên được sync xuống sensor. Nếu không thấy:

- Kiểm tra rule chain `SI Soil Moisture`.
- Kiểm tra message Relation Added or Updated có chạy không.
- Kiểm tra direction relation trong UI.
- Kiểm tra sensor server attributes.

### 14.3 Muốn mô phỏng sensor

Gửi telemetry vào sensor:

```json
{
  "moisture": 36.5,
  "battery": 90
}
```

Kỳ vọng:

- Sensor latest telemetry cập nhật.
- Field `avgMoisture`/`latestAvgMoisture` cập nhật nếu relation đúng.
- Alarm high/low moisture sinh ra nếu moisture vượt threshold.

### 14.4 Muốn mô phỏng water meter

Gửi telemetry vào water meter:

```json
{
  "pulseCounter": 1234
}
```

Kỳ vọng:

- `SI Water Meter` tính delta thành `waterConsumption`.
- Message đổi originator sang Field.
- Nếu Field đang `irrigationState = ON`, Field cộng dồn `currentIrrigationWaterConsumption`.

### 14.5 Muốn start irrigation bằng scheduler

Tạo scheduler event:

```json
{
  "type": "START_IRRIGATION",
  "originator": "SI Field X",
  "configuration": {
    "msgType": "START_IRRIGATION",
    "msgBody": {
      "volumeInLitters": 500
    }
  }
}
```

Hoặc:

```json
{
  "durationInMinutes": 30
}
```

Kỳ vọng:

- Field state chuyển ON.
- Valve nhận RPC `TURN_ON`.
- Khi đạt điều kiện dừng, valve nhận RPC `TURN_OFF`, Field state DONE.

### 14.6 Muốn debug dashboard không hiển thị data

Checklist:

1. Widget đang dùng alias nào?
2. Alias resolve entity đúng không?
3. Entity có đúng profile/type không?
4. Key là attribute hay timeseries?
5. Key nằm ở scope nào?
6. Relation có đúng direction không?
7. Rule chain đã tạo telemetry/attribute đó chưa?
8. Dashboard state có set current entity đúng không?

### 14.7 Muốn debug alarm không lên

Checklist:

1. Device có đúng device profile không?
2. Alarm rule có enable không?
3. Telemetry key có đúng tên không?
4. Threshold server attribute có tồn tại trên device không?
5. Device Profile Node có chạy trong rule chain không?
6. Alarm filter trong UI có đang lọc active/time range sai không?

### 14.8 Muốn debug low battery alarm

Sensor:

- threshold thường là `lowBatteryThreshold = 20`.

Valve/meter:

- threshold thường là `lowBatteryThreshold = 30`.

Kiểm tra:

- latest telemetry `battery`
- server attribute `lowBatteryAlarmEnabled`
- server attribute `lowBatteryThreshold`
- alarm list filter active/all time

### 14.9 Muốn debug irrigation không dừng

Checklist:

1. Field có `irrigationState = ON` không?
2. Field có `irrigationTask` không?
3. `irrigationTask.consumptionThreshold` hoặc `durationThreshold` đúng không?
4. Water meter có gửi `pulseCounter` tăng không?
5. Rule `SI Water Meter` có tạo `waterConsumption` không?
6. Relation `FieldToWaterMeter` đúng không?
7. Field có cập nhật `currentIrrigationWaterConsumption` không?
8. Valve có nhận RPC `TURN_OFF` không?

## 15. Những phần còn chưa đào sâu

Các phần sau chưa phải lõi Smart Irrigation, nhưng nếu muốn hiểu CoreIoT đầy đủ hơn thì nên xem tiếp:

- REST API cụ thể phía sau UI:
  - dashboard API
  - scheduler API
  - rule chain API
  - entity/relation/attribute API
- Device transport payload thực tế qua MQTT/HTTP/CoAP ở mức API chi tiết. Payload mẫu của template đã có ở phần 5.4.
- Integration/Data converters cho thiết bị thật.
- Edge instance assignment/sync ở mức vận hành sâu:
  - sync status chi tiết
  - conflict/offline behavior
  - cách cài CoreIoT Edge thật trên Raspberry Pi nếu chọn hướng đó
- RPC response/timeout khi valve offline hoặc không ack.
- Security/Roles/permissions cho tenant/customer/user.
- OTA updates nếu demo có firmware.
- Version control nếu cần backup/restore config tenant.

## 16. Tóm tắt một câu

Trong Smart Irrigation, `SI Field` là controller trung tâm: sensors gửi moisture để Field aggregate; scheduler gửi `START_IRRIGATION` vào Field; Field bật/tắt valve bằng RPC; water meter gửi pulse để Field tính consumption và quyết định dừng; device profile tạo alarms; `SI Count Alarms` gom alarm count; dashboard là lớp UI workflow để tạo field/sensor/schedule, xem map, xem telemetry và alarm.
