import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from PIL import Image

from database import Database
from services import allocate_group, allocate_items, backup_zip, excel_report, money, parse_money, person_balances, qr_png, send_telegram, send_webhook


st.set_page_config(page_title="Bill Master", page_icon="🧾", layout="wide")


def secret(name, default=""):
    try:
        return st.secrets.get(name, default)
    except Exception:
        return os.getenv(name, default)


DB_NAME = secret("DB_NAME", "bill_master.db")
IMAGE_DIR = Path(secret("IMAGE_STORE_DIR", "saved_bills"))
IMAGE_DIR.mkdir(parents=True, exist_ok=True)
db = Database(DB_NAME)
db.init()
REQUIRED = " :red[*]"


def authenticate():
    password = secret("APP_PASSWORD")
    if not password:
        st.warning("Ứng dụng chưa đặt APP_PASSWORD. Hãy cấu hình mật khẩu trước khi chia sẻ trong công ty.")
        return True
    if st.session_state.get("authenticated"):
        return True
    st.title("🔐 Bill Master")
    with st.form("login"):
        entered = st.text_input(f"Mật khẩu công ty{REQUIRED}", type="password")
        submitted = st.form_submit_button("Đăng nhập", type="primary")
    if submitted and hashlib.sha256(entered.encode()).digest() == hashlib.sha256(str(password).encode()).digest():
        st.session_state.authenticated = True
        st.rerun()
    elif submitted:
        st.error("Mật khẩu không đúng.")
    return False


if not authenticate():
    st.stop()


st.markdown(
    """
    <style>
    .block-container {padding-top: 1.5rem; max-width: 1450px}
    [data-testid="stMetric"] {background:rgba(100,120,255,.08);padding:16px;border-radius:16px;border:1px solid rgba(120,130,255,.18)}
    div[data-testid="stForm"] {border-radius:18px}
    .debt-ok {color:#21c55d;font-weight:700}.debt-bad {color:#ff5c75;font-weight:700}
    div[role="radiogroup"][aria-label="Điều hướng"] {
        display:flex; gap:8px; flex-wrap:wrap; padding:7px;
        border-radius:14px; background:rgba(255,255,255,.035);
        border:1px solid rgba(255,255,255,.08);
    }
    div[role="radiogroup"][aria-label="Điều hướng"] label[data-baseweb="radio"] {
        padding:9px 16px !important; border-radius:10px; margin:0 !important;
        transition:background .18s ease, color .18s ease, box-shadow .18s ease;
    }
    div[role="radiogroup"][aria-label="Điều hướng"] label[data-baseweb="radio"] > div:first-child {display:none !important}
    div[role="radiogroup"][aria-label="Điều hướng"] label[data-baseweb="radio"]:has(input:checked) {
        background:linear-gradient(135deg,#667eea,#764ba2);
        color:white !important; box-shadow:0 5px 14px rgba(102,126,234,.25);
    }
    div[role="radiogroup"][aria-label="Điều hướng"] label[data-baseweb="radio"]:hover {background:rgba(255,255,255,.08)}
    div[role="radiogroup"][aria-label="Điều hướng"] label[data-baseweb="radio"] p {font-weight:600; white-space:nowrap}
    </style>
    """,
    unsafe_allow_html=True,
)


def save_image(uploads):
    if not uploads:
        return ""
    uploads = uploads if isinstance(uploads, list) else [uploads]
    images = [Image.open(file).convert("RGB") for file in uploads]
    width = max(image.width for image in images)
    height = sum(image.height for image in images)
    combined = Image.new("RGB", (width, height), "white")
    offset = 0
    for image in images:
        combined.paste(image, (0, offset))
        offset += image.height
    filename = f"bill_{datetime.now():%Y%m%d_%H%M%S_%f}.jpg"
    path = IMAGE_DIR / filename
    combined.save(path, format="JPEG", quality=92)
    return str(path)


def member_options(active_only=True):
    sql = "SELECT * FROM members" + (" WHERE active=1" if active_only else "") + " ORDER BY name"
    return db.query(sql)


def name_search_label(value):
    return value if value in {"Chưa xác định", "Không có", "Tất cả"} else f"{value} · {db.normalize_name(value)}"


def format_money_state(key):
    st.session_state[key] = f"{parse_money(st.session_state.get(key, 0)):,}"


def money_input(container, label, value=0, key=None, disabled=False):
    callback = {"on_change": format_money_state, "args": (key,)} if key else {}
    raw = container.text_input(label, value=f"{int(value or 0):,}", key=key, disabled=disabled, **callback)
    return parse_money(raw)


def scan_bill(uploads=None, pasted_text=""):
    api_key = secret("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Chưa cấu hình GEMINI_API_KEY.")
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(secret("GEMINI_MODEL", "gemini-2.5-flash"))
    roster = [{"name": member["name"], "aliases": member.get("aliases") or ""} for member in member_options()]
    prompt = """Đọc TOÀN BỘ hóa đơn đặt đồ ăn trong ảnh, từ món đầu tiên đến món cuối cùng. Trả về DUY NHẤT JSON hợp lệ, không markdown:
    {"restaurant":"", "item_count":0, "subtotal":0, "shipping_fee":0, "discount":0, "tip":0, "total":0,
     "items":[{"person_name":"", "raw_person_note":"", "item_name":"", "quantity":1, "unit_price":0, "confidence":0.0}]}
    Giá là số nguyên VND và luôn là số dương; riêng discount cũng trả về trị tuyệt đối dương.
    item_count phải đúng với dòng 'Tổng (... món)' nếu nhìn thấy. subtotal là dòng 'Tổng', shipping_fee là 'Phí giao hàng',
    tip gồm 'Phí áp dụng', phí dịch vụ hoặc phụ phí; discount là dòng 'Giảm giá'; total là số cuối cùng thực trả.
    QUY TẮC ĐỌC TÊN TRÊN GRAB: mỗi khối bắt đầu bằng số lượng + tên món + giá. Các dòng xám tiếp theo là size/topping/độ ngọt,
    và tên người thường là dòng ghi chú ngắn cuối cùng của chính khối đó. Ví dụ 'C Quỳnh' là Quỳnh, 'A Bình' là Bình,
    'Phú: 30% ngọt' là Phú, 'Nghĩa ngây ngô' là Nghĩa, còn 'Nhớ áp mã KM...' là yêu cầu món chứ không phải tên.
    Ghi nguyên văn dòng nghi là tên vào raw_person_note; person_name phải là tên chuẩn trong DANH BẠ nếu ghép chắc chắn,
    nếu không ghép được thì giữ tên ngắn đọc được, chỉ dùng 'Chưa xác định' khi thực sự không có ghi chú tên.
    Không bỏ qua món đầu tiên. Hai ảnh có thể chồng lặp một phần; một món xuất hiện ở cuối ảnh trước và đầu ảnh sau chỉ được trả về MỘT lần.
    confidence từ 0 đến 1 thể hiện độ chắc chắn khi đọc tên người, món, số lượng và giá.
    Nếu có nhiều ảnh, chúng là các phần của CÙNG MỘT hóa đơn: hợp nhất và không tạo món trùng.
    Tự kiểm tra: subtotal + shipping_fee + tip - discount phải bằng total. Không tự bịa dữ liệu không nhìn thấy."""
    prompt += "\nDANH BẠ CÔNG TY (dùng để chuẩn hóa tên, không tự gán nếu ảnh không có bằng chứng):\n" + json.dumps(roster, ensure_ascii=False)
    if pasted_text.strip():
        prompt += f"\nNội dung người dùng sao chép từ đơn hàng (ưu tiên dùng để bổ sung ảnh):\n{pasted_text.strip()}"
    images = [Image.open(file) for file in (uploads or [])]
    response = model.generate_content([prompt, *images])
    raw = response.text.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.I | re.M).strip()
    result = json.loads(raw)
    # Chuẩn hóa các biến thể thường gặp trong phản hồi AI và dùng tổng cuối để
    # khôi phục giảm giá nếu AI bỏ sót dòng có dấu âm.
    result["items"] = result.get("items") or []
    for item in result["items"]:
        item["confidence"] = min(1.0, max(0.0, float(item.get("confidence", 0.5))))
        read_name = str(item.get("person_name") or item.get("raw_person_note") or "Chưa xác định").strip()
        matched_member = db.find_member(read_name)
        if matched_member:
            item["raw_person_name"] = read_name
            item["person_name"] = matched_member["name"]
        else:
            item["raw_person_name"] = str(item.get("raw_person_note") or read_name).strip()
            item["person_name"] = read_name
    for field in ("subtotal", "shipping_fee", "discount", "tip", "total"):
        raw_value = result.get(field, 0)
        result[field] = abs(int(raw_value)) if isinstance(raw_value, (int, float)) else parse_money(raw_value)
    if not result["subtotal"]:
        result["subtotal"] = sum(
            max(1, int(item.get("quantity", 1))) * parse_money(item.get("unit_price", 0))
            for item in result["items"]
        )
    expected_before_discount = result["subtotal"] + result["shipping_fee"] + result["tip"]
    if result["total"] and expected_before_discount >= result["total"]:
        derived_discount = expected_before_discount - result["total"]
        # Tổng cuối hóa đơn đáng tin cậy hơn khi trường discount bị thiếu/sai.
        if not result["discount"] or abs(result["discount"] - derived_discount) > 1:
            result["discount"] = derived_discount
    return result


