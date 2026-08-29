# Tests

Khu vực dành cho unit test, integration test, scenario test và traceability
matrix của các test case trong SRS v2.2.

Các behavioral test của Gateway xác nhận hành vi runtime. Static CoreIoT checks
chỉ xác nhận cấu trúc artifact và không được đổi tên thành platform acceptance.

Baseline sau khi thêm `HIL_TWO_FIELD_4BOARD` là 145 test:

- 109 unit test;
- 36 regression/static-contract test.

Chạy toàn bộ bằng:

```powershell
py -3 -m unittest tests.unit.test_gateway_simulator_v22 tests.unit.test_gateway_protocol tests.unit.test_gateway_control_v23 tests.unit.test_gateway_client tests.unit.test_gateway_runtime_v23 tests.regression.test_coreiot_v23_minimal tests.regression.test_coreiot_v22_regression
```

`PASS` ở đây xác nhận logic cục bộ và contract. Nó không thay thế phép thử
serial/ESP-NOW, Pi, relay, flow, tank hoặc tải tưới thật.
