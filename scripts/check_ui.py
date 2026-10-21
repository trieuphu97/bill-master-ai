"""Run local browser checks on a disposable copy and database.

Usage: python scripts/check_ui.py
Requires Playwright and an installed Chromium browser.
"""
import json
import os
import re
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from database import Database
from services import allocate_items


def login(page):
    page.get_by_role('textbox', name=re.compile('Mật khẩu công ty')).fill('preview-password')
    page.get_by_role('button', name='Đăng nhập', exact=True).click()


def assert_heading_clear(page):
    page.locator('.bm-page-heading h1').evaluate(
        '(el) => { for (let parent = el.parentElement; parent; parent = parent.parentElement) parent.scrollTop = 0; }')
    page.wait_for_timeout(150)
    heading = page.locator('.bm-page-heading h1').bounding_box()
    header = page.get_by_test_id('stHeader').bounding_box()
    assert heading and header and heading['y'] >= header['y'] + header['height'], 'Heading overlaps toolbar'
    assert page.locator('.bm-page-heading h1').evaluate(
        '(el) => parseFloat(getComputedStyle(el).lineHeight) >= parseFloat(getComputedStyle(el).fontSize) * 1.4')


def switch_theme(page, name):
    page.get_by_test_id('stMainMenuButton').click()
    page.get_by_role('menuitemradio', name=re.compile(name, re.I)).click()
    if page.get_by_test_id('stMainMenuButton').get_attribute('aria-expanded') == 'true':
        page.get_by_test_id('stMainMenuButton').click()
    page.wait_for_timeout(600)
    scheme = page.locator('.stApp').evaluate('(el) => getComputedStyle(el).colorScheme')
    assert scheme == name.lower(), scheme
    background = page.locator('.stApp').evaluate('(el) => getComputedStyle(el).backgroundColor')
    assert background == ('rgb(16, 27, 33)' if name == 'Dark' else 'rgb(246, 248, 250)'), background


