"""Presentation helpers. Billing and persistence stay in their existing modules."""
from html import escape
from datetime import datetime
from pathlib import Path

from services import money


def format_datetime(value):
    try:
        return datetime.fromisoformat(str(value)).strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return str(value or "")


def stylesheet():
    return Path(__file__).with_name("style.css").read_text(encoding="utf-8")


def brand_html():
    return '<div class="bm-brand"><span class="bm-logo">B</span><div>Bill Master<small>Chi tiêu chung, rõ ràng hơn</small></div></div>'


def page_header(title, description):
    return f'<div class="bm-page-heading"><h1>{escape(title)}</h1><p>{escape(description)}</p></div>'


def balance_cards(rows):
    cards = []
    for row in rows:
        name = str(row["person_name"])
        remaining = int(row["remaining"])
        state = "owing" if remaining > 0 else "settled"
        label = "Còn phải trả" if remaining > 0 else "Đã thanh toán đủ"
        cards.append(
            f'<div class="bm-person"><span class="bm-avatar">{escape(name[:1].upper())}</span>'
            f'<div class="bm-person-content"><div class="bm-person-name">{escape(name)}</div>'
            f'<span class="bm-balance {state}">{money(remaining)}</span>'
            f'<div class="bm-person-status">{label}</div></div></div>'
        )
    return '<div class="bm-people">' + "".join(cards) + '</div>'
