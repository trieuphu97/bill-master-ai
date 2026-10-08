import io
import json
import re
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

def money(value):
    return f"{int(value or 0):,} đ"


def parse_money(value):
    if isinstance(value, (int, float)):
        return max(0, int(value))
    return max(0, int(re.sub(r"\D", "", str(value)) or 0))


def allocate_items(items, shipping_fee=0, discount=0, tip=0, company_support=0, method="proportional"):
    clean = []
    for raw in items:
        qty = max(1, int(raw.get("quantity", 1)))
        price = parse_money(raw.get("unit_price", 0))
        clean.append({**raw, "quantity": qty, "unit_price": price, "base": qty * price})
    subtotal = sum(x["base"] for x in clean)
    net_extra = shipping_fee + tip - discount - company_support
    if not clean:
        return clean
    weights = [1 / len(clean)] * len(clean) if method == "equal" or subtotal == 0 else [x["base"] / subtotal for x in clean]
    shares = [round(net_extra * w) for w in weights]
    shares[-1] += net_extra - sum(shares)
    for item, share in zip(clean, shares):
        item["allocated_fee"] = max(0, share)
        item["allocated_discount"] = max(0, -share)
        item["amount_due"] = max(0, item["base"] + share)
    return clean


def person_balances(items, payments, claims=None):
    people = {}
    claims_by_item = {}
    for claim in claims or []:
        claims_by_item.setdefault(claim["item_id"], []).append(claim)
    for item in items:
        item_claims = claims_by_item.get(item.get("id"), [])
        if item_claims:
            total_qty = max(1, int(item.get("quantity") or 1))
            total_due = int(item.get("amount_due") or 0)
            unit, remainder = divmod(total_due, total_qty)
            assigned_qty = 0
            assigned_due = 0
            for claim in item_claims:
                qty = int(claim["quantity"])
                claim_due = unit * qty + min(remainder, qty)
                remainder = max(0, remainder - qty)
                key = claim["member_id"]
                row = people.setdefault(key, {"member_id": key, "person_name": claim["person_name"], "due": 0, "paid": 0})
                row["due"] += claim_due
                assigned_qty += qty
                assigned_due += claim_due
            if assigned_qty < total_qty:
                row = people.setdefault("Chưa xác định", {"member_id": None, "person_name": "Chưa xác định", "due": 0, "paid": 0})
                row["due"] += max(0, total_due - assigned_due)
        else:
            key = item.get("member_id") or item.get("person_name")
            row = people.setdefault(key, {"member_id": item.get("member_id"), "person_name": item.get("person_name") or "Chưa xác định", "due": 0, "paid": 0})
            row["due"] += int(item.get("amount_due") or 0)
    for payment in payments:
        key = payment.get("member_id") or payment.get("person_name")
        row = people.setdefault(key, {"member_id": payment.get("member_id"), "person_name": payment.get("person_name") or "Chưa xác định", "due": 0, "paid": 0})
        row["paid"] += int(payment.get("amount") or 0)
    for row in people.values():
        row["remaining"] = max(0, row["due"] - row["paid"])
    return [row for row in people.values() if row["due"] > 0 or row["paid"] > 0]


def allocate_group(participants, total, custom_amounts=None):
    """Chia chính xác một khoản chung; phần dư được cộng lần lượt từ đầu."""
    if not participants:
        return []
    total = max(0, int(total))
    if custom_amounts is not None:
        amounts = [max(0, int(custom_amounts.get(str(p.get("id") or p["name"]), 0))) for p in participants]
        if sum(amounts) != total:
            raise ValueError("Tổng số tiền tùy chỉnh phải bằng tổng thanh toán")
    else:
        base, remainder = divmod(total, len(participants))
        amounts = [base + (1 if i < remainder else 0) for i in range(len(participants))]
    return [
        {
            "member_id": participant.get("id"), "person_name": participant["name"],
            "item_name": "Phần đóng góp chi phí chung", "quantity": 1,
            "unit_price": amount, "allocated_fee": 0, "allocated_discount": 0,
            "amount_due": amount, "base": amount,
        }
        for participant, amount in zip(participants, amounts)
    ]


def excel_report(sessions, debt_rows):
    import pandas as pd

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(sessions).to_excel(writer, sheet_name="Đơn hàng", index=False)
        pd.DataFrame(debt_rows).to_excel(writer, sheet_name="Công nợ", index=False)
    return output.getvalue()


def backup_zip(db_path, image_dir):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        path = Path(db_path)
        if path.exists():
            archive.write(path, path.name)
        folder = Path(image_dir)
        if folder.exists():
            for file in folder.glob("*"):
                if file.is_file():
                    archive.write(file, f"saved_bills/{file.name}")
    return output.getvalue()


def qr_png(text):
    import qrcode

    image = qrcode.make(text)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def send_webhook(url, text):
    request = urllib.request.Request(url, data=json.dumps({"text": text}).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        return 200 <= response.status < 300


def send_telegram(bot_token, chat_id, text):
    """Send a plain-text message with Telegram Bot API without exposing the token in errors."""
    bot_token = str(bot_token or "").strip()
    chat_id = str(chat_id or "").strip()
    if not bot_token or not chat_id:
        raise ValueError("Cần cấu hình TELEGRAM_BOT_TOKEN và TELEGRAM_CHAT_ID.")

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": str(text)[:4096],
        "disable_web_page_preview": True,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            result = json.loads(exc.read().decode("utf-8"))
            detail = result.get("description", "Telegram từ chối yêu cầu.")
        except (ValueError, AttributeError):
            detail = "Telegram từ chối yêu cầu."
        raise RuntimeError(detail) from None
    except urllib.error.URLError:
        raise RuntimeError("Không kết nối được Telegram; hãy kiểm tra mạng của máy chạy ứng dụng.") from None

    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Telegram không gửi được tin nhắn."))
    return True