def main():
    output = Path(tempfile.mkdtemp(prefix='billmaster-ui-screenshots-'))
    with tempfile.TemporaryDirectory(prefix='billmaster-browser-') as folder:
        preview = Path(folder)
        for name in ['app.py', 'ui.py', 'style.css', 'services.py', 'database.py']:
            shutil.copy2(ROOT / name, preview / name)
        config = preview / '.streamlit'
        config.mkdir()
        shutil.copy2(ROOT / '.streamlit' / 'config.toml', config / 'config.toml')
        # Only test credentials. No company secrets or production data copied.
        (config / 'secrets.toml').write_text(
            'DB_NAME = "preview.db"\nIMAGE_STORE_DIR = "images"\nAPP_PASSWORD = "preview-password"\n'
            'GEMINI_API_KEY = ""\nNOTIFICATION_WEBHOOK_URL = ""\n'
            'TELEGRAM_BOT_TOKEN = ""\nTELEGRAM_CHAT_ID = ""\n', encoding='utf-8')
        db = Database(preview / 'preview.db')
        db.init()
        payer = db.add_member('Nguyễn Thái Bình')
        guest = db.add_member('Kim Ngân')
        db.add_member('Lưu Hoài Nam')
        items = allocate_items([
            {'member_id': payer, 'person_name': 'Nguyễn Thái Bình', 'item_name': 'Cơm gà', 'quantity': 1, 'unit_price': 55000},
            {'member_id': guest, 'person_name': 'Kim Ngân', 'item_name': 'Bún bò', 'quantity': 1, 'unit_price': 45000},
            {'member_id': None, 'person_name': 'Chưa xác định', 'item_name': 'Trà sữa', 'quantity': 2, 'unit_price': 35000},
        ], shipping_fee=16000, discount=40080, method='equal')
        session = db.create_session(
            {'title': 'Bữa trưa cùng nhóm', 'restaurant': 'Quán nhà', 'payer_member_id': payer,
             'subtotal': 170000, 'shipping_fee': 16000, 'discount': 40080, 'tip': 0,
             'company_support': 0, 'allocation_method': 'equal'}, items,
            [{'member_id': payer, 'person_name': 'Nguyễn Thái Bình', 'amount': 145920}])
        token = db.query('SELECT share_token FROM sessions WHERE id=?', (session,))[0]['share_token']
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        with (preview / 'server.log').open('w', encoding='utf-8') as log:
            process = subprocess.Popen(
                [sys.executable, '-m', 'streamlit', 'run', 'app.py', '--server.headless=true',
                 '--server.address=127.0.0.1', f'--server.port={port}', '--browser.gatherUsageStats=false'],
                cwd=preview, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            try:
                for _ in range(60):
                    try:
                        if urlopen(url + '/_stcore/health', timeout=1).status == 200:
                            break
                    except Exception:
                        time.sleep(.5)
                else:
                    raise RuntimeError('Preview server did not start')
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch()
                    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(url)
                    login(page)
                    page.locator('.bm-person').first.wait_for()
                    page.wait_for_timeout(1200)
                    assert_heading_clear(page)
                    page.screenshot(path=str(output / 'desktop-overview.png'), full_page=True)
                    for label, title in [
                        ('➕ Đơn mới', 'Đơn mới'), ('📚 Lịch sử', 'Lịch sử'),
                        ('👥 Thành viên', 'Thành viên'), ('📈 Báo cáo', 'Báo cáo'), ('⚙️ Cài đặt', 'Cài đặt')]:
                        page.get_by_test_id('stSidebar').get_by_text(label, exact=True).click()
                        page.locator('.bm-page-heading h1').filter(has_text=title).wait_for()
                        page.wait_for_timeout(1200)
                        assert page.get_by_test_id('stException').count() == 0, title
                        assert_heading_clear(page)
                        if title in ['Đơn mới', 'Lịch sử']:
                            page.screenshot(path=str(output / f'desktop-{title}.png'), full_page=True)
                        if title == 'Lịch sử':
                            page.get_by_role('button', name='Nhận tiền · Kim Ngân', exact=True).click()
                            page.get_by_role('dialog').get_by_role('textbox', name=re.compile('Số tiền nhận')).fill('15,000')
                            page.get_by_role('dialog').get_by_role('button', name='Xác nhận đã nhận', exact=True).click()
                            page.get_by_role('dialog').wait_for(state='hidden')
                            assert db.query('SELECT amount FROM payments WHERE member_id=?', (guest,))[0]['amount'] == 15000
                            page.get_by_role('button', name='🗑️ Xóa đơn', exact=True).click()
                            page.get_by_role('dialog').get_by_role('button', name='Hủy', exact=True).click()
                            page.get_by_role('dialog').wait_for(state='hidden')
                            assert len(db.query('SELECT * FROM sessions')) == 1
                            page.get_by_role('button', name='✏️ Sửa đơn', exact=True).click()
                            edit_dialog = page.get_by_role('dialog')
                            edit_dialog.get_by_role('textbox', name=re.compile('Tên đơn')).fill('Không lưu tên này')
                            edit_dialog.get_by_role('button', name='Hủy chỉnh sửa', exact=True).click()
                            edit_dialog.wait_for(state='hidden')
                            assert db.query('SELECT title FROM sessions WHERE id=?', (session,))[0]['title'] == 'Bữa trưa cùng nhóm'
                            page.get_by_role('button', name='✏️ Sửa đơn', exact=True).click()
                            edit_dialog = page.get_by_role('dialog')
                            edit_dialog.get_by_role('button', name='＋ Thêm món', exact=True).click()
                            edit_dialog.get_by_role('textbox', name=re.compile('Món 4')).wait_for()
                            edit_dialog.get_by_role('button', name='🗑️').last.click()
                            edit_dialog.get_by_role('textbox', name=re.compile('Món 4')).wait_for(state='hidden')
                            edit_dialog.get_by_role('textbox', name=re.compile('Tên đơn')).fill('Bữa trưa đã sửa')
                            edit_dialog.get_by_role('textbox', name='Đơn giá').nth(1).fill('44,000')
                            edit_dialog.get_by_role('button', name='Lưu thay đổi đơn', exact=True).click()
                            edit_dialog.wait_for(state='hidden')
                            assert db.query('SELECT title FROM sessions WHERE id=?', (session,))[0]['title'] == 'Bữa trưa đã sửa'
                            assert db.query('SELECT subtotal FROM sessions WHERE id=?', (session,))[0]['subtotal'] == 169000
                            assert db.query('SELECT unit_price FROM items WHERE session_id=? ORDER BY id', (session,))[1]['unit_price'] == 44000
                            assert db.query('SELECT amount FROM payments WHERE member_id=?', (guest,))[0]['amount'] == 15000
                            page.get_by_text('Lịch sử thanh toán', exact=True).click()
                            page.get_by_role('button', name='Sửa khoản thu', exact=True).click()
                            payment_edit = page.get_by_role('dialog')
                            payment_edit.get_by_role('textbox', name=re.compile('Số tiền đã nhận')).fill('10,000')
                            payment_edit.get_by_role('textbox', name=re.compile('Lý do sửa')).fill('Đối chiếu lại')
                            payment_edit.get_by_role('button', name='Lưu khoản thanh toán', exact=True).click()
                            payment_edit.wait_for(state='hidden')
                            assert db.query('SELECT amount FROM payments WHERE member_id=?', (guest,))[0]['amount'] == 10000
                            if not page.get_by_role('button', name='Hủy khoản thu', exact=True).is_visible():
                                page.get_by_text('Lịch sử thanh toán', exact=True).click()
                            page.get_by_role('button', name='Hủy khoản thu', exact=True).click()
                            page.get_by_role('dialog').get_by_role('textbox', name=re.compile('Lý do hủy')).fill('Nhập nhầm')
                            page.get_by_role('dialog').get_by_role('button', name='Hủy khoản này', exact=True).click()
                            page.get_by_role('dialog').wait_for(state='hidden')
                            assert not db.query('SELECT * FROM payments WHERE member_id=?', (guest,))
                    switch_theme(page, 'Dark')
                    for label, title in [
                        ('📊 Tổng quan', 'Tổng quan'), ('➕ Đơn mới', 'Đơn mới'), ('📚 Lịch sử', 'Lịch sử'),
                        ('👥 Thành viên', 'Thành viên'), ('📈 Báo cáo', 'Báo cáo'), ('⚙️ Cài đặt', 'Cài đặt')]:
                        page.get_by_test_id('stSidebar').get_by_text(label, exact=True).click()
                        page.locator('.bm-page-heading h1').filter(has_text=title).wait_for()
                        page.wait_for_timeout(800)
                        assert_heading_clear(page)
                        assert page.get_by_test_id('stException').count() == 0, title
                        if title in ['Tổng quan', 'Đơn mới']:
                            page.screenshot(path=str(output / f'dark-desktop-{title}.png'), full_page=True)
                    switch_theme(page, 'Light')
                    for width in [390, 320]:
                        mobile = browser.new_page(viewport={'width': width, 'height': 844}, is_mobile=True, has_touch=True)
                        mobile.goto(url)
                        login(mobile)
                        mobile.locator('.bm-person').first.wait_for()
                        mobile.wait_for_timeout(1200)
                        assert_heading_clear(mobile)
                        assert mobile.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Overview overflow'
                        mobile.screenshot(path=str(output / f'mobile-{width}-overview.png'), full_page=True)
                        mobile.get_by_test_id('stExpandSidebarButton').click()
                        mobile.get_by_test_id('stSidebar').get_by_text('➕ Đơn mới', exact=True).click()
                        mobile.locator('.bm-page-heading h1').filter(has_text='Đơn mới').wait_for()
                        mobile.get_by_test_id('stSidebarCollapseButton').click()
                        mobile.get_by_role('button', name='Tính và lưu đơn', exact=True).wait_for()
                        mobile.wait_for_timeout(800)
                        assert mobile.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Order overflow'
                        mobile.screenshot(path=str(output / f'mobile-{width}-order.png'), full_page=True)
                        assert_heading_clear(mobile)
                        switch_theme(mobile, 'Dark')
                        mobile.screenshot(path=str(output / f'dark-mobile-{width}-order.png'), full_page=True)
                        mobile.goto(url + '?bill=' + token)
                        login(mobile)
                        mobile.get_by_role('button', name='Nhận món', exact=True).wait_for()
                        assert_heading_clear(mobile)
                        assert mobile.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Shared page overflow'
                        box = mobile.get_by_role('button', name='Nhận món', exact=True).bounding_box()
                        assert box['height'] >= 44
                        mobile.get_by_role('button', name='Nhận món', exact=True).click()
                        mobile.get_by_role('button', name='Bỏ nhận', exact=True).wait_for()
                        assert mobile.get_by_role('button', name='Nhận món', exact=True).count() == 0
                        mobile.screenshot(path=str(output / f'mobile-{width}-claimed.png'), full_page=True)
                        mobile.get_by_role('button', name='Bỏ nhận', exact=True).click()
                        mobile.get_by_role('button', name='Nhận món', exact=True).wait_for()
                        assert not db.query('SELECT * FROM item_claims')
                        mobile.close()
                    assert not errors, errors
                    browser.close()
                    print(json.dumps({'result': 'passed', 'pages': 6, 'themes': ['light', 'dark'], 'mobile_widths': [390, 320], 'screenshots': str(output)}))
            finally:
                process.terminate()
                process.wait(timeout=15)


if __name__ == '__main__':
    main()
