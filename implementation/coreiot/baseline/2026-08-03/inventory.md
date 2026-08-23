# CoreIoT baseline inventory — 2026-08-03

> Raw exports are rollback references. KEEP/MODIFY/REPLACE/REMOVE applies to the v2.2 target configuration.

## Artifact summary

| File | Kind | Name | SHA-256 |
|---|---|---|---|
| `irrigation_management_dashboard.json` | dashboard | Irrigation Management | `d2a4b1ce88814f8c2910bb5d27a0403a02dfd978981bbf503901e512db3574b0` |
| `root_rule_chain.json` | rule_chain | Root Rule Chain | `a46f26d5f81685b66f4339872436464de6ce6d09ef0981ced6f99700f99c6327` |
| `si_count_alarms.json` | rule_chain | SI Count Alarms | `71147f6286ef88e3be40f42b1eafb7aec1844fcf617778d6e43ee338a81b2a92` |
| `si_field_asset_profile.json` | asset_profile | SI Field | `0c423c10ac5af5f4b58c35d34d29bf4d0c3ff2ac79e240d1bde398f5abad2c16` |
| `si_field_rule_chain.json` | rule_chain | SI Field | `276dff5f55ac8ee1e0ed75385f94a02450cc6ee10d10c31bef505a989e8deb69` |
| `si_smart_valve_device_profile.json` | device_profile | SI Smart Valve | `5bda5b8453ca5dfcf2793754ff83649f5719f20ef3f7a9c2127d9c7793d87bcd` |
| `si_smart_valve_rule_chain.json` | rule_chain | SI Smart Valve | `af0bc870d80e7ac75c686f5cdafe0cbff91a52fe6fa9d8c60b3e0edffc46b1ff` |
| `si_soil_moisture_device_profile.json` | device_profile | SI Soil Moisture Sensor | `8774378b0652075b91337df114b9011bcb1cc0df93198b880109e95b3a618157` |
| `si_soil_moisture_rule_chain.json` | rule_chain | SI Soil Moisture | `6f6f9642a5e586ca562225c4ea13f72787b96d5813186f72360d8df70d1e9ac8` |
| `si_water_meter_device_profile.json` | device_profile | SI Water Meter | `bfcefc8c5ddacccde6c119fa6f62f619eec713b68e6e60092df3f35225723455` |
| `si_water_meter_rule_chain.json` | rule_chain | SI Water Meter | `f4990b56fc950633a154802b549ec2cb852d3c1cf2e2024e9e445a131941a773` |

## Root Rule Chain

Nodes: 10; connections: 11.

| # | Node | Type | Decision | Reason |
|---:|---|---|---|---|
| 0 | Save Timeseries | `TbMsgTimeseriesNode` | **KEEP** | Giữ routing chuẩn của platform; chỉ mở rộng khi profile mới yêu cầu. |
| 1 | Save Attributes | `TbMsgAttributesNode` | **KEEP** | Giữ routing chuẩn của platform; chỉ mở rộng khi profile mới yêu cầu. |
| 2 | Message Type Switch | `TbMsgTypeSwitchNode` | **KEEP** | Giữ routing chuẩn của platform; chỉ mở rộng khi profile mới yêu cầu. |
| 3 | Log RPC from Device | `TbLogNode` | **KEEP** | Giữ routing chuẩn của platform; chỉ mở rộng khi profile mới yêu cầu. |
| 4 | Log Other | `TbLogNode` | **KEEP** | Giữ routing chuẩn của platform; chỉ mở rộng khi profile mới yêu cầu. |
| 5 | RPC Call Request | `TbSendRPCRequestNode` | **KEEP** | Giữ routing chuẩn của platform; chỉ mở rộng khi profile mới yêu cầu. |
| 6 | Is Entity Group | `TbOriginatorTypeFilterNode` | **KEEP** | Giữ routing chuẩn của platform; chỉ mở rộng khi profile mới yêu cầu. |
| 7 | Post attributes or RPC request | `TbMsgTypeFilterNode` | **KEEP** | Giữ routing chuẩn của platform; chỉ mở rộng khi profile mới yêu cầu. |
| 8 | Duplicate To Group Entities | `TbDuplicateMsgToGroupNode` | **KEEP** | Giữ routing chuẩn của platform; chỉ mở rộng khi profile mới yêu cầu. |
| 9 | Device Profile Node | `TbDeviceProfileNode` | **KEEP** | Giữ routing chuẩn của platform; chỉ mở rộng khi profile mới yêu cầu. |

