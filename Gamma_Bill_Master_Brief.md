# Gamma brief: Bill Master

## Mục tiêu bài thuyết trình

Tạo bộ slide tiếng Việt để giới thiệu Bill Master với đồng nghiệp và quản lý công ty. Người nghe cần hiểu vấn đề ứng dụng giải quyết, cách một đơn hàng đi qua hệ thống, cách chia tiền và nhắc thanh toán, cùng những giới hạn hiện tại.

- Thời lượng trình bày: khoảng 5–7 phút.
- Khổ slide: 16:9.
- Giọng điệu: thực tế, dễ hiểu, phù hợp buổi demo sản phẩm nội bộ.
- Không tự thêm số liệu về số người dùng, số tiền tiết kiệm, độ chính xác AI hoặc hiệu quả kinh doanh.
- Không mô tả tính năng chưa có như thể đã hoạt động.

## Hướng thiết kế

Thiết kế hiện đại, gọn và dễ chiếu trong phòng họp. Dùng nền xanh than hoặc tối, chữ có độ tương phản cao, điểm nhấn đỏ san hô lấy cảm hứng từ giao diện Bill Master. Dùng một sơ đồ quy trình xuyên suốt, hình minh họa hóa đơn và ví dụ giao diện đơn giản. Tránh bố cục giống dashboard hoặc lặp lại nhiều thẻ giao diện. Mỗi slide chỉ giữ ý chính và tối đa 3–4 gạch đầu dòng ngắn. Nếu có phần lời dẫn, đặt trong speaker notes, không đưa nguyên đoạn dài lên slide.

## Nội dung từng slide

### Slide 1 — Bill Master

**Quản lý đơn đồ ăn và công nợ nội bộ**

Từ ảnh hóa đơn đến chia món, ghi nhận thanh toán và nhắc người còn nợ.

*Gợi ý lời nói:* Bill Master bắt đầu từ một việc rất quen thuộc: cả nhóm đặt đồ ăn, một người ứng tiền, rồi sau đó khó nhớ ai gọi món gì và ai đã thanh toán.

### Slide 2 — Vấn đề trong một đơn ăn chung

- Hóa đơn có nhiều món, ghi chú tên người có thể nằm trong ảnh hoặc dòng mô tả nhỏ.
- Một món số lượng nhiều có thể do một người đặt hoặc nhiều người mỗi người một phần.
- Phí giao hàng, giảm giá và khoản chi chung khiến việc tính lại bằng tay dễ nhầm.
- Sau khi thanh toán, nhóm vẫn cần biết ai đã trả và ai còn nợ.

*Gợi ý hình:* Một hóa đơn ở giữa, nối tới ba câu hỏi: “Món của ai?”, “Mỗi người bao nhiêu?”, “Ai đã trả?”. Không dùng số liệu giả.

### Slide 3 — Luồng xử lý của Bill Master

Thể hiện thành một quy trình ngang gồm năm bước:

1. Tải ảnh hóa đơn.
2. AI đọc món và số tiền.
3. Người tạo kiểm tra, sửa và chọn cách chia.
4. Lưu đơn, gửi link để mọi người tự nhận món.
5. Theo dõi thanh toán và nhắc người còn nợ.

*Gợi ý lời nói:* AI giúp giảm nhập tay, còn người tạo vẫn kiểm tra các thông tin quan trọng trước khi lưu.

### Slide 4 — Đọc hóa đơn bằng AI, có bước đối soát

- Có thể tải nhiều ảnh của cùng một hóa đơn.
- AI trích xuất tên quán, món, số lượng, đơn giá, phí, giảm giá và tổng tiền.
- Hệ thống cảnh báo khi độ tin cậy thấp hoặc số món, subtotal, tổng tiền có điểm lệch.
- Người tạo rà soát và chỉnh dữ liệu trước khi lưu đơn.

**Lưu ý:** Hệ thống hiện dùng Gemini. Kết quả nhận dạng có thể sai; cần kiểm tra ảnh và số tiền trước khi xác nhận.

*Gợi ý hình:* Ảnh bill → bảng món đã nhận dạng → vùng cảnh báo cần rà soát. Không khẳng định AI luôn nhận đúng tên người.

### Slide 5 — Chia món và chia chi phí nhóm

Cho thấy hai cách xử lý khác nhau:

**Theo từng món**

