# -*- coding: utf-8 -*-
"""真的按下應付帳款的「應付款」按鈕（owner 回報：按了沒反應）。

自己建一筆 ZZ 請款單當標的，按完驗證狀態真的變了，最後刪掉。
"""
import json, sys, urllib.error, urllib.request
sys.path.insert(0, r'E:\Dev\Originsun-Media-Guard')
from core.auth import create_token
from playwright.sync_api import sync_playwright

tok = create_token({'sub': 'admin', 'username': 'admin', 'access_level': 3, 'modules': []})
BASE = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8001'
fails = []


def check(ok, label, extra=''):
    print(('  PASS ' if ok else '  FAIL ') + label + ((' — ' + str(extra)) if extra else ''))
    if not ok:
        fails.append(label)


def api(m, path, body=None):
    d = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + '/api/v1' + path, data=d, method=m,
                               headers={'Authorization': 'Bearer ' + tok,
                                        'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(r, timeout=40) as f:
            return f.status, json.loads(f.read() or b'{}')
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')


st, made = api('POST', '/crm/payments', {
    'request_date': '2026-08-21', 'amount': 12345, 'summary': 'ZZ按鈕測試 請款',
    'category': '專案外包', 'payee_name': 'ZZ測試收款人',
    'payment_status': '應付款', 'planned_month': '2026-08', 'entity': 'parent'})
pid = ((made.get('payment') or made.get('item') or made) or {}).get('id')
print('建立測試請款單:', st, pid)
assert pid, made

try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={'width': 1600, 'height': 1000})
        ctx.add_init_script(f"localStorage.setItem('auth_token', '{tok}')")
        pg = ctx.new_page()
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        pg.on('dialog', lambda d: d.accept())        # confirm 一律按確定
        pg.goto(BASE + '/', wait_until='domcontentloaded')
        pg.wait_for_timeout(4000)
        pg.evaluate("window.switchTab('tab_crm_invoices')")
        pg.wait_for_timeout(3000)
        pg.locator('[data-inv-view="payables"]').first.click()
        pg.wait_for_timeout(4500)

        print('\n[1] 找到那一列並展開')
        row = pg.locator('text=ZZ測試收款人').first
        check(row.count() > 0, '清單看得到測試列')
        row.click()
        pg.wait_for_timeout(1200)

        print('\n[2] 🔴 按下「應付款」')
        btn = pg.locator(f'#payable-mb-{pid}')
        check(btn.count() > 0, '詳情面板有那顆按鈕', btn.count())
        btn.click()
        pg.wait_for_timeout(2500)
        check(not errs, '按下去沒有 JS 例外', errs[:2])

        print('\n[3] 後端狀態真的變了')
        _, got = api('GET', f'/crm/payments/{pid}')
        item = got.get('payment') or got.get('item') or got
        check(item.get('payment_status') == '已付款', '變成已付款',
              item.get('payment_status'))
        check(bool(item.get('payment_date')), '有填付款日', item.get('payment_date'))

        print('\n[4] 畫面也跟著更新')
        # 付掉之後那一筆就不在應付帳款清單裡了（/payables/summary 只列未付），
        # 所以驗的是「那顆應付款按鈕消失」而不是「出現已付款標籤」——
        # 後者在修好與壞掉兩種版本都是 false，等於沒驗到東西。
        check(pg.locator(f'#payable-mb-{pid}').count() == 0, '「應付款」按鈕已消失')

        b.close()
finally:
    print('\n[清理]')
    st, _ = api('DELETE', f'/crm/payments/{pid}')
    check(st in (200, 204), '測試請款單已刪除', st)

print('\n' + ('ALL PASS' if not fails else f'{len(fails)} FAILED: {fails}'))
sys.exit(1 if fails else 0)