def all_debts(date_from=None, date_to=None):
    where, params = "", []
    if date_from:
        where += " AND date(created_at)>=date(?)"
        params.append(str(date_from))
    if date_to:
        where += " AND date(created_at)<=date(?)"
        params.append(str(date_to))
    sessions = db.query(f"SELECT * FROM sessions WHERE 1=1 {where} ORDER BY id DESC", params)
    output = []
    for session in sessions:
        items = db.query("SELECT * FROM items WHERE session_id=?", (session["id"],))
        payments = db.query("SELECT * FROM payments WHERE session_id=?", (session["id"],))
        claims = db.query("""SELECT c.*,m.name person_name FROM item_claims c JOIN members m ON m.id=c.member_id
                              JOIN items i ON i.id=c.item_id WHERE i.session_id=?""", (session["id"],))
        for row in person_balances(items, payments, claims):
            output.append({"Mã đơn": session["id"], "Ngày": session["date"], "Đơn": session["title"], "Người": row["person_name"], "Phải trả": row["due"], "Đã trả": row["paid"], "Còn nợ": row["remaining"]})
    return sessions, output


@st.dialog("Xác nhận xóa")
def confirm_delete(session):
    st.warning(f'Bạn có chắc muốn xóa đơn “{session["title"]}” không? Toàn bộ món và thanh toán liên quan cũng sẽ bị xóa.')
    cancel_col, delete_col = st.columns(2)
    if cancel_col.button("Hủy", use_container_width=True):
        st.rerun()
    if delete_col.button("Xóa đơn", type="primary", use_container_width=True):
        db.delete_session(session["id"])
        st.rerun()


@st.dialog("Ghi nhận thanh toán")
def payment_dialog(session_id, balance):
    st.write(f'**{balance["person_name"]}** còn {money(balance["remaining"])}')
    amount = money_input(st, f"Số tiền nhận{REQUIRED}", max(1, balance["remaining"]), key=f"payment_amount_{session_id}_{balance['person_name']}")
    method = st.selectbox("Hình thức", ["Chuyển khoản", "Tiền mặt", "Ví điện tử", "Khác"])
    note = st.text_input("Ghi chú")
    if st.button("Xác nhận đã nhận", type="primary"):
        if amount <= 0:
            st.error("Số tiền nhận phải lớn hơn 0.")
        else:
            db.add_payment(session_id, balance.get("member_id"), balance["person_name"], amount, method, note)
            st.rerun()


def reminder_text(session, balances):
    payer_name = session.get("payer_name") or "người ứng tiền"
    lines = [f'🧾 {session["title"]} ({session["date"]})', f'💳 Người ứng tiền: {payer_name}']
    lines += [f'- {x["person_name"]}: còn {money(x["remaining"])}' for x in balances if x["remaining"] > 0]
    if session.get("bank_name") or session.get("bank_account"):
        lines.append(f'🏦 Ngân hàng: {session.get("bank_name") or "Chưa cập nhật"}')
        lines.append(f'🔢 Số tài khoản: {session.get("bank_account") or "Chưa cập nhật"}')
        lines.append(f'👤 Chủ tài khoản: {payer_name}')
    return "\n".join(lines) + f"\nVui lòng chuyển khoản cho {payer_name}. Cảm ơn!"


