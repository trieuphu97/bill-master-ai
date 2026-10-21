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
from ui import balance_cards, brand_html, format_datetime, page_header, stylesheet

from database import Database
from services import allocate_group, allocate_items, allocate_items_with_shared_promos, shared_promo_discount, backup_zip, excel_report, money, parse_money, person_balances, qr_png, send_telegram, send_webhook


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
st.markdown(f"<style>{stylesheet()}</style>", unsafe_allow_html=True)


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


def reset_new_bill():
    """Discard only the current unsaved bill and its uploaded images."""
    st.session_state.pop("draft", None)
    st.session_state.pop("scan_signature", None)
    st.session_state["draft_version"] = st.session_state.get("draft_version", 0) + 1
    st.session_state["input_version"] = st.session_state.get("input_version", 0) + 1


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
     "items":[{"person_name":"", "raw_person_note":"", "item_name":"", "quantity":1, "unit_price":0, "original_unit_price":0, "confidence":0.0}]}
    Giá là số nguyên VND và luôn là số dương; riêng discount cũng trả về trị tuyệt đối dương.
    unit_price là giá thực tế đang tính trên hóa đơn. Nếu món có giá gốc gạch ngang (ví dụ 54.000đ còn 1đ),
    ghi giá gạch ngang vào original_unit_price; nếu không có thì trả 0. Không cộng ưu đãi này vào discount của hóa đơn.
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
        if amount <= 0 or amount > balance["remaining"]:
            st.error(f"Số tiền phải từ 1 đến {money(balance['remaining'])}.")
        else:
            try:
                db.add_payment(session_id, balance.get("member_id"), balance["person_name"], amount, method, note)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))


@st.dialog("Sửa khoản thanh toán")
def edit_payment_dialog(payment):
    st.caption(f'{payment["person_name"]} · {format_datetime(payment["paid_at"])}')
    amount = money_input(st, f"Số tiền đã nhận{REQUIRED}", payment["amount"], key=f'edit_payment_amount_{payment["id"]}')
    methods = ["Chuyển khoản", "Tiền mặt", "Ví điện tử", "Khác", "legacy"]
    method = st.selectbox("Hình thức", methods,
                          index=methods.index(payment["method"]) if payment["method"] in methods else 3,
                          key=f'edit_payment_method_{payment["id"]}')
    note = st.text_input("Ghi chú", value=payment.get("note") or "", key=f'edit_payment_note_{payment["id"]}')
    reason = st.text_input(f"Lý do sửa{REQUIRED}", key=f'edit_payment_reason_{payment["id"]}')
    if st.button("Đóng", key=f'close_edit_payment_{payment["id"]}'):
        st.session_state.pop("payment_dialog_action", None)
        st.rerun()
    if st.button("Lưu khoản thanh toán", type="primary"):
        try:
            db.update_payment(payment["id"], amount, method, note, reason)
            st.session_state.pop("payment_dialog_action", None)
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))


@st.dialog("Hủy khoản thanh toán")
def delete_payment_dialog(payment):
    st.warning(f'Bạn muốn hủy khoản {money(payment["amount"])} đã ghi nhận từ {payment["person_name"]}? Công nợ sẽ được tính lại.')
    reason = st.text_input(f"Lý do hủy{REQUIRED}", key=f'delete_payment_reason_{payment["id"]}')
    left, right = st.columns(2)
    if left.button("Giữ lại", use_container_width=True):
        st.session_state.pop("payment_dialog_action", None)
        st.rerun()
    if right.button("Hủy khoản này", type="primary", use_container_width=True):
        try:
            db.delete_payment(payment["id"], reason)
            st.session_state.pop("payment_dialog_action", None)
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))


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
                with st.container(border=True, key=f'claim_card_{item["id"]}'):
                    title_col, state_col = st.columns([4, 1])
                    title_col.markdown(f'**{item["item_name"]}**')
                    title_col.caption(f'{money(item["unit_price"])} / phần · {claimed_qty}/{item["quantity"]} đã nhận')
                    if item.get("promo_original_price") and item["promo_original_price"] > item["unit_price"]:
                        title_col.caption(f'Giá gốc {money(item["promo_original_price"])} · ưu đãi chia chung cả đơn')
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
                            try:
                                db.remove_claim(item["id"], claiming_member["id"])
                                st.rerun(scope="fragment")
                            except ValueError as exc:
                                st.error(str(exc))
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
                        try:
                            db.remove_claim(item["id"], claiming_member["id"])
                            st.rerun(scope="fragment")
                        except ValueError as exc:
                            st.error(str(exc))
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