- Gán món cho thành viên.
- Nếu một món có nhiều phần, chọn một người nhận tất cả hoặc chia số phần cho nhiều người.
- Phí và giảm giá được đưa vào phép tính phần phải trả.

**Chi phí chung**

- Dùng cho sinh nhật, liên hoan hoặc khoản không gắn với một món cụ thể.
- Có thể chia đều cho nhóm hoặc nhập phần tiền từng người.
- Có thể loại người được tổ chức khỏi danh sách đóng góp.

*Gợi ý hình:* So sánh một dòng “món cá nhân” với một dòng “chi phí chung”, tránh bảng dày đặc.

### Slide 6 — Tự nhận món qua link chia sẻ

- Người tạo gửi link của đơn cho nhóm.
- Mỗi người chọn tên mình, tìm tên không dấu nếu cần.
- Người dùng nhận số phần còn trống; người khác thấy trạng thái được cập nhật tự động khoảng mỗi 2 giây.
- Khi món đã được nhận đủ, hệ thống không còn phần để nhận; người dùng có thể bỏ nhận phần của mình.

*Gợi ý lời nói:* Link chia sẻ giúp xác định món chưa rõ người đặt mà không cần người tạo nhắn hỏi từng người.

### Slide 7 — Công nợ, thanh toán và nhắc nợ Telegram

- Lịch sử đơn hiển thị người ứng tiền, số phải trả, số đã thanh toán và số còn nợ.
- Có thể ghi nhận thanh toán một phần hoặc toàn bộ.
- Nội dung nhắc nợ nêu người ứng tiền; có thể kèm ngân hàng, số tài khoản và link xem đơn nếu đã cấu hình.
- Có thể gửi lời nhắc qua Telegram hoặc webhook và xem lịch sử nhắc.

**Giới hạn hiện tại:** Telegram gửi khi người dùng bấm nút trong ứng dụng, chưa tự gửi theo lịch. Hệ thống nhắc xác nhận nếu đơn vừa được nhắc trong 30 phút gần nhất.

*Gợi ý hình:* Một khoản còn nợ → thông tin người nhận tiền → tin nhắn Telegram mẫu; dùng dữ liệu minh họa có nhãn “Ví dụ”.

### Slide 8 — Tổng quan, lịch sử và báo cáo

- **Tổng quan:** xem tình hình đơn hàng và công nợ.
- **Lịch sử:** tìm đơn, lọc theo trạng thái, ghi nhận thanh toán và mở link chia sẻ.
- **Báo cáo:** lọc theo khoảng ngày, xuất Excel hoặc CSV.
- **Sao lưu:** tải ZIP chứa cơ sở dữ liệu và ảnh hóa đơn từ tab Cài đặt.

*Gợi ý hình:* Một đường dẫn đơn giản từ “Đơn hàng” đến “Lịch sử” và “Báo cáo”, thay vì dựng nhiều màn hình giả.

### Slide 9 — Demo và phạm vi hiện tại

**Kịch bản demo ngắn**

1. Tải ảnh bill và cho AI đọc.
2. Rà soát món, số lượng, giá và cảnh báo.
3. Lưu đơn, mở link chia sẻ và nhận thử một món.
4. Quay lại lịch sử, ghi nhận thanh toán hoặc gửi tin nhắn Telegram thử.

**Phạm vi cần nói rõ**

- AI hỗ trợ đọc hóa đơn nhưng chưa thay thế bước rà soát của con người.
- Telegram hiện nhắc thủ công, không phải lịch nhắc tự động.
- Ứng dụng dùng SQLite, phù hợp demo hoặc nhóm nhỏ chạy một tiến trình; cần đánh giá lưu trữ bền vững nếu triển khai chính thức rộng hơn.
- API key và token cần đặt trong secrets/biến môi trường, không đưa lên slide.

*Kết:* Bill Master tập trung giải quyết hai việc cụ thể: giảm nhập lại dữ liệu từ bill và làm rõ công nợ sau mỗi đơn ăn chung.

## Chỉ dẫn cuối cho Gamma

Tạo đúng 9 slide theo thứ tự trên. Giữ nguyên các giới hạn sản phẩm đã nêu. Dùng hình minh họa có tính khái quát; không dựng ảnh chụp màn hình giả như thể là giao diện thật. Nếu cần hình giao diện, để chỗ trống để người trình bày tự chèn ảnh từ ứng dụng. Không tạo biểu đồ KPI hoặc con số hiệu quả khi không có dữ liệu nguồn.
