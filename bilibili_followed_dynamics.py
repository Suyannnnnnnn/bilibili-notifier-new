import random
import requests, qrcode, time, re, json, os, tempfile, filecmp, shutil, schedule
from pathlib import Path
import requests.utils as ru
from datetime import datetime, timedelta

# ================== 配置加载 ==================
def load_config():
    is_docker = os.path.exists('/.dockerenv') or os.environ.get('DOCKER_ENV') == 'true'
    config_file = Path('/app/config.json') if is_docker else Path('./config.json')

    default_config = {
        "followed_dynamic_types": ["DYNAMIC_TYPE_AV", "DYNAMIC_TYPE_DRAW", "DYNAMIC_TYPE_FORWARD"],
        "feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/xxxx",
        "check_interval_minutes": 1,
        "followed_mids": []
    }

    if config_file.exists():
        with open(config_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    else:
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(default_config, f, indent=2, ensure_ascii=False)
        return default_config

CONFIG = load_config()
is_docker = os.path.exists('/.dockerenv') or os.environ.get('DOCKER_ENV') == 'true'

# ================== 路径 ==================
if is_docker:
    DATA_DIR = Path('/app/bili')
    WWW_DIR = Path('/app/www/wwwroot')
else:
    DATA_DIR = Path('./bili')
    WWW_DIR = Path('./www/wwwroot')

DATA_DIR.mkdir(exist_ok=True)
WWW_DIR.mkdir(exist_ok=True)

OLD_BVID_FILE = DATA_DIR / 'old_bvid.json'
COOKIE_FILE = DATA_DIR / 'cookie.txt'
OLD_SELF_COMMENT_FILE = DATA_DIR / 'old_self_comments.json'
TRACKED_CONTENT_FILE = DATA_DIR / 'tracked_content.json'

# ================== 通用请求头 ==================
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json',
    'Connection': 'keep-alive'
}

# ================== 飞书推送 ==================
def send_feishu_self_comment(comment_info):
    if not comment_info:
        return
    FEISHU_WEBHOOK = CONFIG.get("feishu_webhook")
    content = (
        f"**UP：**{comment_info['name']}  \n"
        f"**时间：**{comment_info['comment_time']}  \n"
        f"**类型：**{comment_info['type_text']}  \n"
        f"**内容：**{comment_info['content']}"
    )
    card = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": "💬 UP主自评"}, "template": "green"},
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": content}},
                {"tag": "action", "actions": [
                    {"tag": "button", "text": {"tag": "plain_text", "content": "查看评论"}, "type": "primary",
                     "url": comment_info['jump_url']}
                ]}
            ]
        }
    }
    try:
        requests.post(FEISHU_WEBHOOK, json=card, timeout=10)
    except:
        pass

def send_feishu_card(dynamics):
    if not dynamics:
        return
    FEISHU_WEBHOOK = CONFIG.get("feishu_webhook")
    elements = []
    for d in dynamics:
        if d['type'] == 'video':
            txt = f"**UP**：{d['name']}\n**视频**：{d['title']}"
            url = f"https://bilibili.com/video/{d['bvid']}"
        elif d['type'] == 'dynamic':
            txt = f"**UP**：{d['name']}\n**动态**：{d['title']}"
            url = f"https://t.bilibili.com/{d['dynamic_id']}"
        else:
            continue
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": txt}})
        elements.append({"tag": "action", "actions": [
            {"tag": "button", "text": {"tag": "plain_text", "content": "查看"}, "url": url}
        ]})
        elements.append({"tag": "hr"})

    card = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": "📢 指定UP更新"}, "template": "blue"},
            "elements": elements
        }
    }
    try:
        requests.post(FEISHU_WEBHOOK, json=card, timeout=10)
    except:
        pass

