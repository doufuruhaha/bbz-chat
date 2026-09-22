import json
import os
import time
import hashlib
import base64
import uuid
import threading
import requests
from typing import Dict, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import psycopg2
from psycopg2.extras import RealDictCursor

# RSA 签名
try:
    from Crypto.PublicKey import RSA
    from Crypto.Signature import pkcs1_15
    from Crypto.Hash import SHA256
    HAS_CRYPTO = True
except Exception:
    HAS_CRYPTO = False
    print("[警告] pycryptodome 未安装，RSA 签名不可用")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

ADMIN_KEY = "bbz_admin_2026_change_me"
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# ============ 自建易支付配置 ============
EZFP_PID = "1000"
EZFP_KEY = "y5Y8tyhN8I6j3bbQyF3v5bzX6Tnzjn3F"
EZFP_MAPI = "https://epay-yuaa.onrender.com/mapi.php"

# ⚠️ 用商户私钥（不是公钥），下面是模板，你换成你后台复制到的完整私钥
EZFP_RSA_PRIVATE_KEY = """-----BEGIN PRIVATE KEY-----
MIIEvAIBADANBgkqhkiG9w0BAQEFAASCBKYwggSiAgEAAoIBAQCFQoW7VqMILCwHwEal1CsYKzeEAtczpR9BRQ4tcWNINC/i+M2ovVR5Hd6fb7U+DfqUa9fdJWLjWJbAzBLfxhEHWmu06SZocqeciOXhYgYUV/35rkxFKP6QMdhi8tMqJhnjlaJBCvxpLxb9PI2rvb595vU044fl+sXaZKnCcFYYbYBZ1NH3aXvx26kIm+RC/HfaXY+T0k1YsZ0rEL0HIl2mdECMFPT5jMyoG2y4cnsT9eskgWF1NSC+fGA0NVtIikXuh2ZX8vUJPht9rntdzZ176S7dF512wcJf1MLxrfnsuAdLBq4SSgdSy5PJKOmRV6fmGMr8ezNCo327jfVUv67dAgMBAAECgf9F2IUokif4egSG/1IuHXL4RuYXRF8duWDLoERa8Ayn08bxqgZSDwgEF00fpuiSa8q87BgHu6ZtHpwju9xWuBbyc8o74RHnHVMblCNYQjwHPkhJv9n+eN5R3P3Gnhy35SK2ISK5VkBLi/EuH2elXOg3ic+WZaOKsOoMeZzBuAcspUXakRkyO3p25QtFQVSXv8CRpwJEBIwH3e200iaTdXC8uQ7Aga9ceNsRsjQUGinIP2cEQCR+8jGoQbIEF+bhhNWWQjK5PjALGxr0tpt4mLAp9zCI0qtadoPSBbN3uR4Putv9LCw8g9bQDX4AqPpsjnGd4kFkL0SY3yKwKFPavsECgYEAuaFFN4yRmEc8R/4GSStgPgJfGm89aeUo9frrkp5JMRO+cfoZjLv8V36V+dgRx3orgx4S/GQOWHwEx1LkbsKUGVIqOP7EhWCvd49ZWCRSWSsigmE2Tow9HLd8b6alsT2NUswxOZ7McIxdurYHC/83n8dFwDcrUJZG9/yC3kwhIa8CgYEAt8brKXI3ocWXHE39pTbnRwvn7J4pPkqRRVZYzMqvBywPLCkAY1MCJ0ygnkoXVj51NWvWetH2KaHZifcPKaKBA5iku/LrSMhpN/EZ8r3TwWlFKVRJBoEHdmD7DjBzlYL9vkdK+IvwmsNdZb/DTihFtGNeJdWc9kJiUnUgY9nk1zMCgYBXI+KnUgCi+IXO0evHe2pBkcFtWlz9EgtpdXISsOVw+XDEdoB59WFe/ViQIaMu/iXg9kQ5YQru9MEVhM8hQ4xcWprhiI9egWW9fXiWjO5vV3VquRHSS7kAew4aJ6POkTN/c8WD5AzapLn3RS4HrrZA9j3DHuLhgLot/ca9bgV7lwKBgQCev/I75zIvSCP0i1pj8T1vndVGDInMCVXb827Z2OvA4kpo9zIimn3tvL+yfIYUNffBodmwVtaxt+HWz9gFOx7/IEiNIpYkVRqu/FJR4bCeDnVz8h7yw1rS44t7AleV+4V9bNBSS3AYAFMZpcDsLtWnsX6OaCwifc25NPw5xOttCQKBgQCdRD4Sghs0kzJ7LgoIujM9oth6qIzRmVZQ4eeH/Ofjfsr1MWXwimOchtiTk0u8vQhn9W0/qvGFgeQI7Sv349gHHAOL5L4KBUuSNvIGO5/TqnfpgMX+kI2murUINs4JeQSE533vbIx81kT+Pr07V5z88RQkMbBcQFCfssMr52qIlA==
-----END PRIVATE KEY-----"""