## SI Count Alarms

Nodes: 2; connections: 1.

| # | Node | Type | Decision | Reason |
|---:|---|---|---|---|
| 0 | Count Alarms | `TbAlarmsCountNodeV2` | **MODIFY** | Mở rộng đếm Warning và alarm an toàn v2.2. |
| 1 | Save as attribute | `TbMsgAttributesNode` | **MODIFY** | Mở rộng đếm Warning và alarm an toàn v2.2. |

## SI Field

Nodes: 30; connections: 31.

| # | Node | Type | Decision | Reason |
|---:|---|---|---|---|
| 0 | SwitchEventType | `TbMsgTypeSwitchNode` | **MODIFY** | Refactor vào orchestration v2.2 với mode, safety, target và quota. |
| 1 | Save telemetry | `TbMsgTimeseriesNode` | **MODIFY** | Refactor vào orchestration v2.2 với mode, safety, target và quota. |
| 2 | To Irrigation Start | `TbTransformMsgNode` | **MODIFY** | Refactor vào orchestration v2.2 với mode, safety, target và quota. |
| 3 | IsMsgFromWaterMeter | `TbCheckMessageNode` | **MODIFY** | Refactor vào orchestration v2.2 với mode, safety, target và quota. |
| 4 | Start Irrigation Using Volume | `TbMsgTypeFilterNode` | **MODIFY** | Refactor vào orchestration v2.2 với mode, safety, target và quota. |
| 5 | Save Timeseries | `TbMsgTimeseriesNode` | **MODIFY** | Refactor vào orchestration v2.2 với mode, safety, target và quota. |
| 6 | Start Irrigation | `TbSendRPCRequestNode` | **MODIFY** | Refactor vào orchestration v2.2 với mode, safety, target và quota. |
| 7 | To Smart Valve | `TbChangeOriginatorNode` | **MODIFY** | Refactor vào orchestration v2.2 với mode, safety, target và quota. |
| 8 | To Turn On RPC call | `TbTransformMsgNode` | **MODIFY** | Refactor vào orchestration v2.2 với mode, safety, target và quota. |
| 9 | calculateIrrigationWaterConsumption | `TbMathNode` | **MODIFY** | Refactor vào orchestration v2.2 với mode, safety, target và quota. |
| 10 | Fetch Task | `TbGetAttributesNode` | **MODIFY** | Refactor vào orchestration v2.2 với mode, safety, target và quota. |
| 11 | Should Turn Off? | `TbJsFilterNode` | **MODIFY** | Refactor vào orchestration v2.2 với mode, safety, target và quota. |
| 12 | Stop Irrigation | `TbSendRPCRequestNode` | **MODIFY** | Refactor vào orchestration v2.2 với mode, safety, target và quota. |
| 13 | To Smart Valve | `TbChangeOriginatorNode` | **MODIFY** | Refactor vào orchestration v2.2 với mode, safety, target và quota. |
| 14 | To Turn Off RPC call | `TbTransformMsgNode` | **MODIFY** | Refactor vào orchestration v2.2 với mode, safety, target và quota. |
| 15 | To stopped state | `TbTransformMsgNode` | **MODIFY** | Refactor vào orchestration v2.2 với mode, safety, target và quota. |
| 16 | Update state | `TbTransformMsgNode` | **MODIFY** | Refactor vào orchestration v2.2 với mode, safety, target và quota. |
| 17 | Save Timeseries | `TbMsgTimeseriesNode` | **MODIFY** | Refactor vào orchestration v2.2 với mode, safety, target và quota. |
| 18 | Fetch Irrigation State | `TbGetAttributesNode` | **MODIFY** | Refactor vào orchestration v2.2 với mode, safety, target và quota. |
| 19 | Is Irrigation On? | `TbJsFilterNode` | **MODIFY** | Refactor vào orchestration v2.2 với mode, safety, target và quota. |
| 20 | To Moisture Sensor | `TbDuplicateMsgToRelatedNode` | **MODIFY** | Refactor vào orchestration v2.2 với mode, safety, target và quota. |
| 21 | Save Thresholds | `TbMsgAttributesNode` | **MODIFY** | Refactor vào orchestration v2.2 với mode, safety, target và quota. |
| 22 | Has Thresholds? | `TbCheckMessageNode` | **MODIFY** | Refactor vào orchestration v2.2 với mode, safety, target và quota. |
| 23 | To Attributes Update | `TbTransformMsgNode` | **MODIFY** | Refactor vào orchestration v2.2 với mode, safety, target và quota. |
| 24 | Ignore | `TbAckNode` | **MODIFY** | Refactor vào orchestration v2.2 với mode, safety, target và quota. |
| 25 | Field 1 Water Consumption Simulator | `TbMsgGeneratorNode` | **REMOVE** | Simulator ngoài platform sẽ phát telemetry; tránh dữ liệu trùng. |
| 26 | Field 2 Water Consumption Simulator | `TbMsgGeneratorNode` | **REMOVE** | Simulator ngoài platform sẽ phát telemetry; tránh dữ liệu trùng. |
| 27 | Fetch Irrigation State | `TbGetAttributesNode` | **MODIFY** | Refactor vào orchestration v2.2 với mode, safety, target và quota. |
| 28 | Is Irrigation On? | `TbJsFilterNode` | **MODIFY** | Refactor vào orchestration v2.2 với mode, safety, target và quota. |
| 29 | Save telemetry | `TbMsgTimeseriesNode` | **MODIFY** | Refactor vào orchestration v2.2 với mode, safety, target và quota. |

