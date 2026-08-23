# EV-V23-PILOT-20260807-R02

Bundle này đóng gói checkpoint v2.3 Minimal sau pilot Field 1 ngày
07/08/2026. Mục tiêu là tái sử dụng evidence đã có và không để Dashboard
Command Button chặn tiến độ.

## Kết luận

- Gateway là lớp quyết định và thực thi tưới cho Field 1.
- Chu trình live đã được người vận hành quan sát: van/bơm bật, độ ẩm tăng và
  tự dừng khi đạt ngưỡng.
- Ảnh cuối chu trình xác nhận van `OFF`, bơm `OFF`, `commandStatus=EXECUTED`,
  `avgMoisture=56.21` và water meter đạt `11.436 L`.
- `19/20` acceptance case cốt lõi có evidence `PASS`; một case live start
  được giữ `PARTIAL` vì chưa có raw log/ảnh đúng thời điểm ON.
- 30 static regression check của CoreIoT vẫn là kiểm tra cấu trúc, không được
  đổi tên thành acceptance test.
- Manual RPC bằng Dashboard Command Button là `DEFERRED`: từng có lần Gateway
  nhận và thực thi OFF, nhưng việc delivery/response của widget không ổn định.

## Nội dung

- `run.json`: metadata máy đọc được.
- `acceptance_matrix.md`: 20 case cốt lõi và evidence tương ứng.
- `result.md`: kết luận kỹ thuật, giới hạn và gate tiếp theo.
- `screenshots/`: sáu ảnh CoreIoT live do người vận hành cung cấp.

