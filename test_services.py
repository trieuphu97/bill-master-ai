import tempfile
import unittest
from pathlib import Path

from database import Database
from services import allocate_group, allocate_items, person_balances


class BillingTests(unittest.TestCase):
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
