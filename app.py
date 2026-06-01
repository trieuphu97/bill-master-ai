import streamlit as st
import google.generativeai as genai
from PIL import Image
import sqlite3
from datetime import datetime
import pandas as pd
import os

# --- CẤU HÌNH ---
API_KEY = st.secrets["GEMINI_API_KEY"]
DB_NAME = st.secrets["DB_NAME"]
IMAGE_STORE_DIR = "saved_bills"  # Thư mục lưu file ảnh trên server/host

# Tự động tạo thư mục lưu ảnh nếu chưa tồn tại
if not os.path.exists(IMAGE_STORE_DIR):
    os.makedirs(IMAGE_STORE_DIR)


# --- DATABASE LOGIC (CẬP NHẬT THÊM CỘT IMAGE_PATH) ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # Thêm cột image_path vào bảng sessions để lưu đường dẫn ảnh
    c.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            date TEXT, 
            title TEXT,
            image_path TEXT
        )
    ''')
    c.execute('CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, content TEXT, is_paid INTEGER)')
    conn.commit()
    conn.close()

def save_bill(title, items_list, uploaded_file):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    date_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    
    # Xử lý lưu file ảnh gốc
    image_path = ""
    if uploaded_file is not None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_extension = os.path.splitext(uploaded_file.name)[1]
        saved_filename = f"bill_{timestamp}{file_extension}"
        image_path = os.path.join(IMAGE_STORE_DIR, saved_filename)
        
        # Ghi dữ liệu ảnh vào thư mục
        with open(image_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
            
    # Chèn thông tin kèm đường dẫn ảnh vào DB
    c.execute("INSERT INTO sessions (date, title, image_path) VALUES (?, ?, ?)", (date_str, title, image_path))
    session_id = c.lastrowid
    for item in items_list:
        if item.strip():
            c.execute("INSERT INTO items (session_id, content, is_paid) VALUES (?, ?, ?)", (session_id, item.strip(), 0))
    conn.commit()
    conn.close()

def delete_session(session_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Lấy đường dẫn ảnh cũ để xóa file vật lý tránh rác bộ nhớ
    c.execute("SELECT image_path FROM sessions WHERE id = ?", (session_id,))
    row = c.fetchone()
    if row and row[0] and os.path.exists(row[0]):
        try:
            os.remove(row[0])
        except Exception:
            pass

    c.execute("DELETE FROM items WHERE session_id = ?", (session_id,))
    c.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()

def delete_all_history():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Xóa sạch toàn bộ file ảnh trong thư mục lưu trữ
    if os.path.exists(IMAGE_STORE_DIR):
        for filename in os.listdir(IMAGE_STORE_DIR):
            file_path = os.path.join(IMAGE_STORE_DIR, filename)
            if os.path.isfile(file_path):
                os.remove(file_path)
                
    c.execute("DELETE FROM items")
    c.execute("DELETE FROM sessions")
    conn.commit()
    conn.close()

# --- POPUP XEM ẢNH HÓA ĐƠN GỐC ---
@st.dialog("🖼️ Ảnh hóa đơn gốc")
def show_image_popup(img_path):
    if os.path.exists(img_path):
        st.image(img_path, use_container_width=True)
    else:
        st.error("Không tìm thấy file ảnh gốc trên hệ thống.")

# --- GIAO DIỆN (NÂNG CẤP TAB & LAYOUT) ---
st.set_page_config(page_title="Bill Master", layout="wide")
init_db()

st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;800&display=swap');
    
    html, body, [class*="css"] { font-family: 'Plus Jakarta Sans', sans-serif; background-color: #0E1117; }

    /* Header Wow */
    .header-container {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 2.5rem; border-radius: 30px; text-align: center; margin-bottom: 2rem;
        box-shadow: 0 15px 35px rgba(0,0,0,0.3);
    }
    .header-container h1 { color: white !important; font-weight: 800 !important; letter-spacing: -1px; }

    /* THIẾT KẾ TAB MỚI - FIX LỖI XẤU */
    .stTabs [data-baseweb="tab-list"] {
        gap: 15px;
        background-color: rgba(255, 255, 255, 0.03);
        padding: 10px 15px;
        border-radius: 20px;
        border: 1px solid rgba(255, 255, 255, 0.05);
    }
    
    .stTabs [data-baseweb="tab"] {
        height: 45px;
        border-radius: 12px !important;
        background-color: transparent !important;
        border: none !important;
        color: #888 !important;
        transition: all 0.3s ease;
        padding: 0 25px !important;
    }

    /* Khi tab được chọn */
    .stTabs [aria-selected="true"] {
        background: linear-gradient(135deg, #00C9FF 0%, #92FE9D 100%) !important;
        color: #072e33 !important;
        font-weight: 700 !important;
        box-shadow: 0 8px 15px rgba(0, 201, 255, 0.3);
        transform: translateY(-2px);
    }

    /* Loại bỏ gạch chân mặc định của Streamlit */
    .stTabs [data-baseweb="tab-highlight"] { display: none !important; }

    /* Card Item nợ */
    div[data-testid="stCheckbox"] {
        background: rgba(255, 255, 255, 0.04) !important;
        border: 1px solid rgba(255, 255, 255, 0.08) !important;
        padding: 15px 20px !important;
        border-radius: 18px !important;
        margin-bottom: 10px !important;
    }
    
    /* Nút xóa tinh tế hơn */
    button[key^="del_"] {
        background: rgba(255, 75, 75, 0.1) !important;
        border: 1px solid rgba(255, 75, 75, 0.2) !important;
        color: #ff4b4b !important;
        border-radius: 10px !important;
    }
        
    /* Thẻ bao quanh từng món nợ trong danh sách kết quả */
    .scan-result-card {
        background: rgba(255, 255, 255, 0.03);
        border: 1px solid rgba(255, 255, 255, 0.05);
        padding: 12px 15px;
        border-radius: 14px;
        margin-bottom: 8px;
        display: flex;
        align-items: center;
        gap: 10px;
    }
    
    /* Hiệu ứng cho vùng tải ảnh */
    [data-testid="stFileUploader"] {
        background: rgba(0, 201, 255, 0.02);
        border: 2px dashed rgba(0, 201, 255, 0.2);
        border-radius: 20px;
        padding: 20px;
    }
    </style>
    """, unsafe_allow_html=True)

st.markdown('<div class="header-container"><h1>🧾 Bill Master AI</h1><p style="color:rgba(255,255,255,0.7);">Công nghệ quét hóa đơn đỉnh cao</p></div>', unsafe_allow_html=True)

tab1, tab2 = st.tabs(["✨ QUÉT MỚI", "📁 LỊCH SỬ"])

# --- TAB 1: QUÉT BILL MỚI ---
with tab1:
    col_upload, col_result = st.columns([1, 1.2], gap="large")
    
    # Khởi tạo khóa ngẫu nhiên cho uploader để có thể chủ động reset xóa ảnh
    if "uploader_key" not in st.session_state:
        st.session_state.uploader_key = 0

    with col_upload:
        st.markdown("### 📸 Bước 1: Tải hóa đơn")
        with st.container(border=True):
            file = st.file_uploader("Kéo thả ảnh bill vào đây", type=["jpg", "png", "jpeg"], key=f"uploader_{st.session_state.uploader_key}", label_visibility="visible")
            if file:
                st.image(file, use_container_width=True, caption="Hóa đơn đã tải lên")
                
                # Logic Auto-Scan
                if 'last_file' not in st.session_state or st.session_state.last_file != file.name:
                    genai.configure(api_key=API_KEY)
                    model = genai.GenerativeModel('gemini-2.5-flash')
                    with st.status("🚀 Đang phân tích hóa đơn...", expanded=True) as status:
                        try:
                            img = Image.open(file)
                            prompt = "Đọc bill và liệt kê: [Tên người]: [Món] - [Giá]. Lưu ý tên người ở dòng ghi chú dưới tên món. Chỉ trả về danh sách."
                            response = model.generate_content([prompt, img])
                            st.session_state.current_items = response.text.strip().split('\n')
                            st.session_state.last_file = file.name
                            status.update(label="✅ Đã phân tích xong!", state="complete", expanded=False)
                        except Exception as e:
                            status.update(label="❌ Lỗi phân tích", state="error")
                            st.error(f"Chi tiết: {e}")

    with col_result:
        st.markdown("### 📝 Bước 2: Kiểm tra & Lưu")
        
        if 'current_items' in st.session_state:
            st.info("💡 Bạn có thể sửa trực tiếp nội dung bên dưới nếu AI đọc sai hoặc thiếu tên.")

            updated_items = []
            for i, item in enumerate(st.session_state.current_items):
                new_val = st.text_input(f"Món {i+1}", value=item, key=f"edit_{i}")
                updated_items.append(new_val)
            
            if st.button("➕ Thêm người/món mới"):
                st.session_state.current_items.append("Tên: Món ăn - 0.000")
                st.rerun()

            st.write("---")
            title = st.text_input("Tên đợt thu tiền:", value=f"Bill ngày {datetime.now().strftime('%d/%m')}", key="bill_title_input")
            
            if st.button("💾 XÁC NHẬN & LƯU", type="primary", use_container_width=True):
                # Gọi hàm lưu hóa đơn kèm file ảnh
                save_bill(title, updated_items, file)
                st.balloons()
                
                # --- TIẾN HÀNH RESET TOÀN BỘ TRẠNG THÁI VỀ ĐẦU TRANG ---
                del st.session_state.current_items
                if 'last_file' in st.session_state:
                    del st.session_state.last_file
                # Thay đổi key uploader để Streamlit ép buộc dọn file ảnh cũ trên UI
                st.session_state.uploader_key += 1 
                
                st.success("Đã lưu dữ liệu chính xác và reset giao diện thành công!")
                st.rerun()
        else:
            st.info("Hệ thống đang chờ bạn tải ảnh lên để bắt đầu phân tích...")
            st.markdown("""
                <div style="text-align: center; padding: 40px; color: #666;">
                    <span style="font-size: 50px;">🤖</span><br>
                    Đang tự động tách tên người, món ăn và giá tiền ngay khi nhận được ảnh.
                </div>
            """, unsafe_allow_html=True)

# --- TAB 2: LỊCH SỬ ---
with tab2:
    conn = sqlite3.connect(DB_NAME)
    sessions = pd.read_sql_query("SELECT * FROM sessions ORDER BY id DESC", conn)
    conn.close()

    if sessions.empty:
        st.info("Chưa có dữ liệu.")
    else:
        for _, session in sessions.iterrows():
            conn = sqlite3.connect(DB_NAME)
            items = pd.read_sql_query(f"SELECT * FROM items WHERE session_id = {session['id']}", conn)
            conn.close()
            
            p_count = len(items[items['is_paid'] == 1])
            t_count = len(items)
            is_done = (t_count > 0 and p_count == t_count)
            
            header = f"{'✅' if is_done else '🔴'} {session['title']} ({p_count}/{t_count})"
            
            with st.expander(header, expanded=not is_done):
                # Tạo 2 cột: Cột trái hiện thông tin ngày, Cột phải hiện nút popup xem ảnh gốc
                top_c1, top_c2 = st.columns([3, 1])
                with top_c1:
                    st.caption(f"📅 {session['date']}")
                with top_c2:
                    img_path = session.get('image_path', '')
                    if img_path and os.path.exists(img_path):
                        # Khi người dùng nhấn nút này, Popup Dialog xem ảnh sẽ xuất hiện
                        if st.button("🔍 Xem ảnh gốc", key=f"view_img_{session['id']}", use_container_width=True):
                            show_image_popup(img_path)
                    else:
                        st.caption("Không có ảnh gốc")
                        
                st.progress(p_count/t_count if t_count > 0 else 0)
                st.write("") 

                # Danh sách món nợ công nợ
                cols = st.columns(2)
                for i, (_, item) in enumerate(items.iterrows()):
                    with cols[i % 2]:
                        ck = st.checkbox(item['content'], value=(item['is_paid']==1), key=f"it_{session['id']}_{item['id']}")
                        if ck != (item['is_paid']==1):
                            conn = sqlite3.connect(DB_NAME)
                            conn.execute("UPDATE items SET is_paid = ? WHERE id = ?", (1 if ck else 0, item['id']))
                            conn.commit()
                            conn.close()
                            st.rerun()
                
                # Nút xóa hóa đơn dài hết cỡ
                st.write("---") 
                if st.button("🗑️ Xóa hóa đơn này", key=f"del_{session['id']}", use_container_width=True, help="Xóa hóa đơn này vĩnh viễn"):
                    delete_session(session['id'])
                    st.toast(f" Đã xóa hóa đơn: {session['title']}")
                    st.rerun()

        # Nút dọn dẹp tổng thể ở ngoài cùng
        st.divider()
        if st.button("🚨 XÓA TẤT CẢ LỊCH SỬ", use_container_width=True):
            delete_all_history()
            st.rerun()