@st.fragment(run_every="2s")
def render_shared_claims_modern(session):
    """Render self-service claims as scannable cards with a compact balance view."""
    shared_items = db.query("SELECT * FROM items WHERE session_id=?", (session["id"],))
    shared_claims = db.query(
        """SELECT c.*,m.name person_name FROM item_claims c JOIN members m ON m.id=c.member_id
           JOIN items i ON i.id=c.item_id WHERE i.session_id=?""", (session["id"],)
    )
    payments = db.query("SELECT * FROM payments WHERE session_id=?", (session["id"],))
    claimable = [item for item in shared_items if not item.get("member_id") and item.get("amount_due", 0) > 0]

    if claimable:
        total_quantity = sum(int(item.get("quantity") or 1) for item in claimable)
        claimable_ids = {item["id"] for item in claimable}
        claimed_quantity = sum(int(claim["quantity"]) for claim in shared_claims if claim["item_id"] in claimable_ids)
        remaining_total = max(0, total_quantity - claimed_quantity)
        st.markdown("### Chọn món của bạn")
        st.caption("Chọn tên của bạn, xem số phần còn lại rồi nhận món. Tình trạng tự cập nhật mỗi 2 giây.")
        claim_members = member_options()
        if claim_members:
            claiming_member = st.selectbox(
                "Bạn là ai? (có thể tìm không dấu)", claim_members,
                format_func=lambda member: f'{member["name"]} · {db.normalize_name(member["name"])}',
                key=f"claim_member_{session['id']}",
            )
            st.progress(1.0 if total_quantity == 0 else min(1.0, claimed_quantity / total_quantity))
            st.caption(f"Đã nhận {claimed_quantity}/{total_quantity} phần · Còn lại {remaining_total} phần")

            for item in claimable:
                claimed_qty = sum(int(claim["quantity"]) for claim in shared_claims if claim["item_id"] == item["id"])
                remaining_qty = max(0, int(item["quantity"]) - claimed_qty)
                own_claim = next(
                    (claim for claim in shared_claims if claim["item_id"] == item["id"] and claim["member_id"] == claiming_member["id"]),
                    None,
                )
                with st.container(border=True):
                    title_col, state_col = st.columns([4, 1])
                    title_col.markdown(f'**{item["item_name"]}**')
                    title_col.caption(f'{money(item["unit_price"])} / phần · {claimed_qty}/{item["quantity"]} đã nhận')
                    if own_claim:
                        state_col.success(f'Bạn đã nhận {int(own_claim["quantity"])}')
                    elif remaining_qty == 0:
                        state_col.info("Đã nhận đủ")
                    else:
                        state_col.caption(f"Còn {remaining_qty} phần")

                    control_col, action_col = st.columns([1, 1], vertical_alignment="bottom")
                    if own_claim:
                        control_col.caption("Nếu đổi ý, bạn có thể bỏ nhận món này.")
                        if action_col.button("Bỏ nhận", key=f'unclaim_{item["id"]}_{claiming_member["id"]}', use_container_width=True):
                            db.remove_claim(item["id"], claiming_member["id"])
                            st.rerun(scope="fragment")
                    elif remaining_qty > 0:
                        claim_qty = control_col.number_input(
                            "Số lượng nhận", 1, remaining_qty, value=1,
                            label_visibility="collapsed",
                            key=f'claim_qty_{item["id"]}_{claiming_member["id"]}',
                        )
                        if action_col.button(
                            "Nhận món", key=f'claim_{item["id"]}_{claiming_member["id"]}',
                            type="primary", use_container_width=True,
                        ):
                            try:
                                db.claim_item(item["id"], claiming_member["id"], claim_qty)
                                st.rerun(scope="fragment")
                            except (ValueError, sqlite3.OperationalError) as exc:
                                st.error(f"Món vừa được người khác nhận hoặc hệ thống đang bận: {exc}")
        else:
            st.warning("Chưa có thành viên trong danh bạ để nhận món.")
    else:
        st.success("Đơn này không còn món cần nhận.")

    balances = person_balances(shared_items, payments, shared_claims)
    with st.expander("Công nợ đơn hàng", expanded=False):
        if balances:
            st.dataframe(
                pd.DataFrame([
                    {"Người": row["person_name"], "Phải trả": money(row["due"]), "Đã trả": money(row["paid"]), "Còn nợ": money(row["remaining"])}
                    for row in balances
                ]),
                hide_index=True, use_container_width=True,
            )
        else:
            st.caption("Chưa có công nợ để hiển thị.")


@st.fragment(run_every="2s")
def render_shared_claims(session):
    """Tự đồng bộ món và công nợ mà không tải lại toàn bộ trang."""
    shared_items = db.query("SELECT * FROM items WHERE session_id=?", (session["id"],))
    shared_claims = db.query(
        """SELECT c.*,m.name person_name FROM item_claims c JOIN members m ON m.id=c.member_id
           JOIN items i ON i.id=c.item_id WHERE i.session_id=?""", (session["id"],)
    )
    payments = db.query("SELECT * FROM payments WHERE session_id=?", (session["id"],))
    claimable = [item for item in shared_items if not item.get("member_id") and item.get("amount_due", 0) > 0]
    if claimable:
        st.markdown("**Tự nhận món của bạn · tự cập nhật mỗi 2 giây**")
        claim_members = member_options()
        if claim_members:
            claiming_member = st.selectbox("Bạn là ai? (có thể tìm không dấu)", claim_members, format_func=lambda member: f'{member["name"]} · {db.normalize_name(member["name"])}', key=f"claim_member_{session['id']}")
            for item in claimable:
                claimed_qty = sum(int(claim["quantity"]) for claim in shared_claims if claim["item_id"] == item["id"])
                remaining_qty = max(0, int(item["quantity"]) - claimed_qty)
                own_claim = next((claim for claim in shared_claims if claim["item_id"] == item["id"] and claim["member_id"] == claiming_member["id"]), None)
                c1, c2, c3 = st.columns([4, 1, 1.2], vertical_alignment="bottom")
                c1.write(f'**{item["item_name"]}** · còn {remaining_qty}/{item["quantity"]} · {money(item["unit_price"])}/phần')
                if own_claim:
                    c2.markdown(f'Đã nhận: **{int(own_claim["quantity"])}**')
                    if c3.button("Bỏ nhận", key=f'unclaim_{item["id"]}_{claiming_member["id"]}', use_container_width=True):
                        db.remove_claim(item["id"], claiming_member["id"])
                        st.rerun(scope="fragment")
                elif remaining_qty > 0:
                    claim_qty = c2.number_input("SL nhận", 1, remaining_qty, value=1, key=f'claim_qty_{item["id"]}_{claiming_member["id"]}')
                    if c3.button("Nhận món", key=f'claim_{item["id"]}_{claiming_member["id"]}', use_container_width=True):
                        try:
                            db.claim_item(item["id"], claiming_member["id"], claim_qty)
                            st.rerun(scope="fragment")
                        except (ValueError, sqlite3.OperationalError) as exc:
                            st.error(f"Món vừa được người khác nhận hoặc hệ thống đang bận: {exc}")
                else:
                    c2.caption("Đã nhận đủ")
        else:
            st.warning("Không tìm thấy thành viên phù hợp.")
    balances = person_balances(shared_items, payments, shared_claims)
    st.dataframe(pd.DataFrame([{"Người": row["person_name"], "Phải trả": money(row["due"]), "Đã trả": money(row["paid"]), "Còn nợ": money(row["remaining"])} for row in balances]), hide_index=True, use_container_width=True)


st.title("🧾 Bill Master")
st.caption("Quản lý người ứng tiền, chia hóa đơn và công nợ đặt đồ ăn nội bộ")

query_token = st.query_params.get("bill")
if query_token:
    shared = db.query("SELECT * FROM sessions WHERE share_token=?", (query_token,))
    if shared:
        session = shared[0]
        st.subheader(f'Đơn được chia sẻ: {session["title"]}')
        render_shared_claims_modern(session)
        if st.button("Đóng chế độ chia sẻ"):
            del st.query_params["bill"]
            st.rerun()
        st.divider()
        # Trang nhận món là một chế độ độc lập; không dựng các tab quản trị và
        # các bộ đếm tự làm mới ở phía dưới trong cùng phiên trình duyệt.
        st.stop()


navigation_options = ["📊 Tổng quan", "➕ Đơn mới", "📚 Lịch sử", "👥 Thành viên", "📈 Báo cáo", "⚙️ Cài đặt"]
if hasattr(st, "segmented_control"):
    active_page = st.segmented_control(
        "Điều hướng", navigation_options, default=navigation_options[0],
        selection_mode="single", label_visibility="collapsed", key="main_navigation",
    )