query_token = st.query_params.get("bill")
if query_token:
    shared = db.query("SELECT * FROM sessions WHERE share_token=?", (query_token,))
    if shared:
        session = shared[0]
        st.markdown(brand_html(), unsafe_allow_html=True)
        st.markdown(page_header(session["title"], "Cùng nhận món và theo dõi phần thanh toán của mình."), unsafe_allow_html=True)
        render_shared_claims_modern(session)
        if st.button("Đóng chế độ chia sẻ"):
            del st.query_params["bill"]
            st.rerun()
        st.divider()
        # Trang nhận món là một chế độ độc lập; không dựng các tab quản trị và
        # các bộ đếm tự làm mới ở phía dưới trong cùng phiên trình duyệt.
        st.stop()


navigation_options = ["📊 Tổng quan", "➕ Đơn mới", "📚 Lịch sử", "👥 Thành viên", "📈 Báo cáo", "⚙️ Cài đặt"]
with st.sidebar:
    st.markdown(brand_html(), unsafe_allow_html=True)
    active_page = st.radio(
        "Điều hướng", navigation_options, label_visibility="collapsed", key="main_navigation",
    )
    st.divider()
    st.caption("☀️ / 🌙 Đổi sáng/tối: menu ⋮ góc trên → Light / Dark.")
    st.caption("Quét hóa đơn, chia món và theo dõi thanh toán trong cùng một nơi.")

# Streamlit's dialog close (X/Escape) does not run the cancel button callback.
# Discard the pending edit whenever navigation leaves History, so returning to
# the page cannot reopen a dialog the user already dismissed.
if st.session_state.get("previous_page") != active_page:
    if active_page != "📚 Lịch sử":
        st.session_state.pop("payment_dialog_action", None)
        editing_id = st.session_state.pop("editing_session_id", None)
        if editing_id is not None:
            st.session_state.pop(f"edit_rows_{editing_id}", None)
    st.session_state.previous_page = active_page

