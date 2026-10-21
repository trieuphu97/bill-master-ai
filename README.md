# Bill Master

Ứng dụng Streamlit quản lý hóa đơn và công nợ đặt đồ ăn nội bộ.

## Chức năng

- AI đọc ảnh hóa đơn, sau đó cho phép kiểm tra và sửa từng món.
- Món giá ưu đãi (ví dụ 1đ): giữ giá thực trả trên hóa đơn, nhập giá gốc để chia phần chênh lệch như giảm giá chung.
- Cảnh báo hóa đơn có thể bị lưu trùng dựa trên quán, danh sách món, tổng tiền và thời điểm.
- Sửa đơn đã lưu trong Lịch sử, giữ các lượt nhận món và khoản đã thu hợp lệ; lưu nhật ký thay đổi.
- Sửa/hủy khoản thanh toán nhập nhầm kèm lý do, tính lại công nợ; chặn số tiền thu vượt phần phải trả.
- Danh bạ thành viên, biệt danh, phòng ban và thông tin ngân hàng.
- Một hoặc nhiều người ứng tiền; chia phí giao hàng, tip, voucher và hỗ trợ công ty.
- Theo dõi thanh toán một phần/toàn bộ và lịch sử thanh toán.
- Dashboard công nợ, tìm kiếm, lọc, báo cáo Excel/CSV.
- Link và QR chia sẻ từng đơn; webhook tương thích Slack/Teams.
- Mật khẩu truy cập, xác nhận xóa, nhật ký thao tác và tải backup ZIP.
- Tự động nâng cấp dữ liệu từ phiên bản cũ khi khởi động.

## Cài đặt

Với món giảm riêng từ 54.000đ còn 1đ, nhập **Đơn giá = 1**, bật **Chia ưu đãi giá món cho cả đơn** và nhập **Giá gốc = 54.000**. Khi chia theo từng món, người đặt được tính từ giá gốc, còn 53.999đ ưu đãi được phân bổ như khoản giảm giá chung. Voucher của toàn hóa đơn vẫn nhập riêng; tổng tiền thực trả không thay đổi. Nếu nhiều phần có giá khác nhau, hãy tách thành các dòng món riêng.

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py --server.address 0.0.0.0
```

Tạo `.streamlit/secrets.toml` dựa trên tệp `.streamlit/secrets.example.toml`. Không commit tệp chứa khóa thật.

### Telegram

1. Trong Telegram, mở `@BotFather`, gửi `/newbot`, hoàn tất tạo bot và sao chép token.
2. Mở chat với bot vừa tạo, bấm **Start** hoặc gửi `/start` để bot được phép nhắn riêng cho bạn.
3. Lấy chat ID cá nhân bằng cách nhắn `@userinfobot`. Nếu gửi vào nhóm: thêm bot vào nhóm, gửi `/start@TenBot`, sau đó mở `https://api.telegram.org/bot<TOKEN>/getUpdates` (thay `<TOKEN>` bằng token bot) và lấy `message.chat.id` trong tin nhắn vừa gửi. Chat ID nhóm thường là số âm. Không chia sẻ URL này vì nó chứa token bot.
4. Điền `TELEGRAM_BOT_TOKEN` và `TELEGRAM_CHAT_ID` trong `.streamlit/secrets.toml`, khởi động lại ứng dụng. Trong tab **Cài đặt**, dùng nút **Gửi tin nhắn thử tới Telegram** để kiểm tra.

Token bot giống mật khẩu: chỉ lưu trong secrets hoặc biến môi trường, không commit hay chia sẻ. Khi xem một đơn trong **Lịch sử**, nút **Gửi Telegram** gửi nội dung nhắc nợ tới chat ID đã cấu hình và ghi lại lịch sử gửi. Máy chạy ứng dụng cần có kết nối Internet. Đây là gửi thủ công từ ứng dụng, chưa phải lịch gửi tự động theo giờ.

## Lưu ý triển khai

SQLite phù hợp cho nhóm nhỏ và một tiến trình ứng dụng. Nếu công ty có nhiều người ghi dữ liệu đồng thời hoặc host xóa ổ đĩa sau mỗi lần deploy, nên chuyển database và ảnh sang dịch vụ lưu trữ bền vững. Tải backup định kỳ trong tab **Cài đặt**.

Webhook chỉ gửi thông báo khi đã cấu hình URL. Đối soát ngân hàng tự động chưa thể bật an toàn nếu chưa có nhà cung cấp API, thông tin tài khoản và sự chấp thuận của công ty; ứng dụng hiện hỗ trợ ghi nhận giao dịch thủ công đầy đủ.

## Kiểm thử

```powershell
python -m unittest discover -v
```

Giao diện nền sáng, xanh ngọc, điều hướng bên trái và bố cục mobile được kiểm tra trên Streamlit 1.60. Khởi động lại Streamlit sau khi cập nhật để áp dụng theme mới.

Ứng dụng có hai theme sáng/tối đồng bộ cho form, bảng và biểu đồ. Mở menu ⋮ ở góc trên bên phải, chọn **Light** hoặc **Dark**. Lựa chọn được Streamlit ghi nhớ trong trình duyệt.

Kiểm tra trình duyệt desktop/mobile (320px và 390px):

```powershell
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python scripts/check_ui.py
```

Kiểm thử sử dụng database và cấu hình tạm riêng, không thay đổi dữ liệu thật và không gửi yêu cầu tới AI/Telegram/webhook. Ảnh chụp kiểm tra được lưu trong thư mục tạm; đường dẫn được in khi chạy xong.
