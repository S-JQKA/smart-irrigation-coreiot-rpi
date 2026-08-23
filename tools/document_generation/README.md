# DOCX generation tool

`build_smartfarm_design_v22.py` là script lịch sử dùng để dựng lại tài liệu thiết kế v2.2.

- Input template hiện vẫn là `C:\Users\voles\Downloads\Thiet_ke_he_thong_v2.1_SmartFarm_an_toan.docx`.
- Output được ghi vào `docs/final/system_design/RB Thiet_ke_he_thong_v2.2_SmartFarm_an_toan_rebuild.docx`.

Không chạy lại script nếu chưa sao lưu artifact final hiện tại. Khi cần làm build reproducible hoàn toàn, nên đưa template v2.1 vào khu vực reference/archive rồi cập nhật biến `TEMPLATE`.