else:
    active_page = st.radio(
        "Điều hướng", navigation_options, horizontal=True,
        label_visibility="collapsed", key="main_navigation",
    )

if active_page == "📊 Tổng quan":
    sessions, debts = all_debts()
    total_due = sum(x["Phải trả"] for x in debts)
    total_paid = sum(x["Đã trả"] for x in debts)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tổng số đơn", len(sessions))
    c2.metric("Tổng chi tiêu", money(total_due))
    c3.metric("Đã thu", money(total_paid))
    c4.metric("Còn phải thu", money(max(0, total_due - total_paid)))
    st.subheader("Công nợ theo thành viên")
    if debts:
        frame = pd.DataFrame(debts).groupby("Người", as_index=False)[["Phải trả", "Đã trả", "Còn nợ"]].sum().sort_values("Còn nợ", ascending=False)
        frame_display = frame.copy()
        for column in ["Phải trả", "Đã trả", "Còn nợ"]:
            frame_display[column] = frame_display[column].map(money)
        st.dataframe(frame_display, hide_index=True, use_container_width=True)
        import altair as alt
        chart_data = frame[["Người", "Đã trả", "Còn nợ"]].melt(
            id_vars="Người", var_name="Trạng thái", value_name="Số tiền"
        )
        debt_chart = (
            alt.Chart(chart_data)
            .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
            .encode(
                x=alt.X("Người:N", title=None, sort="-y"),
                y=alt.Y("Số tiền:Q", title="Số tiền (đ)"),
                color=alt.Color(
                    "Trạng thái:N",
                    scale=alt.Scale(
                        domain=["Đã trả", "Còn nợ"],
                        range=["#605eea", "#ff6b81"],
                    ),
                    legend=alt.Legend(title=None),
                ),
                tooltip=["Người", "Trạng thái", alt.Tooltip("Số tiền:Q", format=",.0f")],
            )
            .properties(height=320)
        )
        st.altair_chart(debt_chart, use_container_width=True)
    else:
        st.info("Chưa có dữ liệu. Hãy tạo đơn đầu tiên.")

