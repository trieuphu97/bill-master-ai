"""Regression checks against an isolated database, never the company database."""
import tempfile
import unittest
import tomllib
import re
from pathlib import Path

from streamlit.testing.v1 import AppTest

from database import Database
from services import allocate_items, allocate_items_with_shared_promos
from ui import balance_cards, format_datetime, page_header, stylesheet


class PresentationTests(unittest.TestCase):
    def test_payment_timestamp_uses_vietnamese_display_format(self):
        self.assertEqual(format_datetime('2026-10-14T16:16:12'), '14/10/2026 16:16')
        self.assertEqual(format_datetime(''), '')
    def test_native_light_and_dark_palettes_are_configured(self):
        config = tomllib.loads(Path('.streamlit/config.toml').read_text(encoding='utf-8'))
        self.assertEqual(config['theme']['light']['backgroundColor'], '#f6f8fa')
        self.assertEqual(config['theme']['dark']['backgroundColor'], '#101b21')
        for mode in ['light', 'dark']:
            self.assertIn('textColor', config['theme'][mode])
            self.assertIn('sidebar', config['theme'][mode])

    def test_styles_follow_native_theme_and_reserve_toolbar_space(self):
        css = stylesheet()
        self.assertIn('light-dark(', css)
        self.assertIn('padding: 4.5rem', css)
        self.assertIn('line-height: 1.5', css)

    def test_user_names_and_titles_are_escaped(self):
        html = balance_cards([{"person_name": '<script>alert(1)</script>', "remaining": 40080}])
        self.assertNotIn('<script>', html)
        self.assertIn('40,080 đ', html)
        self.assertIn('owing', html)
        self.assertIn('&lt;script&gt;', page_header('<script>', 'A & B'))

    def test_settled_balances_have_correct_state(self):
        html = balance_cards([{"person_name": 'An', "remaining": 0}])
        self.assertIn('settled', html)
        self.assertIn('Đã thanh toán đủ', html)


class AppRegressionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.db = Database(Path(self.folder.name) / 'test.db')
        self.db.init()
        self.payer = self.db.add_member('An')
        self.member = self.db.add_member('Bình', 'Binh')
        rows = allocate_items([
            {'member_id': self.member, 'person_name': 'Bình', 'item_name': 'Cơm', 'quantity': 1, 'unit_price': 40000},
            {'member_id': None, 'person_name': 'Chưa xác định', 'item_name': 'Trà', 'quantity': 2, 'unit_price': 30000},
        ])
        self.session_id = self.db.create_session(
            {'title': 'Bữa trưa', 'restaurant': 'Quán nhà', 'payer_member_id': self.payer,
             'subtotal': 100000, 'shipping_fee': 0, 'discount': 0, 'tip': 0,
             'company_support': 0, 'allocation_method': 'equal'}, rows,
            [{'member_id': self.payer, 'person_name': 'An', 'amount': 100000}],
        )
        self.app = AppTest.from_file('app.py', default_timeout=30)
        self.app.secrets.update({
            'DB_NAME': self.db.path, 'IMAGE_STORE_DIR': self.folder.name,
            'APP_PASSWORD': '', 'GEMINI_API_KEY': '', 'APP_BASE_URL': 'http://localhost:8501/',
            'NOTIFICATION_WEBHOOK_URL': '', 'TELEGRAM_BOT_TOKEN': '', 'TELEGRAM_CHAT_ID': '',
        })
        self.app.run()
        self.assertHealthy()

    def assertHealthy(self):
        self.assertEqual(len(self.app.exception), 0, str(self.app.exception))

    def go(self, name):
        self.app.radio(key='main_navigation').set_value(name).run()
        self.assertHealthy()

    def field(self, collection, prefix):
        return next(field for field in collection if field.label.startswith(prefix))

    def button(self, label):
        return next(button for button in self.app.button if button.label == label)

    def select_payment_row(self, row_index):
        self.app.session_state[f'payment_history_{self.session_id}'] = {
            'selection': {'rows': [row_index], 'columns': [], 'cells': []}
        }
        self.app.run()
        self.assertHealthy()
        self.assertTrue(any(button.label == '✏️ Sửa khoản thu' for button in self.app.button),
                        str(self.app.session_state[f'payment_history_{self.session_id}']))

    def test_all_pages_render_with_existing_data(self):
        for page in self.app.radio(key='main_navigation').options:
            with self.subTest(page=page):
                self.go(page)

    def test_member_rows_have_their_own_delete_buttons(self):
        self.go("👥 Thành viên")
        buttons = {button.key for button in self.app.button}
        self.assertIn(f"delete_member_{self.payer}", buttons)
        self.assertIn(f"delete_member_{self.member}", buttons)
        self.assertNotIn("delete_member_button", buttons)

    def test_history_pagination_controls(self):
        for number in range(11):
            rows = allocate_items([{'member_id': self.member, 'person_name': 'Bình',
                                    'item_name': 'Trà', 'quantity': 1, 'unit_price': 10000}])
            self.db.create_session(
                {'title': f'Đơn {number}', 'payer_member_id': self.payer,
                 'subtotal': 10000, 'shipping_fee': 0, 'discount': 0, 'tip': 0,
                 'company_support': 0, 'allocation_method': 'equal'}, rows, [],
            )
        self.go('📚 Lịch sử')
        self.assertEqual(len([row for row in self.app.expander if '#'
                              in row.label and 'còn' in row.label]), 10)
        self.button('Sau →').click().run()
        self.assertHealthy()
        self.assertEqual(self.app.session_state['history_page'], 2)

    def test_edit_dialog_does_not_reopen_after_leaving_history(self):
        self.go('📚 Lịch sử')
        self.button('✏️ Sửa đơn').click().run()
        self.assertHealthy()
        self.assertEqual(self.app.session_state['editing_session_id'], self.session_id)
        self.go('➕ Đơn mới')
        self.assertNotIn('editing_session_id', self.app.session_state)
        self.go('📚 Lịch sử')
        self.assertFalse(any(field.label.startswith('Tên đơn') for field in self.app.text_input))

    def test_manual_order_save_resets_and_updates_overview(self):
        self.go('➕ Đơn mới')
        self.assertEqual(self.field(self.app.metric, 'Tổng tạm tính').value, '0 đ')
        self.field(self.app.selectbox, 'Người ứng tiền').set_value('An')
        self.field(self.app.selectbox, 'Người').set_value('Bình')
        self.field(self.app.text_input, 'Món').set_value('Bún')
        self.field(self.app.text_input, 'Đơn giá').set_value('55,000')
        self.button('Tính và lưu đơn').click().run()
        self.assertHealthy()
        self.assertEqual(len(self.db.query('SELECT * FROM sessions')), 2)
        self.assertEqual(self.db.query('SELECT subtotal FROM sessions ORDER BY id DESC')[0]['subtotal'], 55000)
        self.assertEqual(self.field(self.app.text_input, 'Món').value, '')
        self.assertNotIn('new_bill_scroll', self.app.session_state)
        self.assertTrue(any('Đã lưu đơn' in alert.value for alert in self.app.success))
        self.go('📊 Tổng quan')
        self.assertEqual(self.app.metric[1].value, '155,000 đ')

    def test_new_bill_scroll_request_is_consumed_after_render(self):
        self.app.session_state['new_bill_scroll'] = ('review', 'test-scroll')
        self.go('➕ Đơn mới')
        self.assertNotIn('new_bill_scroll', self.app.session_state)

    def test_new_bill_shows_scanned_total_and_difference_before_saving(self):
        self.app.session_state['draft'] = {
            'restaurant': 'Quán thử', 'subtotal': 30_000, 'total': 35_000,
            'shipping_fee': 0, 'discount': 0, 'tip': 0,
            'items': [{'person_name': 'Bình', 'item_name': 'Trà', 'quantity': 1, 'unit_price': 30_000}],
        }
        self.go('➕ Đơn mới')
        self.assertEqual(self.field(self.app.metric, 'App tính từ các món').value, '30,000 đ')
        self.assertEqual(self.field(self.app.metric, 'AI đọc từ hóa đơn').value, '35,000 đ')
        self.assertEqual(self.field(self.app.metric, 'Chênh lệch').value, '5,000 đ')
        self.assertTrue(any('Tổng tiền chưa khớp' in message.value for message in self.app.error))

    def test_item_count_accepts_total_quantity_not_just_line_count(self):
        self.app.session_state['draft'] = {
            'restaurant': 'Quán thử', 'item_count': 3, 'subtotal': 80_000, 'total': 80_000,
            'shipping_fee': 0, 'discount': 0, 'tip': 0,
            'items': [
                {'person_name': 'Bình', 'item_name': 'Trà', 'quantity': 2, 'unit_price': 30_000},
                {'person_name': 'An', 'item_name': 'Bánh', 'quantity': 1, 'unit_price': 20_000},
            ],
        }
        self.go('➕ Đơn mới')
        self.assertFalse(any('AI đọc 3 món nhưng form' in warning.value for warning in self.app.warning))
        self.app.session_state['draft']['item_count'] = 4
        self.app.run()
        self.assertHealthy()
        self.assertTrue(any('AI đọc 4 món nhưng form' in warning.value for warning in self.app.warning))

    def test_reset_new_bill_discards_draft_without_deleting_saved_orders(self):
        self.go('➕ Đơn mới')
        self.field(self.app.text_input, 'Tên đơn').set_value('Đơn nhập nhầm')
        self.field(self.app.text_input, 'Quán').set_value('Quán thử')
        self.field(self.app.selectbox, 'Người ứng tiền').set_value('An')
        self.field(self.app.text_input, 'Món').set_value('Trà')
        self.field(self.app.text_input, 'Đơn giá').set_value('54,000')
        self.app.session_state['scan_signature'] = 'old-image.png:100'
        previous_version = self.app.session_state['input_version'] if 'input_version' in self.app.session_state else 0
        self.button('↺ Làm lại').click().run()
        self.assertHealthy()
        self.assertEqual(self.field(self.app.text_input, 'Quán').value, '')
        self.assertEqual(self.field(self.app.text_input, 'Món').value, '')
        self.assertEqual(self.field(self.app.text_input, 'Đơn giá').value, '0')
        self.assertEqual(self.field(self.app.selectbox, 'Người ứng tiền').value, 'Chưa xác định')
        self.assertNotIn('scan_signature', self.app.session_state)
        self.assertEqual(self.app.session_state['input_version'], previous_version + 1)
        self.assertEqual(len(self.db.query('SELECT * FROM sessions')), 1)

    def test_vat_bill_uses_tax_inclusive_item_prices_without_adding_tax_twice(self):
        self.app.session_state['draft'] = {
            'restaurant': 'Mơ Nướng', 'subtotal': 354_240, 'tax_included': 26_240,
            'shipping_fee': 0, 'discount': 0, 'tip': 0, 'total': 354_240,
            'items': [
                {'person_name': 'Bình', 'item_name': 'Bánh phồng tôm', 'quantity': 3,
                 'unit_price': 10_800},
                {'person_name': 'An', 'item_name': 'Gỏi ngó sen', 'quantity': 2,
                 'unit_price': 160_920},
            ],
        }
        self.go('➕ Đơn mới')
        self.assertEqual(self.field(self.app.text_input, 'Thuế GTGT').value, '26,240')
        self.assertEqual(self.field(self.app.text_input, 'Đơn giá').value, '10,800')
        self.assertEqual(self.field(self.app.text_input, 'Quán').value, 'Mơ Nướng')
        self.field(self.app.selectbox, 'Người ứng tiền').set_value('An')
        self.button('Tính và lưu đơn').click().run()
        self.assertHealthy()
        saved = self.db.query('SELECT subtotal,tax_included FROM sessions ORDER BY id DESC LIMIT 1')[0]
        self.assertEqual((saved['subtotal'], saved['tax_included']), (354_240, 26_240))

    def test_one_dong_promo_option_keeps_receipt_total(self):
        self.app.session_state['draft'] = {
            'restaurant': 'Quán trà', 'subtotal': 0, 'discount': 0,
            'shipping_fee': 0, 'tip': 0,
            'items': [
                {'person_name': 'Bình', 'item_name': 'Trà 1đ', 'quantity': 1,
                 'unit_price': 1, 'original_unit_price': 54000},
                {'person_name': 'An', 'item_name': 'Trà thường', 'quantity': 1,
                 'unit_price': 54000},
            ],
        }
        self.go('➕ Đơn mới')
        self.field(self.app.selectbox, 'Người ứng tiền').set_value('An')
        self.assertTrue(any(field.label.startswith('🎁 Chia ưu đãi') and field.value
                            for field in self.app.checkbox))
        self.assertEqual(sum(field.label.startswith('🎁 Chia ưu đãi')
                             for field in self.app.checkbox), 1)
        self.button('Tính và lưu đơn').click().run()
        self.assertHealthy()
        saved = self.db.query('SELECT id,subtotal,discount FROM sessions ORDER BY id DESC LIMIT 1')[0]
        self.assertEqual((saved['subtotal'], saved['discount']), (54001, 0))
        items = self.db.query('SELECT unit_price,promo_original_price,amount_due FROM items WHERE session_id=? ORDER BY id', (saved['id'],))
        self.assertEqual((items[0]['unit_price'], items[0]['promo_original_price']), (1, 54000))
        self.assertEqual(sum(item['amount_due'] for item in items), 54001)

    def test_edit_bill_preserves_one_dong_promo(self):
        raw = [
            {'member_id': self.member, 'person_name': 'Bình', 'item_name': 'Trà 1đ',
             'quantity': 1, 'unit_price': 1, 'promo_original_price': 54000},
            {'member_id': self.payer, 'person_name': 'An', 'item_name': 'Trà thường',
             'quantity': 1, 'unit_price': 54000},
        ]
        session_id = self.db.create_session(
            {'title': 'Đơn ưu đãi', 'payer_member_id': self.payer,
             'subtotal': 54001, 'shipping_fee': 0, 'discount': 0, 'tip': 0,
             'company_support': 0, 'allocation_method': 'equal'},
            allocate_items_with_shared_promos(raw), [],
        )
        self.go('📚 Lịch sử')
        next(button for button in self.app.button if button.key == f'edit_session_{session_id}').click().run()
        self.assertHealthy()
        self.assertEqual(sum(field.label.startswith('🎁 Chia ưu đãi')
                             for field in self.app.checkbox), 1)
        self.assertEqual(self.field(self.app.text_input, 'Giá gốc trước ưu đãi').value, '54,000')
        self.button('Lưu thay đổi đơn').click().run()
        self.assertHealthy()
        saved = self.db.query('SELECT unit_price,promo_original_price FROM items WHERE session_id=? ORDER BY id', (session_id,))
        self.assertEqual((saved[0]['unit_price'], saved[0]['promo_original_price']), (1, 54000))

    def test_group_modes_and_required_validation(self):
        self.go('➕ Đơn mới')
        self.button('Tính và lưu đơn').click().run()
        self.assertHealthy()
        self.assertTrue(self.app.error)
        self.assertEqual(len(self.db.query('SELECT * FROM sessions')), 1)
        mode = self.field(self.app.radio, 'Cách tính công nợ')
        for value in ['Chia đều cho nhóm', 'Nhập số tiền từng người']:
            mode.set_value(value).run()
            self.assertHealthy()
            self.assertTrue(self.app.multiselect)
            mode = self.field(self.app.radio, 'Cách tính công nợ')

    def test_custom_group_amount_and_percentage_rebalance_automatically(self):
        third_member = self.db.add_member('Cường')
        self.go('➕ Đơn mới')
        self.field(self.app.text_input, 'Món').set_value('Liên hoan')
        self.field(self.app.text_input, 'Đơn giá').set_value('100,000')
        self.field(self.app.selectbox, 'Người ứng tiền').set_value('An')
        self.field(self.app.radio, 'Cách tính công nợ').set_value('Nhập số tiền từng người').run()
        self.assertHealthy()
        self.field(self.app.multiselect, 'Người cùng đóng').set_value(
            [self.payer, self.member, third_member]
        ).run()
        self.assertHealthy()
        self.app.text_input(key=f'group_amount_0_{self.payer}').set_value('abc').run()
        self.assertHealthy()
        self.assertTrue(any('Chỉ nhập số tiền hoặc phần trăm' in error.value for error in self.app.error))
        self.assertEqual(self.app.text_input(key=f'group_amount_0_{self.payer}').value, '33,334')
        self.app.text_input(key=f'group_amount_0_{self.payer}').set_value('60,000').run()
        self.assertHealthy()
        state = self.app.session_state['group_custom_state_0']['amounts']
        self.assertEqual(state, {str(self.payer): 60_000, str(self.member): 20_000,
                                 str(third_member): 20_000})
        self.assertFalse(any(field.label == 'Cách nhập phần đóng' for field in self.app.radio))
        self.app.text_input(key=f'group_amount_0_{self.payer}').set_value('50%').run()
        self.assertHealthy()
        state = self.app.session_state['group_custom_state_0']['amounts']
        self.assertEqual(state, {str(self.payer): 50_000, str(self.member): 25_000,
                                 str(third_member): 25_000})
        self.assertEqual(self.app.text_input(key=f'group_amount_0_{self.payer}').value, '50,000')
        self.field(self.app.text_input, 'Đơn giá').set_value('120,000').run()
        self.assertHealthy()
        state = self.app.session_state['group_custom_state_0']['amounts']
        self.assertEqual(state, {str(self.payer): 60_000, str(self.member): 30_000,
                                 str(third_member): 30_000})
        self.button('Tính và lưu đơn').click().run()
        self.assertHealthy()
        saved = self.db.query('SELECT id FROM sessions ORDER BY id DESC LIMIT 1')[0]
        contributions = self.db.query(
            "SELECT person_name,amount_due FROM items WHERE session_id=? "
            "AND item_name='Phần đóng góp chi phí chung' ORDER BY id", (saved['id'],),
        )
        self.assertEqual([row['amount_due'] for row in contributions], [60_000, 30_000, 30_000])

    def test_split_item_prefills_first_person_and_allows_unknown_second(self):
        self.app.session_state['draft'] = {
            'restaurant': 'Quán trà', 'subtotal': 40_000, 'total': 40_000,
            'shipping_fee': 0, 'discount': 0, 'tip': 0,
            'items': [{'person_name': 'Bình', 'item_name': 'Trà chanh',
                       'quantity': 2, 'unit_price': 20_000}],
        }
        self.go('➕ Đơn mới')
        self.field(self.app.selectbox, 'Người ứng tiền').set_value('An')
        self.field(self.app.radio, 'Phân bổ').set_value('Chia cho nhiều người').run()
        self.assertHealthy()
        self.assertEqual(self.field(self.app.selectbox, 'Người 1').value, 'Bình')
        self.assertEqual(self.field(self.app.selectbox, 'Người 2').value, 'Chưa xác định')
        self.assertTrue(any('1 phần chưa xác định' in alert.value for alert in self.app.info))
        self.button('Tính và lưu đơn').click().run()
        self.assertHealthy()
        saved = self.db.query('SELECT id FROM sessions ORDER BY id DESC LIMIT 1')[0]
        rows = self.db.query('SELECT person_name,quantity,amount_due FROM items WHERE session_id=? ORDER BY id',
                             (saved['id'],))
        self.assertEqual([(row['person_name'], row['quantity']) for row in rows],
                         [('Bình', 1), ('Chưa xác định', 1)])
        self.assertEqual(sum(row['amount_due'] for row in rows), 40_000)

    def test_split_item_can_save_when_all_people_are_unknown(self):
        self.app.session_state['draft'] = {
            'restaurant': 'Quán trà', 'subtotal': 40_000, 'total': 40_000,
            'shipping_fee': 0, 'discount': 0, 'tip': 0,
            'items': [{'person_name': 'Chưa xác định', 'item_name': 'Trà chanh',
                       'quantity': 2, 'unit_price': 20_000}],
        }
        self.go('➕ Đơn mới')
        self.field(self.app.selectbox, 'Người ứng tiền').set_value('An')
        self.field(self.app.radio, 'Phân bổ').set_value('Chia cho nhiều người').run()
        self.assertHealthy()
        self.assertEqual(self.field(self.app.selectbox, 'Người 1').value, 'Chưa xác định')
        self.assertEqual(self.field(self.app.selectbox, 'Người 2').value, 'Chưa xác định')
        self.button('Tính và lưu đơn').click().run()
        self.assertHealthy()
        self.assertEqual(len(self.db.query("SELECT id FROM sessions WHERE title LIKE 'Đồ ăn ngày %'")), 1)

    def test_shared_page_has_claim_controls_and_no_admin_navigation(self):
        token = self.db.query('SELECT share_token FROM sessions WHERE id=?', (self.session_id,))[0]['share_token']
        shared = AppTest.from_file('app.py', default_timeout=15)
        shared.secrets.update(dict(self.app.secrets))
        shared.query_params['bill'] = token
        shared.run()
        self.assertEqual(len(shared.exception), 0)
        self.assertFalse(shared.radio)
        self.assertTrue(any(button.label == 'Nhận món' for button in shared.button))
        self.assertEqual(shared.number_input[0].max, 2)

    def test_password_gate_still_blocks_navigation(self):
        locked = AppTest.from_file('app.py', default_timeout=15)
        locked.secrets.update(dict(self.app.secrets))
        locked.secrets['APP_PASSWORD'] = 'test-password'
        locked.run()
        self.assertFalse(locked.radio)
        locked.text_input[0].set_value('test-password')
        locked.button[0].click().run()
        self.assertEqual(len(locked.exception), 0)
        self.assertTrue(locked.radio)

    def test_payment_dialog_opens_without_implicitly_recording_payment(self):
        self.go('📚 Lịch sử')
        self.button('Nhận tiền · Bình').click().run()
        self.assertHealthy()
        self.assertIsNotNone(self.button('Xác nhận đã nhận'))
        self.assertFalse(self.db.query('SELECT * FROM payments WHERE member_id=?', (self.member,)))

    def test_payment_dialog_records_partial_payment(self):
        self.go('📚 Lịch sử')
        self.button('Nhận tiền · Bình').click().run()
        self.assertHealthy()
        self.field(self.app.text_input, 'Số tiền nhận').set_value('10,000').run()
        self.assertHealthy()
        self.button('Xác nhận đã nhận').click().run()
        self.assertHealthy()
        received = self.db.query('SELECT amount FROM payments WHERE member_id=?', (self.member,))
        self.assertEqual([row['amount'] for row in received], [10_000])

    def test_payment_dialog_uses_fresh_remaining_balance(self):
        self.db.add_payment(self.session_id, self.member, 'Bình', 10_000, 'Chuyển khoản')
        self.go('📚 Lịch sử')
        self.button('Nhận tiền · Bình').click().run()
        self.assertHealthy()
        self.assertEqual(self.field(self.app.text_input, 'Số tiền nhận').value, '30,000')

    def test_duplicate_bill_requires_explicit_acknowledgement(self):
        self.go('➕ Đơn mới')
        self.field(self.app.selectbox, 'Người ứng tiền').set_value('An')
        self.field(self.app.text_input, 'Quán').set_value('Quán nhà')
        self.field(self.app.selectbox, 'Người').set_value('Bình')
        self.field(self.app.text_input, 'Món').set_value('Cơm')
        self.field(self.app.text_input, 'Đơn giá').set_value('40,000')
        self.button('＋ Thêm món').click().run()
        dish_fields = [field for field in self.app.text_input if field.label.startswith('Món')]
        price_fields = [field for field in self.app.text_input if field.label.startswith('Đơn giá')]
        dish_fields[-1].set_value('Trà')
        price_fields[-1].set_value('30,000')
        self.app.number_input[1].set_value(2)
        self.app.run()
        self.assertHealthy()
        self.assertTrue(any('có thể đã được lưu' in warning.value for warning in self.app.warning))
        self.assertTrue(self.button('Tính và lưu đơn').disabled)

    def test_bill_edit_dialog_opens_with_existing_values(self):
        self.go('📚 Lịch sử')
        self.button('✏️ Sửa đơn').click().run()
        self.assertHealthy()
        self.assertEqual(self.field(self.app.text_input, 'Tên đơn').value, 'Bữa trưa')
        self.assertIsNotNone(self.button('Lưu thay đổi đơn'))

    def test_payment_history_formats_amount_and_timestamp(self):
        self.db.add_payment(self.session_id, self.member, 'Bình', 15000, 'Chuyển khoản')
        self.go('📚 Lịch sử')
        payment_table = next(table.value for table in self.app.dataframe if 'Số tiền' in table.value.columns)
        self.assertIn('15,000 đ', payment_table['Số tiền'].tolist())
        self.assertTrue(all(re.fullmatch(r'\d{2}/\d{2}/\d{4} \d{2}:\d{2}', value)
                            for value in payment_table['Ngày trả']))

    def test_many_payments_show_only_one_action_pair_after_selection(self):
        for _ in range(6):
            self.db.add_payment(self.session_id, self.member, 'Bình', 1000, 'Chuyển khoản')
        self.go('📚 Lịch sử')
        self.assertFalse(any(button.label in {'✏️ Sửa khoản thu', '🗑️ Hủy khoản thu'}
                             for button in self.app.button))
        self.select_payment_row(0)
        self.assertEqual(sum(button.label == '✏️ Sửa khoản thu' for button in self.app.button), 1)
        self.assertEqual(sum(button.label == '🗑️ Hủy khoản thu' for button in self.app.button), 1)

    def test_auto_offset_payment_cannot_be_edited_from_table(self):
        self.go('📚 Lịch sử')
        self.app.session_state[f'payment_history_{self.session_id}'] = {
            'selection': {'rows': [0], 'columns': [], 'cells': []}
        }
        self.app.run()
        self.assertHealthy()
        self.assertFalse(any(button.label in {'✏️ Sửa khoản thu', '🗑️ Hủy khoản thu'}
                             for button in self.app.button))

    def test_manual_reminder_is_logged_without_sending_and_dates_are_formatted(self):
        self.go('📚 Lịch sử')
        self.assertIsNotNone(self.button('✓ Ghi nhận đã nhắc'))
        self.assertIsNotNone(self.button('✈️ Gửi Telegram'))
        self.assertIsNotNone(self.button('↗ Gửi webhook'))
        self.button('✓ Ghi nhận đã nhắc').click().run()
        self.assertHealthy()
        logs = self.db.query('SELECT channel, sent_at FROM reminder_logs WHERE session_id=?', (self.session_id,))
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]['channel'], 'Thủ công')
        self.assertRegex(format_datetime(logs[0]['sent_at']),
                         r'^\d{2}/\d{2}/\d{4} \d{2}:\d{2}$')

    def test_payment_history_actions_open_dialogs(self):
        self.db.add_payment(self.session_id, self.member, 'Bình', 15000, 'Chuyển khoản')
        payment_id = self.db.query("SELECT id FROM payments WHERE session_id=? AND method='Chuyển khoản'", (self.session_id,))[0]['id']
        self.go('📚 Lịch sử')
        self.select_payment_row(0)
        self.button('✏️ Sửa khoản thu').click().run()
        self.assertHealthy()
        self.assertIn('payment_dialog_action', self.app.session_state)
        self.assertEqual(self.app.session_state['payment_dialog_action'], ('edit', payment_id))
        self.assertIsNotNone(self.button('Lưu khoản thanh toán'))
        self.field(self.app.text_input, 'Số tiền đã nhận').set_value('12,000')
        self.field(self.app.text_input, 'Lý do sửa').set_value('Nhập nhầm số tiền')
        self.button('Lưu khoản thanh toán').click().run()
        self.assertHealthy()
        self.assertEqual(self.db.query('SELECT amount FROM payments WHERE id=?', (payment_id,))[0]['amount'], 12000)
    def test_payment_history_delete_action_removes_payment(self):
        self.db.add_payment(self.session_id, self.member, 'Bình', 15000, 'Chuyển khoản')
        payment_id = self.db.query("SELECT id FROM payments WHERE session_id=? AND method='Chuyển khoản'", (self.session_id,))[0]['id']
        self.go('📚 Lịch sử')
        self.select_payment_row(0)
        self.button('🗑️ Hủy khoản thu').click().run()
        self.assertHealthy()
        self.assertIsNotNone(self.button('Hủy khoản này'))
        self.field(self.app.text_input, 'Lý do hủy').set_value('Ghi trùng')
        self.button('Hủy khoản này').click().run()
        self.assertHealthy()
        self.assertFalse(self.db.query('SELECT * FROM payments WHERE id=?', (payment_id,)))

    def test_cancelled_bill_edit_does_not_reopen_after_deleting_payment(self):
        self.db.add_payment(self.session_id, self.member, 'Bình', 15000, 'Chuyển khoản')
        self.go('📚 Lịch sử')
        self.button('✏️ Sửa đơn').click().run()
        self.assertHealthy()
        self.assertEqual(self.app.session_state['editing_session_id'], self.session_id)
        # The modal X closes in the browser without running a Streamlit callback.
        self.select_payment_row(0)
        self.button('🗑️ Hủy khoản thu').click().run()
        self.assertHealthy()
        self.assertNotIn('editing_session_id', self.app.session_state)
        self.assertIsNotNone(self.button('Hủy khoản này'))
        self.field(self.app.text_input, 'Lý do hủy').set_value('Nhập nhầm')
        self.button('Hủy khoản này').click().run()
        self.assertHealthy()
        self.assertNotIn('editing_session_id', self.app.session_state)


if __name__ == '__main__':
    unittest.main()
