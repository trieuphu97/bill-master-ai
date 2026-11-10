import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook

from database import Database
from services import allocate_group, allocate_items, allocate_items_with_shared_promos, amount_from_percentage, excel_report, redistribute_custom_shares, person_balances, shared_promo_discount


class BillingTests(unittest.TestCase):
    def test_delete_member_removes_unused_member_and_template_reference(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Database(Path(folder) / "members.db")
            database.init()
            member_id = database.add_member("An")
            database.execute("INSERT INTO group_templates(name,member_ids,created_at) VALUES(?,?,?)",
                             ("Nhóm", str(member_id), "2026-10-22"))
            self.assertEqual(database.delete_member(member_id), "deleted")
            self.assertEqual(database.query("SELECT * FROM members WHERE id=?", (member_id,)), [])
            self.assertEqual(database.query("SELECT member_ids FROM group_templates")[0]["member_ids"], "")

    def test_delete_member_with_order_archives_without_losing_history(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Database(Path(folder) / "members.db")
            database.init()
            member_id = database.add_member("An")
            session_id = database.execute("INSERT INTO sessions(payer_member_id,title) VALUES(?,?)",
                                          (member_id, "Đơn cũ"))
            self.assertEqual(database.delete_member(member_id), "archived")
            self.assertEqual(database.query("SELECT active FROM members WHERE id=?", (member_id,))[0]["active"], 0)
            self.assertEqual(database.query("SELECT payer_member_id FROM sessions WHERE id=?", (session_id,))[0]["payer_member_id"], member_id)

    def test_excel_export_matches_csv_detail_columns_and_values(self):
        rows = [{"Mã đơn": 12, "Ngày": "2026-10-22", "Đơn": "Đồ ăn",
                 "Người": "An", "Phải trả": 54000, "Đã trả": 40000, "Còn nợ": 14000}]
        workbook = load_workbook(BytesIO(excel_report(rows)), read_only=True, data_only=True)
        self.assertEqual(workbook.sheetnames, ["Chi tiết công nợ"])
        self.assertEqual(list(workbook.active.values), [tuple(rows[0]), tuple(rows[0].values())])

    def test_excel_export_merges_order_fields_and_formats_vnd(self):
        rows = [
            {"Mã đơn": 12, "Ngày": "2026-10-22", "Đơn": "Đồ ăn", "Người": person,
             "Phải trả": amount, "Đã trả": 0, "Còn nợ": amount}
            for person, amount in [("An", 54000), ("Bình", 36000)]
        ]
        rows.append({"Mã đơn": 13, "Ngày": "2026-10-23", "Đơn": "Trà",
                     "Người": "An", "Phải trả": 20000, "Đã trả": 20000, "Còn nợ": 0})
        workbook = load_workbook(BytesIO(excel_report(rows)), data_only=True)
        sheet = workbook.active
        self.assertEqual({str(region) for region in sheet.merged_cells.ranges},
                         {"A2:A3", "B2:B3", "C2:C3"})
        self.assertEqual((sheet["A2"].value, sheet["A4"].value), (12, 13))
        self.assertEqual((sheet["E2"].value, sheet["E3"].value), (54000, 36000))
        self.assertEqual(sheet["E2"].number_format, '#,##0" ₫"')
        self.assertEqual(sheet["G4"].number_format, '#,##0" ₫"')
        self.assertTrue(sheet["A1"].font.bold)
        self.assertEqual(sheet["D4"].border.top.style, "medium")
        self.assertIsNone(sheet["D3"].border.top)

    def test_custom_group_share_redistributes_exact_remainder(self):
        shares = redistribute_custom_shares(100_001, [1, 2, 3], 1, 50_000)
        self.assertEqual(shares, {'1': 50_000, '2': 25_001, '3': 25_000})
        self.assertEqual(sum(shares.values()), 100_001)
        self.assertEqual(redistribute_custom_shares(100_000, [1, 2], 1, 200_000),
                         {'1': 100_000, '2': 0})
        self.assertEqual(redistribute_custom_shares(100_000, [1], 1, 30_000), {'1': 100_000})

    def test_percentage_share_is_rounded_to_vnd_and_rest_redistributed(self):
        boss_amount = amount_from_percentage(100_001, 50)
        self.assertEqual(boss_amount, 50_001)
        shares = redistribute_custom_shares(100_001, [1, 2, 3], 1, boss_amount)
        self.assertEqual(shares, {'1': 50_001, '2': 25_000, '3': 25_000})

    def test_one_dong_item_uses_original_price_and_shares_savings(self):
        prices = [69_000, 54_000, 54_000, 54_000, 1, 54_000, 69_000]
        items = [
            {"person_name": f"Người {index}", "item_name": f"Món {index}",
             "quantity": 1, "unit_price": price,
             "promo_original_price": 54_000 if price == 1 else None}
            for index, price in enumerate(prices)
        ]
        allocated = allocate_items_with_shared_promos(items, shipping_fee=16_000,
                                                       discount=77_880, tip=3_000)
        self.assertEqual(shared_promo_discount(items), 53_999)
        self.assertEqual(sum(row["amount_due"] for row in allocated), 295_121)
        self.assertEqual(allocated[4]["unit_price"], 1)
        self.assertEqual(allocated[4]["promo_original_price"], 54_000)
        self.assertGreater(allocated[4]["amount_due"], 1)
        self.assertLess(allocated[4]["amount_due"], 54_000)

    def test_shared_item_promo_survives_create_and_edit(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Database(Path(folder) / "promo.db")
            database.init()
            payer = database.add_member("An")
            guest = database.add_member("Bình")
            raw = [
                {"member_id": payer, "person_name": "An", "item_name": "Trà thường",
                 "quantity": 1, "unit_price": 54_000},
                {"member_id": guest, "person_name": "Bình", "item_name": "Trà 1đ",
                 "quantity": 1, "unit_price": 1, "promo_original_price": 54_000},
            ]
            data = {"title": "Ưu đãi chung", "payer_member_id": payer,
                    "subtotal": 54_001, "shipping_fee": 0, "discount": 0,
                    "tip": 0, "company_support": 0, "allocation_method": "equal"}
            session_id = database.create_session(
                data, allocate_items_with_shared_promos(raw), [],
            )
            rows = database.query("SELECT * FROM items WHERE session_id=? ORDER BY id", (session_id,))
            self.assertEqual((rows[1]["unit_price"], rows[1]["promo_original_price"]), (1, 54_000))
            self.assertEqual(sum(row["amount_due"] for row in rows), 54_001)
            database.init()
            self.assertEqual(database.query("SELECT promo_original_price FROM items WHERE id=?", (rows[1]["id"],))[0]["promo_original_price"], 54_000)
            revised = [dict(row) for row in rows]
            revised[1]["promo_original_price"] = 60_000
            calculated = allocate_items_with_shared_promos(revised)
            revision = database.query("SELECT revision FROM sessions WHERE id=?", (session_id,))[0]["revision"]
            database.update_session(session_id, revision, data, calculated)
            updated = database.query("SELECT unit_price,promo_original_price,amount_due FROM items WHERE session_id=? ORDER BY id", (session_id,))
            self.assertEqual((updated[1]["unit_price"], updated[1]["promo_original_price"]), (1, 60_000))
            self.assertEqual(sum(row["amount_due"] for row in updated), 54_001)

    def test_duplicate_detection_ignores_item_order_and_accents(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Database(Path(folder) / "duplicates.db")
            database.init()
            payer = database.add_member("An")
            items = allocate_items([
                {"member_id": payer, "person_name": "An", "item_name": "Trà sữa", "quantity": 2, "unit_price": 30000},
                {"member_id": payer, "person_name": "An", "item_name": "Bánh mì", "quantity": 1, "unit_price": 20000},
            ])
            data = {"title": "Bữa sáng", "restaurant": "Quán Trà", "payer_member_id": payer,
                    "subtotal": 80000, "shipping_fee": 0, "discount": 0, "tip": 0,
                    "company_support": 0, "allocation_method": "equal"}
            session_id = database.create_session(data, items, [{"member_id": payer, "person_name": "An", "amount": 80000}])
            reversed_items = list(reversed(items))
            self.assertEqual(
                [row["id"] for row in database.find_possible_duplicates("quan tra", reversed_items, 80000)],
                [session_id],
            )
            split_drinks = [
                {"item_name": "Trà sữa", "quantity": 1, "unit_price": 30000},
                {"item_name": "Bánh mì", "quantity": 1, "unit_price": 20000},
                {"item_name": "Trà sữa", "quantity": 1, "unit_price": 30000},
            ]
            self.assertEqual(len(database.find_possible_duplicates("Quán Trà", split_drinks, 80000)), 1)
            self.assertFalse(database.find_possible_duplicates("quan tra", reversed_items, 81000))
            self.assertFalse(database.find_possible_duplicates("quán khác", reversed_items, 80000))

    def test_edit_bill_keeps_payments_and_rejects_overpayment(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Database(Path(folder) / "edit.db")
            database.init()
            payer = database.add_member("An")
            guest = database.add_member("Bình")
            items = allocate_items([
                {"member_id": payer, "person_name": "An", "item_name": "Cơm", "quantity": 1, "unit_price": 40000},
                {"member_id": guest, "person_name": "Bình", "item_name": "Trà", "quantity": 1, "unit_price": 60000},
            ])
            data = {"title": "Trưa", "restaurant": "Quán", "payer_member_id": payer,
                    "subtotal": 100000, "shipping_fee": 0, "discount": 0, "tip": 0,
                    "company_support": 0, "allocation_method": "equal"}
            session_id = database.create_session(data, items, [{"member_id": payer, "person_name": "An", "amount": 100000}])
            database.add_payment(session_id, guest, "Bình", 30000, "Chuyển khoản")
            payment_id = database.query("SELECT id FROM payments WHERE member_id=? AND method<>'Tự đối trừ'", (guest,))[0]["id"]
            current = database.query("SELECT * FROM sessions WHERE id=?", (session_id,))[0]
            revised = database.query("SELECT * FROM items WHERE session_id=? ORDER BY id", (session_id,))
            revised[1]["unit_price"] = 20000
            revised[1]["amount_due"] = 20000
            lower = {**data, "subtotal": 60000, "title": "Trưa đã sửa"}
            with self.assertRaisesRegex(ValueError, "vượt"):
                database.update_session(session_id, current["revision"], lower, revised)
            self.assertEqual(database.query("SELECT subtotal FROM sessions WHERE id=?", (session_id,))[0]["subtotal"], 100000)
            database.update_payment(payment_id, 10000, "Tiền mặt", "Đã đối chiếu", "Nhập dư 20,000")
            fresh = database.query("SELECT revision FROM sessions WHERE id=?", (session_id,))[0]
            database.update_session(session_id, fresh["revision"], lower, revised)
            self.assertEqual(database.query("SELECT amount FROM payments WHERE id=?", (payment_id,))[0]["amount"], 10000)
            self.assertEqual(database.query("SELECT amount FROM advances WHERE session_id=?", (session_id,))[0]["amount"], 60000)
            self.assertEqual(database.query("SELECT status FROM sessions WHERE id=?", (session_id,))[0]["status"], "open")
            self.assertEqual(len(database.query("SELECT * FROM audit_log WHERE action='edit' AND entity_type='session'")), 1)
            with self.assertRaisesRegex(ValueError, "Tải lại"):
                database.update_session(session_id, current["revision"], lower, revised)
            database.delete_payment(payment_id, "Giao dịch chưa thực hiện")
            self.assertFalse(database.query("SELECT * FROM payments WHERE id=?", (payment_id,)))
            self.assertEqual(database.query("SELECT status FROM sessions WHERE id=?", (session_id,))[0]["status"], "open")
            self.assertEqual(
                len(database.query("SELECT * FROM audit_log WHERE entity_type='payment' AND entity_id=?", (payment_id,))),
                2,
            )

    def test_edit_preserves_claims_and_checks_claimed_quantity(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Database(Path(folder) / "claimed.db")
            database.init()
            payer = database.add_member("An")
            guest = database.add_member("Bình")
            items = allocate_items([
                {"member_id": None, "person_name": "Chưa xác định", "item_name": "Trà", "quantity": 2, "unit_price": 30000}
            ])
            data = {"title": "Trà", "restaurant": "Quán", "payer_member_id": payer,
                    "subtotal": 60000, "shipping_fee": 0, "discount": 0, "tip": 0,
                    "company_support": 0, "allocation_method": "equal"}
            session_id = database.create_session(data, items, [{"member_id": payer, "person_name": "An", "amount": 60000}])
            row = database.query("SELECT * FROM items WHERE session_id=?", (session_id,))[0]
            database.claim_item(row["id"], guest, 2)
            current = database.query("SELECT revision FROM sessions WHERE id=?", (session_id,))[0]
            with self.assertRaisesRegex(ValueError, "đã được nhận"):
                database.update_session(session_id, current["revision"], data, [{**row, "quantity": 1}])
            updated = {**row, "item_name": "Trà đào"}
            database.update_session(session_id, current["revision"], data, [updated])
            self.assertEqual(database.query("SELECT quantity FROM item_claims WHERE item_id=?", (row["id"],))[0]["quantity"], 2)
            self.assertEqual(database.query("SELECT item_name FROM items WHERE id=?", (row["id"],))[0]["item_name"], "Trà đào")

    def test_payment_cannot_exceed_remaining_or_edit_auto_credit(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Database(Path(folder) / "money.db")
            database.init()
            payer = database.add_member("An")
            guest = database.add_member("Bình")
            items = allocate_items([{"member_id": guest, "person_name": "Bình", "item_name": "Trà",
                                     "quantity": 1, "unit_price": 30000}])
            data = {"title": "Trà", "payer_member_id": payer, "subtotal": 30000,
                    "shipping_fee": 0, "discount": 0, "tip": 0, "company_support": 0,
                    "allocation_method": "equal"}
            session_id = database.create_session(data, items, [{"member_id": payer, "person_name": "An", "amount": 30000}])
            with self.assertRaisesRegex(ValueError, "vượt"):
                database.add_payment(session_id, guest, "Bình", 30001, "Chuyển khoản")
            self.assertFalse(database.query("SELECT * FROM payments WHERE session_id=?", (session_id,)))
            database.add_payment(session_id, guest, "Bình", 30000, "Chuyển khoản")
            self.assertEqual(database.query("SELECT status FROM sessions WHERE id=?", (session_id,))[0]["status"], "settled")
            payment_id = database.query("SELECT id FROM payments WHERE session_id=?", (session_id,))[0]["id"]
            with self.assertRaisesRegex(ValueError, "vượt"):
                database.update_payment(payment_id, 30001, "Chuyển khoản", "", "Nhập lại số tiền")
            self.assertEqual(database.query("SELECT amount FROM payments WHERE id=?", (payment_id,))[0]["amount"], 30000)

    def test_group_equal_edit_recalculates_contributions(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Database(Path(folder) / "group-edit.db")
            database.init()
            payer = database.add_member("An")
            guest = database.add_member("Bình")
            source = allocate_items([{"member_id": None, "person_name": "Chưa xác định",
                                      "item_name": "Bánh sinh nhật", "quantity": 1, "unit_price": 100000}])
            source[0]["amount_due"] = 0
            group = allocate_group([{"id": payer, "name": "An"}, {"id": guest, "name": "Bình"}], 100000)
            data = {"title": "Sinh nhật", "restaurant": "Tiệm bánh", "payer_member_id": payer,
                    "subtotal": 100000, "shipping_fee": 0, "discount": 0, "tip": 0,
                    "company_support": 0, "allocation_method": "group_equal"}
            session_id = database.create_session(data, source + group,
                                                 [{"member_id": payer, "person_name": "An", "amount": 100000}])
            rows = database.query("SELECT * FROM items WHERE session_id=? ORDER BY id", (session_id,))
            rows[0]["unit_price"] = 120000
            group[0]["amount_due"] = group[0]["unit_price"] = 60000
            group[1]["amount_due"] = group[1]["unit_price"] = 60000
            rows[1]["amount_due"] = rows[1]["unit_price"] = 60000
            rows[2]["amount_due"] = rows[2]["unit_price"] = 60000
            current = database.query("SELECT revision FROM sessions WHERE id=?", (session_id,))[0]
            database.update_session(session_id, current["revision"], {**data, "subtotal": 120000}, rows)
            self.assertEqual(database.query("SELECT SUM(amount_due) total FROM items WHERE session_id=?",
                                            (session_id,))[0]["total"], 120000)
            self.assertEqual(database.query("SELECT amount FROM payments WHERE method='Tự đối trừ' AND session_id=?",
                                            (session_id,))[0]["amount"], 60000)

    def test_unclaim_rejected_after_payment_without_losing_claim(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Database(Path(folder) / "paid-claim.db")
            database.init()
            payer = database.add_member("An")
            guest = database.add_member("Bình")
            items = allocate_items([{"member_id": None, "person_name": "Chưa xác định",
                                     "item_name": "Trà", "quantity": 1, "unit_price": 30000}])
            data = {"title": "Trà", "payer_member_id": payer, "subtotal": 30000, "shipping_fee": 0,
                    "discount": 0, "tip": 0, "company_support": 0, "allocation_method": "equal"}
            session_id = database.create_session(data, items, [{"member_id": payer, "person_name": "An", "amount": 30000}])
            item_id = database.query("SELECT id FROM items WHERE session_id=?", (session_id,))[0]["id"]
            database.claim_item(item_id, guest, 1)
            database.add_payment(session_id, guest, "Bình", 30000, "Chuyển khoản")
            with self.assertRaisesRegex(ValueError, "Không thể bỏ nhận"):
                database.remove_claim(item_id, guest)
            self.assertEqual(database.query("SELECT quantity FROM item_claims WHERE item_id=?", (item_id,))[0]["quantity"], 1)

    def test_claim_does_not_move_already_paid_unknown_debt(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Database(Path(folder) / "unknown-paid.db")
            database.init()
            payer = database.add_member("An")
            guest = database.add_member("Bình")
            items = allocate_items([{"member_id": None, "person_name": "Chưa xác định",
                                     "item_name": "Trà", "quantity": 1, "unit_price": 30000}])
            data = {"title": "Trà", "payer_member_id": payer, "subtotal": 30000, "shipping_fee": 0,
                    "discount": 0, "tip": 0, "company_support": 0, "allocation_method": "equal"}
            session_id = database.create_session(data, items, [{"member_id": payer, "person_name": "An", "amount": 30000}])
            item_id = database.query("SELECT id FROM items WHERE session_id=?", (session_id,))[0]["id"]
            database.add_payment(session_id, None, "Chưa xác định", 30000, "Chuyển khoản")
            with self.assertRaisesRegex(ValueError, "Hãy sửa khoản thanh toán"):
                database.claim_item(item_id, guest, 1)
            self.assertFalse(database.query("SELECT * FROM item_claims WHERE item_id=?", (item_id,)))

    def test_proportional_allocation_keeps_exact_total(self):
        items = [
            {"person_name": "An", "item_name": "Cơm", "quantity": 1, "unit_price": 40_000},
            {"person_name": "Bình", "item_name": "Bún", "quantity": 1, "unit_price": 60_000},
        ]
        result = allocate_items(items, shipping_fee=15_000, discount=12_000, tip=2_000, company_support=5_000)
        self.assertEqual(sum(x["amount_due"] for x in result), 100_000)
        self.assertEqual(result[0]["amount_due"], 40_000)
        self.assertEqual(result[1]["amount_due"], 60_000)

    def test_equal_allocation_handles_rounding(self):
        items = [{"person_name": str(i), "item_name": "Món", "quantity": 1, "unit_price": 10_000} for i in range(3)]
        result = allocate_items(items, shipping_fee=10_000, method="equal")
        self.assertEqual(sum(x["amount_due"] for x in result), 40_000)

    def test_discount_larger_than_cheap_item_preserves_total(self):
        items = [
            {"person_name": "An", "item_name": "Cơm", "quantity": 1, "unit_price": 10000},
            {"person_name": "Bình", "item_name": "Kẹo", "quantity": 1, "unit_price": 1000},
        ]
        result = allocate_items(items, discount=10000, method="equal")
        self.assertEqual(sum(item["amount_due"] for item in result), 1000)
        self.assertTrue(all(item["amount_due"] >= 0 for item in result))

    def test_partial_payment_balance(self):
        rows = [{"member_id": 1, "person_name": "An", "amount_due": 50_000}]
        payments = [{"member_id": 1, "person_name": "An", "amount": 20_000}]
        self.assertEqual(person_balances(rows, payments)[0]["remaining"], 30_000)

    def test_claims_split_one_bill_line_between_people(self):
        items = [{"id": 10, "member_id": None, "person_name": "Chưa xác định", "quantity": 2, "amount_due": 120_001}]
        claims = [
            {"item_id": 10, "member_id": 1, "person_name": "An", "quantity": 1},
            {"item_id": 10, "member_id": 2, "person_name": "Bình", "quantity": 1},
        ]
        balances = person_balances(items, [], claims)
        self.assertEqual(sum(row["due"] for row in balances), 120_001)
        self.assertEqual([row["due"] for row in balances], [60_001, 60_000])

    def test_member_alias_matching_ignores_accents(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Database(Path(folder) / "alias.db")
            database.init()
            database.add_member("Nguyễn Kim Ngân", "Ngan, KN")
            database.add_member("Nguyễn Thái Bình")
            database.add_member("Trần Thị Quỳnh")
            database.add_member("Lê Văn Nghĩa")
            self.assertEqual(database.find_member("nguyen kim ngan")["name"], "Nguyễn Kim Ngân")
            self.assertEqual(database.find_member("NGAN")["name"], "Nguyễn Kim Ngân")
            self.assertEqual(database.find_member("A Bình")["name"], "Nguyễn Thái Bình")
            self.assertEqual(database.find_member("C Quỳnh")["name"], "Trần Thị Quỳnh")
            self.assertEqual(database.find_member("Nghĩa ngây ngô")["name"], "Lê Văn Nghĩa")

    def test_item_cannot_be_claimed_beyond_remaining_quantity(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Database(Path(folder) / "claims.db")
            database.init()
            first = database.add_member("An")
            second = database.add_member("Bình")
            payer = database.add_member("Người ứng")
            item = allocate_items([{"member_id": None, "person_name": "Chưa xác định", "item_name": "Trà", "quantity": 1, "unit_price": 40_000}])
            data = {"title": "Trà", "payer_member_id": payer, "subtotal": 40_000, "shipping_fee": 0, "discount": 0, "tip": 0, "company_support": 0, "allocation_method": "equal"}
            session_id = database.create_session(data, item, [{"member_id": payer, "person_name": "Người ứng", "amount": 40_000}])
            item_id = database.query("SELECT id FROM items WHERE session_id=?", (session_id,))[0]["id"]
            database.claim_item(item_id, first, 1)
            with self.assertRaises(ValueError):
                database.claim_item(item_id, second, 1)

    def test_group_allocation_keeps_exact_total(self):
        people = [{"id": i, "name": name} for i, name in enumerate(["An", "Bình", "Chi", "Dũng", "Em"], 1)]
        result = allocate_group(people, 1_000_003)
        self.assertEqual(sum(x["amount_due"] for x in result), 1_000_003)
        self.assertEqual([x["amount_due"] for x in result], [200_001, 200_001, 200_001, 200_000, 200_000])

    def test_group_source_prices_survive_database_reinitialization(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Database(Path(folder) / "group.db")
            database.init()
            payer = database.add_member("An")
            items = [
                {"member_id": None, "person_name": "Chưa xác định", "item_name": "Trà",
                 "quantity": 1, "unit_price": 34_000, "allocated_fee": 0,
                 "allocated_discount": 0, "amount_due": 0},
                {"member_id": payer, "person_name": "An", "item_name": "Phần đóng góp chi phí chung",
                 "quantity": 1, "unit_price": 34_000, "allocated_fee": 0,
                 "allocated_discount": 0, "amount_due": 34_000},
            ]
            data = {"title": "Bao cả nhóm", "payer_member_id": payer, "subtotal": 34_000,
                    "shipping_fee": 0, "discount": 0, "tip": 0,
                    "company_support": 0, "allocation_method": "group_equal"}
            session_id = database.create_session(data, items, [])
            database.init()
            database.init()
            rows = database.query("SELECT item_name, unit_price, amount_due FROM items WHERE session_id=? ORDER BY id", (session_id,))
            self.assertEqual(rows[0]["unit_price"], 34_000)
            self.assertEqual(rows[0]["amount_due"], 0)
            self.assertEqual(rows[1]["amount_due"], 34_000)

    def test_history_page_filters_and_limits_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Database(Path(folder) / "history.db")
            database.init()
            payer = database.add_member("An")
            guest = database.add_member("Bình")
            for number in range(12):
                items = allocate_items([{"member_id": guest, "person_name": "Bình",
                                         "item_name": "Trà", "quantity": 1, "unit_price": 10_000}])
                data = {"title": f"Đơn {number}", "restaurant": "Quán",
                        "payer_member_id": payer, "subtotal": 10_000, "shipping_fee": 0,
                        "discount": 0, "tip": 0, "company_support": 0,
                        "allocation_method": "equal"}
                session_id = database.create_session(data, items, [])
                if number == 0:
                    database.add_payment(session_id, guest, "Bình", 10_000, "Chuyển khoản")
            count, first = database.history_page(page=1, page_size=5)
            self.assertEqual((count, len(first)), (12, 5))
            self.assertEqual([row["id"] for row in first], [12, 11, 10, 9, 8])
            self.assertEqual([row["id"] for row in database.history_page(page=3, page_size=5)[1]], [2, 1])
            self.assertEqual(database.history_page(keyword="don 7")[0], 1)
            self.assertEqual(database.history_page(keyword="binh")[0], 12)
            self.assertEqual(database.history_page(status="Đã thu đủ")[0], 1)
            self.assertEqual(database.history_page(status="Chưa thu đủ")[0], 11)
            self.assertEqual(database.history_page(payer="Bình")[0], 0)

    def test_custom_group_allocation_must_match_total(self):
        people = [{"id": 1, "name": "An"}, {"id": 2, "name": "Bình"}]
        with self.assertRaises(ValueError):
            allocate_group(people, 100_000, {"1": 40_000, "2": 50_000})

    def test_database_create_and_cascade_delete(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Database(Path(folder) / "test.db")
            database.init()
            member_id = database.add_member("An")
            items = allocate_items([{"member_id": member_id, "person_name": "An", "item_name": "Cơm", "quantity": 1, "unit_price": 45_000}])
            data = {"title": "Trưa", "payer_member_id": member_id, "subtotal": 45_000, "shipping_fee": 0, "discount": 0, "tip": 0, "company_support": 0, "allocation_method": "equal"}
            session_id = database.create_session(data, items, [{"member_id": member_id, "person_name": "An", "amount": 45_000}])
            payments = database.query("SELECT * FROM payments WHERE session_id=?", (session_id,))
            self.assertEqual(payments[0]["amount"], 45_000)
            self.assertEqual(payments[0]["method"], "Tự đối trừ")
            self.assertEqual(database.query("SELECT status FROM sessions WHERE id=?", (session_id,))[0]["status"], "settled")
            database.delete_session(session_id)
            self.assertFalse(database.query("SELECT * FROM items WHERE session_id=?", (session_id,)))


if __name__ == "__main__":
    unittest.main()