if active_page == "➕ Đơn mới":
    st.subheader("1. Quét hóa đơn")
    if st.session_state.pop("bill_saved_message", None) is not None:
        st.success(st.session_state.pop("bill_saved_message_text", "Đã lưu đơn và cập nhật Tổng quan."))
    input_version = st.session_state.get("input_version", 0)
    upload = st.file_uploader(
        "Ảnh hóa đơn (có thể chọn nhiều ảnh của cùng một đơn)",
        type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True,
        key=f"bill_upload_{input_version}",
    )
    if upload:
        st.image(upload, width=250)
    if st.button("✨ Đọc hóa đơn bằng AI", disabled=not upload):
        with st.spinner("Đang hợp nhất và phân tích hóa đơn..."):
            try:
                st.session_state.draft = scan_bill(upload)
                st.session_state.scan_signature = "|".join(f"{file.name}:{file.size}" for file in upload)
                st.session_state.draft_version = st.session_state.get("draft_version", 0) + 1
                st.success("Đã đọc xong. Chỉ cần kiểm tra các dòng được cảnh báo.")
            except Exception as exc:
                st.error(f"Không thể đọc hóa đơn: {exc}")
    draft = st.session_state.get("draft", {"restaurant": "", "subtotal": 0, "shipping_fee": 0, "discount": 0, "tip": 0, "items": []})
    draft_version = st.session_state.get("draft_version", 0)
    members = member_options()
    names = ["Chưa xác định"] + [x["name"] for x in members]
    name_to_member = {x["name"]: x for x in members}
    st.subheader("2. Kiểm tra thông tin")
    with st.container(border=True):
        a, b, c = st.columns(3)
        title = a.text_input(f"Tên đơn{REQUIRED}", value=f"Đồ ăn ngày {datetime.now():%d/%m}")
        restaurant = b.text_input("Quán", value=draft.get("restaurant", ""))
        payer_name = c.selectbox(f"Người ứng tiền{REQUIRED}", names, format_func=name_search_label)
        debt_mode = st.radio(
            "Cách tính công nợ",
            ["Theo từng món", "Chia đều cho nhóm", "Nhập số tiền từng người"],
            horizontal=True,
            help="Chi phí chung dùng cho sinh nhật, liên hoan hoặc các khoản không tính theo món của từng người.",
        )
        st.markdown("**Các món**")
        if not draft.get("items"):
            draft["items"] = [{"person_name": "Chưa xác định", "item_name": "", "quantity": 1, "unit_price": 0, "_row_id": uuid.uuid4().hex}]
            st.session_state.draft = draft
        initial_items = draft["items"]
        for item in initial_items:
            item.setdefault("_row_id", uuid.uuid4().hex)
        raw_items = []
        allocation_errors = []
        review_issues = []
        entered_item_count = 0
        for i, item in enumerate(initial_items):
            row_id = item["_row_id"]
            item_confidence = float(item.get("confidence", 1))
            cols = st.columns([2, 3, 1, 2, 0.45], vertical_alignment="bottom")
            guessed = item.get("person_name", "Chưa xác định")
            matched = db.find_member(guessed)
            default_name = matched["name"] if matched else (guessed if guessed in names else "Chưa xác định")
            person = cols[0].selectbox(
                "Người" if debt_mode == "Theo từng món" else "Người (không áp dụng)",
                names, index=names.index(default_name), key=f"person_{row_id}",
                format_func=name_search_label,
                disabled=debt_mode != "Theo từng món",
            )
            item_name = cols[1].text_input(f"Món{REQUIRED}", value=item.get("item_name", ""), key=f"dish_{row_id}")
            quantity = cols[2].number_input(f"SL{REQUIRED}", 1, 50, value=max(1, int(item.get("quantity", 1))), key=f"qty_{row_id}")
            unit_price = money_input(cols[3], f"Đơn giá{REQUIRED}", parse_money(item.get("unit_price", 0)), key=f"price_{row_id}")
            if cols[4].button("🗑️", key=f"remove_{row_id}", help="Bỏ món này", use_container_width=True):
                draft["items"] = [row for row in initial_items if row["_row_id"] != row_id]
                if not draft["items"]:
                    draft["items"].append({"person_name": "Chưa xác định", "item_name": "", "quantity": 1, "unit_price": 0, "_row_id": uuid.uuid4().hex})
                st.session_state.draft = draft
                st.rerun()
            if item_confidence < 0.75:
                review_issues.append(f'AI chưa chắc chắn về món “{item_name or "chưa đọc được"}” (độ tin cậy {item_confidence:.0%}).')
            raw_person_note = str(item.get("raw_person_name") or item.get("raw_person_note") or "").strip()
            if raw_person_note and raw_person_note != "Chưa xác định" and not matched:
                st.warning(f"AI đọc ghi chú tên là “{raw_person_note}” nhưng chưa khớp danh bạ. Hãy chọn người hoặc thêm giá trị này vào Biệt danh.")
            if item_name.strip():
                entered_item_count += 1
                if debt_mode != "Theo từng món":
                    raw_items.append({"member_id": None, "person_name": "Chưa xác định", "item_name": item_name.strip(), "quantity": quantity, "unit_price": unit_price, "ai_confidence": item_confidence})
                elif quantity == 1:
                    member = name_to_member.get(person)
                    raw_items.append({"member_id": member["id"] if member else None, "person_name": person, "item_name": item_name.strip(), "quantity": 1, "unit_price": unit_price, "ai_confidence": item_confidence})
                else:
                    allocation_mode = st.radio(
                        f"Phân bổ {quantity} × {item_name}",
                        ["Một người đặt tất cả", "Chia cho nhiều người"],
                        horizontal=True,
                        key=f"allocation_mode_{row_id}",
                    )
                    if allocation_mode == "Một người đặt tất cả":
                        member = name_to_member.get(person)
                        raw_items.append({"member_id": member["id"] if member else None, "person_name": person, "item_name": item_name.strip(), "quantity": quantity, "unit_price": unit_price, "ai_confidence": item_confidence})
                    else:
                        assignee_count = st.number_input(
                            f"Số người nhận món{REQUIRED}", 2, int(quantity), value=min(2, int(quantity)),
                            key=f"assignee_count_{row_id}",
                        )
                        allocated_total = 0
                        split_columns = st.columns(min(4, int(assignee_count)))
                        for j in range(int(assignee_count)):
                            with split_columns[j % len(split_columns)]:
                                split_person = st.selectbox(
                                    f"Người {j + 1}{REQUIRED}", names,
                                    format_func=name_search_label,
                                    key=f"split_person_{row_id}_{j}",
                                )
                                default_qty = 1 if j < int(assignee_count) - 1 else max(1, int(quantity) - (int(assignee_count) - 1))
                                split_qty = st.number_input(
                                    f"Số lượng của người {j + 1}{REQUIRED}", 1, int(quantity), value=default_qty,
                                    key=f"split_qty_{row_id}_{j}",
                                )
                            allocated_total += int(split_qty)
                            if split_person == "Chưa xác định":
                                allocation_errors.append(f"{item_name}: chưa chọn người nhận phần {j + 1}")
                            member = name_to_member.get(split_person)
                            raw_items.append({"member_id": member["id"] if member else None, "person_name": split_person, "item_name": item_name.strip(), "quantity": int(split_qty), "unit_price": unit_price, "ai_confidence": item_confidence})
                        if allocated_total != int(quantity):
                            allocation_errors.append(f"{item_name}: đã phân {allocated_total}/{quantity} phần")
                        else:
                            st.caption(f"✅ Đã phân đủ {quantity}/{quantity} phần")
        if st.button("＋ Thêm món", key=f"add_item_{draft_version}"):
            draft["items"].append({"person_name": "Chưa xác định", "item_name": "", "quantity": 1, "unit_price": 0, "_row_id": uuid.uuid4().hex})
            st.session_state.draft = draft
            st.rerun()
        st.markdown("**Điều chỉnh tổng tiền**")
        f1, f2, f3 = st.columns(3)
        shipping = money_input(f1, "Phí giao hàng", parse_money(draft.get("shipping_fee", 0)), key=f"shipping_{draft_version}")
        discount = money_input(f2, "Voucher/giảm giá", parse_money(draft.get("discount", 0)), key=f"discount_{draft_version}")
        tip = money_input(f3, "Tip/phụ phí", parse_money(draft.get("tip", 0)), key=f"tip_{draft_version}")
        with st.expander("Tùy chọn: công ty hỗ trợ một phần"):
            support = money_input(st, "Số tiền công ty hỗ trợ", 0, key=f"support_{draft_version}")
        group_participants = []
        group_custom_amounts = {}
        group_total = max(0, sum(item["quantity"] * item["unit_price"] for item in raw_items) + shipping + tip - discount - support)
        if debt_mode != "Theo từng món":
            st.markdown("**Nhóm cùng đóng góp**")
            templates = db.query("SELECT * FROM group_templates ORDER BY name")
            template = st.selectbox("Mẫu nhóm", [None] + templates, format_func=lambda row: "Không dùng mẫu" if row is None else row["name"])
            template_ids = {int(value) for value in template["member_ids"].split(",") if value} if template else set()
            member_by_id = {member["id"]: member for member in members}
            default_group_ids = [member["id"] for member in members if member["id"] in template_ids]
            g1, g2 = st.columns([1, 2])
            honoree_id = g1.selectbox(
                "Người được tổ chức/miễn đóng", [None] + list(member_by_id),
                format_func=lambda member_id: "Không có" if member_id is None else f'{member_by_id[member_id]["name"]} · {db.normalize_name(member_by_id[member_id]["name"])}',
            )
            selected_ids = g2.multiselect(
                f"Người cùng đóng{REQUIRED}", list(member_by_id), default=default_group_ids,
                key=f"group_members_{draft_version}_{template['id'] if template else 0}",
                format_func=lambda member_id: f'{member_by_id[member_id]["name"]} · {db.normalize_name(member_by_id[member_id]["name"])}',
                help="Chỉ chọn những người phải chia khoản chi này.",
            )
            if honoree_id is not None and honoree_id in selected_ids:
                selected_ids.remove(honoree_id)
                st.info(f'Đã loại {member_by_id[honoree_id]["name"]} khỏi danh sách đóng góp.')
            group_participants = [member_by_id[member_id] for member_id in selected_ids]
            if group_participants:
                if debt_mode == "Chia đều cho nhóm":
                    preview = allocate_group(group_participants, group_total)
                    st.caption(f"Tổng chia: {money(group_total)} · {len(preview)} người")
                    st.dataframe(
                        pd.DataFrame([{"Người": row["person_name"], "Phải đóng": money(row["amount_due"])} for row in preview]),
                        hide_index=True, use_container_width=True,
                    )
                else:
                    st.caption(f"Nhập số tiền của từng người. Tổng phải đúng {money(group_total)}.")
                    amount_columns = st.columns(min(4, len(group_participants)))
                    for i, participant in enumerate(group_participants):
                        default_amount = group_total // len(group_participants)
                        group_custom_amounts[str(participant["id"])] = money_input(
                            amount_columns[i % len(amount_columns)], f'{participant["name"]}{REQUIRED}', default_amount,
                            key=f'group_amount_{draft_version}_{participant["id"]}',
                        )
                    custom_sum = sum(group_custom_amounts.values())
                    st.caption(f'Đã phân: {money(custom_sum)} / {money(group_total)}')
        line_subtotal = sum(int(row["quantity"]) * int(row["unit_price"]) for row in raw_items)
        detected_subtotal = parse_money(draft.get("subtotal", 0))
        detected_total = parse_money(draft.get("total", 0))
        expected_bill_total = max(0, line_subtotal + shipping + tip - discount)
        declared_count = int(draft.get("item_count") or 0)
        if declared_count and declared_count != entered_item_count:
            review_issues.append(f'AI đọc {declared_count} món nhưng form hiện có {entered_item_count} dòng món.')
        if detected_subtotal and abs(detected_subtotal - line_subtotal) > 1:
            review_issues.append(f'Tổng các món ({money(line_subtotal)}) lệch subtotal AI đọc ({money(detected_subtotal)}).')
        if detected_total and abs(detected_total - expected_bill_total) > 1:
            review_issues.append(f'Tổng hóa đơn dự kiến ({money(expected_bill_total)}) lệch số thực trả AI đọc ({money(detected_total)}).')
        if debt_mode == "Theo từng món" and any(row["person_name"] == "Chưa xác định" for row in raw_items):
            review_issues.append("Có món chưa gán người đặt; bạn có thể chia sẻ link nhận món sau khi lưu.")
        review_issues = list(dict.fromkeys(review_issues))
        review_confirmed = True
        if review_issues:
            st.warning("Cần kiểm tra kết quả quét trước khi lưu:\n\n" + "\n".join(f"- {issue}" for issue in review_issues))
            review_key = f'bill_review_ack_{draft_version}_{abs(hash(tuple(review_issues)))}'
            review_confirmed = st.checkbox("Tôi đã kiểm tra các cảnh báo và xác nhận số liệu trước khi lưu", key=review_key)
        submitted = st.button("Tính và lưu đơn", type="primary", use_container_width=True, disabled=not review_confirmed)
    if submitted:
        if not title.strip():
            st.error("Tên đơn là thông tin bắt buộc.")
        elif not raw_items:
            st.error("Cần ít nhất một món hợp lệ.")
        elif any(item["unit_price"] <= 0 for item in raw_items):
            st.error("Đơn giá của tất cả món phải lớn hơn 0.")
        elif allocation_errors:
            st.error("Không thể lưu vì tổng số lượng phân bổ chưa khớp:\n- " + "\n- ".join(allocation_errors))
        elif debt_mode != "Theo từng món" and not group_participants:
            st.error("Vui lòng chọn ít nhất một người cùng đóng góp.")
        elif debt_mode == "Nhập số tiền từng người" and sum(group_custom_amounts.values()) != group_total:
            st.error(f"Tổng số tiền từng người phải đúng {money(group_total)}.")
        elif payer_name == "Chưa xác định":
            st.error("Vui lòng chọn người ứng tiền.")
        else:
            method = "equal"
            subtotal = sum(x["quantity"] * x["unit_price"] for x in raw_items)
            expected = max(0, subtotal + shipping + tip - discount - support)
            if debt_mode == "Theo từng món":
                calculated = allocate_items(raw_items, shipping, discount, tip, support, method)
            else:
                # Giữ các dòng món để đối chiếu nhưng công nợ đến từ nhóm đóng góp.
                source_items = allocate_items(raw_items)
                for source in source_items:
                    source["amount_due"] = source["allocated_fee"] = source["allocated_discount"] = 0
                custom = group_custom_amounts if debt_mode == "Nhập số tiền từng người" else None
                calculated = source_items + allocate_group(group_participants, expected, custom)
                method = "group_custom" if custom is not None else "group_equal"
            payer = name_to_member[payer_name]
            advances = [{"member_id": payer["id"], "person_name": payer_name, "amount": expected}]
            data = {"title": title.strip(), "restaurant": restaurant.strip(), "payer_member_id": payer["id"], "subtotal": subtotal, "shipping_fee": shipping, "discount": discount, "tip": tip, "company_support": support, "allocation_method": method, "payment_method": "Chuyển khoản", "notes": ""}
            session_id = db.create_session(data, calculated, advances, save_image(upload))
            st.session_state.pop("draft", None)
            st.session_state.pop("scan_signature", None)
            st.session_state.draft_version = draft_version + 1
            st.session_state.input_version = input_version + 1
            st.session_state.bill_saved_message = True
            st.session_state.bill_saved_message_text = f"Đã lưu đơn #{session_id}, tổng cần thu {money(expected)}. Tổng quan đã được cập nhật."
            st.rerun()

