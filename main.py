#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ぷにぷに 対話型自動周回スクリプト（ハイブリッド設定版）
メール/パスワードは .env から読み込み、周回設定はコード内で直接指定します
"""

import base64
import hashlib
import json
import os
import random
import re
import time
import zlib
from urllib.parse import urljoin, urlparse, parse_qs
from Crypto.Cipher import AES
import requests
from dotenv import load_dotenv

# .envファイルから環境変数を読み込む（メール・パスワード用）
load_dotenv()

# ============================================================================
# 【ユーザー設定エリア】 周回設定はここで変更してください
# ============================================================================

# 周回設定
PUNIPUNI_STAGE_ID = 29704006                    # 周回したいステージのID
PUNIPUNI_COUNT = 1000                          # 周回回数
PUNIPUNI_REQUEST_DELAY = 10                 # リクエスト前の待機時間（秒）
PUNIPUNI_COOLDOWN = 10                      # バトル間のクールダウン（秒）

# ============================================================================
# 定数設定
# ============================================================================

AESK = bytes.fromhex('a865d7e5e2458f8ce1b5ecd087e54594')
K = b'0bk2kvtFE2'
APKEY = 'a-zrhgm09pgcgjc1iv9cxvpk3xm9b0ynyo4u00sny6bjq10nrx3up2yrhjnq2lhg'
SIGNATURE = ('s4X9CoyxGma3kGuAp5woThgvBX3dCi77Slh5RcOo6ybmMTt0J4CGiZwyiCsil7P3'
             'MVgjiVt+kGE1MqvttCXLB+hlOpyTkJp5a78TXthBNVw=')
GS = 'https://gameserver.yw-p.com'
L5 = 'https://api.level5-id.com'

MODEL = 'GA00747-UK'
OSVER = '9'
APPVER = '4.174.0'
BATTERY = {'level': 100, 'state': 3, 'technology': 'Li-poly', 'temperature': 261, 'voltage': 4300}

UA = 'Dalvik/2.1.0 (Linux; U; Android %s; %s Build/PI) com.Level5.YWP/%s' % (OSVER, MODEL, APPVER)
HDR = {'Accept-Encoding': 'identity', 'User-Agent': UA, 'Accept': 'application/json',
       'Content-Type': 'application/json', 'Connection': 'Keep-Alive'}

RC = {0: 'OK', -1: 'IPブロック/復号不可', 20: 'appVerかマスタ版が古い', 30: '署名の不整合',
      32: 'tokenズレ', 37: 'チュートリアル未完了', 101: 'マスタ版が現行と違う', 202: 'BAN'}

GAME_CONST = [
    {"constType": 5, "mstKey": "blockComboAdjustNumA", "mstValue": "0.051800"},
    {"constType": 5, "mstKey": "blockComboAdjustNumB", "mstValue": "0.995500"},
    {"constType": 5, "mstKey": "blockSizeAdjustA", "mstValue": "0.000800"},
    {"constType": 5, "mstKey": "blockSizeAdjustB", "mstValue": "0.077000"},
    {"constType": 5, "mstKey": "blockSizeAdjustC", "mstValue": "-0.058500"},
    {"constType": 5, "mstKey": "blockSizeAdjustSkillA", "mstValue": "5.780200"},
    {"constType": 5, "mstKey": "blockSizeAdjustSkillB", "mstValue": "-2.201000"},
    {"constType": 5, "mstKey": "blockSizeAdjustSkillC", "mstValue": "1.000000"},
    {"constType": 5, "mstKey": "blockSizeRate1", "mstValue": "0.019300"},
    {"constType": 5, "mstKey": "blockSizeRate2", "mstValue": "0.058600"},
    {"constType": 5, "mstKey": "blockSizeRate3", "mstValue": "0.137900"},
    {"constType": 5, "mstKey": "comboEnableSec", "mstValue": "3.000000"},
    {"constType": 5, "mstKey": "comboEnableSize", "mstValue": "2"},
    {"constType": 5, "mstKey": "damageSwitchSize", "mstValue": "3"},
    {"constType": 5, "mstKey": "feverDamageAdjustNum", "mstValue": "0.100000"},
    {"constType": 5, "mstKey": "feverScoreAdjustNum", "mstValue": "0.100000"},
    {"constType": 5, "mstKey": "saBlockSizeAdjustSubA", "mstValue": "0.006200"},
    {"constType": 5, "mstKey": "saBlockSizeAdjustSubB", "mstValue": "-0.024500"},
    {"constType": 5, "mstKey": "saBlockSizeAdjustSubC", "mstValue": "0.416900"},
    {"constType": 5, "mstKey": "scoreAdjustNumA", "mstValue": "11.223000"},
    {"constType": 5, "mstKey": "scoreAdjustNumB", "mstValue": "0.047700"},
    {"constType": 5, "mstKey": "skillGaugeIncrementSize1", "mstValue": "0.100000"},
    {"constType": 5, "mstKey": "skillGaugeIncrementSize2", "mstValue": "1.200000"},
    {"constType": 5, "mstKey": "skillGaugeIncrementSize3", "mstValue": "2.400000"},
]

# ============================================================================
# 暗号化・復号化関数
# ============================================================================

def salt20(body):
    return hashlib.sha1(K + hashlib.sha1(K + b' ' + body).digest()).digest()

def enc(body):
    pt = salt20(body) + body
    p = 16 - len(pt) % 16
    pt += bytes([p]) * p
    return base64.urlsafe_b64encode(AES.new(AESK, AES.MODE_ECB).encrypt(pt)).decode().rstrip('=')

def dec(s):
    s = s.strip()
    ct = base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))
    pt = AES.new(AESK, AES.MODE_ECB).decrypt(ct)
    rest = pt[20:]
    g = rest.find(b'\x1f\x8b')
    if g >= 0:
        try:
            return zlib.decompress(rest[g:], 47)
        except Exception:
            pass
    try:
        rest = rest[:-pt[-1]]
    except Exception:
        pass
    e = max(rest.rfind(b'}'), rest.rfind(b']'))
    return rest[:e + 1] if e >= 0 else rest

def jbody(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')

# ============================================================================
# API通信関数
# ============================================================================

def post_nhn(name, obj, timeout=30, retry=3):
    for attempt in range(1, retry + 1):
        try:
            r = requests.post('%s/%s' % (GS, name), data=enc(jbody(obj)),
                             headers={**HDR, 'Host': 'gameserver.yw-p.com'}, timeout=timeout)
            
            if r.status_code != 200:
                if attempt < retry:
                    time.sleep(2 ** attempt)
                    continue
                return r.status_code, {'resultCode': -1, '_error': f'HTTP{r.status_code}'}
            
            try:
                out = dec(r.text)
            except Exception:
                if attempt < retry:
                    time.sleep(2 ** attempt)
                    continue
                return r.status_code, {'resultCode': -1, '_raw': (r.text or '')[:200]}
            try:
                return r.status_code, json.loads(out)
            except Exception:
                if attempt < retry:
                    time.sleep(2 ** attempt)
                    continue
                return r.status_code, {'_raw': out[:300].decode('utf-8', 'replace')}
        except requests.exceptions.Timeout:
            if attempt < retry:
                time.sleep(3 * attempt)
                continue
            return None, {'resultCode': -1, '_error': 'Timeout'}
        except requests.exceptions.ConnectionError:
            if attempt < retry:
                time.sleep(3 * attempt)
                continue
            return None, {'resultCode': -1, '_error': 'ConnectionError'}
    
    return None, {'resultCode': -1, '_error': 'Max retries exceeded'}

def active(udkey=None, timeout=30):
    p = {'apkey': APKEY, 'device_cd': '%s_%s' % (MODEL, OSVER), 'device_type_cd': 'Android',
         'sign': 'true', 'version': APPVER}
    if udkey:
        p['udkey'] = udkey
    return requests.get('%s/api/v1/active/' % L5, params=p,
                        headers={'User-Agent': UA}, timeout=timeout).json()

def new_udkey():
    return active()['udkey']['value']

def _parse_forms(html):
    out = []
    for fm in re.finditer(r'<form\b[^>]*>(.*?)</form>', html, re.S | re.I):
        block = fm.group(0)
        am = re.search(r'action="([^"]*)"', block, re.I)
        inputs = {}
        for im in re.finditer(r'<input\b[^>]*>', block, re.I):
            nm = re.search(r'name="([^"]*)"', im.group(0), re.I)
            vm = re.search(r'value="([^"]*)"', im.group(0), re.I)
            if nm:
                inputs[nm.group(1)] = vm.group(1) if vm else ''
        out.append({'action': am.group(1) if am else '', 'inputs': inputs})
    return out

def link_email(udkey, email, pw, timeout=25):
    s = requests.Session()
    s.headers.update({'User-Agent': UA, 'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8',
                      'Accept-Language': 'ja'})
    r = s.get('%s/api/v1/link_account' % L5, params={'apkey': APKEY, 'udkey': udkey},
              allow_redirects=True, timeout=timeout)
    lf = [x for x in _parse_forms(r.text) if any('email' in k.lower() for k in x['inputs'])]
    if not lf:
        raise RuntimeError('ログイン画面が出ない(既に連携済み or メール不正)')
    inp = dict(lf[0]['inputs'])
    inp['form[email]'] = email
    inp['form[password]'] = pw
    r = s.post(urljoin(r.url, lf[0]['action']), data=inp, allow_redirects=True, timeout=timeout)
    ap = [x for x in _parse_forms(r.text) if 'client_id' in x['inputs'] and x['inputs'].get('_method', '') != 'delete']
    if not ap:
        raise RuntimeError('consent画面が出ない(メール/パスが違う?)')
    inp = dict(ap[0]['inputs'])
    inp.setdefault('commit', 'Authorize')
    r2 = s.post(urljoin(r.url, ap[0]['action']), data=inp, allow_redirects=False, timeout=timeout)
    code = (parse_qs(urlparse(r2.headers.get('Location', '')).query).get('code') or [None])[0]
    if not code:
        raise RuntimeError('認可コードが取れない')
    fin = s.get('%s/api/v1/link_account' % L5,
                params={'code': code, 'apkey': APKEY, 'udkey': udkey,
                        'device_cd': '%s_%s' % (MODEL, OSVER), 'device_type_cd': 'Android'},
                timeout=timeout).json()
    if not fin.get('result'):
        raise RuntimeError('連携finalize失敗: %s' % fin)
    return fin

def rows(s):
    if isinstance(s, str):
        s = json.loads(s) if s.startswith('{') else s
    return [r.split('|') for r in (s or '').split('*') if r] if isinstance(s, str) else (s.get('rows') if isinstance(s, dict) else (s or []))

# ============================================================================
# Clientクラス
# ============================================================================

class Client:
    def __init__(self, udkey):
        self.udkey = udkey
        self.gdkey = None
        self.userId = None
        self.token = '0'
        self.mst = 16897
        self.save = {}

    def _active_with_gdkeys(self, retries=6):
        a = active(self.udkey)
        for _ in range(retries):
            if a.get('gdkeys'):
                break
            time.sleep(1.2)
            a = active(self.udkey)
        if not a.get('gdkeys'):
            raise RuntimeError('gdkeyが無い(このudkeyに紐づくゲームデータが無い)')
        return a

    def _enum(self, a):
        gds = a['gdkeys']
        pl = []
        for _ in range(5):
            rc, j = post_nhn('getGdkeyAccounts.nhn', {
                'appVer': APPVER, 'deviceId': self.udkey,
                'gdkeys': [{'gdkey': g['value']} for g in gds],
                'level5UserId': '0', 'mstVersionVer': self.mst, 'osType': 2,
                'userId': '0', 'ywpToken': '0'})
            pl = j.get('udkeyPlayerList') or []
            if len(pl) >= len(gds):
                break
            time.sleep(1.2)
        by_g = {str(p.get('gdkey')): p for p in pl if p.get('gdkey')}
        out = []
        for i, g in enumerate(gds):
            p = by_g.get(g['value'], {})
            out.append({'idx': i, 'userId': p.get('userId'), 'playerName': p.get('playerName'),
                        'gdkey': g['value'], 'gdsig': g['signature']})
        return out

    def init_nhn(self):
        rc, j = post_nhn('init.nhn', {
            'appGuardDeviceId': hashlib.sha256(self.udkey.encode()).hexdigest(),
            'appVer': APPVER, 'deviceId': self.udkey, 'level5UserId': '0',
            'mstVersionVer': self.mst, 'osType': 2, 'signature': SIGNATURE,
            'userId': '0', 'ywpToken': '0'})
        v = j.get('mstVersionMaster')
        if isinstance(v, int) and v > 0:
            self.mst = v
        return j

    def login(self, userId=None):
        self.init_nhn()
        a = self._active_with_gdkeys()
        accs = self._enum(a)
        sel = None
        if userId is not None:
            sel = next((x for x in accs if str(x['userId']) == str(userId)), None)
            if sel is None:
                raise RuntimeError('userId %s が見つからない' % userId)
        if sel is None:
            sel = accs[0]
        rc, j = post_nhn('login.nhn', {
            'appVer': APPVER, 'batteryInfo': BATTERY, 'deviceId': self.udkey,
            'deviceName': MODEL, 'gdkeySignature': sel['gdsig'], 'gdkeyValue': sel['gdkey'],
            'isL5IDLinked': 1, 'level5UserId': sel['gdkey'],
            'modelName': MODEL, 'mstVersionVer': self.mst, 'osType': 2, 'osVersion': OSVER,
            'signNonce': a['sign_nonce'], 'signTimestamp': str(a['sign_timestamp']),
            'signature': SIGNATURE, 'udkeySignature': a['udkey']['signature'],
            'udkeyValue': self.udkey, 'userId': sel['userId'], 'ywpToken': '0'})
        if j.get('resultCode') != 0:
            code = j.get('resultCode')
            error_desc = RC.get(code, '不明なエラー')
            error_detail = j.get('_raw') or j.get('_error') or j.get('dialogMsg') or ''
            raise RuntimeError('login失敗: rc=%s (%s) %s' % (code, error_desc, error_detail[:100]))
        self.gdkey, self.userId, self.token = sel['gdkey'], sel['userId'], j.get('token')
        self.save = j
        return j

    def call(self, name, extra=None):
        if not self.token or self.token == '0':
            raise RuntimeError('先に login() してください')
        body = {'activeDeckId': 1, 'appVer': APPVER, 'deviceId': self.udkey,
                'level5UserId': self.gdkey, 'mstVersionVer': self.mst, 'osType': 2,
                'token': self.token, 'userId': str(self.userId), 'ywpToken': '0'}
        if extra:
            body.update(extra)
        rc, j = post_nhn(name, body)
        t = j.get('token')
        if t and t != 'null':
            self.token = t
        return rc, j

    def build_game_end(self, stageId, battleType, start):
        reqId = start.get('requestId')
        yk = start.get('userYoukaiList') or []
        en = start.get('enemyYoukaiList') or []
        ehp = sum(e.get('hp', 0) for e in en)
        dmg = ehp + random.randint(50, max(ehp // 50, 500)) if ehp else 50000
        users = []
        for i, y in enumerate(yk):
            users.append({
                'damageMax': int(dmg * 0.062) if i == 0 else 0,
                'damageTotal': dmg if i == 0 else 0,
                'eraseNum': 26 if i == 0 else 0,
                'eraseSize': 1711 if i == 0 else 0,
                'eraseSizeMax': 101 if i == 0 else 0,
                'linkSizeMax': 2 if i == 0 else 0,
                'recoveryActual': 0,
                'recoveryMax': 0,
                'sSkillUseNum': 0,
                'skillUseNum': 0,
                'youkaiId': y.get('youkaiId'),
            })
        enemies = []
        for i, e in enumerate(en):
            enemies.append({
                'deadEndOrder': i + 1,
                'deadEndType': 0,
                'dropItemCheckKey': (e.get('lotItemInfoList') or '00000|0').split('|')[0],
                'dropItemFlg': 0,
                'dropItemId': 0,
                'dropTreasureFlg': 0,
                'dropTreasureId': 0,
                'dropYoukaiCheckKey': (e.get('lotYoukaiInfoList') or '00000|0').split('|')[0],
                'dropYoukaiFlg': 0,
                'enemyId': e.get('enemyId'),
                'itemId': 0,
                'useItemLLarge': 0,
                'useItemLarge': 0,
                'useItemMiddle': 0,
                'useItemSmall': 0,
            })
        return {
            'stageId': stageId,
            'battleType': battleType,
            'requestId': str(reqId),
            'damageTotal': dmg,
            'score': min(dmg * 20, 2000000000),
            'userYoukaiResultList': users,
            'enemyYoukaiResultList': enemies,
            'bonusBlockNum': 0,
            'cheatFlg': 0,
            'clearTimeLongSec': 0,
            'clearTimeSec': 29,
            'comboMax': 13,
            'eraseNumTotal': 26,
            'eraseSizeAve': '65.80',
            'eraseSizeMax': 101,
            'eventPoint': 0,
            'eventSubPoint': 0,
            'eventTeamPoint': 0,
            'feverTimeNum': 0,
            'linkSizeMax': 2,
            'pauseAtkNum': 0,
            'recvDamageTotal': 0,
            'resultRecvAtkNum': 0,
            'resultYoukaiHP': 755,
            'scoreLog': '',
            'spMissionIntValue1': 0,
            'suspendFlg': 0,
            'themeResultList': [],
            'ywp_mst_game_const': GAME_CONST,
        }

    def battle(self, stageId, battleType=None, wait=2.0):
        if battleType is None:
            battleType = 6 if str(stageId).startswith(('28805', '28904', '29008', '29304')) else 1
        rc, js = self.call('gameStart.nhn', {'stageId': stageId, 'battleType': battleType,
                                             'battleCode': '', 'retryFlg': 0})
        if js.get('resultCode') != 0:
            return js.get('resultCode'), js
        time.sleep(wait)
        ge = self.build_game_end(stageId, battleType, js)
        return self.call('gameEnd.nhn', ge)

    def info(self):
        d = self.save.get('ywp_user_data')
        if isinstance(d, str):
            d = json.loads(d)
        return d or {}

    def stages(self):
        return {int(r[0]): r for r in rows(self.save.get('ywp_user_stage'))}

# ============================================================================
# ログイン関数
# ============================================================================

def login_email(email, pw, userId=None):
    print('   UDkey取得中...')
    udkey = new_udkey()
    print(f'   UDkey: {udkey}')
    
    print('   メール連携中...')
    try:
        link_email(udkey, email, pw)
        print(f'   メール連携成功')
    except Exception as e:
        print(f'   ⚠ メール連携エラー: {str(e)[:100]}')
        raise
    
    print('   サーバー反映待機中... 3秒')
    time.sleep(3)
    
    print('   ゲームサーバーログイン中...')
    c = Client(udkey)
    try:
        c.login(userId=userId)
    except Exception as e:
        print(f'   ⚠ ログインエラー詳細: {str(e)[:150]}')
        raise
    
    return c

# ============================================================================
# メイン処理
# ============================================================================

def main():
    print('=' * 70)
    print('   ぷにぷに 自動周回スクリプト（ハイブリッド設定版）')
    print('=' * 70)
    print()

    # .env からメールとパスワードを取得
    email = os.getenv('PUNIPUNI_EMAIL')
    pw = os.getenv('PUNIPUNI_PASSWORD', '')

    if not email:
        print('✗ エラー: 環境変数「PUNIPUNI_EMAIL」が設定されていません。')
        print('  スクリプトと同階層に .env ファイルを作成してください。')
        print()
        print('  【.env ファイルの記述例】')
        print('  PUNIPUNI_EMAIL=your_email@example.com')
        print('  PUNIPUNI_PASSWORD=your_password')
        return 1

    print('【設定確認】')
    print('-' * 70)
    print(f'   メール (.env): {email}')
    print(f'   ステージID (コード内): {PUNIPUNI_STAGE_ID}')
    print(f'   周回回数 (コード内): {PUNIPUNI_COUNT}回')
    print(f'   リクエスト前待機: {PUNIPUNI_REQUEST_DELAY}秒 (±30%でランダム変動)')
    print(f'   クールダウン: {PUNIPUNI_COOLDOWN}秒 (±30%でランダム変動)')
    print()

    print('【1/2】ログイン処理')
    print('-' * 70)

    try:
        print('ログイン中...')
        c = login_email(email, pw)
        info = c.info()
        print(f'✓ ログイン成功')
        print(f'   プレイヤー名: {info.get("playerName")}')
        print(f'   ユーザーID: {c.userId}')
        print(f'   UDkey: {c.udkey}')
        print()
    except RuntimeError as e:
        error_msg = str(e)
        if 'rc=-1' in error_msg:
            print(f'✗ ログイン失敗: ネットワークエラーまたはサーバー不具合 (rc=-1)')
        else:
            print(f'✗ ログイン失敗: {error_msg[:150]}')
        return 1
    except Exception as e:
        print(f'✗ ログイン失敗 (予期しないエラー): {str(e)[:150]}')
        return 1

    stages = c.stages()
    if PUNIPUNI_STAGE_ID not in stages:
        print(f'✗ ステージID {PUNIPUNI_STAGE_ID} はクリアされていないか見つかりません')
        return 1

    print('【2/2】自動周回開始')
    print('-' * 70)
    print(f'ステージ: {PUNIPUNI_STAGE_ID}')
    print()

    success_count = 0
    fail_count = 0
    start_time = time.time()

    for i in range(1, PUNIPUNI_COUNT + 1):
        randomized_request_delay = PUNIPUNI_REQUEST_DELAY * random.uniform(0.7, 1.3)
        randomized_cooldown = PUNIPUNI_COOLDOWN * random.uniform(0.7, 1.3)

        print(f'[{i}/{PUNIPUNI_COUNT}] リクエスト前待機中 ({randomized_request_delay:.1f}秒)...', end='', flush=True)
        time.sleep(randomized_request_delay)
        print(' 完了')

        print(f'[{i}/{PUNIPUNI_COUNT}] バトル実行中...', end='', flush=True)

        try:
            rc, result = c.battle(PUNIPUNI_STAGE_ID)
            result_code = result.get('resultCode')

            if result_code == 0:
                print(f' ✓ クリア', end='')
                event_point = result.get('eventPoint', 0)
                if event_point > 0:
                    print(f' (イベントポイント: {event_point})', end='')
                print()
                success_count += 1
            else:
                error_msg = RC.get(result_code, '不明なエラー')
                print(f' ✗ 失敗 (rc={result_code}: {error_msg})')
                fail_count += 1

                if result_code in [202, 30, 32]:
                    print(f'✗ 致命的なエラーが発生したため中止します')
                    break
        except Exception as e:
            print(f' ✗ 例外エラー: {str(e)[:60]}')
            fail_count += 1

        if i < PUNIPUNI_COUNT:
            print(f'   → {randomized_cooldown:.1f}秒 待機中...', end='', flush=True)
            time.sleep(randomized_cooldown)
            print(' 完了')

        print()

    elapsed_time = time.time() - start_time
    print('=' * 70)
    print('【周回完了】')
    print('-' * 70)
    print(f'実行時間: {elapsed_time:.1f}秒')
    print(f'成功: {success_count}回')
    print(f'失敗: {fail_count}回')
    print(f'成功率: {success_count / PUNIPUNI_COUNT * 100:.1f}%' if PUNIPUNI_COUNT > 0 else '成功率: N/A')
    print()

    if success_count == PUNIPUNI_COUNT:
        print('✓ すべてのバトルが成功しました！')
    elif success_count > 0:
        print(f'⚠ {fail_count}回のバトルが失敗しました')
    else:
        print('✗ すべてのバトルが失敗しました')

    print('=' * 70)

    return 0 if fail_count == 0 else 1

if __name__ == '__main__':
    try:
        exit(main())
    except KeyboardInterrupt:
        print()
        print()
        print('✗ ユーザーによって中断されました')
        exit(1)