## SI Smart Valve

Nodes: 8; connections: 11.

| # | Node | Type | Decision | Reason |
|---:|---|---|---|---|
| 0 | Save Timeseries | `TbMsgTimeseriesNode` | **MODIFY** | Bổ sung valveState, command ACK và SAFETY_BLOCK. |
| 1 | Save Client Attributes | `TbMsgAttributesNode` | **MODIFY** | Bổ sung valveState, command ACK và SAFETY_BLOCK. |
| 2 | Message Type Switch | `TbMsgTypeSwitchNode` | **MODIFY** | Bổ sung valveState, command ACK và SAFETY_BLOCK. |
| 3 | Log RPC from Device | `TbLogNode` | **MODIFY** | Bổ sung valveState, command ACK và SAFETY_BLOCK. |
| 4 | Log Other | `TbLogNode` | **MODIFY** | Bổ sung valveState, command ACK và SAFETY_BLOCK. |
| 5 | RPC Call Request | `TbSendRPCRequestNode` | **MODIFY** | Bổ sung valveState, command ACK và SAFETY_BLOCK. |
| 6 | Device Profile Node | `TbDeviceProfileNode` | **MODIFY** | Bổ sung valveState, command ACK và SAFETY_BLOCK. |
| 7 | Count Alarms | `TbRuleChainInputNode` | **MODIFY** | Bổ sung valveState, command ACK và SAFETY_BLOCK. |

## SI Soil Moisture

Nodes: 17; connections: 21.

