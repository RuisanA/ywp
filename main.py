import base64
import hashlib
import json
import os
import random
import re
import threading
import time
import zlib
from urllib.parse import urljoin, urlparse, parse_qs
from Crypto.Cipher import AES
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template_string, request

# .envファイルから環境変数を読み込む
load_dotenv()

# Flaskアプリの初期化
app = Flask(__name__)

# ============================================================================
# 【ユーザー設定エリア】 周回設定はここで変更してください
# ============================================================================

PUNIPUNI_STAGE_ID = 29704006                  # 周回したいステージのID
PUNIPUNI_COUNT = 4500                        # 周回回数
PUNIPUNI_REQUEST_DELAY = 5                  # リクエスト前の待機時間（秒）
PUNIPUNI_COOLDOWN = 3                       # バトル間のクールダウン（秒）

# ============================================================================
# 定数設定・暗号化関数など
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
            raise RuntimeError('gdkeyが無い')
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
        sel = accs[0] if userId is None else next((x for x in accs if str(x['userId']) == str(userId)), accs[0])
        rc, j = post_nhn('login.nhn', {
            'appVer': APPVER, 'batteryInfo': BATTERY, 'deviceId': self.udkey,
            'deviceName': MODEL, 'gdkeySignature': sel['gdsig'], 'gdkeyValue': sel['gdkey'],
            'isL5IDLinked': 1, 'level5UserId': sel['gdkey'],
            'modelName': MODEL, 'mstVersionVer': self.mst, 'osType': 2, 'osVersion': OSVER,
            'signNonce': a['sign_nonce'], 'signTimestamp': str(a['sign_timestamp']),
            'signature': SIGNATURE, 'udkeySignature': a['udkey']['signature'],
            'udkeyValue': self.udkey, 'userId': sel['userId'], 'ywpToken': '0'})
        if j.get('resultCode') != 0:
            raise RuntimeError('login失敗: rc=%s' % j.get('resultCode'))
        self.gdkey, self.userId, self.token = sel['gdkey'], sel['userId'], j.get('token')
        self.save = j
        return j

    def build_game_end(self, stageId, battleType, start):
        reqId = start.get('requestId')
        yk = start.get('userYoukaiList') or []
        en = start.get('enemyYoukaiList') or []
        ehp = sum(e.get('hp', 0) for e in en)
        dmg = ehp + random.randint(50, max(ehp // 50, 500)) if ehp else 50000
        users = [{'damageMax': int(dmg * 0.062) if i == 0 else 0, 'damageTotal': dmg if i == 0 else 0,
                  'eraseNum': 26 if i == 0 else 0, 'eraseSize': 1711 if i == 0 else 0,
                  'eraseSizeMax': 101 if i == 0 else 0, 'linkSizeMax': 2 if i == 0 else 0,
                  'recoveryActual': 0, 'recoveryMax': 0, 'sSkillUseNum': 0, 'skillUseNum': 0,
                  'youkaiId': y.get('youkaiId')} for i, y in enumerate(yk)]
        enemies = [{'deadEndOrder': i + 1, 'deadEndType': 0, 'dropItemCheckKey': (e.get('lotItemInfoList') or '00000|0').split('|')[0],
                    'dropItemFlg': 0, 'dropItemId': 0, 'dropTreasureFlg': 0, 'dropTreasureId': 0,
                    'dropYoukaiCheckKey': (e.get('lotYoukaiInfoList') or '00000|0').split('|')[0],
                    'dropYoukaiFlg': 0, 'enemyId': e.get('enemyId'), 'itemId': 0, 'useItemLLarge': 0,
                    'useItemLarge': 0, 'useItemMiddle': 0, 'useItemSmall': 0} for i, e in enumerate(en)]
        return {
            'stageId': stageId, 'battleType': battleType, 'requestId': str(reqId),
            'damageTotal': dmg, 'score': min(dmg * 20, 2000000000),
            'userYoukaiResultList': users, 'enemyYoukaiResultList': enemies,
            'bonusBlockNum': 0, 'cheatFlg': 0, 'clearTimeLongSec': 0, 'clearTimeSec': 29,
            'comboMax': 13, 'eraseNumTotal': 26, 'eraseSizeAve': '65.80', 'eraseSizeMax': 101,
            'eventPoint': 0, 'eventSubPoint': 0, 'eventTeamPoint': 0, 'feverTimeNum': 0,
            'linkSizeMax': 2, 'pauseAtkNum': 0, 'recvDamageTotal': 0, 'resultRecvAtkNum': 0,
            'resultYoukaiHP': 755, 'scoreLog': '', 'spMissionIntValue1': 0, 'suspendFlg': 0,
            'themeResultList': [], 'ywp_mst_game_const': GAME_CONST,
        }

    def battle(self, stageId, battleType=1):
        rc, js = self.call('gameStart.nhn', {'stageId': stageId, 'battleType': battleType, 'battleCode': '', 'retryFlg': 0})
        if js.get('resultCode') != 0:
            return js.get('resultCode'), js
        # 利用停止対策として開始から終了までのウェイトを確実に10秒以上に調整
        time.sleep(10)
        ge = self.build_game_end(stageId, battleType, js)
        return self.call('gameEnd.nhn', ge)

    def call(self, name, extra=None):
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


# ============================================================================
# アカウント名の永続保存
# ============================================================================
NAME_FILE = 'name.txt'


def load_saved_names():
    """name.txt から「アカウント番号 -> 保存名」を読み込む"""
    saved = {}
    if not os.path.exists(NAME_FILE):
        return saved

    try:
        with open(NAME_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                # 形式: アカウント2(〇〇)
                m = re.match(r'^アカウント(\d+)\((.*)\)$', line)
                if m:
                    acc_id = int(m.group(1))
                    name = m.group(2).strip()
                    if name:
                        saved[acc_id] = name
    except Exception as e:
        print(f"name.txt読み込みエラー: {e}")

    return saved


def save_account_name(acc_id, name):
    """指定アカウントの名前を name.txt に保存する"""
    name = (name or '').strip()
    if not name:
        return False

    try:
        saved = load_saved_names()
        saved[acc_id] = name

        # アカウント番号順に保存
        with open(NAME_FILE, 'w', encoding='utf-8') as f:
            for saved_id in sorted(saved):
                f.write(f"アカウント{saved_id}({saved[saved_id]})\n")

        return True
    except Exception as e:
        print(f"name.txt保存エラー: {e}")
        return False


SAVED_NAMES = load_saved_names()

# ============================================================================
# マルチアカウント管理・ログ機能
# ============================================================================

sessions = {
    1: {
        "id": 1,
        "name": SAVED_NAMES.get(1, "アカウント1"),
        "running": False,
        "success": 0,
        "fail": 0,
        "ypoint": "取得前",
        "logs": ["システム初期化完了。周回スタートボタンを押してください。"]
    }
}

def add_log(acc_id, msg):
    if acc_id not in sessions:
        return
    timestamp = time.strftime("[%H:%M:%S]")
    line = f"{timestamp} {msg}"
    sessions[acc_id]["logs"].append(line)
    if len(sessions[acc_id]["logs"]) > 100:
        sessions[acc_id]["logs"].pop(0)

def extract_ypoint(data):
    """レスポンスの辞書からYポイントを再帰的に探し出すヘルパー"""
    if isinstance(data, dict):
        if 'yPoint' in data:
            return data['yPoint']
        if 'ypoint' in data:
            return data['ypoint']
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                res = extract_ypoint(v)
                if res is not None:
                    return res
    elif isinstance(data, list):
        for item in data:
            res = extract_ypoint(item)
            if res is not None:
                return res
    return None

def run_bot_loop(acc_id):
    session = sessions[acc_id]
    session["running"] = True
    session["success"] = 0
    session["fail"] = 0
    session["logs"] = []

    email_env = 'PUNIPUNI_EMAIL' if acc_id == 1 else f'PUNIPUNI_EMAIL{acc_id}'
    pw_env = 'PUNIPUNI_PASSWORD' if acc_id == 1 else f'PUNIPUNI_PASSWORD{acc_id}'

    email = os.getenv(email_env)
    pw = os.getenv(pw_env, '')

    add_log(acc_id, f"=== {session['name']} 周回セッション開始 ===")

    if not email:
        add_log(acc_id, f"エラー: .env に {email_env} が設定されていません。")
        session["running"] = False
        return

    try:
        add_log(acc_id, "UDkey取得中...")
        udkey = new_udkey()
        add_log(acc_id, f"UDkey取得成功: {udkey[:10]}...")

        add_log(acc_id, "メール連携中...")
        link_email(udkey, email, pw)
        add_log(acc_id, "メール連携成功")

        time.sleep(2)
        add_log(acc_id, "ゲームサーバーログイン中...")
        c = Client(udkey)
        c.login()

        info = c.save.get('ywp_user_data', {})

        # ログイン結果からワイポを抽出
        yp = extract_ypoint(c.save)
        if yp is not None:
            session["ypoint"] = yp

        add_log(acc_id, f"ログイン成功！プレイヤー名:{info.get('playerName','不明')}(現在のYポイント:{session['ypoint']})")
        add_log(acc_id, f"ステージ {PUNIPUNI_STAGE_ID} の自動周回を開始します（全 {PUNIPUNI_COUNT} 回）")

        for i in range(1, PUNIPUNI_COUNT + 1):
            if not session["running"]:
                add_log(acc_id, "■ ユーザー操作により周回を停止しました。")
                break

            randomized_request_delay = PUNIPUNI_REQUEST_DELAY * random.uniform(0.7, 1.3)
            randomized_cooldown = PUNIPUNI_COOLDOWN * random.uniform(0.7, 1.3)

            add_log(acc_id, f"[{i}/{PUNIPUNI_COUNT}] リクエスト前待機中 ({randomized_request_delay:.1f}秒)...")

            for _ in range(int(randomized_request_delay * 5)):
                if not session["running"]: break
                time.sleep(0.2)

            if not session["running"]:
                add_log(acc_id, "■ ユーザー操作により周回を停止しました。")
                break

            add_log(acc_id, f"[{i}/{PUNIPUNI_COUNT}] バトル実行中...")
            try:
                rc, result = c.battle(PUNIPUNI_STAGE_ID)
                result_code = result.get('resultCode')

                if result_code == 0:
                    # バトル結果からワイポを更新
                    yp_res = extract_ypoint(result)
                    if yp_res is not None:
                        session["ypoint"] = yp_res

                    event_point = result.get('eventPoint', 0)
                    pt_str = f" (Y: {event_point})" if event_point > 0 else ""
                    add_log(acc_id, f"[{i}/{PUNIPUNI_COUNT}] ✓ クリア{pt_str} [Yp: {session['ypoint']}]")
                    session["success"] += 1
                else:
                    error_msg = RC.get(result_code, '不明なエラー')
                    add_log(acc_id, f"[{i}/{PUNIPUNI_COUNT}] ✗ 失敗 (rc={result_code}: {error_msg})")
                    session["fail"] += 1

                    if result_code in [202, 30, 32]:
                        add_log(acc_id, "エラーのため周回を中止します")
                        break
            except Exception as e:
                add_log(acc_id, f"[{i}/{PUNIPUNI_COUNT}] エラー: {str(e)[:60]}")
                session["fail"] += 1

            if i < PUNIPUNI_COUNT and session["running"]:
                add_log(acc_id, f"  → クールダウン中 ({randomized_cooldown:.1f}秒)...")
                for _ in range(int(randomized_cooldown * 5)):
                    if not session["running"]: break
                    time.sleep(0.2)

        add_log(acc_id, "=== 周回処理セッション終了 ===")
    except Exception as e:
        add_log(acc_id, f"エラー: {str(e)[:150]}")
    finally:
        session["running"] = False


# ============================================================================
# Flask ルート定義 (マルチアカウント対応 UI)
# ============================================================================

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>ぷにぷに 自動周回コンソール</title>
    <meta charset="utf-8">
    <style>
        body {
            background-color: #0f172a;
            color: #e2e8f0;
            font-family: 'Courier New', Courier, monospace;
            margin: 0;
            padding: 20px;
            display: flex;
            flex-direction: column;
            align-items: center;
        }
        .container {
            width: 100%;
            max-width: 900px;
        }
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: #1e293b;
            padding: 15px 25px;
            border-radius: 8px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
            margin-bottom: 20px;
        }
        h2 { margin: 0; font-size: 20px; color: #38bdf8; }

        .add-account-btn {
            background-color: #10b981;
            color: white;
            border: none;
            padding: 8px 15px;
            border-radius: 6px;
            cursor: pointer;
            font-weight: bold;
            font-size: 14px;
        }
        .add-account-btn:hover { background-color: #34d399; }

        .panels-grid {
            display: grid;
            grid-template-columns: 1fr;
            gap: 20px;
        }

        .account-card {
            background: #1e293b;
            border-radius: 8px;
            padding: 15px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
            border-left: 4px solid #38bdf8;
        }

        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
            border-bottom: 1px solid #334155;
            padding-bottom: 10px;
        }

        .name-input-group {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .name-input {
            background: #0f172a;
            border: 1px solid #475569;
            color: #f8fafc;
            padding: 5px 10px;
            border-radius: 4px;
            font-family: inherit;
            font-size: 16px;
            font-weight: bold;
            width: 150px;
        }
        .name-input:focus {
            border-color: #38bdf8;
            outline: none;
        }

        .save-name-btn {
            background-color: #16a34a;
            color: white;
            border: none;
            padding: 6px 10px;
            border-radius: 4px;
            cursor: pointer;
            font-family: inherit;
            font-size: 13px;
            font-weight: bold;
        }

        .save-name-btn:hover {
            background-color: #22c55e;
        }

        .ypoint-badge {
            background: #f59e0b;
            color: #fff;
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 14px;
            font-weight: bold;
            display: flex;
            align-items: center;
            gap: 5px;
        }

        .stats {
            display: flex;
            gap: 20px;
            font-size: 14px;
            margin-bottom: 15px;
        }
        .stat-box span { font-weight: bold; }
        .stat-success { color: #4ade80; }
        .stat-fail { color: #f87171; }

        .controls {
            display: flex;
            gap: 10px;
            margin-bottom: 15px;
        }
        button {
            border: none;
            padding: 8px 15px;
            font-size: 14px;
            font-weight: bold;
            border-radius: 6px;
            cursor: pointer;
            color: white;
        }
        .start-btn { background-color: #0284c7; }
        .start-btn:hover:not(:disabled) { background-color: #0ea5e9; }
        .stop-btn { background-color: #e11d48; }
        .stop-btn:hover:not(:disabled) { background-color: #f43f5e; }
        button:disabled { background-color: #475569; cursor: not-allowed; color: #94a3b8; }

        .console-panel {
            background-color: #020617;
            border: 1px solid #334155;
            border-radius: 8px;
            padding: 15px;
            height: 250px;
            overflow-y: auto;
        }
        pre {
            margin: 0;
            white-space: pre-wrap;
            word-wrap: break-word;
            font-size: 13px;
            line-height: 1.5;
            color: #38bdf8;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h2>ぷにぷに自動周回 (マルチ対応)</h2>
            <button class="add-account-btn" onclick="addAccount()">＋ アカウント追加</button>
        </header>

        <div class="panels-grid" id="panelsContainer">
        </div>
    </div>

    <script>
        let fetchInterval = null;
        // 入力フォームのフォーカス中に画面が再描画されて入力がリセットされるのを防ぐための記録用
        let activeInputId = null;

        document.addEventListener('focusin', (e) => {
            if (e.target.classList.contains('name-input')) {
                activeInputId = e.target.id;
            }
        });
        document.addEventListener('focusout', (e) => {
            if (e.target.classList.contains('name-input') && activeInputId === e.target.id) {
                activeInputId = null;
            }
        });

        function addAccount() {
            fetch('/add_account', { method: 'POST' })
            .then(res => res.json())
            .then(data => {
                if (!data.success) {
                    alert(data.message);
                } else {
                    fetchStatus();
                }
            })
            .catch(err => console.error(err));
        }

        function updateName(id, newName) {
            fetch(`/update_name/${id}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: newName })
            });
        }

        function saveName(id) {
            const input = document.getElementById(`name-input-${id}`);
            if (!input) return;

            const name = input.value.trim();

            if (!name) {
                alert('名前を入力してください。');
                return;
            }

            fetch(`/save_name/${id}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name })
            })
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    alert(data.message);
                    fetchStatus();
                } else {
                    alert(data.message || '名前の保存に失敗しました。');
                }
            })
            .catch(err => {
                console.error(err);
                alert('名前の保存中にエラーが発生しました。');
            });
        }

        function startBot(id) {
            fetch(`/start/${id}`, {method: 'POST'})
            .then(res => res.json())
            .then(() => fetchStatus());
        }

        function stopBot(id) {
            fetch(`/stop/${id}`, {method: 'POST'})
            .then(res => res.json())
            .then(() => fetchStatus());
        }

        function fetchStatus() {
            fetch('/status')
            .then(res => res.json())
            .then(data => {
                const container = document.getElementById('panelsContainer');

                Object.values(data).forEach(session => {
                    let card = document.getElementById(`card-${session.id}`);

                    if (!card) {
                        card = document.createElement('div');
                        card.className = 'account-card';
                        card.id = `card-${session.id}`;
                        card.innerHTML = `
                            <div class="card-header">
                                <div class="name-input-group">
                                    <input type="text" id="name-input-${session.id}" class="name-input" value="${session.name}" oninput="updateName(${session.id}, this.value)">
                                    <button class="save-name-btn" onclick="saveName(${session.id})">保存</button>
                                </div>
<div class="ypoint-badge">Yポイント周回<span id="ypoint-${session.id}"> </span></div>
                            </div>
                            <div class="stats">
                                <div class="stat-box">成功: <span id="success-${session.id}" class="stat-success">0</span></div>
                                <div class="stat-box">失敗: <span id="fail-${session.id}" class="stat-fail">0</span></div>
                            </div>
                            <div class="controls">
                                <button id="start-${session.id}" class="start-btn" onclick="startBot(${session.id})">▶ 周回スタート</button>
                                <button id="stop-${session.id}" class="stop-btn" onclick="stopBot(${session.id})" disabled>■ 周回停止</button>
                            </div>
                            <div class="console-panel" id="console-${session.id}">
                                <pre id="log-${session.id}"></pre>
                            </div>
                        `;
                        container.appendChild(card);
                    } else {
                        // ユーザーが入力中でなければ名前の値を同期
                        const inputElem = document.getElementById(`name-input-${session.id}`);
                        if (inputElem && document.activeElement !== inputElem) {
                            if (inputElem.value !== session.name) {
                                inputElem.value = session.name;
                            }
                        }
                    }

                    document.getElementById(`success-${session.id}`).innerText = session.success;
                    document.getElementById(`fail-${session.id}`).innerText = session.fail;
                    document.getElementById(`ypoint-${session.id}`).innerText = session.ypoint;

                    const startBtn = document.getElementById(`start-${session.id}`);
                    const stopBtn = document.getElementById(`stop-${session.id}`);

                    if (session.running) {
                        startBtn.disabled = true;
                        startBtn.innerText = "実行中...";
                        stopBtn.disabled = false;
                    } else {
                        startBtn.disabled = false;
                        startBtn.innerText = "▶ 周回スタート";
                        stopBtn.disabled = true;
                    }

                    if (session.logs && session.logs.length > 0) {
                        const logElement = document.getElementById(`log-${session.id}`);
                        logElement.innerText = session.logs.join('\\n');

                        const panel = document.getElementById(`console-${session.id}`);
                        panel.scrollTop = panel.scrollHeight;
                    }
                });
            });
        }

        fetchStatus();
        fetchInterval = setInterval(fetchStatus, 2000);
    </script>
</body>
</html>
"""

@app.route('/', methods=['GET','POST'])
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/status')
def status():
    return jsonify(sessions)

@app.route('/add_account', methods=['POST'])
def add_account():
    next_id = max(sessions.keys()) + 1 if sessions else 1
    email_key = f'PUNIPUNI_EMAIL{next_id}'
    email = os.getenv(email_key)

    if not email:
        return jsonify({
            "success": False, 
            "message": f"アカウント追加エラー:\\n.envファイルに {email_key} が設定されていません。"
        })

    # 以前保存した名前があれば、その名前を自動復元
    saved_names = load_saved_names()
    restored_name = saved_names.get(next_id, f"アカウント{next_id}")

    sessions[next_id] = {
        "id": next_id,
        "name": restored_name,
        "running": False,
        "success": 0,
        "fail": 0,
        "ypoint": "取得前",
        "logs": [f"システム初期化完了。周回スタートボタンを押してください。"]
    }
    return jsonify({"success": True, "session": sessions[next_id]})

@app.route('/update_name/<int:acc_id>', methods=['POST'])
def update_name(acc_id):
    if acc_id not in sessions:
        return jsonify({"success": False}), 404

    data = request.get_json() or {}
    new_name = data.get('name', '').strip()

    if not new_name:
        return jsonify({
            "success": False,
            "message": "名前を入力してください。"
        }), 400

    sessions[acc_id]["name"] = new_name
    return jsonify({"success": True})


@app.route('/save_name/<int:acc_id>', methods=['POST'])
def save_name(acc_id):
    if acc_id not in sessions:
        return jsonify({"success": False}), 404

    data = request.get_json() or {}
    new_name = data.get('name', '').strip()

    if not new_name:
        return jsonify({
            "success": False,
            "message": "名前を入力してください。"
        }), 400

    sessions[acc_id]["name"] = new_name

    if not save_account_name(acc_id, new_name):
        return jsonify({
            "success": False,
            "message": "name.txtへの保存に失敗しました。"
        }), 500

    return jsonify({
        "success": True,
        "message": f"アカウント{acc_id} の名前を保存しました。"
    })

@app.route('/start/<int:acc_id>', methods=['POST'])
def start(acc_id):
    if acc_id not in sessions:
        return jsonify({"message": "アカウントが見つかりません"}), 404

    if sessions[acc_id]["running"]:
        return jsonify({"message": "すでに実行中です"})

    threading.Thread(target=run_bot_loop, args=(acc_id,), daemon=True).start()
    return jsonify({"message": "周回を開始しました"})

@app.route('/stop/<int:acc_id>', methods=['POST'])
def stop(acc_id):
    if acc_id not in sessions:
        return jsonify({"message": "アカウントが見つかりません"}), 404

    if not sessions[acc_id]["running"]:
        return jsonify({"message": "実行していません"})

    sessions[acc_id]["running"] = False
    return jsonify({"message": "停止リクエストを送信しました"})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)