@st.fragment(run_every="5s")
def render_history():
    st.subheader("Lịch sử và thu tiền")
    h1, h2, h3 = st.columns([2, 1, 1])
    keyword = h1.text_input("Tìm tên đơn, quán hoặc thành viên")
    status_filter = h2.selectbox("Trạng thái", ["Tất cả", "Chưa thu đủ", "Đã thu đủ"])
    payer_filter = h3.selectbox("Người ứng", ["Tất cả"] + [m["name"] for m in member_options()], format_func=name_search_label)
    sessions = db.query("""SELECT s.*,m.name payer_name,m.bank_name payer_bank_name,m.bank_account payer_bank_account
                            FROM sessions s LEFT JOIN members m ON m.id=s.payer_member_id ORDER BY s.id DESC""")
    for session in sessions:
        items = db.query("SELECT * FROM items WHERE session_id=?", (session["id"],))
        payments = db.query("SELECT * FROM payments WHERE session_id=? ORDER BY paid_at DESC", (session["id"],))
        claims = db.query("""SELECT c.*,m.name person_name FROM item_claims c JOIN members m ON m.id=c.member_id
                              JOIN items i ON i.id=c.item_id WHERE i.session_id=?""", (session["id"],))
        balances = person_balances(items, payments, claims)
        searchable = db.normalize_name(" ".join([session["title"], session.get("restaurant") or "", *(x["person_name"] for x in balances)]))
        if keyword and db.normalize_name(keyword) not in searchable:
            continue
        if payer_filter != "Tất cả" and session.get("payer_name") != payer_filter:
            continue
        session["bank_name"] = session.pop("payer_bank_name", "")
        session["bank_account"] = session.pop("payer_bank_account", "")
        remaining = sum(x["remaining"] for x in balances)
        if status_filter == "Chưa thu đủ" and remaining == 0:
            continue
        if status_filter == "Đã thu đủ" and remaining > 0:
            continue
        label = f'{"✅" if remaining == 0 else "⏳"} #{session["id"]} · {session["title"]} · còn {money(remaining)}'
        with st.expander(label, expanded=remaining > 0):
            st.caption(f'{session["date"]} · {session.get("restaurant") or "Không rõ quán"} · Người ứng: {session.get("payer_name") or "Chưa xác định"}')
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Tiền món", money(session["subtotal"]))
            m2.metric("Phí + tip", money(session["shipping_fee"] + session["tip"]))
            m3.metric("Giảm + hỗ trợ", money(session["discount"] + session["company_support"]))
            m4.metric("Còn thu", money(remaining))
            table = pd.DataFrame([{"Người": x["person_name"], "Phải trả": money(x["due"]), "Đã trả": money(x["paid"]), "Còn nợ": money(x["remaining"])} for x in balances])
            st.dataframe(table, hide_index=True, use_container_width=True)
            cols = st.columns(min(4, max(1, len(balances))))
            for i, balance in enumerate(balances):
                if balance["remaining"] > 0 and cols[i % len(cols)].button(f'Nhận tiền · {balance["person_name"]}', key=f'pay_btn_{session["id"]}_{i}'):
                    payment_dialog(session["id"], balance)
            base_url = str(secret("APP_BASE_URL", "")).rstrip("/")
            share_url = f"{base_url}?bill={session['share_token']}" if base_url else ""
            claimable_count = 0
            for item in items:
                if item.get("member_id") or item.get("amount_due", 0) <= 0:
                    continue
                claimed_quantity = sum(int(claim["quantity"]) for claim in claims if claim["item_id"] == item["id"])
                if claimed_quantity < int(item.get("quantity") or 1):
                    claimable_count += 1
            if claimable_count:
                st.info(f"🍽️ Có {claimable_count} món chưa xác định người. Gửi link để mọi người tự nhận món.")
            else:
                st.caption("✅ Các món đã có người nhận. Link chia sẻ vẫn dùng để mọi người xem công nợ.")
            share_c1, share_c2 = st.columns([1, 2], vertical_alignment="bottom")
            if share_url:
                share_c1.link_button("🔗 Mở trang chia sẻ", share_url, use_container_width=True)
                share_c2.text_input("Link gửi vào Teams", share_url, key=f"share_link_{session['id']}")
                if "localhost" in base_url or "127.0.0.1" in base_url:
                    st.warning("Link localhost chỉ mở được trên máy của bạn. Muốn đồng nghiệp truy cập, hãy dùng địa chỉ IP mạng nội bộ hoặc triển khai ứng dụng lên Internet.")
            else:
                st.warning("Chưa cấu hình APP_BASE_URL nên chưa tạo được link đầy đủ. Xem hướng dẫn trong tab Cài đặt.")
            with st.popover("📨 Nhắc thanh toán / gửi Teams"):
                text = reminder_text(session, balances)
                text_with_link = text + (f"\n\nTự nhận món/xem công nợ: {share_url}" if share_url else "")
                st.text_area("Nội dung", text_with_link, height=180, key=f"reminder_{session['id']}")
                reminder_logs = db.query("SELECT * FROM reminder_logs WHERE session_id=? ORDER BY id DESC", (session["id"],))
                latest_reminder = reminder_logs[0] if reminder_logs else None
                recent_reminder = False
                if latest_reminder:
                    st.caption(f'Đã nhắc {latest_reminder["recipient_count"]} người · {latest_reminder["channel"]} · {latest_reminder["sent_at"]}')
                    try:
                        elapsed = (datetime.now() - datetime.fromisoformat(latest_reminder["sent_at"])).total_seconds()
                        recent_reminder = elapsed < 30 * 60
                    except (TypeError, ValueError):
                        pass
                else:
                    st.caption("Chưa ghi nhận lần nhắc thanh toán nào cho đơn này.")
                if recent_reminder:
                    st.warning("Đơn vừa được nhắc trong 30 phút qua. Xác nhận bên dưới nếu bạn vẫn muốn gửi nhắc lại.")
                    allow_repeat = st.checkbox("Tôi vẫn muốn nhắc lại", key=f'repeat_reminder_{session["id"]}_{latest_reminder["id"]}')
                else:
                    allow_repeat = True
                if share_url:
                    st.code(share_url, language=None)
                if base_url:
                    st.image(qr_png(share_url), width=180, caption="Quét để xem công nợ của đơn")
                else:
                    st.caption("Cấu hình APP_BASE_URL để tạo link và mã QR chia sẻ.")
                webhook = secret("NOTIFICATION_WEBHOOK_URL")
                telegram_token = secret("TELEGRAM_BOT_TOKEN")
                telegram_chat_id = secret("TELEGRAM_CHAT_ID")
                unpaid_count = sum(1 for balance in balances if balance["remaining"] > 0)
                action_cols = st.columns(3)
                if action_cols[0].button("Đã gửi nhắc thủ công", key=f'manual_reminder_{session["id"]}', disabled=not unpaid_count or not allow_repeat, use_container_width=True):
                    db.log_reminder(session["id"], "Thủ công", unpaid_count, text_with_link)
                    st.success("Đã lưu lịch sử nhắc nợ.")
                    st.rerun(scope="fragment")
                if action_cols[1].button("Gửi Telegram", key=f"telegram_{session['id']}", disabled=not telegram_token or not telegram_chat_id or not unpaid_count or not allow_repeat, use_container_width=True):
                    try:
                        if send_telegram(telegram_token, telegram_chat_id, text_with_link):
                            db.log_reminder(session["id"], "Telegram", unpaid_count, text_with_link)
                            st.success("Đã gửi Telegram và lưu lịch sử nhắc nợ.")
                            st.rerun(scope="fragment")
                    except Exception as exc:
                        st.error(f"Gửi Telegram thất bại: {exc}")
                if action_cols[2].button("Gửi webhook", key=f"hook_{session['id']}", disabled=not webhook or not unpaid_count or not allow_repeat, use_container_width=True):
                    try:
                        if send_webhook(webhook, text_with_link):
                            db.log_reminder(session["id"], "Webhook", unpaid_count, text_with_link)
                            st.success("Đã gửi và lưu lịch sử nhắc nợ.")
                            st.rerun(scope="fragment")
                        else:
                            st.error("Dịch vụ từ chối yêu cầu.")
                    except Exception as exc:
                        st.error(f"Gửi thất bại: {exc}")
                if not webhook:
                    st.caption("Cấu hình NOTIFICATION_WEBHOOK_URL để gửi Slack/Teams.")
                if not telegram_token or not telegram_chat_id:
                    st.caption("Cấu hình TELEGRAM_BOT_TOKEN và TELEGRAM_CHAT_ID để gửi qua Telegram.")
                if reminder_logs:
                    with st.expander(f"Lịch sử nhắc ({len(reminder_logs)} lần)"):
                        for reminder in reminder_logs:
                            st.caption(f'{reminder["sent_at"]} · {reminder["channel"]} · {reminder["recipient_count"]} người')
            if session.get("image_path") and Path(session["image_path"]).exists():
                with st.popover("🖼️ Ảnh gốc"):
                    st.image(session["image_path"], use_container_width=True)
            if payments:
                with st.popover("Lịch sử thanh toán"):
                    st.dataframe(pd.DataFrame(payments)[["person_name", "amount", "method", "note", "paid_at"]], hide_index=True)
            if st.button("🗑️ Xóa đơn", key=f"delete_{session['id']}"):
                confirm_delete(session)

