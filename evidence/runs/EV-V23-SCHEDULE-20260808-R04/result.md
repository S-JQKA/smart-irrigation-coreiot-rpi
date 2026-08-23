# Result

## PASS

- Startup nhận shared attribute của `SI Smart Valve 1` với
  `result=STALE_ONE_SHOT_IGNORED`; lịch one-shot cũ không chạy trễ và không còn
  bị từ chối `INVALID_COMMAND`.
- Tick 20 ngắt MQTT; tick 36 reconnect thành công, re-announce 16 downstream
  device, phục hồi 2 attribute watch và thấy buffer depth 32.
- Sau reconnect, cùng `localScheduleConfig` được nhận diện
  `accepted=True result=DUPLICATE`.
- Process kết thúc exit code 0, final pump `OFF`, mode `NORMAL`.
- Không sửa/import CoreIoT artifact và không lưu Gateway token.

## Phạm vi chứng minh

Batch này chứng minh startup và reconnect an toàn với shared attribute hiện có
trên tenant. Shared attribute hiện tại là one-shot đã quá hạn; vì vậy batch không
thay dữ liệu tenant để tạo một recurring/future schedule mới.