| # | Node | Type | Decision | Reason |
|---:|---|---|---|---|
| 0 | Save Timeseries | `TbMsgTimeseriesNode` | **MODIFY** | Bổ sung dataQuality và contract telemetry v2.2. |
| 1 | Save Client Attributes | `TbMsgAttributesNode` | **MODIFY** | Bổ sung dataQuality và contract telemetry v2.2. |
| 2 | Message Type Switch | `TbMsgTypeSwitchNode` | **MODIFY** | Bổ sung dataQuality và contract telemetry v2.2. |
| 3 | Log RPC from Device | `TbLogNode` | **MODIFY** | Bổ sung dataQuality và contract telemetry v2.2. |
| 4 | Log Other | `TbLogNode` | **MODIFY** | Bổ sung dataQuality và contract telemetry v2.2. |
| 5 | RPC Call Request | `TbSendRPCRequestNode` | **MODIFY** | Bổ sung dataQuality và contract telemetry v2.2. |
| 6 | Device Profile Node | `TbDeviceProfileNode` | **MODIFY** | Bổ sung dataQuality và contract telemetry v2.2. |
| 7 | Fetch Moisture Thresholds | `TbGetRelatedAttributeNode` | **MODIFY** | Bổ sung dataQuality và contract telemetry v2.2. |
| 8 | To Field Asset | `TbChangeOriginatorNode` | **KEEP** | Tái sử dụng aggregation/relation lõi. |
| 9 | Aggregate Avg | `TbSimpleAggMsgNode` | **KEEP** | Tái sử dụng aggregation/relation lõi. |
| 10 | Has Moisture? | `TbCheckMessageNode` | **MODIFY** | Bổ sung dataQuality và contract telemetry v2.2. |
| 11 | To Field Rule Chain | `TbRuleChainInputNode` | **MODIFY** | Bổ sung dataQuality và contract telemetry v2.2. |
| 12 | Save Attributes | `TbMsgAttributesNode` | **MODIFY** | Bổ sung dataQuality và contract telemetry v2.2. |
| 13 | Change msg type | `TbTransformMsgNode` | **MODIFY** | Bổ sung dataQuality và contract telemetry v2.2. |
| 14 | Check Field relation | `TbJsFilterNode` | **MODIFY** | Bổ sung dataQuality và contract telemetry v2.2. |
| 15 | Count Alarms | `TbRuleChainInputNode` | **MODIFY** | Bổ sung dataQuality và contract telemetry v2.2. |
| 16 | Aggregate Latest Moisture | `TbAggLatestTelemetryNodeV2` | **KEEP** | Tái sử dụng aggregation/relation lõi. |

## SI Water Meter

Nodes: 14; connections: 17.

| # | Node | Type | Decision | Reason |
|---:|---|---|---|---|
| 0 | Save Timeseries | `TbMsgTimeseriesNode` | **MODIFY** | Bổ sung flowRate, quality và safety event. |
| 1 | Save Client Attributes | `TbMsgAttributesNode` | **MODIFY** | Bổ sung flowRate, quality và safety event. |
| 2 | Message Type Switch | `TbMsgTypeSwitchNode` | **MODIFY** | Bổ sung flowRate, quality và safety event. |
| 3 | Log RPC from Device | `TbLogNode` | **MODIFY** | Bổ sung flowRate, quality và safety event. |
| 4 | Log Other | `TbLogNode` | **MODIFY** | Bổ sung flowRate, quality và safety event. |
| 5 | RPC Call Request | `TbSendRPCRequestNode` | **MODIFY** | Bổ sung flowRate, quality và safety event. |
| 6 | Device Profile Node | `TbDeviceProfileNode` | **MODIFY** | Bổ sung flowRate, quality và safety event. |
| 7 | Calculate Delta | `CalculateDeltaNode` | **KEEP** | Tái sử dụng delta và mapping waterConsumption. |
| 8 | To Field | `TbChangeOriginatorNode` | **KEEP** | Tái sử dụng delta và mapping waterConsumption. |
| 9 | To Field Rule Chain | `TbRuleChainInputNode` | **MODIFY** | Bổ sung flowRate, quality và safety event. |
| 10 | Count Alarms | `TbRuleChainInputNode` | **MODIFY** | Bổ sung flowRate, quality và safety event. |
| 11 | Ignore Failure | `TbAckNode` | **MODIFY** | Bổ sung flowRate, quality và safety event. |
| 12 | Sequential | `TbCheckpointNode` | **MODIFY** | Bổ sung flowRate, quality và safety event. |
| 13 | Main | `TbCheckpointNode` | **MODIFY** | Bổ sung flowRate, quality và safety event. |

## Dashboard

Reference dashboard contains 10 states, 20 widgets and 6 aliases. All are marked MODIFY so v2.2 may retain, merge, replace or remove them after review.