page_descriptions = {
    "📊 Tổng quan": "Nắm nhanh chi tiêu chung và phần thanh toán của từng thành viên.",
    "➕ Đơn mới": "Tải ảnh hóa đơn, kiểm tra thông tin và chia chi phí cho mọi người.",
    "📚 Lịch sử": "Xem người ứng tiền, cập nhật thanh toán và chia sẻ đơn với nhóm.",
    "👥 Thành viên": "Quản lý danh bạ và các nhóm thường cùng đặt đồ ăn.",
    "📈 Báo cáo": "Đối chiếu chi tiêu và công nợ theo khoảng thời gian bạn chọn.",
    "⚙️ Cài đặt": "Sao lưu dữ liệu và kiểm tra các kết nối của Bill Master.",
}
st.markdown(page_header(active_page.split(" ", 1)[1], page_descriptions[active_page]), unsafe_allow_html=True)

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
        st.markdown(balance_cards([
            {"person_name": row["Người"], "remaining": row["Còn nợ"]}
            for row in frame.to_dict("records")
        ]), unsafe_allow_html=True)
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
                        range=["#149c87", "#d5533e"],
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
    scan_column, reset_column = st.columns([3, 1], vertical_alignment="bottom")
    if reset_column.button("↺ Làm lại", key="reset_new_bill", help="Xóa ảnh và thông tin đơn đang nhập; không ảnh hưởng các đơn đã lưu.", use_container_width=True):
        reset_new_bill()
        st.rerun()
    if scan_column.button("✨ Đọc hóa đơn bằng AI", disabled=not upload, use_container_width=True):
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
    with st.container(border=True, key="bill_editor"):
        a, b, c = st.columns(3)
        title = a.text_input(f"Tên đơn{REQUIRED}", value=f"Đồ ăn ngày {datetime.now():%d/%m}", key=f"new_bill_title_{draft_version}")
        restaurant = b.text_input("Quán", value=draft.get("restaurant", ""), key=f"new_bill_restaurant_{draft_version}")
        payer_name = c.selectbox(f"Người ứng tiền{REQUIRED}", names, format_func=name_search_label, key=f"new_bill_payer_{draft_version}")
        debt_mode = st.radio(
            "Cách tính công nợ",
            ["Theo từng món", "Chia đều cho nhóm", "Nhập số tiền từng người"],
            horizontal=True,
            key=f"new_bill_debt_mode_{draft_version}",
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
        promo_errors = []
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
            promo_key = f"promo_{row_id}"
            show_promo = (unit_price == 1 or
                          parse_money(item.get("original_unit_price")) > parse_money(item.get("unit_price")) or
                          bool(st.session_state.get(promo_key)))
            has_promo = st.checkbox(
                "🎁 Chia ưu đãi giá món cho cả đơn", key=f"promo_{row_id}",
                value=parse_money(item.get("original_unit_price")) > parse_money(item.get("unit_price")),
                help="Ví dụ món trên hóa đơn 1đ, giá gốc 54.000đ: người đặt được tính từ giá gốc, phần giảm 53.999đ chia chung cho cả đơn.",
            ) if show_promo else False
            promo_original_price = (money_input(
                st, f"Giá gốc trước ưu đãi{REQUIRED}", parse_money(item.get("original_unit_price")),
                key=f"promo_original_{row_id}",
            ) if has_promo else None)
            if has_promo and promo_original_price <= unit_price:
                promo_errors.append(f"{item_name or 'Món chưa đặt tên'}: giá gốc phải lớn hơn giá trên hóa đơn.")
            promo_fields = {"promo_original_price": promo_original_price}
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
                    raw_items.append({"member_id": None, "person_name": "Chưa xác định", "item_name": item_name.strip(), "quantity": quantity, "unit_price": unit_price, "ai_confidence": item_confidence, **promo_fields})
                elif quantity == 1:
                    member = name_to_member.get(person)
                    raw_items.append({"member_id": member["id"] if member else None, "person_name": person, "item_name": item_name.strip(), "quantity": 1, "unit_price": unit_price, "ai_confidence": item_confidence, **promo_fields})
                else:
                    allocation_mode = st.radio(
                        f"Phân bổ {quantity} × {item_name}",
                        ["Một người đặt tất cả", "Chia cho nhiều người"],
                        horizontal=True,
                        key=f"allocation_mode_{row_id}",
                    )
                    if allocation_mode == "Một người đặt tất cả":
                        member = name_to_member.get(person)
                        raw_items.append({"member_id": member["id"] if member else None, "person_name": person, "item_name": item_name.strip(), "quantity": quantity, "unit_price": unit_price, "ai_confidence": item_confidence, **promo_fields})
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
                            raw_items.append({"member_id": member["id"] if member else None, "person_name": split_person, "item_name": item_name.strip(), "quantity": int(split_qty), "unit_price": unit_price, "ai_confidence": item_confidence, **promo_fields})
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
        promo_total = shared_promo_discount(raw_items)
        if promo_total:
            st.caption(f"🎁 Ưu đãi giá món chia chung: {money(promo_total)} · giữ nguyên số tiền thực trả trên hóa đơn.")
        with st.expander("Tùy chọn: công ty hỗ trợ một phần"):
            support = money_input(st, "Số tiền công ty hỗ trợ", 0, key=f"support_{draft_version}")
        group_participants = []
        group_custom_amounts = {}
        group_total = max(0, sum(item["quantity"] * item["unit_price"] for item in raw_items) + shipping + tip - discount - support)
        if debt_mode != "Theo từng món":
            st.markdown("**Nhóm cùng đóng góp**")
            templates = db.query("SELECT * FROM group_templates ORDER BY name")
            template = st.selectbox("Mẫu nhóm", [None] + templates, format_func=lambda row: "Không dùng mẫu" if row is None else row["name"], key=f"new_bill_template_{draft_version}")
            template_ids = {int(value) for value in template["member_ids"].split(",") if value} if template else set()
            member_by_id = {member["id"]: member for member in members}
            default_group_ids = [member["id"] for member in members if member["id"] in template_ids]
            g1, g2 = st.columns([1, 2])
            honoree_id = g1.selectbox(
                "Người được tổ chức/miễn đóng", [None] + list(member_by_id),
                key=f"new_bill_honoree_{draft_version}",
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
        possible_duplicates = db.find_possible_duplicates(
            restaurant, raw_items, max(0, line_subtotal + shipping + tip - discount - support)
        )
        duplicate_confirmed = True
        if possible_duplicates:
            duplicate_labels = ", ".join(f'#{row["id"]} {row["title"]}' for row in possible_duplicates[:3])
            st.warning(f"Đơn này có thể đã được lưu: {duplicate_labels}. Kiểm tra Lịch sử trước khi tạo thêm.")
            duplicate_confirmed = st.checkbox(
                "Tôi đã kiểm tra và vẫn muốn lưu đơn mới",
                key=f'duplicate_ack_{draft_version}_{"_".join(str(row["id"]) for row in possible_duplicates)}',
            )
        submitted = st.button("Tính và lưu đơn", type="primary", use_container_width=True,
                              disabled=not review_confirmed or not duplicate_confirmed)
    if submitted:
        if not title.strip():
            st.error("Tên đơn là thông tin bắt buộc.")
        elif not raw_items:
            st.error("Cần ít nhất một món hợp lệ.")
        elif any(item["unit_price"] <= 0 for item in raw_items):
            st.error("Đơn giá của tất cả món phải lớn hơn 0.")
        elif promo_errors:
            st.error("Không thể chia ưu đãi:\n- " + "\n- ".join(promo_errors))
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
                calculated = allocate_items_with_shared_promos(raw_items, shipping, discount, tip, support, method)
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
            reset_new_bill()
            st.session_state.bill_saved_message = True
            st.session_state.bill_saved_message_text = f"Đã lưu đơn #{session_id}, tổng cần thu {money(expected)}. Tổng quan đã được cập nhật."
            st.rerun()

@st.dialog("Sửa đơn hàng", width="large")
def edit_session_dialog(session):
    current_items = db.query("SELECT * FROM items WHERE session_id=? ORDER BY id", (session["id"],))
    group_mode = session["allocation_method"] in {"group_equal", "group_custom"}
    contribution_name = "Phần đóng góp chi phí chung"
    contribution_rows = [row for row in current_items if group_mode and row["item_name"] == contribution_name]
    source_rows = [row for row in current_items if row not in contribution_rows]
    missing_source_prices = group_mode and any(row["unit_price"] == 0 for row in source_rows)
    claimed_ids = {claim["item_id"] for claim in db.query(
        "SELECT c.item_id FROM item_claims c JOIN items i ON i.id=c.item_id WHERE i.session_id=?",
        (session["id"],),
    )}
    state_key = f'edit_rows_{session["id"]}'
    if state_key not in st.session_state:
        st.session_state[state_key] = [dict(row) for row in source_rows]
    rows = st.session_state[state_key]
    members = member_options(False)
    member_by_id = {member["id"]: member for member in members}
    member_ids = list(member_by_id)
    st.caption(f'Đơn #{session["id"]} · Các khoản đã thu vẫn được giữ lại.')
    title = st.text_input(f"Tên đơn{REQUIRED}", value=session["title"], key=f'edit_title_{session["id"]}')
    restaurant = st.text_input("Quán", value=session.get("restaurant") or "", key=f'edit_restaurant_{session["id"]}')
    payer_id = st.selectbox(
        f"Người ứng tiền{REQUIRED}", member_ids,
        index=member_ids.index(session["payer_member_id"]) if session["payer_member_id"] in member_ids else 0,
        format_func=lambda member_id: member_by_id[member_id]["name"],
        key=f'edit_payer_{session["id"]}',
    )
    st.markdown("**Các món trong hóa đơn**")
    if missing_source_prices:
        st.warning("Đơn giá từng món của đơn cũ đã bị mất. Tổng tiền đơn vẫn được giữ. Bạn có thể sửa thông tin đơn; nếu muốn sửa giá hoặc số lượng, hãy nhập lại đầy đủ đơn giá từ hóa đơn gốc.")
    edited_rows = []
    promo_errors = []
    for index, row in enumerate(rows):
        row_key = row.get("_row_id") or str(row.get("id") or uuid.uuid4().hex)
        row["_row_id"] = row_key
        with st.container(border=True):
            a, b, c, d = st.columns([3, 1, 2, 1], vertical_alignment="bottom")
            item_name = a.text_input(f"Món {index + 1}{REQUIRED}", value=row["item_name"], key=f'edit_dish_{session["id"]}_{row_key}')
            quantity = b.number_input("SL", min_value=1, max_value=50, value=int(row["quantity"]),
                                      key=f'edit_qty_{session["id"]}_{row_key}')
            unit_price = money_input(c, "Đơn giá", row["unit_price"], key=f'edit_price_{session["id"]}_{row_key}')
            promo_key = f'edit_promo_{session["id"]}_{row_key}'
            show_promo = (unit_price == 1 or
                          bool(row.get("promo_original_price") and row["promo_original_price"] > row["unit_price"]) or
                          bool(st.session_state.get(promo_key)))
            has_promo = st.checkbox(
                "🎁 Chia ưu đãi giá món cho cả đơn",
                value=bool(row.get("promo_original_price") and row["promo_original_price"] > row["unit_price"]),
                key=promo_key,
            ) if show_promo else False
            promo_original_price = (money_input(
                st, "Giá gốc trước ưu đãi", row.get("promo_original_price") or 0,
                key=f'edit_promo_original_{session["id"]}_{row_key}',
            ) if has_promo else None)
            if has_promo and promo_original_price <= unit_price:
                promo_errors.append(f"{item_name or 'Món chưa đặt tên'}: giá gốc phải lớn hơn giá trên hóa đơn.")
            if d.button("🗑️", key=f'edit_remove_{session["id"]}_{row_key}',
                        help="Món đã có người nhận cần được bỏ nhận trước." if row.get("id") in claimed_ids else "Bỏ món",
                        disabled=row.get("id") in claimed_ids):
                rows.pop(index)
                st.rerun(scope="fragment")
            member_id = None
            person_name = "Chưa xác định"
            if not group_mode:
                options = [None] + member_ids
                member_id = st.selectbox(
                    "Người đặt", options, index=options.index(row.get("member_id")) if row.get("member_id") in options else 0,
                    format_func=lambda value: "Chưa xác định" if value is None else member_by_id[value]["name"],
                    key=f'edit_person_{session["id"]}_{row_key}',
                )
                person_name = member_by_id[member_id]["name"] if member_id is not None else "Chưa xác định"
                if row.get("id") in claimed_ids:
                    st.caption("Món đã có người nhận: giữ người đặt là 'Chưa xác định', hoặc bỏ nhận trước khi gán lại.")
            edited_rows.append({**row, "member_id": member_id, "person_name": person_name,
                                "item_name": item_name.strip(), "quantity": int(quantity), "unit_price": unit_price,
                                "promo_original_price": promo_original_price})
    if st.button("＋ Thêm món", key=f'edit_add_{session["id"]}'):
        rows[:] = edited_rows
        rows.append({"_row_id": uuid.uuid4().hex, "member_id": None, "person_name": "Chưa xác định",
                     "item_name": "", "quantity": 1, "unit_price": 0})
        st.rerun(scope="fragment")
    rows[:] = edited_rows
    x, y, z = st.columns(3)
    shipping = money_input(x, "Phí giao hàng", session["shipping_fee"], key=f'edit_shipping_{session["id"]}')
    discount = money_input(y, "Voucher/giảm giá", session["discount"], key=f'edit_discount_{session["id"]}')
    tip = money_input(z, "Tip/phụ phí", session["tip"], key=f'edit_tip_{session["id"]}')
    support = money_input(st, "Công ty hỗ trợ", session["company_support"], key=f'edit_support_{session["id"]}')
    prices_incomplete = group_mode and any(row["unit_price"] <= 0 for row in edited_rows)
    source_structure_changed = len(edited_rows) != len(source_rows) or any(
        row.get("id") != original["id"] or row["quantity"] != original["quantity"]
        or row["unit_price"] != original["unit_price"]
        or row.get("promo_original_price") != original.get("promo_original_price")
        for row, original in zip(edited_rows, source_rows)
    )
    subtotal = (session["subtotal"] if missing_source_prices and prices_incomplete
                else sum(row["quantity"] * row["unit_price"] for row in edited_rows))
    total = max(0, subtotal + shipping + tip - discount - support)
    promo_total = shared_promo_discount(edited_rows)
    if promo_total:
        st.caption(f"🎁 Ưu đãi giá món chia chung: {money(promo_total)}")
    st.caption(f"Tổng mới: {money(total)}")
    custom_amounts = {}
    if group_mode:
        st.markdown("**Người cùng đóng**")
        for group_row in contribution_rows:
            if session["allocation_method"] == "group_custom":
                custom_amounts[group_row["member_id"]] = money_input(
                    st, group_row["person_name"], group_row["amount_due"],
                    key=f'edit_group_amount_{session["id"]}_{group_row["id"]}',
                )
            else:
                st.caption(group_row["person_name"])
    cancel_col, save_col = st.columns(2)
    if cancel_col.button("Hủy chỉnh sửa", use_container_width=True):
        st.session_state.pop(state_key, None)
        st.session_state.pop("editing_session_id", None)
        st.rerun()
    if save_col.button("Lưu thay đổi đơn", type="primary", use_container_width=True):
        if not title.strip() or not edited_rows or any(not row["item_name"] for row in edited_rows) or (not group_mode and any(row["unit_price"] <= 0 for row in edited_rows)):
            st.error("Cần tên đơn và ít nhất một món có tên, đơn giá hợp lệ.")
        elif promo_errors:
            st.error("Không thể chia ưu đãi:\n- " + "\n- ".join(promo_errors))
        elif missing_source_prices and prices_incomplete and source_structure_changed:
            st.error("Cần nhập lại đầy đủ đơn giá trước khi đổi giá, số lượng, thêm hoặc xóa món trong đơn cũ.")
        elif group_mode and prices_incomplete and not missing_source_prices:
            st.error("Cần nhập đơn giá hợp lệ cho tất cả món.")
        elif group_mode and not contribution_rows:
            st.error("Đơn nhóm chưa có người cùng đóng.")
        elif group_mode and session["allocation_method"] == "group_custom" and sum(custom_amounts.values()) != total:
            st.error(f"Tổng tiền từng người phải đúng {money(total)}.")
        else:
            if group_mode:
                calculated = [{**row, "allocated_fee": 0, "allocated_discount": 0, "amount_due": 0}
                              for row in edited_rows]
                if session["allocation_method"] == "group_equal":
                    participants = [member_by_id[row["member_id"]] for row in contribution_rows]
                    amounts = allocate_group(participants, total)
                    calculated.extend({**row, "amount_due": amount["amount_due"], "unit_price": amount["unit_price"]}
                                      for row, amount in zip(contribution_rows, amounts))
                else:
                    calculated.extend({**row, "amount_due": custom_amounts[row["member_id"]],
                                       "unit_price": custom_amounts[row["member_id"]]}
                                      for row in contribution_rows)
            else:
                calculated = allocate_items_with_shared_promos(edited_rows, shipping, discount, tip, support, method="equal")
            data = {"title": title.strip(), "restaurant": restaurant.strip(), "payer_member_id": payer_id,
                    "subtotal": subtotal, "shipping_fee": shipping, "discount": discount, "tip": tip,
                    "company_support": support, "allocation_method": session["allocation_method"]}
            try:
                db.update_session(session["id"], session["revision"], data, calculated)
                st.session_state.pop(state_key, None)
                st.session_state.pop("editing_session_id", None)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))