# ================== B站登录管理 ==================
class BiliSession:
    def __init__(self):
        self.sess = requests.Session()
        self.sess.headers.update(HEADERS)
        self.load_cookies()
        self.load_comment_history()
        self.load_track_list()

    def load_cookies(self):
        if COOKIE_FILE.exists():
            try:
                with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
                    cj = ru.cookiejar_from_dict(json.load(f))
                    self.sess.cookies.update(cj)
                print("✅ Cookie 加载成功")
            except:
                COOKIE_FILE.unlink(missing_ok=True)

    def load_comment_history(self):
        try:
            with open(OLD_SELF_COMMENT_FILE, 'r', encoding='utf-8') as f:
                self.sent_comments = set(json.load(f))
        except:
            self.sent_comments = set()

    def load_track_list(self):
        try:
            with open(TRACKED_CONTENT_FILE, 'r', encoding='utf-8') as f:
                self.track_list = json.load(f)
        except:
            self.track_list = []

    def save_track_list(self):
        with open(TRACKED_CONTENT_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.track_list, f, ensure_ascii=False)

    def save_comment_history(self):
        with open(OLD_SELF_COMMENT_FILE, 'w', encoding='utf-8') as f:
            json.dump(list(self.sent_comments), f, ensure_ascii=False)

    def is_login(self):
        try:
            r = self.sess.get("https://api.bilibili.com/x/space/myinfo", timeout=10)
            return r.json().get("code") == 0
        except:
            return False

    def login(self):
        while not self.is_login():
            print("🔐 请登录B站")
            r = self.sess.get("https://passport.bilibili.com/x/passport-login/web/qrcode/generate").json()
            key = r['data']['qrcode_key']
            url = r['data']['url']
            qr = qrcode.QRCode(border=1)
            qr.add_data(url)
            qr.print_ascii(invert=True)

            while True:
                time.sleep(3)
                poll = self.sess.get(
                    "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
                    params={'qrcode_key': key}
                ).json()
                code = poll['data']['code']
                if code == 0:
                    with open(COOKIE_FILE, 'w', encoding='utf-8') as f:
                        json.dump(ru.dict_from_cookiejar(self.sess.cookies), f)
                    print("✅ 登录成功")
                    return
                if code not in (86101, 86090):
                    break

    def get_user_dynamics(self, mid):
        items = []
        # 只需要一页就能拿到最新动态
        try:
            url = f"https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/all?host_mid={mid}&page=1"
            headers = {
                'Referer': f'https://space.bilibili.com/{mid}',
                'User-Agent': HEADERS['User-Agent']
            }
            r = self.sess.get(url, headers=headers, timeout=10)
            j = r.json()
            items = j.get('data', {}).get('items', [])
        except:
            pass
        return items

    def check_video(self, bvid, up_mid, up_name):
        try:
            next_p = 0
            while next_p is not None and next_p < 4:
                url = f"https://api.bilibili.com/x/v2/reply/main?mode=3&type=1&oid={bvid}&next={next_p}"
                j = self.sess.get(url, timeout=10).json()
                if j.get('code') != 0:
                    break
                for rep in j.get('data', {}).get('replies', []):
                    try:
                        rpid = rep['rpid']
                        mid = str(rep['member']['mid'])
                        msg = rep['content']['message']
                        ctime = datetime.fromtimestamp(rep['ctime']).strftime("%Y-%m-%d %H:%M")
                        if mid == up_mid:
                            cid = f"v_{bvid}_{rpid}"
                            if cid not in self.sent_comments:
                                self.sent_comments.add(cid)
                                send_feishu_self_comment({
                                    'name': up_name,
                                    'comment_time': ctime,
                                    'type_text': '视频评论',
                                    'content': msg[:150] + '...' if len(msg) > 150 else msg,
                                    'jump_url': f'https://bilibili.com/video/{bvid}#reply{rpid}'
                                })
                                print(f"✅ 发现自评：{msg[:40]}")
                    except:
                        continue
                next_p = j['data'].get('next')
        except Exception as e:
            print(f"视频检测异常：{e}")

    def check_dynamic(self, dyn_id, up_mid, up_name):
        print(f"📝 检测动态 {dyn_id}")
        try:
            next_p = 0
            while next_p is not None and next_p < 4:
                # 修复：动态评论 type = 17（原11错误）
                url = f"https://api.bilibili.com/x/v2/reply/main?mode=3&type=17&oid={dyn_id}&next={next_p}"
                j = self.sess.get(url, timeout=10).json()
                if j.get('code') != 0:
                    break
                for rep in j.get('data', {}).get('replies', []):
                    try:
                        rpid = rep['rpid']
                        mid = str(rep['member']['mid'])
                        msg = rep['content']['message']
                        ctime = datetime.fromtimestamp(rep['ctime']).strftime("%Y-%m-%d %H:%M")
                        if mid == up_mid:
                            cid = f"d_{dyn_id}_{rpid}"
                            if cid not in self.sent_comments:
                                self.sent_comments.add(cid)
                                send_feishu_self_comment({
                                    'name': up_name,
                                    'comment_time': ctime,
                                    'type_text': '动态评论',
                                    'content': msg[:150] + '...' if len(msg) > 150 else msg,
                                    'jump_url': f'https://t.bilibili.com/{dyn_id}#reply{rpid}'
                                })
                                print(f"✅ 发现动态自评：{msg[:40]}")
                    except:
                        continue
                next_p = j['data'].get('next')
        except Exception as e:
            print(f"动态检测异常：{e}")

    def run(self):
        print("\n==================== 开始一轮检查 ====================")
        mid_list = CONFIG.get("followed_mids", [])
        if not mid_list:
            print("⚠️ 未设置 followed_mids")
            return

        now = datetime.now()
        cutoff = now - timedelta(days=3)
        new_contents = []
        push_list = []

        # 逐个UP爬主页动态
        for mid in mid_list:
            items = self.get_user_dynamics(mid)
            for item in items:
                try:
                    mod = item['modules']
                    pub_ts = mod['module_author']['pub_ts']
                    pub_time = datetime.fromtimestamp(pub_ts)
                    if pub_time < cutoff:
                        continue

                    name = mod['module_author']['name']
                    dtype = item.get('type')
                    card = None

                    # 视频
                    if dtype == 'DYNAMIC_TYPE_AV':
                        bv = mod['module_dynamic']['major']['archive']['bvid']
                        title = mod['module_dynamic']['major']['archive']['title']
                        card = {'type': 'video', 'mid': mid, 'name': name, 'bvid': bv, 'title': title, 'pub': pub_ts}

                    # 图文动态
                    elif dtype == 'DYNAMIC_TYPE_DRAW':
                        did = item['id_str']
                        opus = mod['module_dynamic']['major']['opus']
                        title = opus.get('title', opus.get('desc', opus.get('summary', {}).get('text', '无标题')))
                        title = title[:80]
                        card = {'type': 'dynamic', 'mid': mid, 'name': name, 'dynamic_id': did, 'title': title,
                                'pub': pub_ts}
                    if card:
                        new_contents.append(card)
                except:
                    continue

        # 去重
        key_set = set()
        unique_new = []
        for c in new_contents:
            key = c.get('bvid') or c.get('dynamic_id')
            if key and key not in key_set:
                key_set.add(key)
                unique_new.append(c)

        # 合并到待检测列表
        old_keys = {t.get('bvid') or t.get('dynamic_id') for t in self.track_list}
        for item in unique_new:
            k = item.get('bvid') or item.get('dynamic_id')
            if k not in old_keys:
                push_list.append(item)
        self.track_list = unique_new
        self.save_track_list()

        # 推送新动态
        if push_list:
            send_feishu_card(push_list)
            print(f"📤 推送 {len(push_list)} 条新动态")

        # 统一检测所有3天内内容的自评
        print(f"\n🔍 开始检测 {len(self.track_list)} 条内容的评论")
        for item in self.track_list:
            try:
                if item['type'] == 'video':
                    self.check_video(item['bvid'], item['mid'], item['name'])
                elif item['type'] == 'dynamic':
                    self.check_dynamic(item['dynamic_id'], item['mid'], item['name'])
                time.sleep(0.3)
            except Exception as e:
                print(f"跳过异常项：{e}")

        self.save_comment_history()
        print("\n==================== 本轮完成 ====================\n")

# ================== 定时任务 ==================
def task():
    b = BiliSession()
    b.login()
    b.run()

if __name__ == '__main__':
    interval = CONFIG.get("check_interval_minutes", 1)
    schedule.every(interval).minutes.do(task)
    print(f"⏰ 已启动，每 {interval} 分钟检查一次")
    while True:
        schedule.run_pending()
        time.sleep(1)