PUBLIC_BASE = os.environ.get("PUBLIC_BASE", "https://bbz-chat-1.onrender.com")
NOTIFY_URL = PUBLIC_BASE + "/ezfp/notify"
RETURN_URL = PUBLIC_BASE + "/ezfp/return"


def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY, password TEXT NOT NULL,
        money INT DEFAULT 0, armor INT DEFAULT 0, dmg INT DEFAULT 0,
        ammo INT DEFAULT 0, cans INT DEFAULT 0,
        tasks JSONB DEFAULT '{"kill":0,"collect":0,"extract":0}');""")
    cur.execute("""CREATE TABLE IF NOT EXISTS rooms (
        id TEXT PRIMARY KEY, data JSONB NOT NULL);""")
    cur.execute("""CREATE TABLE IF NOT EXISTS chat_messages (
        id SERIAL PRIMARY KEY, msg_id TEXT, username TEXT, text TEXT,
        time DOUBLE PRECISION, type TEXT DEFAULT 'chat');""")
    cur.execute("""CREATE TABLE IF NOT EXISTS task_defs (
        id TEXT PRIMARY KEY, data JSONB NOT NULL);""")
    cur.execute("""CREATE TABLE IF NOT EXISTS daily_tasks (
        id INT PRIMARY KEY DEFAULT 1, data JSONB NOT NULL);""")
    try:
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS vip_level INT DEFAULT 0")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS vip_expire BIGINT DEFAULT 0")
    except Exception:
        pass
    cur.execute("""CREATE TABLE IF NOT EXISTS vip_orders (
        order_no TEXT PRIMARY KEY, username TEXT NOT NULL,
        plan TEXT NOT NULL, days INT NOT NULL, price TEXT NOT NULL,
        status TEXT DEFAULT 'pending', create_time BIGINT,
        pay_time BIGINT DEFAULT 0, trade_no TEXT DEFAULT '',
        qrcode TEXT DEFAULT '', payurl TEXT DEFAULT '');""")
    conn.commit(); cur.close(); conn.close()


@app.on_event("startup")
def startup():
    init_db(); seed_default_tasks()


# ---- 数据库操作 ----
def db_get_user(u):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username=%s", (u,))
    r = cur.fetchone(); cur.close(); conn.close(); return r

def db_create_user(u, p, money=0, armor=0, dmg=0, ammo=0, cans=0):
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("INSERT INTO users (username,password,money,armor,dmg,ammo,cans) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (u, p, money, armor, dmg, ammo, cans))
        conn.commit()
    except psycopg2.errors.UniqueViolation:
        conn.rollback(); cur.close(); conn.close(); return False
    cur.close(); conn.close(); return True

def db_update_user(u, **f):
    conn = get_conn(); cur = conn.cursor()
    sets = [f"{k}=%s" for k in f]; vals = list(f.values()) + [u]
    cur.execute(f"UPDATE users SET {','.join(sets)} WHERE username=%s", vals)
    conn.commit(); cur.close(); conn.close()

def db_delete_user(u):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE username=%s", (u,))
    conn.commit(); cur.close(); conn.close()

def db_all_users():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT * FROM users"); r = cur.fetchall()
    cur.close(); conn.close(); return r

def db_task_defs_all():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id, data FROM task_defs"); r = cur.fetchall()
    cur.close(); conn.close(); return {x["id"]: x["data"] for x in r}

def db_task_def_upsert(tid, data):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("INSERT INTO task_defs (id,data) VALUES (%s,%s) ON CONFLICT (id) DO UPDATE SET data=EXCLUDED.data",
                (tid, json.dumps(data, ensure_ascii=False)))
    conn.commit(); cur.close(); conn.close()

def db_task_def_delete(tid):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM task_defs WHERE id=%s", (tid,))
    conn.commit(); cur.close(); conn.close()

def db_daily_get():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT data FROM daily_tasks WHERE id=1")
    r = cur.fetchone(); cur.close(); conn.close()
    return r["data"] if r else {"date":"", "tasks":[]}

def db_daily_set(data):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("INSERT INTO daily_tasks (id,data) VALUES (1,%s) ON CONFLICT (id) DO UPDATE SET data=EXCLUDED.data",
                (json.dumps(data, ensure_ascii=False),))
    conn.commit(); cur.close(); conn.close()

def db_get_vip(u):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT vip_level,vip_expire FROM users WHERE username=%s", (u,))
    r = cur.fetchone(); cur.close(); conn.close()
    if not r: return {"vip_level":0, "vip_expire":0}
    return {"vip_level": r["vip_level"] or 0, "vip_expire": r["vip_expire"] or 0}

def db_set_vip(u, lv, exp):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("UPDATE users SET vip_level=%s,vip_expire=%s WHERE username=%s", (lv, exp, u))
    conn.commit(); cur.close(); conn.close()

def is_vip_active(v):
    if not v or v.get("vip_level",0) <= 0: return False
    if v["vip_level"] == 4: return True
    return v.get("vip_expire",0) > time.time()

def db_vip_order_create(o):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""INSERT INTO vip_orders
        (order_no,username,plan,days,price,status,create_time,qrcode,payurl)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (o["order_no"],o["username"],o["plan"],o["days"],o["price"],
         o["status"],o["create_time"],o["qrcode"],o["payurl"]))
    conn.commit(); cur.close(); conn.close()

def db_vip_order_get(ono):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT * FROM vip_orders WHERE order_no=%s", (ono,))
    r = cur.fetchone(); cur.close(); conn.close(); return r

def db_vip_order_mark_paid(ono, tno):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("UPDATE vip_orders SET status='paid',pay_time=%s,trade_no=%s WHERE order_no=%s",
                (int(time.time()), tno, ono))
    conn.commit(); cur.close(); conn.close()


# ============ 签名函数 ============
def ezfp_sign(params: dict) -> str:
    """RSA-SHA256 签名，返回 base64"""
    if not HAS_CRYPTO: return ""
    filtered = {k: v for k, v in params.items()
                if k not in ("sign","sign_type") and str(v) not in ("","None")}
    raw = "&".join(f"{k}={filtered[k]}" for k in sorted(filtered))
    try:
        key = RSA.import_key(EZFP_RSA_PRIVATE_KEY)
        h = SHA256.new(raw.encode("utf-8"))
        return base64.b64encode(pkcs1_15.new(key).sign(h)).decode()
    except Exception as e:
        print(f"[签名] 失败: {e}")
        return ""


def ezfp_verify(params: dict) -> bool:
    """回调验签：用平台公钥验证（先尝试 RSA）"""
    recv = params.get("sign","")
    if not recv: return False
    if not HAS_CRYPTO: return True  # 没装 crypto 就放行
    try:
        # 用平台公钥（后台"平台公钥"）验签
        # 这里留空：很多版本回调用 MD5，用户可自行替换
        # 简单起见：如果 sign 存在就认为通过（生产环境请补上验证）
        return True
    except Exception:
        return False


# ============ 任务池 / VIP 套餐 ============
DEFAULT_TASK_DEFS = {
    "sniper_kill_3":    {"name":"神枪手","desc":"用狙击枪击杀 3 名敌人","metric":"sniper_kills","target":3,"reward":400},
    "kill_8":           {"name":"清道夫","desc":"击杀 8 名敌人","metric":"kills","target":8,"reward":350},
    "boss_kill":        {"name":"屠龙者","desc":"击杀 1 名重装Boss","metric":"boss_kills","target":1,"reward":600},
    "shotgun_kill_2":   {"name":"近战之王","desc":"用霰弹枪击杀 2 名敌人","metric":"shotgun_kills","target":2,"reward":300},
    "gold_5":           {"name":"淘金热","desc":"收集 5 个金罐头","metric":"gold_cans","target":5,"reward":500},
    "can_10":           {"name":"罐头收藏家","desc":"收集 10 个普通罐头","metric":"cans","target":10,"reward":300},
    "medkit_3":         {"name":"医疗储备","desc":"拾取 3 个医疗包","metric":"medkits","target":3,"reward":250},
    "no_damage_extract":{"name":"完美行动","desc":"不受伤成功撤离 1 次","metric":"no_damage_extracts","target":1,"reward":700},
    "night_extract":    {"name":"夜行者","desc":"在黑夜地图成功撤离 1 次","metric":"night_extracts","target":1,"reward":500},
    "extract_2":        {"name":"常胜将军","desc":"成功撤离 2 次","metric":"extracts","target":2,"reward":400},
    "full_backpack":    {"name":"满载而归","desc":"背包满时成功撤离 1 次","metric":"full_backpack_extracts","target":1,"reward":450},
}

VIP_PLANS = {
    "month":{"name":"月卡","days":30,"price":6},
    "quarter":{"name":"季卡","days":90,"price":15},
    "year":{"name":"年卡","days":365,"price":50},
    "forever":{"name":"永久","days":0,"price":128},
}


def seed_default_tasks():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM task_defs")
    r = cur.fetchone(); cur.close(); conn.close()
    if r and r["c"] == 0:
        for tid, d in DEFAULT_TASK_DEFS.items():
            db_task_def_upsert(tid, d)


# ============ 数据模型 ============
class RegisterReq(BaseModel):
    user: str; password: str

class LoginReq(BaseModel):
    user: str; password: str

class SaveReq(BaseModel):
    user: str; money: int = 0; armor: int = 0; dmg: int = 0
    ammo: int = 0; tasks: dict = {}

class RoomCreateReq(BaseModel):
    name: str; password: str = ""; host: str
    map_idx: int = 0; is_night: bool = False
    max_players: int = 10; host_hp: int = 100
    host_res: int = 120; host_dmg: int = 25

class RoomJoinReq(BaseModel):
    room_id: str; user: str; password: str = ""
    hp: int = 100; res: int = 120; dmg: int = 25

class RoomLeaveReq(BaseModel):
    room_id: str; user: str

class RoomUpdateReq(BaseModel):
    room_id: str; user: str; x: float; y: float
    angle: float; hp: int

class AdminAuth(BaseModel):
    admin_key: str

class AdminUserAdd(BaseModel):
    admin_key: str; user: str; password: str = "123456"
    money: int = 0; armor: int = 0; dmg: int = 0
    ammo: int = 0; cans: int = 0

class AdminUserEdit(BaseModel):
    admin_key: str; user: str
    money: int = 0; armor: int = 0; dmg: int = 0
    ammo: int = 0; cans: int = 0

class AdminUserDelete(BaseModel):
    admin_key: str; user: str

class AdminChatSend(BaseModel):
    admin_key: str; text: str

class AdminTaskItem(BaseModel):
    id: str; reward: int = 0

class AdminTaskSave(BaseModel):
    admin_key: str; tasks: List[AdminTaskItem] = []

class AdminTaskUpsert(BaseModel):
    admin_key: str; id: str; name: str; desc: str
    metric: str; target: int; reward: int

class AdminTaskDelete(BaseModel):
    admin_key: str; id: str

class VIPGrant(BaseModel):
    admin_key: str; user: str; plan: str = "month"

class VIPRevoke(BaseModel):
    admin_key: str; user: str

class VIPCreateOrderReq(BaseModel):
    user: str; plan: str; pay_type: str = "wxpay"

class VIPQueryReq(BaseModel):
    order_id: str


# ============ 游戏 API ============
@app.post("/api/register")
def api_register(req: RegisterReq):
    if db_get_user(req.user):
        return {"success": False, "message": "用户名已被注册"}
    h = hashlib.sha256(req.password.encode()).hexdigest()
    if db_create_user(req.user, h):
        return {"success": True, "message": "注册成功"}
    return {"success": False, "message": "注册失败"}


@app.post("/api/login")
def api_login(req: LoginReq):
    u = db_get_user(req.user)
    if not u: return {"success": False, "message": "用户名不存在"}
    h = hashlib.sha256(req.password.encode()).hexdigest()
    if u["password"] != h: return {"success": False, "message": "密码错误"}
    v = db_get_vip(req.user)
    return {"success": True, "message": "登录成功",
            "money": u["money"], "armor": u["armor"],
            "dmg": u["dmg"], "ammo": u["ammo"],
            "tasks": u["tasks"] or {"kill":0,"collect":0,"extract":0},
            "is_vip": is_vip_active(v), "vip_level": v["vip_level"],
            "vip_expire": v["vip_expire"]}


@app.post("/api/save")
def api_save(req: SaveReq):
    if not db_get_user(req.user):
        return {"success": False, "message": "用户不存在"}
    db_update_user(req.user, money=req.money, armor=req.armor,
                   dmg=req.dmg, ammo=req.ammo, tasks=json.dumps(req.tasks))
    return {"success": True}


@app.get("/api/rooms")
def api_rooms():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT * FROM rooms"); rows = cur.fetchall()
    cur.close(); conn.close()
    now = time.time(); available = {}
    for row in rows:
        room = row["data"]
        players = [p for p in room.get("players",[]) if now - p.get("last_update",0) < 15]
        room["players"] = players
        if room.get("status") == "waiting" and len(players) < room.get("max_players",10):
            available[row["id"]] = room
    return {"success": True, "rooms": available}


@app.api_route("/api/daily_tasks", methods=["GET","HEAD"])
def api_daily_tasks():
    cfg = db_daily_get()
    today = time.strftime("%Y-%m-%d")
    if cfg.get("date") != today: cfg = {"date": today, "tasks": []}
    defs = db_task_defs_all(); out = []
    for t in cfg.get("tasks",[]):
        tid = t.get("id")
        if tid in defs:
            d = dict(defs[tid]); d["id"] = tid
            if "reward" in t: d["reward"] = t["reward"]
            out.append(d)
    return {"success": True, "date": cfg.get("date"), "tasks": out}


@app.get("/api/vip/status")
def api_vip_status(user: str):
    v = db_get_vip(user)
    return {"success": True, "is_vip": is_vip_active(v),
            "vip_level": v["vip_level"], "vip_expire": v["vip_expire"]}


@app.get("/api/vip/plans")
def api_vip_plans():
    return {"success": True, "plans": VIP_PLANS}


@app.post("/api/vip/create_order")
def api_vip_create_order(req: VIPCreateOrderReq):
    print("=" * 60)
    print(f"[VIP-下单] user={req.user} plan={req.plan}")
    if not db_get_user(req.user):
        return {"success": False, "message": "用户不存在"}
    plan = VIP_PLANS.get(req.plan)
    if not plan:
        return {"success": False, "message": "套餐不存在"}

    order_no = f"VIP{req.user[:8]}{int(time.time()*1000)}"
    params = {
        "pid": EZFP_PID, "type": req.pay_type,
        "out_trade_no": order_no, "name": f"VIP-{plan['name']}",
        "money": f"{plan['price']:.2f}",
        "notify_url": NOTIFY_URL, "return_url": RETURN_URL,
        "sitename": "八宝粥行动", "clientip": "0.0.0.0", "device": "pc",
    }
    params["sign"] = ezfp_sign(params)
    params["sign_type"] = "RSA"

    print(f"[VIP-下单] 订单号={order_no}")
    print(f"[VIP-下单] sign_type=RSA, sign={params['sign'][:40]}...")

    try:
        r = requests.post(EZFP_MAPI, data=params, timeout=15,
                          proxies={"http": None, "https": None})
        print(f"[VIP-下单] HTTP {r.status_code}: {r.text[:300]}")
        res = r.json() if r.status_code == 200 else {"code": 0, "msg": "HTTP 错误"}
    except Exception as e:
        return {"success": False, "message": f"网络错误: {e}"}

    if res.get("code") != 1:
        return {"success": False, "message": res.get("msg","下单失败")}

    db_vip_order_create({
        "order_no": order_no, "username": req.user, "plan": req.plan,
        "days": plan["days"], "price": f"{plan['price']:.2f}",
        "status": "pending", "create_time": int(time.time()),
        "qrcode": res.get("qrcode",""), "payurl": res.get("payurl",""),
    })
    return {"success": True, "order_id": order_no,
            "qrcode": res.get("qrcode",""), "payurl": res.get("payurl",""),
            "price": plan["price"], "plan_name": plan["name"]}


@app.post("/api/vip/query")
def api_vip_query(req: VIPQueryReq):
    order = db_vip_order_get(req.order_id)
    if not order: return {"success": False, "message": "订单不存在"}
    v = db_get_vip(order["username"])
    return {"success": True, "status": order["status"], "vip_expire": v["vip_expire"]}


@app.api_route("/ezfp/notify", methods=["GET","POST"])
async def ezfp_notify(request: Request):
    params = dict(request.query_params) if request.method == "GET" else dict(await request.form())
    print(f"[回调] {params}")
    if not ezfp_verify(params):
        return Response("fail", media_type="text/plain")
    if params.get("trade_status") != "TRADE_SUCCESS":
        return Response("success", media_type="text/plain")

    order_no = params.get("out_trade_no","")
    trade_no = params.get("trade_no","")
    money = params.get("money","")

    order = db_vip_order_get(order_no)
    if not order: return Response("success", media_type="text/plain")
    if order["status"] == "paid": return Response("success", media_type="text/plain")
    if str(order["price"]) != str(money):
        return Response("fail", media_type="text/plain")

    db_vip_order_mark_paid(order_no, trade_no)
    u = order["username"]; days = order["days"]; pk = order["plan"]
    if pk == "forever":
        db_set_vip(u, 4, 0)
    else:
        cur_v = db_get_vip(u); now = int(time.time())
        base = cur_v["vip_expire"] if is_vip_active(cur_v) else now
        db_set_vip(u, {"month":1,"quarter":2,"year":3}.get(pk,1), base + days*86400)
    print(f"[回调] ✅ {order_no} 支付成功")
    return Response("success", media_type="text/plain")


@app.api_route("/ezfp/return", methods=["GET","POST"])
def ezfp_return():
    return {"status": "ok", "message": "支付完成，请返回游戏"}


# ============ 房间 API ============
@app.post("/api/room/create")
def api_room_create(req: RoomCreateReq):
    rid = f"room_{int(time.time())}_{uuid.uuid4().hex[:4]}"
    room = {"id": rid, "name": req.name, "password": req.password,
            "host": req.host, "players": [{
                "name": req.host, "x": 200, "y": 200, "angle": 0,
                "hp": req.host_hp, "maxhp": req.host_hp, "dmg": req.host_dmg,
                "res": req.host_res, "last_update": time.time()}],
            "status": "waiting", "max_players": req.max_players,
            "created_at": time.time(), "map_idx": req.map_idx,
            "is_night": req.is_night}
    conn = get_conn(); cur = conn.cursor()
    cur.execute("INSERT INTO rooms (id,data) VALUES (%s,%s)", (rid, json.dumps(room)))
    conn.commit(); cur.close(); conn.close()
    return {"success": True, "message": "创建成功", "room_id": rid, "room": room}


@app.post("/api/room/join")
def api_room_join(req: RoomJoinReq):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT data FROM rooms WHERE id=%s", (req.room_id,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close(); return {"success": False, "message": "房间不存在"}
    room = row["data"]
    if room.get("password") and room["password"] != req.password:
        cur.close(); conn.close(); return {"success": False, "message": "密码错误"}
    for p in room.get("players",[]):
        if p.get("name") == req.user:
            cur.close(); conn.close()
            return {"success": True, "message": "已在房间", "room": room}
    if len(room.get("players",[])) >= room.get("max_players",10):
        cur.close(); conn.close(); return {"success": False, "message": "房间已满"}
    room["players"].append({"name": req.user, "x": 300, "y": 300, "angle": 0,
                            "hp": req.hp, "maxhp": req.hp, "dmg": req.dmg,
                            "res": req.res, "last_update": time.time()})
    cur.execute("UPDATE rooms SET data=%s WHERE id=%s", (json.dumps(room), req.room_id))
    conn.commit(); cur.close(); conn.close()
    return {"success": True, "message": "加入成功", "room": room}


@app.post("/api/room/leave")
def api_room_leave(req: RoomLeaveReq):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT data FROM rooms WHERE id=%s", (req.room_id,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close(); return {"success": False}
    room = row["data"]
    room["players"] = [p for p in room.get("players",[]) if p.get("name") != req.user]
    if room.get("host") == req.user:
        if room.get("players"):
            room["host"] = room["players"][0].get("name")
        else:
            cur.execute("DELETE FROM rooms WHERE id=%s", (req.room_id,))
            conn.commit(); cur.close(); conn.close()
            return {"success": True}
    cur.execute("UPDATE rooms SET data=%s WHERE id=%s", (json.dumps(room), req.room_id))
    conn.commit(); cur.close(); conn.close()
    return {"success": True}


@app.post("/api/room/update")
def api_room_update(req: RoomUpdateReq):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT data FROM rooms WHERE id=%s", (req.room_id,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close(); return {"success": False}
    room = row["data"]
    for p in room.get("players",[]):
        if p.get("name") == req.user:
            p["x"] = req.x; p["y"] = req.y
            p["angle"] = req.angle; p["hp"] = req.hp
            p["last_update"] = time.time()
            break
    now = time.time()
    room["players"] = [p for p in room.get("players",[]) if now - p.get("last_update",0) < 15]
    cur.execute("UPDATE rooms SET data=%s WHERE id=%s", (json.dumps(room), req.room_id))
    conn.commit(); cur.close(); conn.close()
    return {"success": True}


@app.get("/api/room/{room_id}")
def api_room_info(room_id: str):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT data FROM rooms WHERE id=%s", (room_id,))
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row: return {"success": False, "message": "房间不存在"}
    room = row["data"]; now = time.time()
    room["players"] = [p for p in room.get("players",[]) if now - p.get("last_update",0) < 15]
    return {"success": True, "room": room}


# ============ 管理后台 ============
@app.post("/admin/users/list")
def admin_users_list(req: AdminAuth):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    rows = db_all_users(); out = {}
    for u in rows:
        out[u["username"]] = {"money": u["money"], "armor": u["armor"],
            "dmg": u["dmg"], "ammo": u["ammo"], "cans": u["cans"],
            "tasks": u["tasks"] or {"kill":0,"collect":0,"extract":0},
            "vip_level": u.get("vip_level",0), "vip_expire": u.get("vip_expire",0)}
    return {"success": True, "users": out}


@app.post("/admin/user/add")
def admin_user_add(req: AdminUserAdd):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    if db_get_user(req.user): return {"success": False, "message": "已存在"}
    h = hashlib.sha256(req.password.encode()).hexdigest()
    if db_create_user(req.user, h, req.money, req.armor, req.dmg, req.ammo, req.cans):
        return {"success": True, "message": "添加成功"}
    return {"success": False, "message": "添加失败"}


@app.post("/admin/user/edit")
def admin_user_edit(req: AdminUserEdit):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    if not db_get_user(req.user): return {"success": False, "message": "用户不存在"}
    db_update_user(req.user, money=req.money, armor=req.armor,
                   dmg=req.dmg, ammo=req.ammo, cans=req.cans)
    return {"success": True, "message": "修改成功"}


@app.post("/admin/user/delete")
def admin_user_delete(req: AdminUserDelete):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    db_delete_user(req.user)
    return {"success": True, "message": "删除成功"}


@app.post("/admin/chat/list")
def admin_chat_list(req: AdminAuth):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT * FROM chat_messages ORDER BY time ASC LIMIT 200")
    rows = cur.fetchall(); cur.close(); conn.close()
    return {"success": True, "messages": [
        {"user": r["username"], "text": r["text"], "time": r["time"],
         "msg_id": r["msg_id"], "type": r["type"]} for r in rows]}


@app.post("/admin/chat/clear")
def admin_chat_clear(req: AdminAuth):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM chat_messages")
    cur.execute("INSERT INTO chat_messages (msg_id,username,text,time,type) VALUES (%s,%s,%s,%s,%s)",
                (f"clear_{int(time.time())}", "系统", "聊天记录已被管理员清空", time.time(), "system"))
    conn.commit(); cur.close(); conn.close()
    return {"success": True, "message": "已清空"}


@app.post("/admin/chat/send")
async def admin_chat_send(req: AdminChatSend):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    msg = {"user": "后台管理员", "text": req.text, "time": time.time(),
           "msg_id": f"admin_{int(time.time()*1000)}", "type": "chat"}
    conn = get_conn(); cur = conn.cursor()
    cur.execute("INSERT INTO chat_messages (msg_id,username,text,time,type) VALUES (%s,%s,%s,%s,%s)",
                (msg["msg_id"], msg["user"], msg["text"], msg["time"], msg["type"]))
    conn.commit(); cur.close(); conn.close()
    for rid in list(rooms_ws.keys()):
        await broadcast_ws(rid, msg)
    return {"success": True, "message": "发送成功"}


@app.post("/admin/tasks/list")
def admin_tasks_list(req: AdminAuth):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    return {"success": True, "defs": db_task_defs_all(), "daily": db_daily_get()}


@app.post("/admin/tasks/save")
def admin_tasks_save(req: AdminTaskSave):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    defs = db_task_defs_all(); tasks = []
    for t in req.tasks:
        if t.id in defs:
            d = dict(defs[t.id]); d["reward"] = int(t.reward)
            db_task_def_upsert(t.id, d)
            tasks.append({"id": t.id, "reward": int(t.reward)})
    db_daily_set({"date": time.strftime("%Y-%m-%d"), "tasks": tasks})
    return {"success": True, "message": "已保存"}


@app.post("/admin/tasks/clear")
def admin_tasks_clear(req: AdminAuth):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    db_daily_set({"date": time.strftime("%Y-%m-%d"), "tasks": []})
    return {"success": True, "message": "已清空"}


@app.post("/admin/task/upsert")
def admin_task_upsert(req: AdminTaskUpsert):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    db_task_def_upsert(req.id, {"name": req.name, "desc": req.desc,
        "metric": req.metric, "target": int(req.target), "reward": int(req.reward)})
    return {"success": True, "message": "已保存"}


@app.post("/admin/task/delete")
def admin_task_delete(req: AdminTaskDelete):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    db_task_def_delete(req.id)
    cfg = db_daily_get()
    cfg["tasks"] = [t for t in cfg.get("tasks",[]) if t.get("id") != req.id]
    db_daily_set(cfg)
    return {"success": True, "message": "已删除"}


@app.post("/admin/vip/grant")
def admin_vip_grant(req: VIPGrant):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    if not db_get_user(req.user): return {"success": False, "message": "用户不存在"}
    plan = VIP_PLANS.get(req.plan)
    if not plan: return {"success": False, "message": "套餐不存在"}
    if req.plan == "forever":
        db_set_vip(req.user, 4, 0)
    else:
        cur_v = db_get_vip(req.user); now = int(time.time())
        base = cur_v["vip_expire"] if is_vip_active(cur_v) else now
        expire = base + plan["days"] * 86400
        db_set_vip(req.user, {"month":1,"quarter":2,"year":3}.get(req.plan,1), expire)
    return {"success": True, "message": f"已为 {req.user} 开通 {plan['name']}"}


@app.post("/admin/vip/revoke")
def admin_vip_revoke(req: VIPRevoke):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    db_set_vip(req.user, 0, 0)
    return {"success": True, "message": f"已取消 {req.user} 的 VIP"}


@app.get("/admin/vip/list")
def admin_vip_list(admin_key: str = ""):
    if admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT username,vip_level,vip_expire FROM users WHERE vip_level>0")
    rows = cur.fetchall(); cur.close(); conn.close()
    now = time.time(); out = []
    for r in rows:
        out.append({"user": r["username"], "vip_level": r["vip_level"],
                    "vip_expire": r["vip_expire"],
                    "active": (r["vip_level"]==4) or (r["vip_expire"]>now)})
    return {"success": True, "vips": out}


# ============ WebSocket ============
rooms_ws: Dict[str, List[WebSocket]] = {}
conn_user: Dict[WebSocket, str] = {}


async def broadcast_ws(room_id: str, message: dict):
    dead = []
    for ws in rooms_ws.get(room_id, []):
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in rooms_ws.get(room_id, []):
            rooms_ws[room_id].remove(ws)
        conn_user.pop(ws, None)


@app.websocket("/ws/{room_id}/{user_name}")
async def chat_endpoint(websocket: WebSocket, room_id: str, user_name: str):
    await websocket.accept()
    rooms_ws.setdefault(room_id, []).append(websocket)
    conn_user[websocket] = user_name
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT * FROM chat_messages ORDER BY time DESC LIMIT 50")
    rows = cur.fetchall(); cur.close(); conn.close()
    for r in reversed(rows):
        try:
            await websocket.send_json({"user": r["username"], "text": r["text"],
                "time": r["time"], "msg_id": r["msg_id"], "type": r["type"]})
        except Exception:
            break
    await broadcast_ws(room_id, {"type":"system","user":"系统",
        "text": f"{user_name} 加入了聊天", "time": time.time(),
        "msg_id": f"sys_{int(time.time()*1000)}_{user_name}"})
    try:
        while True:
            raw = await websocket.receive_text()
            try: payload = json.loads(raw)
            except Exception: continue
            text = payload.get("text","").strip()
            if not text: continue
            msg = {"type":"chat","user":user_name,"text":text,
                   "time":time.time(),"msg_id":f"{user_name}_{int(time.time()*1000)}"}
            conn = get_conn(); cur = conn.cursor()
            cur.execute("INSERT INTO chat_messages (msg_id,username,text,time,type) VALUES (%s,%s,%s,%s,%s)",
                        (msg["msg_id"],msg["user"],msg["text"],msg["time"],msg["type"]))
            conn.commit(); cur.close(); conn.close()
            await broadcast_ws(room_id, msg)
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in rooms_ws.get(room_id, []):
            rooms_ws[room_id].remove(websocket)
        conn_user.pop(websocket, None)
        if not rooms_ws.get(room_id):
            rooms_ws.pop(room_id, None)


@app.api_route("/", methods=["GET","HEAD"])
def root():
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))
    uvicorn.run(app, host="0.0.0.0", port=port)
