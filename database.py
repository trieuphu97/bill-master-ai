import os
import json
import sqlite3
import uuid
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


class Database:
    def __init__(self, path):
        self.path = str(path)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self):
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS members (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    aliases TEXT DEFAULT '', department TEXT DEFAULT '',
                    bank_account TEXT DEFAULT '', bank_name TEXT DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT, title TEXT, image_path TEXT DEFAULT '',
                    restaurant TEXT DEFAULT '', payer_member_id INTEGER,
                    subtotal INTEGER NOT NULL DEFAULT 0,
                    shipping_fee INTEGER NOT NULL DEFAULT 0,
                    discount INTEGER NOT NULL DEFAULT 0,
                    tip INTEGER NOT NULL DEFAULT 0,
                    company_support INTEGER NOT NULL DEFAULT 0,
                    allocation_method TEXT NOT NULL DEFAULT 'proportional',
                    payment_method TEXT DEFAULT '', notes TEXT DEFAULT '',
                    share_token TEXT, status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT, updated_at TEXT,
                    FOREIGN KEY(payer_member_id) REFERENCES members(id)
                );
                CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    member_id INTEGER,
                    person_name TEXT NOT NULL DEFAULT '',
                    item_name TEXT NOT NULL DEFAULT '',
                    quantity INTEGER NOT NULL DEFAULT 1,
                    unit_price INTEGER NOT NULL DEFAULT 0,
                    promo_original_price INTEGER,
                    allocated_fee INTEGER NOT NULL DEFAULT 0,
                    allocated_discount INTEGER NOT NULL DEFAULT 0,
                    amount_due INTEGER NOT NULL DEFAULT 0,
                    content TEXT DEFAULT '', is_paid INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                    FOREIGN KEY(member_id) REFERENCES members(id)
                );
                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL, member_id INTEGER,
                    person_name TEXT NOT NULL DEFAULT '', amount INTEGER NOT NULL,
                    method TEXT DEFAULT '', note TEXT DEFAULT '', paid_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                    FOREIGN KEY(member_id) REFERENCES members(id)
                );
                CREATE TABLE IF NOT EXISTS advances (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL, member_id INTEGER,
                    person_name TEXT NOT NULL DEFAULT '', amount INTEGER NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                    FOREIGN KEY(member_id) REFERENCES members(id)
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL,
                    entity_type TEXT NOT NULL, entity_id INTEGER, detail TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS item_claims (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id INTEGER NOT NULL, member_id INTEGER NOT NULL,
                    quantity INTEGER NOT NULL, claimed_at TEXT NOT NULL,
                    UNIQUE(item_id, member_id),
                    FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE,
                    FOREIGN KEY(member_id) REFERENCES members(id)
                );
                CREATE TABLE IF NOT EXISTS group_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    member_ids TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reminder_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL, channel TEXT NOT NULL,
                    recipient_count INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '', sent_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_items_session ON items(session_id);
                CREATE INDEX IF NOT EXISTS idx_payments_session ON payments(session_id);
                CREATE INDEX IF NOT EXISTS idx_claims_item ON item_claims(item_id);
                CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id, action);
                """
            )
            self._migrate_columns(conn)
            self._migrate_legacy(conn)

    def _migrate_columns(self, conn):
        required = {
            "sessions": {
                "image_path": "TEXT DEFAULT ''", "restaurant": "TEXT DEFAULT ''",
                "payer_member_id": "INTEGER", "subtotal": "INTEGER NOT NULL DEFAULT 0",
                "shipping_fee": "INTEGER NOT NULL DEFAULT 0", "discount": "INTEGER NOT NULL DEFAULT 0",
                "tip": "INTEGER NOT NULL DEFAULT 0", "company_support": "INTEGER NOT NULL DEFAULT 0",
                "allocation_method": "TEXT NOT NULL DEFAULT 'proportional'",
                "payment_method": "TEXT DEFAULT ''", "notes": "TEXT DEFAULT ''",
                "share_token": "TEXT", "status": "TEXT NOT NULL DEFAULT 'open'",
                "created_at": "TEXT", "updated_at": "TEXT",
                "revision": "INTEGER NOT NULL DEFAULT 0"
            },
            "items": {
                "member_id": "INTEGER", "person_name": "TEXT NOT NULL DEFAULT ''",
                "item_name": "TEXT NOT NULL DEFAULT ''", "quantity": "INTEGER NOT NULL DEFAULT 1",
                "unit_price": "INTEGER NOT NULL DEFAULT 0", "promo_original_price": "INTEGER",
                "allocated_fee": "INTEGER NOT NULL DEFAULT 0",
                "allocated_discount": "INTEGER NOT NULL DEFAULT 0", "amount_due": "INTEGER NOT NULL DEFAULT 0",
                "content": "TEXT DEFAULT ''", "is_paid": "INTEGER NOT NULL DEFAULT 0"
                , "ai_confidence": "REAL NOT NULL DEFAULT 1"
            }
        }
        for table, columns in required.items():
            existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            for name, sql_type in columns.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")

    def _migrate_legacy(self, conn):
        stamp = now_iso()
        conn.execute("UPDATE sessions SET share_token = lower(hex(randomblob(16))) WHERE share_token IS NULL")
        conn.execute("UPDATE sessions SET created_at = COALESCE(created_at, ?), updated_at = COALESCE(updated_at, ?)", (stamp, stamp))
        # A zero balance is valid for source items in a group-paid bill. Only
        # rows without a parsed item name belong to the legacy schema.
        rows = conn.execute("SELECT * FROM items WHERE item_name IS NULL OR TRIM(item_name) = ''").fetchall()
        for row in rows:
            content = row["content"] or ""
            person, item, price = self.parse_legacy(content)
            conn.execute(
                "UPDATE items SET person_name=?, item_name=?, unit_price=?, amount_due=? WHERE id=?",
                (person, item, price, price, row["id"]),
            )
            if row["is_paid"] and price:
                exists = conn.execute("SELECT 1 FROM payments WHERE session_id=? AND person_name=?", (row["session_id"], person)).fetchone()
                if not exists:
                    conn.execute("INSERT INTO payments(session_id,person_name,amount,method,note,paid_at) VALUES(?,?,?,?,?,?)", (row["session_id"], person, price, "legacy", "Dữ liệu chuyển đổi", stamp))
        conn.execute("UPDATE sessions SET subtotal=(SELECT COALESCE(SUM(quantity*unit_price),0) FROM items WHERE session_id=sessions.id) WHERE subtotal=0")

    @staticmethod
    def parse_legacy(text):
        import re
        person, item = "Chưa xác định", text.strip()
        if ":" in item:
            person, item = [x.strip() for x in item.split(":", 1)]
        match = re.search(r"(?:-|–)\s*([\d.,]+)\s*(?:đ|vnd)?\s*$", item, re.I)
        price = 0
        if match:
            price = int(re.sub(r"\D", "", match.group(1)) or 0)
            item = item[:match.start()].strip()
        return person or "Chưa xác định", item or "Món chưa xác định", price

    def query(self, sql, params=()):
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def history_page(self, keyword="", status="Tất cả", payer="Tất cả", page=1, page_size=10):
        """Filter and page bills before loading their detailed rows."""
        page = max(1, int(page))
        page_size = max(1, min(100, int(page_size)))
        clauses = []
        params = []
        if payer != "Tất cả":
            clauses.append("m.name = ?")
            params.append(payer)
        if status != "Tất cả":
            remaining = "(SELECT COALESCE(SUM(amount_due), 0) FROM items WHERE session_id=s.id) - " \
                        "(SELECT COALESCE(SUM(amount), 0) FROM payments WHERE session_id=s.id)"
            clauses.append(f"({remaining}) {'>' if status == 'Chưa thu đủ' else '<='} 0")
        keyword = self.normalize_name(keyword)
        if keyword:
            clauses.append("""(
                instr(normalize_name(s.title), ?) > 0 OR instr(normalize_name(s.restaurant), ?) > 0
                OR EXISTS (SELECT 1 FROM items i WHERE i.session_id=s.id AND instr(normalize_name(i.person_name), ?) > 0)
                OR EXISTS (SELECT 1 FROM payments p WHERE p.session_id=s.id AND instr(normalize_name(p.person_name), ?) > 0)
                OR EXISTS (SELECT 1 FROM item_claims c JOIN items i ON i.id=c.item_id
                           JOIN members cm ON cm.id=c.member_id
                           WHERE i.session_id=s.id AND instr(normalize_name(cm.name), ?) > 0)
            )""")
            params.extend([keyword] * 5)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        source = " FROM sessions s LEFT JOIN members m ON m.id=s.payer_member_id" + where
        with self.connect() as conn:
            conn.create_function("normalize_name", 1, self.normalize_name, deterministic=True)
            total = conn.execute("SELECT COUNT(*)" + source, params).fetchone()[0]
            rows = conn.execute(
                "SELECT s.*,m.name payer_name,m.bank_name payer_bank_name,m.bank_account payer_bank_account"
                + source + " ORDER BY s.id DESC LIMIT ? OFFSET ?",
                (*params, page_size, (page - 1) * page_size),
            ).fetchall()
        return total, [dict(row) for row in rows]

    def find_possible_duplicates(self, restaurant, items, total, exclude_id=None, days=2):
        from services import bill_signature

        if not items or int(total) <= 0:
            return []
        target_restaurant, target_lines, target_total = bill_signature(restaurant, items, total)
        if not target_lines:
            return []
        since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        matches = []
        for session in self.query(
            "SELECT id,title,restaurant,subtotal,shipping_fee,discount,tip,company_support,created_at "
            "FROM sessions WHERE created_at>=? AND (? IS NULL OR id<>?) ORDER BY id DESC",
            (since, exclude_id, exclude_id),
        ):
            existing = self.query(
                "SELECT item_name,quantity,unit_price FROM items WHERE session_id=? AND unit_price>0",
                (session["id"],),
            )
            # Group bills also contain generated contribution rows, not food.
            existing = [row for row in existing if row["item_name"] != "Phần đóng góp chi phí chung"]
            existing_total = max(
                0, session["subtotal"] + session["shipping_fee"] + session["tip"]
                - session["discount"] - session["company_support"]
            )
            found_restaurant, found_lines, found_total = bill_signature(session["restaurant"], existing, existing_total)
            if target_lines == found_lines and target_total == found_total and (
                not target_restaurant or not found_restaurant or target_restaurant == found_restaurant
            ):
                matches.append(session)
        return matches

    @staticmethod
    def _balances_in_transaction(conn, session_id, items=None):
        from services import person_balances

        if items is None:
            items = [dict(row) for row in conn.execute("SELECT * FROM items WHERE session_id=?", (session_id,))]
        payments = [dict(row) for row in conn.execute("SELECT * FROM payments WHERE session_id=?", (session_id,))]
        claims = [dict(row) for row in conn.execute(
            """SELECT c.*,m.name person_name FROM item_claims c JOIN members m ON m.id=c.member_id
               JOIN items i ON i.id=c.item_id WHERE i.session_id=?""", (session_id,)
        )]
        return person_balances(items, payments, claims)

    def update_session(self, session_id, expected_revision, data, items):
        """Update a bill atomically, preserving claims and explicit payment history."""
        if not items:
            raise ValueError("Đơn phải có ít nhất một món.")
        if not str(data.get("title") or "").strip():
            raise ValueError("Tên đơn không được để trống.")
        total = max(0, int(data["subtotal"]) + int(data["shipping_fee"]) + int(data["tip"])
                    - int(data["discount"]) - int(data["company_support"]))
        if sum(int(item["amount_due"]) for item in items) != total:
            raise ValueError("Tổng công nợ theo món không khớp tổng đơn.")
        stamp = now_iso()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not current:
                raise ValueError("Đơn không còn tồn tại.")
            if current["revision"] != expected_revision:
                raise ValueError("Đơn vừa được người khác thay đổi. Tải lại trước khi sửa.")
            old_items = {row["id"]: dict(row) for row in conn.execute("SELECT * FROM items WHERE session_id=?", (session_id,))}
            existing_ids = set(old_items)
            submitted_ids = {int(item["id"]) for item in items if item.get("id") is not None}
            if not submitted_ids.issubset(existing_ids) or len(submitted_ids) != sum(item.get("id") is not None for item in items):
                raise ValueError("Danh sách món đã thay đổi. Tải lại trước khi sửa.")
            claim_rows = conn.execute(
                """SELECT c.item_id,c.member_id,c.quantity FROM item_claims c
                   JOIN items i ON i.id=c.item_id WHERE i.session_id=?""", (session_id,)
            ).fetchall()
            claims_by_item = {}
            for claim in claim_rows:
                claims_by_item.setdefault(claim["item_id"], []).append(claim)
            for item in items:
                item_id = item.get("id")
                if int(item["quantity"]) < 1 or int(item["unit_price"]) < 0:
                    raise ValueError("Số lượng hoặc đơn giá món không hợp lệ.")
                promo_price = item.get("promo_original_price")
                if promo_price is not None and int(promo_price) <= int(item["unit_price"]):
                    raise ValueError("Giá gốc ưu đãi phải lớn hơn giá trên hóa đơn.")
                existing_claims = claims_by_item.get(item_id, [])
                if existing_claims and (
                    item["member_id"] is not None
                    or sum(claim["quantity"] for claim in existing_claims) > int(item["quantity"])
                ):
                    raise ValueError("Món đã được nhận; không thể đổi người hoặc giảm dưới số phần đã nhận.")
            for item_id in existing_ids - submitted_ids:
                if claims_by_item.get(item_id):
                    raise ValueError("Không thể xóa món đã có người nhận.")
            before = {"session": dict(current), "items": list(old_items.values())}
            conn.execute(
                """UPDATE sessions SET title=?,restaurant=?,payer_member_id=?,subtotal=?,shipping_fee=?,
                   discount=?,tip=?,company_support=?,allocation_method=?,updated_at=?,revision=revision+1
                   WHERE id=?""",
                (data["title"].strip(), data.get("restaurant", "").strip(), data["payer_member_id"],
                 int(data["subtotal"]), int(data["shipping_fee"]), int(data["discount"]),
                 int(data["tip"]), int(data["company_support"]), data["allocation_method"], stamp, session_id),
            )
            for item in items:
                values = (item.get("member_id"), item["person_name"], item["item_name"].strip(),
                          int(item["quantity"]), int(item["unit_price"]), item.get("promo_original_price"),
                          int(item["allocated_fee"]),
                          int(item["allocated_discount"]), int(item["amount_due"]), float(item.get("ai_confidence", 1)))
                if item.get("id") is not None:
                    conn.execute(
                        """UPDATE items SET member_id=?,person_name=?,item_name=?,quantity=?,unit_price=?,promo_original_price=?,
                           allocated_fee=?,allocated_discount=?,amount_due=?,ai_confidence=? WHERE id=?""",
                        (*values, int(item["id"])),
                    )
                else:
                    conn.execute(
                        """INSERT INTO items(session_id,member_id,person_name,item_name,quantity,unit_price,promo_original_price,
                           allocated_fee,allocated_discount,amount_due,ai_confidence)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (session_id, *values),
                    )
            for item_id in existing_ids - submitted_ids:
                conn.execute("DELETE FROM items WHERE id=?", (item_id,))
            payer = conn.execute("SELECT name FROM members WHERE id=?", (data["payer_member_id"],)).fetchone()
            if not payer:
                raise ValueError("Người ứng tiền không hợp lệ.")
            conn.execute("DELETE FROM advances WHERE session_id=?", (session_id,))
            conn.execute(
                "INSERT INTO advances(session_id,member_id,person_name,amount) VALUES(?,?,?,?)",
                (session_id, data["payer_member_id"], payer["name"], total),
            )
            # The initial payer credit is derived from their own share, unlike manually received money.
            conn.execute("DELETE FROM payments WHERE session_id=? AND method='Tự đối trừ'", (session_id,))
            balances = self._balances_in_transaction(conn, session_id)
            payer_due = next((row["due"] for row in balances if row["member_id"] == data["payer_member_id"]), 0)
            payer_credit = min(total, payer_due)
            if payer_credit:
                conn.execute(
                    """INSERT INTO payments(session_id,member_id,person_name,amount,method,note,paid_at)
                       VALUES(?,?,?,?,?,?,?)""",
                    (session_id, data["payer_member_id"], payer["name"], payer_credit,
                     "Tự đối trừ", "Phần của người ứng tiền", stamp),
                )
            balances = self._balances_in_transaction(conn, session_id)
            if any(row["paid"] > row["due"] for row in balances):
                raise ValueError("Số tiền đã nhận vượt phần phải trả mới. Hãy sửa khoản thanh toán trước.")
            status = "settled" if total > 0 and sum(row["paid"] for row in balances) >= total else "open"
            conn.execute("UPDATE sessions SET status=? WHERE id=?", (status, session_id))
            detail = json.dumps({"before": before, "after": {"data": data, "items": items}}, ensure_ascii=False)
            conn.execute(
                "INSERT INTO audit_log(action,entity_type,entity_id,detail,created_at) VALUES(?,?,?,?,?)",
                ("edit", "session", session_id, detail, stamp),
            )

    def update_payment(self, payment_id, amount, method, note, reason):
        self._change_payment(payment_id, amount, method, note, reason, delete=False)

    def delete_payment(self, payment_id, reason):
        self._change_payment(payment_id, None, None, None, reason, delete=True)

    def _change_payment(self, payment_id, amount, method, note, reason, delete):
        if not str(reason or "").strip():
            raise ValueError("Vui lòng nhập lý do thay đổi.")
        stamp = now_iso()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            payment = conn.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
            if not payment:
                raise ValueError("Khoản thanh toán không còn tồn tại.")
            if payment["method"] == "Tự đối trừ":
                raise ValueError("Khoản tự đối trừ thay đổi theo đơn, không sửa tại đây.")
            if not delete:
                amount = int(amount)
                if amount <= 0:
                    raise ValueError("Số tiền phải lớn hơn 0.")
                conn.execute("UPDATE payments SET amount=?,method=?,note=? WHERE id=?",
                             (amount, method, note, payment_id))
            else:
                conn.execute("DELETE FROM payments WHERE id=?", (payment_id,))
            balances = self._balances_in_transaction(conn, payment["session_id"])
            if any(row["paid"] > row["due"] for row in balances):
                raise ValueError("Số tiền đã nhận vượt phần phải trả.")
            total_due = sum(row["due"] for row in balances)
            status = "settled" if total_due > 0 and sum(row["paid"] for row in balances) >= total_due else "open"
            conn.execute("UPDATE sessions SET status=?,updated_at=?,revision=revision+1 WHERE id=?",
                         (status, stamp, payment["session_id"]))
            conn.execute(
                "INSERT INTO audit_log(action,entity_type,entity_id,detail,created_at) VALUES(?,?,?,?,?)",
                ("delete" if delete else "edit", "payment", payment_id,
                 json.dumps({"reason": str(reason).strip(), "before": dict(payment), "after": None if delete else
                            {"amount": amount, "method": method, "note": note}}, ensure_ascii=False), stamp),
            )

    def execute(self, sql, params=()):
        with self.connect() as conn:
            cur = conn.execute(sql, params)
            return cur.lastrowid

    def add_member(self, name, aliases="", department="", bank_account="", bank_name=""):
        return self.execute("INSERT INTO members(name,aliases,department,bank_account,bank_name,created_at) VALUES(?,?,?,?,?,?)", (name.strip(), aliases.strip(), department.strip(), bank_account.strip(), bank_name.strip(), now_iso()))

    def find_member(self, name):
        target = self.normalize_name(name)
        members = self.query("SELECT * FROM members WHERE active=1")
        for member in members:
            names = [member["name"], *(member["aliases"] or "").split(",")]
            if target in {self.normalize_name(n) for n in names if n.strip()}:
                return member
        # Ghi chú Grab thường thêm cách xưng hô hoặc mô tả: "A Bình",
        # "C Quỳnh", "Phú: 30% ngọt", "Nghĩa ngây ngô". Chỉ ghép mềm
        # khi đúng duy nhất một người để không âm thầm gán nhầm.
        before_colon = target.split(":", 1)[0].strip()
        tokens = before_colon.split()
        if len(tokens) > 1 and tokens[0] in {"a", "c", "anh", "chi", "ban"}:
            tokens = tokens[1:]
        probes = {before_colon, " ".join(tokens)}
        if tokens:
            probes.add(tokens[0])
        probes = {probe for probe in probes if len(probe) >= 2}
        matches = []
        for member in members:
            variants = [self.normalize_name(member["name"]), *(self.normalize_name(alias) for alias in (member["aliases"] or "").split(",") if alias.strip())]
            variant_tokens = {token for variant in variants for token in variant.split() if len(token) >= 2}
            if any(probe in variants or probe in variant_tokens for probe in probes):
                matches.append(member)
        if len(matches) == 1:
            return matches[0]
        return None

    @staticmethod
    def normalize_name(value):
        text = unicodedata.normalize("NFD", str(value).strip().casefold())
        text = "".join(char for char in text if unicodedata.category(char) != "Mn")
        return " ".join(text.replace("đ", "d").split())

    def claim_item(self, item_id, member_id, quantity):
        quantity = int(quantity)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            # Khóa ghi trước khi đọc số lượng để hai người không thể cùng vượt
            # qua bước kiểm tra phần còn lại trong hai kết nối khác nhau.
            conn.execute("BEGIN IMMEDIATE")
            item = conn.execute("SELECT quantity FROM items WHERE id=?", (item_id,)).fetchone()
            if not item or quantity < 1:
                raise ValueError("Món hoặc số lượng không hợp lệ")
            claimed = conn.execute("SELECT COALESCE(SUM(quantity),0) FROM item_claims WHERE item_id=? AND member_id<>?", (item_id, member_id)).fetchone()[0]
            if claimed + quantity > item["quantity"]:
                raise ValueError("Số lượng nhận vượt quá phần còn lại")
            conn.execute(
                """INSERT INTO item_claims(item_id,member_id,quantity,claimed_at) VALUES(?,?,?,?)
                   ON CONFLICT(item_id,member_id) DO UPDATE SET quantity=excluded.quantity,claimed_at=excluded.claimed_at""",
                (item_id, member_id, quantity, now_iso()),
            )
            session_id = conn.execute("SELECT session_id FROM items WHERE id=?", (item_id,)).fetchone()["session_id"]
            if any(balance["paid"] > balance["due"] for balance in self._balances_in_transaction(conn, session_id)):
                raise ValueError("Món đã ghi nhận thanh toán cho người khác. Hãy sửa khoản thanh toán trước.")
            conn.execute(
                "UPDATE sessions SET revision=revision+1,updated_at=? WHERE id=?",
                (now_iso(), session_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def remove_claim(self, item_id, member_id):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT session_id FROM items WHERE id=?", (item_id,)).fetchone()
            if not row:
                raise ValueError("Món không còn tồn tại.")
            conn.execute("DELETE FROM item_claims WHERE item_id=? AND member_id=?", (item_id, member_id))
            if any(balance["paid"] > balance["due"] for balance in self._balances_in_transaction(conn, row["session_id"])):
                raise ValueError("Không thể bỏ nhận món đã ghi nhận thanh toán. Hãy sửa khoản thanh toán trước.")
            conn.execute("UPDATE sessions SET revision=revision+1,updated_at=? WHERE id=?",
                         (now_iso(), row["session_id"]))

    def create_session(self, data, items, advances, image_path=""):
        for item in items:
            promo_price = item.get("promo_original_price")
            if promo_price is not None and int(promo_price) <= int(item["unit_price"]):
                raise ValueError("Giá gốc ưu đãi phải lớn hơn giá trên hóa đơn.")
        stamp = now_iso()
        with self.connect() as conn:
            cur = conn.execute(
                """INSERT INTO sessions(date,title,image_path,restaurant,payer_member_id,subtotal,shipping_fee,discount,tip,company_support,allocation_method,payment_method,notes,share_token,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (datetime.now().strftime("%d/%m/%Y %H:%M"), data["title"], image_path, data.get("restaurant", ""), data.get("payer_member_id"), data["subtotal"], data["shipping_fee"], data["discount"], data["tip"], data["company_support"], data["allocation_method"], data.get("payment_method", ""), data.get("notes", ""), uuid.uuid4().hex, "open", stamp, stamp),
            )
            session_id = cur.lastrowid
            for item in items:
                conn.execute(
                    """INSERT INTO items(session_id,member_id,person_name,item_name,quantity,unit_price,promo_original_price,allocated_fee,allocated_discount,amount_due,content,is_paid,ai_confidence)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,0,?)""",
                    (session_id, item.get("member_id"), item["person_name"], item["item_name"], item["quantity"], item["unit_price"], item.get("promo_original_price"), item["allocated_fee"], item["allocated_discount"], item["amount_due"], f'{item["person_name"]}: {item["item_name"]}', float(item.get("ai_confidence", 1))),
                )
            for advance in advances:
                if advance["amount"] > 0:
                    conn.execute("INSERT INTO advances(session_id,member_id,person_name,amount) VALUES(?,?,?,?)", (session_id, advance.get("member_id"), advance["person_name"], advance["amount"]))
                    own_due = sum(
                        int(item.get("amount_due") or 0) for item in items
                        if (advance.get("member_id") and item.get("member_id") == advance.get("member_id"))
                        or (not advance.get("member_id") and item.get("person_name") == advance["person_name"])
                    )
                    own_offset = min(int(advance["amount"]), own_due)
                    if own_offset > 0:
                        conn.execute(
                            "INSERT INTO payments(session_id,member_id,person_name,amount,method,note,paid_at) VALUES(?,?,?,?,?,?,?)",
                            (session_id, advance.get("member_id"), advance["person_name"], own_offset, "Tự đối trừ", "Phần của người ứng tiền", stamp),
                        )
            total_due = sum(int(item.get("amount_due") or 0) for item in items)
            total_paid = conn.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE session_id=?", (session_id,)).fetchone()[0]
            if total_due > 0 and total_paid >= total_due:
                conn.execute("UPDATE sessions SET status='settled' WHERE id=?", (session_id,))
            conn.execute("INSERT INTO audit_log(action,entity_type,entity_id,detail,created_at) VALUES('create','session',?,?,?)", (session_id, data["title"], stamp))
            return session_id

    def delete_session(self, session_id):
        rows = self.query("SELECT image_path FROM sessions WHERE id=?", (session_id,))
        with self.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            conn.execute("INSERT INTO audit_log(action,entity_type,entity_id,detail,created_at) VALUES('delete','session',?,'',?)", (session_id, now_iso()))
        if rows and rows[0]["image_path"]:
            try:
                Path(rows[0]["image_path"]).unlink(missing_ok=True)
            except OSError:
                pass

    def add_payment(self, session_id, member_id, person_name, amount, method, note=""):
        amount = int(amount)
        if amount <= 0:
            raise ValueError("Số tiền nhận phải lớn hơn 0.")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
                raise ValueError("Đơn không còn tồn tại.")
            conn.execute(
                "INSERT INTO payments(session_id,member_id,person_name,amount,method,note,paid_at) VALUES(?,?,?,?,?,?,?)",
                (session_id, member_id, person_name, amount, method, note, now_iso()),
            )
            balances = self._balances_in_transaction(conn, session_id)
            if any(row["paid"] > row["due"] for row in balances):
                raise ValueError("Số tiền nhận vượt phần còn nợ.")
            status = "settled" if balances and all(row["remaining"] == 0 for row in balances) else "open"
            conn.execute("UPDATE sessions SET status=?,updated_at=?,revision=revision+1 WHERE id=?",
                         (status, now_iso(), session_id))

    def log_reminder(self, session_id, channel, recipient_count, message):
        return self.execute(
            "INSERT INTO reminder_logs(session_id,channel,recipient_count,message,sent_at) VALUES(?,?,?,?,?)",
            (session_id, channel, int(recipient_count), message, now_iso()),
        )

    def refresh_status(self, session_id):
        summary = self.query(
            """SELECT COALESCE((SELECT SUM(amount_due) FROM items WHERE session_id=?),0) due,
                      COALESCE((SELECT SUM(amount) FROM payments WHERE session_id=?),0) paid""", (session_id, session_id)
        )[0]
        status = "settled" if summary["due"] > 0 and summary["paid"] >= summary["due"] else "open"
        self.execute("UPDATE sessions SET status=?,updated_at=? WHERE id=?", (status, now_iso(), session_id))