@st.fragment(run_every="5s")
def render_history():
    st.subheader("Lịch sử và thu tiền")
    h1, h2, h3 = st.columns([2, 1, 1])
    keyword = h1.text_input("Tìm tên đơn, quán hoặc thành viên")
    status_filter = h2.selectbox("Trạng thái", ["Tất cả", "Chưa thu đủ", "Đã thu đủ"])
    payer_filter = h3.selectbox("Người ứng", ["Tất cả"] + [m["name"] for m in member_options()], format_func=name_search_label)
    filters = (db.normalize_name(keyword), status_filter, payer_filter)
    if st.session_state.get("history_filters") != filters:
        st.session_state.history_filters = filters
        st.session_state.history_page = 1
    page = max(1, int(st.session_state.get("history_page", 1)))
    total_count, sessions = db.history_page(keyword, status_filter, payer_filter, page)
    total_pages = max(1, (total_count + 9) // 10)
    if page > total_pages:
        page = total_pages
        st.session_state.history_page = page
        total_count, sessions = db.history_page(keyword, status_filter, payer_filter, page)
    results = st.container(key="history_results")
    if not sessions:
        results.info("Không có đơn phù hợp bộ lọc.")
    visible_ids = [session["id"] for session in sessions]
    items_by_session = {session_id: [] for session_id in visible_ids}
    payments_by_session = {session_id: [] for session_id in visible_ids}
    claims_by_session = {session_id: [] for session_id in visible_ids}
    changes_by_session = {session_id: [] for session_id in visible_ids}
    payment_changes_by_session = {session_id: [] for session_id in visible_ids}
    if visible_ids:
        placeholders = ",".join("?" for _ in visible_ids)
        for item in db.query(f"SELECT * FROM items WHERE session_id IN ({placeholders}) ORDER BY id", visible_ids):
            items_by_session[item["session_id"]].append(item)
        for payment in db.query(
            f"SELECT * FROM payments WHERE session_id IN ({placeholders}) ORDER BY paid_at DESC, id DESC", visible_ids,
        ):
            payments_by_session[payment["session_id"]].append(payment)
        for claim in db.query(
            "SELECT c.*,m.name person_name,i.session_id FROM item_claims c "
            "JOIN members m ON m.id=c.member_id JOIN items i ON i.id=c.item_id "
            f"WHERE i.session_id IN ({placeholders})", visible_ids,
        ):
            claims_by_session[claim["session_id"]].append(claim)
        for change in db.query(
            "SELECT entity_id,detail,created_at FROM audit_log WHERE entity_type='session' "
            f"AND action='edit' AND entity_id IN ({placeholders}) ORDER BY id DESC", visible_ids,
        ):
            changes_by_session[change["entity_id"]].append(change)
        for change in db.query(
            "SELECT detail,created_at FROM audit_log WHERE entity_type='payment' "
            f"AND CASE WHEN json_valid(detail) THEN json_extract(detail, '$.before.session_id') END IN ({placeholders}) "
            "ORDER BY id DESC", visible_ids,
        ):
            detail = json.loads(change["detail"])
            session_id = detail.get("before", {}).get("session_id")
            if session_id in payment_changes_by_session:
                payment_changes_by_session[session_id].append((change, detail))
    for session in sessions:
        items = items_by_session[session["id"]]
        payments = payments_by_session[session["id"]]
        claims = claims_by_session[session["id"]]
        balances = person_balances(items, payments, claims)
        session["bank_name"] = session.pop("payer_bank_name", "")
        session["bank_account"] = session.pop("payer_bank_account", "")
        remaining = sum(x["remaining"] for x in balances)
        label = f'{"✅" if remaining == 0 else "⏳"} #{session["id"]} · {session["title"]} · còn {money(remaining)}'
        with results.expander(label, expanded=remaining > 0):
            st.caption(f'{session["date"]} · {session.get("restaurant") or "Không rõ quán"} · Người ứng: {session.get("payer_name") or "Chưa xác định"}')
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Tiền món", money(session["subtotal"]))
            m2.metric("Phí + tip", money(session["shipping_fee"] + session["tip"]))
            m3.metric("Giảm + hỗ trợ", money(session["discount"] + session["company_support"]))
            m4.metric("Còn thu", money(remaining))
            promo_savings = shared_promo_discount(items)
            if promo_savings:
                st.caption(f"🎁 Ưu đãi giá món {money(promo_savings)} đã được chia chung khi tính công nợ; tổng thanh toán vẫn theo hóa đơn.")
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
            actions = st.container(horizontal=True, vertical_alignment="center", gap="small")
            with actions.popover("📨 Nhắc thanh toán"):
                text = reminder_text(session, balances)
                text_with_link = text + (f"\n\nTự nhận món/xem công nợ: {share_url}" if share_url else "")
                st.text_area("Nội dung", text_with_link, height=180, key=f"reminder_{session['id']}")
                reminder_logs = db.query("SELECT * FROM reminder_logs WHERE session_id=? ORDER BY id DESC", (session["id"],))
                latest_reminder = reminder_logs[0] if reminder_logs else None
                recent_reminder = False
                if latest_reminder:
                    st.caption(f'Đã nhắc {latest_reminder["recipient_count"]} người · {latest_reminder["channel"]} · {format_datetime(latest_reminder["sent_at"])}')
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
                with action_cols[0].container(border=True):
                    st.caption("Đã nhắc qua kênh khác")
                    manual_clicked = st.button("✓ Ghi nhận đã nhắc", key=f'manual_reminder_{session["id"]}',
                                               help="Chỉ lưu lịch sử; không gửi tin nhắn.",
                                               disabled=not unpaid_count or not allow_repeat, use_container_width=True)
                if manual_clicked:
                    db.log_reminder(session["id"], "Thủ công", unpaid_count, text_with_link)
                    st.success("Đã lưu lịch sử nhắc nợ.")
                    st.rerun()
                with action_cols[1].container(border=True):
                    st.caption("Gửi qua bot Telegram")
                    telegram_clicked = st.button("✈️ Gửi Telegram", key=f"telegram_{session['id']}", type="primary",
                                                 disabled=not telegram_token or not telegram_chat_id or not unpaid_count or not allow_repeat,
                                                 use_container_width=True)
                if telegram_clicked:
                    try:
                        if send_telegram(telegram_token, telegram_chat_id, text_with_link):
                            db.log_reminder(session["id"], "Telegram", unpaid_count, text_with_link)
                            st.success("Đã gửi Telegram và lưu lịch sử nhắc nợ.")
                            st.rerun()
                    except Exception as exc:
                        st.error(f"Gửi Telegram thất bại: {exc}")
                with action_cols[2].container(border=True):
                    st.caption("Gửi qua Slack / Teams")
                    webhook_clicked = st.button("↗ Gửi webhook", key=f"hook_{session['id']}", type="primary",
                                                disabled=not webhook or not unpaid_count or not allow_repeat,
                                                use_container_width=True)
                if webhook_clicked:
                    try:
                        if send_webhook(webhook, text_with_link):
                            db.log_reminder(session["id"], "Webhook", unpaid_count, text_with_link)
                            st.success("Đã gửi và lưu lịch sử nhắc nợ.")
                            st.rerun()
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
                            st.caption(f'{format_datetime(reminder["sent_at"])} · {reminder["channel"]} · {reminder["recipient_count"]} người')
            if session.get("image_path") and Path(session["image_path"]).exists():
                with actions.popover("🖼️ Ảnh gốc"):
                    st.image(session["image_path"], use_container_width=True)
            if payments:
                with st.expander("Lịch sử thanh toán"):
                    payment_table = pd.DataFrame([
                        {"Người": payment["person_name"], "Số tiền": money(payment["amount"]),
                         "Hình thức": payment["method"], "Ghi chú": payment["note"],
                         "Ngày trả": format_datetime(payment["paid_at"])}
                        for payment in payments
                    ])
                    selected = st.dataframe(
                        payment_table, hide_index=True, use_container_width=True,
                        on_select="rerun", selection_mode="single-row",
                        key=f'payment_history_{session["id"]}',
                    )
                    selected_rows = selected.selection.rows
                    selected_key = f'selected_payment_{session["id"]}'
                    if selected_rows and selected_rows[0] < len(payments):
                        st.session_state[selected_key] = payments[selected_rows[0]]["id"]
                    payment = next((row for row in payments if row["id"] == st.session_state.get(selected_key)), None)
                    if payment:
                        if payment["method"] == "Tự đối trừ":
                            st.caption("Khoản tự đối trừ được tính theo đơn nên không sửa hoặc hủy tại đây.")
                        else:
                            st.caption(f'Đã chọn: {payment["person_name"]} · {money(payment["amount"])}')
                            edit_col, remove_col = st.columns(2)
                            if edit_col.button("✏️ Sửa khoản thu", key=f'edit_payment_{payment["id"]}'):
                                pending_edit = st.session_state.pop("editing_session_id", None)
                                if pending_edit is not None:
                                    st.session_state.pop(f'edit_rows_{pending_edit}', None)
                                st.session_state.payment_dialog_action = ("edit", payment["id"])
                                st.rerun()
                            if remove_col.button("🗑️ Hủy khoản thu", key=f'delete_payment_{payment["id"]}'):
                                pending_edit = st.session_state.pop("editing_session_id", None)
                                if pending_edit is not None:
                                    st.session_state.pop(f'edit_rows_{pending_edit}', None)
                                st.session_state.payment_dialog_action = ("delete", payment["id"])
                                st.rerun()
                    else:
                        st.caption("Chọn một dòng trong bảng để sửa hoặc hủy khoản thu.")
            if actions.button("✏️ Sửa đơn", key=f'edit_session_{session["id"]}'):
                st.session_state.pop("payment_dialog_action", None)
                st.session_state.editing_session_id = session["id"]
                st.rerun()
            changes = changes_by_session[session["id"]]
            payment_changes = payment_changes_by_session[session["id"]]
            if changes or payment_changes:
                with actions.popover(f"Lịch sử điều chỉnh ({len(changes) + len(payment_changes)})"):
                    for change in changes:
                        detail = json.loads(change["detail"])
                        st.caption(f'Sửa đơn · {format_datetime(change["created_at"])} · {detail["before"]["session"]["title"]} → {detail["after"]["data"]["title"]}')
                    for change, detail in payment_changes:
                        before = detail["before"]
                        after = detail["after"]
                        result = "Đã hủy" if after is None else f'{money(before["amount"])} → {money(after["amount"])}'
                        st.caption(f'{format_datetime(change["created_at"])} · {before["person_name"]} · {result} · {detail["reason"]}')
            if actions.button("🗑️ Xóa đơn", key=f"delete_{session['id']}"):
                confirm_delete(session)
    prev_col, count_col, next_col = st.columns([1, 3, 1], vertical_alignment="center")
    if prev_col.button("← Trước", key="history_previous", disabled=page <= 1, use_container_width=True):
        st.session_state.history_page = page - 1
        st.rerun()
    count_col.caption(f"{total_count} đơn · Trang {page}/{total_pages}")
    if next_col.button("Sau →", key="history_next", disabled=page >= total_pages, use_container_width=True):
        st.session_state.history_page = page + 1
        st.rerun()

if active_page == "📚 Lịch sử":
    render_history()
    payment_action = st.session_state.get("payment_dialog_action")
    if payment_action:
        selected_payment = db.query("SELECT * FROM payments WHERE id=?", (payment_action[1],))
        if selected_payment:
            if payment_action[0] == "edit":
                edit_payment_dialog(selected_payment[0])
            elif payment_action[0] == "delete":
                delete_payment_dialog(selected_payment[0])
        else:
            st.session_state.pop("payment_dialog_action", None)
    editing_session_id = st.session_state.get("editing_session_id")
    if editing_session_id is not None and not payment_action:
        editing_session = db.query("SELECT * FROM sessions WHERE id=?", (editing_session_id,))
        if editing_session:
            edit_session_dialog(editing_session[0])
        else:
            st.session_state.pop("editing_session_id", None)


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