if active_page == "📚 Lịch sử":
    render_history()


if active_page == "👥 Thành viên":
    st.subheader("Danh bạ thành viên")
    with st.form("add_member", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        name = c1.text_input(f"Tên hiển thị{REQUIRED}")
        aliases = c2.text_input("Biệt danh", help="Phân cách bằng dấu phẩy")
        department = c3.text_input("Phòng ban")
        bank_account = c1.text_input("Số tài khoản")
        bank_name = c2.text_input("Ngân hàng")
        if st.form_submit_button("Thêm thành viên"):
            if not name.strip():
                st.error("Tên hiển thị là thông tin bắt buộc.")
            else:
              try:
                db.add_member(name, aliases, department, bank_account, bank_name)
                st.success("Đã thêm thành viên.")
                st.rerun()
              except Exception:
                st.error("Tên không được để trống hoặc đã tồn tại.")
    members = member_options(False)
    if members:
        edited = st.data_editor(pd.DataFrame(members)[["id", "name", "aliases", "department", "bank_account", "bank_name", "active"]], hide_index=True, use_container_width=True, disabled=["id"], key="member_editor")
        if st.button("Lưu thay đổi thành viên"):
            with db.connect() as conn:
                for row in edited.to_dict("records"):
                    conn.execute("UPDATE members SET name=?,aliases=?,department=?,bank_account=?,bank_name=?,active=? WHERE id=?", (row["name"], row["aliases"], row["department"], row["bank_account"], row["bank_name"], int(row["active"]), row["id"]))
            st.success("Đã cập nhật.")
        st.divider()
        st.subheader("Mẫu nhóm thường dùng")
        with st.form("group_template", clear_on_submit=True):
            template_name = st.text_input(f"Tên mẫu{REQUIRED}", placeholder="Ví dụ: Phòng IT, Nhóm trà sữa")
            template_member_by_id = {member["id"]: member for member in members}
            template_member_ids = st.multiselect(
                f"Thành viên{REQUIRED}", list(template_member_by_id),
                format_func=lambda member_id: f'{template_member_by_id[member_id]["name"]} · {db.normalize_name(template_member_by_id[member_id]["name"])}',
            )
            if st.form_submit_button("Lưu mẫu nhóm"):
                if not template_name.strip() or not template_member_ids:
                    st.error("Vui lòng nhập tên và chọn ít nhất một thành viên.")
                else:
                    try:
                        db.execute("INSERT INTO group_templates(name,member_ids,created_at) VALUES(?,?,?)", (template_name.strip(), ",".join(str(member_id) for member_id in template_member_ids), datetime.now().isoformat(timespec="seconds")))
                        st.success("Đã lưu mẫu nhóm.")
                        st.rerun()
                    except Exception:
                        st.error("Tên mẫu đã tồn tại.")
        for saved_template in db.query("SELECT * FROM group_templates ORDER BY name"):
            tc1, tc2 = st.columns([5, 1], vertical_alignment="center")
            ids = {int(value) for value in saved_template["member_ids"].split(",") if value}
            tc1.write(f'**{saved_template["name"]}** · {len(ids)} thành viên')
            if tc2.button("🗑️", key=f'delete_template_{saved_template["id"]}', help="Xóa mẫu"):
                db.execute("DELETE FROM group_templates WHERE id=?", (saved_template["id"],))
                st.rerun()

if active_page == "📈 Báo cáo":
    st.subheader("Báo cáo")
    r1, r2 = st.columns(2)
    date_from = r1.date_input("Từ ngày", value=datetime.now().date().replace(day=1))
    date_to = r2.date_input("Đến ngày", value=datetime.now().date())
    sessions, debts = all_debts(date_from, date_to)
    if debts:
        report = pd.DataFrame(debts)
        report_display = report.copy()
        for column in ["Phải trả", "Đã trả", "Còn nợ"]:
            report_display[column] = report_display[column].map(money)
        st.dataframe(report_display, hide_index=True, use_container_width=True)
        st.download_button("⬇️ Xuất Excel", excel_report(sessions, debts), file_name=f"bao-cao-{date_from}-{date_to}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.download_button("⬇️ Xuất CSV", report.to_csv(index=False).encode("utf-8-sig"), file_name=f"cong-no-{date_from}-{date_to}.csv", mime="text/csv")
    else:
        st.info("Không có dữ liệu trong khoảng thời gian này.")

if active_page == "⚙️ Cài đặt":
    st.subheader("An toàn dữ liệu và tích hợp")
    st.write("Tải bản sao lưu gồm database và toàn bộ ảnh hóa đơn. Nên thực hiện định kỳ.")
    st.download_button("💾 Tải bản sao lưu ZIP", backup_zip(DB_NAME, IMAGE_DIR), file_name=f"bill-master-backup-{datetime.now():%Y%m%d-%H%M}.zip", mime="application/zip")
    st.divider()
    checks = {
        "Mật khẩu ứng dụng": bool(secret("APP_PASSWORD")),
        "Gemini AI": bool(secret("GEMINI_API_KEY")),
        "Webhook Slack/Teams": bool(secret("NOTIFICATION_WEBHOOK_URL")),
        "Telegram Bot": bool(secret("TELEGRAM_BOT_TOKEN") and secret("TELEGRAM_CHAT_ID")),
        "Địa chỉ ứng dụng để tạo QR": bool(secret("APP_BASE_URL")),
    }
    for label, configured in checks.items():
        st.write(f'{"✅" if configured else "⚠️"} {label}: {"đã cấu hình" if configured else "chưa cấu hình"}')
    st.caption("Thiết lập các giá trị trong .streamlit/secrets.toml hoặc biến môi trường. Không nhập khóa bí mật trực tiếp vào giao diện.")
    with st.expander("Cấu hình Telegram để demo nhắc nợ"):
        st.markdown("""
        1. Mở Telegram, tìm **@BotFather**, gửi `/newbot` và làm theo hướng dẫn để tạo bot. Lưu token BotFather trả về.
        2. Mở cuộc trò chuyện với bot mới tạo, bấm **Start** và gửi `/start`.
        3. Lấy chat ID cá nhân bằng cách nhắn **@userinfobot**. Nếu gửi vào nhóm, thêm bot vào nhóm, gửi `/start@TenBot` trong nhóm rồi lấy chat ID nhóm từ bot quản lý chat hoặc Telegram Bot API `getUpdates`.
        4. Điền token và chat ID vào `.streamlit/secrets.toml` theo mẫu trong README, rồi khởi động lại Streamlit.

        Token là mật khẩu của bot: không dán vào chat nhóm, mã nguồn, ảnh chụp hoặc kho Git. Tin nhắn được gửi từ bot tới chat đã cấu hình; ứng dụng không đọc tin nhắn Telegram.
        """)
        telegram_token = secret("TELEGRAM_BOT_TOKEN")
        telegram_chat_id = secret("TELEGRAM_CHAT_ID")
        if telegram_token and telegram_chat_id:
            if st.button("Gửi tin nhắn thử tới Telegram", key="telegram_test_message"):
                try:
                    send_telegram(telegram_token, telegram_chat_id, "Bill Master: kết nối Telegram thành công. Bạn có thể dùng bot này để demo nhắc thanh toán.")
                    st.success("Đã gửi tin nhắn thử. Mở Telegram để kiểm tra.")
                except Exception as exc:
                    st.error(f"Gửi tin nhắn thử thất bại: {exc}")
        else:
            st.info("Sau khi cấu hình đủ token và chat ID, nút gửi tin nhắn thử sẽ xuất hiện tại đây.")
    with st.expander("Cách bật link tự nhận món khi chạy local"):
        st.markdown("""
        1. Chạy `ipconfig` và lấy địa chỉ **IPv4** của máy, ví dụ `192.168.1.25`.
        2. Đặt `APP_BASE_URL = "http://192.168.1.25:8501"` trong `.streamlit/secrets.toml`.
        3. Khởi động bằng `python -m streamlit run app.py --server.address 0.0.0.0`.
        4. Đồng nghiệp phải ở cùng mạng Wi-Fi/LAN và dùng mật khẩu ứng dụng để mở link.

        Nếu triển khai lên Internet, hãy đặt `APP_BASE_URL` thành tên miền HTTPS của ứng dụng.
        """)
