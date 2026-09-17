import json
import os
import time
import hashlib
import uuid
import threading
from typing import Dict, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import psycopg2
from psycopg2.extras import RealDictCursor

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ADMIN_KEY = "bbz_admin_2026_change_me"

# ============ 数据库连接 ============
DATABASE_URL = os.environ.get("DATABASE_URL", "")

def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            money INT DEFAULT 0,
            armor INT DEFAULT 0,
            dmg INT DEFAULT 0,
            ammo INT DEFAULT 0,
            cans INT DEFAULT 0,
            tasks JSONB DEFAULT '{"kill":0,"collect":0,"extract":0}'
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rooms (
            id TEXT PRIMARY KEY,
            data JSONB NOT NULL
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id SERIAL PRIMARY KEY,
            msg_id TEXT,
            username TEXT,
            text TEXT,
            time DOUBLE PRECISION,
            type TEXT DEFAULT 'chat'
        );
    """)
    # ============ 新增：任务系统表 ============
    cur.execute("""
        CREATE TABLE IF NOT EXISTS task_defs (
            id TEXT PRIMARY KEY,
            data JSONB NOT NULL
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_tasks (
            id INT PRIMARY KEY DEFAULT 1,
            data JSONB NOT NULL
        );
    """)
    conn.commit()
    cur.close()
    conn.close()


@app.on_event("startup")
def startup():
    init_db()
    seed_default_tasks()


def db_get_user(username):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username=%s", (username,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def db_create_user(username, password_hash, money=0, armor=0, dmg=0, ammo=0, cans=0):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (username, password, money, armor, dmg, ammo, cans) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (username, password_hash, money, armor, dmg, ammo, cans)
        )
        conn.commit()
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        cur.close()
        conn.close()
        return False
    cur.close()
    conn.close()
    return True


def db_update_user(username, **fields):
    conn = get_conn()
    cur = conn.cursor()
    sets = []
    values = []
    for k, v in fields.items():
        sets.append(f"{k}=%s")
        values.append(v)
    values.append(username)
    cur.execute(f"UPDATE users SET {', '.join(sets)} WHERE username=%s", values)
    conn.commit()
    cur.close()
    conn.close()


def db_delete_user(username):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE username=%s", (username,))
    conn.commit()
    cur.close()
    conn.close()


def db_all_users():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


# ============ 任务系统数据库操作 ============
def db_task_defs_all():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, data FROM task_defs")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {r["id"]: r["data"] for r in rows}


def db_task_def_upsert(tid, data):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO task_defs (id, data) VALUES (%s, %s) "
        "ON CONFLICT (id) DO UPDATE SET data=EXCLUDED.data",
        (tid, json.dumps(data, ensure_ascii=False))
    )
    conn.commit()
    cur.close()
    conn.close()


def db_task_def_delete(tid):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM task_defs WHERE id=%s", (tid,))
    conn.commit()
    cur.close()
    conn.close()


def db_daily_get():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT data FROM daily_tasks WHERE id=1")
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return {"date": "", "tasks": []}
    return row["data"]


def db_daily_set(data):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO daily_tasks (id, data) VALUES (1, %s) "
        "ON CONFLICT (id) DO UPDATE SET data=EXCLUDED.data",
        (json.dumps(data, ensure_ascii=False),)
    )
    conn.commit()
    cur.close()
    conn.close()


# ============ 默认任务池 ============
DEFAULT_TASK_DEFS = {
    "sniper_kill_3":    {"name": "神枪手",     "desc": "用狙击枪击杀 3 名敌人",     "metric": "sniper_kills",          "target": 3, "reward": 400},
    "kill_8":           {"name": "清道夫",     "desc": "击杀 8 名敌人",             "metric": "kills",                 "target": 8, "reward": 350},
    "boss_kill":        {"name": "屠龙者",     "desc": "击杀 1 名重装Boss",         "metric": "boss_kills",            "target": 1, "reward": 600},
    "shotgun_kill_2":   {"name": "近战之王",   "desc": "用霰弹枪击杀 2 名敌人",     "metric": "shotgun_kills",         "target": 2, "reward": 300},
    "gold_5":           {"name": "淘金热",     "desc": "收集 5 个金罐头",           "metric": "gold_cans",             "target": 5, "reward": 500},
    "can_10":           {"name": "罐头收藏家", "desc": "收集 10 个普通罐头",        "metric": "cans",                  "target": 10, "reward": 300},
    "medkit_3":         {"name": "医疗储备",   "desc": "拾取 3 个医疗包",           "metric": "medkits",               "target": 3, "reward": 250},
    "no_damage_extract":{"name": "完美行动",   "desc": "不受伤成功撤离 1 次",       "metric": "no_damage_extracts",    "target": 1, "reward": 700},
    "night_extract":    {"name": "夜行者",     "desc": "在黑夜地图成功撤离 1 次",   "metric": "night_extracts",        "target": 1, "reward": 500},
    "extract_2":        {"name": "常胜将军",   "desc": "成功撤离 2 次",             "metric": "extracts",              "target": 2, "reward": 400},
    "full_backpack":    {"name": "满载而归",   "desc": "背包满时成功撤离 1 次",     "metric": "full_backpack_extracts","target": 1, "reward": 450},
}


def seed_default_tasks():
    """首次启动时把默认任务池写入 DB"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM task_defs")
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row and row["c"] == 0:
        for tid, d in DEFAULT_TASK_DEFS.items():
            db_task_def_upsert(tid, d)


# ============ 数据模型 ============
class RegisterReq(BaseModel):
    user: str
    password: str


class LoginReq(BaseModel):
    user: str
    password: str


class SaveReq(BaseModel):
    user: str
    money: int = 0
    armor: int = 0
    dmg: int = 0
    ammo: int = 0
    tasks: dict = {}


class RoomCreateReq(BaseModel):
    name: str
    password: str = ""
    host: str
    map_idx: int = 0
    is_night: bool = False
    max_players: int = 10
    host_hp: int = 100
    host_res: int = 120
    host_dmg: int = 25


class RoomJoinReq(BaseModel):
    room_id: str
    user: str
    password: str = ""
    hp: int = 100
    res: int = 120
    dmg: int = 25


class RoomLeaveReq(BaseModel):
    room_id: str
    user: str


class RoomUpdateReq(BaseModel):
    room_id: str
    user: str
    x: float
    y: float
    angle: float
    hp: int


class AdminAuth(BaseModel):
    admin_key: str


class AdminUserAdd(BaseModel):
    admin_key: str
    user: str
    password: str = "123456"
    money: int = 0
    armor: int = 0
    dmg: int = 0
    ammo: int = 0
    cans: int = 0


class AdminUserEdit(BaseModel):
    admin_key: str
    user: str
    money: int = 0
    armor: int = 0
    dmg: int = 0
    ammo: int = 0
    cans: int = 0


class AdminUserDelete(BaseModel):
    admin_key: str
    user: str


class AdminChatSend(BaseModel):
    admin_key: str
    text: str


# ============ 新增：任务系统请求模型 ============
class AdminTaskItem(BaseModel):
    id: str
    reward: int = 0


class AdminTaskSave(BaseModel):
    admin_key: str
    tasks: List[AdminTaskItem] = []


class AdminTaskUpsert(BaseModel):
    admin_key: str
    id: str
    name: str
    desc: str
    metric: str
    target: int
    reward: int


class AdminTaskDelete(BaseModel):
    admin_key: str
    id: str


# ============ 游戏 API ============
@app.post("/api/register")
def api_register(req: RegisterReq):
    if db_get_user(req.user):
        return {"success": False, "message": "用户名已被注册"}
    hashed = hashlib.sha256(req.password.encode()).hexdigest()
    ok = db_create_user(req.user, hashed)
    if ok:
        return {"success": True, "message": "注册成功"}
    return {"success": False, "message": "注册失败"}


@app.post("/api/login")
def api_login(req: LoginReq):
    u = db_get_user(req.user)
    if not u:
        return {"success": False, "message": "用户名不存在"}
    hashed = hashlib.sha256(req.password.encode()).hexdigest()
    if u["password"] != hashed:
        return {"success": False, "message": "密码错误"}
    return {
        "success": True, "message": "登录成功",
        "money": u["money"], "armor": u["armor"],
        "dmg": u["dmg"], "ammo": u["ammo"],
        "tasks": u["tasks"] or {"kill": 0, "collect": 0, "extract": 0}
    }


@app.post("/api/save")
def api_save(req: SaveReq):
    u = db_get_user(req.user)
    if not u:
        return {"success": False, "message": "用户不存在"}
    db_update_user(req.user,
                   money=req.money, armor=req.armor,
                   dmg=req.dmg, ammo=req.ammo, tasks=json.dumps(req.tasks))
    return {"success": True}


@app.get("/api/rooms")
def api_rooms():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM rooms")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    now = time.time()
    available = {}
    for row in rows:
        room = row["data"]
        players = [p for p in room.get("players", []) if now - p.get("last_update", 0) < 15]
        room["players"] = players
        if room.get("status") == "waiting" and len(players) < room.get("max_players", 10):
            available[row["id"]] = room
    return {"success": True, "rooms": available}


# ============ 新增：前台每日任务拉取 ============
@app.get("/api/daily_tasks")
def api_daily_tasks():
    cfg = db_daily_get()
    today = time.strftime("%Y-%m-%d")
    # 日期跨了自动视为空，让后台重新布置
    if cfg.get("date") != today:
        cfg = {"date": today, "tasks": []}
    defs = db_task_defs_all()
    out = []
    for t in cfg.get("tasks", []):
        tid = t.get("id")
        if tid in defs:
            d = dict(defs[tid])
            d["id"] = tid
            if "reward" in t:
                d["reward"] = t["reward"]
            out.append(d)
    return {"success": True, "date": cfg.get("date"), "tasks": out}


@app.post("/api/room/create")
def api_room_create(req: RoomCreateReq):
    room_id = f"room_{int(time.time())}_{uuid.uuid4().hex[:4]}"
    room = {
        "id": room_id, "name": req.name, "password": req.password,
        "host": req.host, "players": [{
            "name": req.host, "x": 200, "y": 200, "angle": 0,
            "hp": req.host_hp, "maxhp": req.host_hp, "dmg": req.host_dmg,
            "res": req.host_res, "last_update": time.time()
        }],
        "status": "waiting", "max_players": req.max_players,
        "created_at": time.time(), "map_idx": req.map_idx,
        "is_night": req.is_night
    }
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO rooms (id, data) VALUES (%s, %s)", (room_id, json.dumps(room)))
    conn.commit()
    cur.close()
    conn.close()
    return {"success": True, "message": "创建成功", "room_id": room_id, "room": room}


@app.post("/api/room/join")
def api_room_join(req: RoomJoinReq):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT data FROM rooms WHERE id=%s", (req.room_id,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return {"success": False, "message": "房间不存在"}
    room = row["data"]
    if room.get("password") and room["password"] != req.password:
        cur.close()
        conn.close()
        return {"success": False, "message": "密码错误"}
    for p in room.get("players", []):
        if p.get("name") == req.user:
            cur.close()
            conn.close()
            return {"success": True, "message": "已在房间", "room": room}
    if len(room.get("players", [])) >= room.get("max_players", 10):
        cur.close()
        conn.close()
        return {"success": False, "message": "房间已满"}
    room["players"].append({
        "name": req.user, "x": 300, "y": 300, "angle": 0,
        "hp": req.hp, "maxhp": req.hp, "dmg": req.dmg,
        "res": req.res, "last_update": time.time()
    })
    cur.execute("UPDATE rooms SET data=%s WHERE id=%s", (json.dumps(room), req.room_id))
    conn.commit()
    cur.close()
    conn.close()
    return {"success": True, "message": "加入成功", "room": room}


@app.post("/api/room/leave")
def api_room_leave(req: RoomLeaveReq):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT data FROM rooms WHERE id=%s", (req.room_id,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return {"success": False}
    room = row["data"]
    room["players"] = [p for p in room.get("players", []) if p.get("name") != req.user]
    if room.get("host") == req.user:
        if room.get("players"):
            room["host"] = room["players"][0].get("name")
        else:
            cur.execute("DELETE FROM rooms WHERE id=%s", (req.room_id,))
            conn.commit()
            cur.close()
            conn.close()
            return {"success": True}
    cur.execute("UPDATE rooms SET data=%s WHERE id=%s", (json.dumps(room), req.room_id))
    conn.commit()
    cur.close()
    conn.close()
    return {"success": True}


@app.post("/api/room/update")
def api_room_update(req: RoomUpdateReq):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT data FROM rooms WHERE id=%s", (req.room_id,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return {"success": False}
    room = row["data"]
    for p in room.get("players", []):
        if p.get("name") == req.user:
            p["x"] = req.x
            p["y"] = req.y
            p["angle"] = req.angle
            p["hp"] = req.hp
            p["last_update"] = time.time()
            break
    now = time.time()
    room["players"] = [p for p in room.get("players", []) if now - p.get("last_update", 0) < 15]
    cur.execute("UPDATE rooms SET data=%s WHERE id=%s", (json.dumps(room), req.room_id))
    conn.commit()
    cur.close()
    conn.close()
    return {"success": True}


@app.get("/api/room/{room_id}")
def api_room_info(room_id: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT data FROM rooms WHERE id=%s", (room_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return {"success": False, "message": "房间不存在"}
    room = row["data"]
    now = time.time()
    room["players"] = [p for p in room.get("players", []) if now - p.get("last_update", 0) < 15]
    return {"success": True, "room": room}


# ============ 管理后台 ============
@app.post("/admin/users/list")
def admin_users_list(req: AdminAuth):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    rows = db_all_users()
    result = {}
    for u in rows:
        result[u["username"]] = {
            "money": u["money"], "armor": u["armor"],
            "dmg": u["dmg"], "ammo": u["ammo"], "cans": u["cans"],
            "tasks": u["tasks"] or {"kill": 0, "collect": 0, "extract": 0}
        }
    return {"success": True, "users": result}


@app.post("/admin/user/add")
def admin_user_add(req: AdminUserAdd):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    if db_get_user(req.user):
        return {"success": False, "message": "已存在"}
    hashed = hashlib.sha256(req.password.encode()).hexdigest()
    ok = db_create_user(req.user, hashed, req.money, req.armor, req.dmg, req.ammo, req.cans)
    if ok:
        return {"success": True, "message": "添加成功"}
    return {"success": False, "message": "添加失败"}


@app.post("/admin/user/edit")
def admin_user_edit(req: AdminUserEdit):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    if not db_get_user(req.user):
        return {"success": False, "message": "用户不存在"}
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
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM chat_messages ORDER BY time ASC LIMIT 200")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    msgs = [{"user": r["username"], "text": r["text"],
             "time": r["time"], "msg_id": r["msg_id"], "type": r["type"]} for r in rows]
    return {"success": True, "messages": msgs}


@app.post("/admin/chat/clear")
def admin_chat_clear(req: AdminAuth):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM chat_messages")
    cur.execute(
        "INSERT INTO chat_messages (msg_id, username, text, time, type) VALUES (%s,%s,%s,%s,%s)",
        (f"clear_{int(time.time())}", "系统", "💬 聊天记录已被管理员清空", time.time(), "system")
    )
    conn.commit()
    cur.close()
    conn.close()
    return {"success": True, "message": "已清空"}


@app.post("/admin/chat/send")
async def admin_chat_send(req: AdminChatSend):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    msg = {
        "user": "后台管理员", "text": req.text,
        "time": time.time(),
        "msg_id": f"admin_{int(time.time()*1000)}",
        "type": "chat"
    }
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO chat_messages (msg_id, username, text, time, type) VALUES (%s,%s,%s,%s,%s)",
        (msg["msg_id"], msg["user"], msg["text"], msg["time"], msg["type"])
    )
    conn.commit()
    cur.close()
    conn.close()
    for rid in list(rooms_ws.keys()):
        await broadcast_ws(rid, msg)
    return {"success": True, "message": "发送成功"}


# ============ 新增：管理后台 - 每日任务 ============
@app.post("/admin/tasks/list")
def admin_tasks_list(req: AdminAuth):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    return {
        "success": True,
        "defs": db_task_defs_all(),
        "daily": db_daily_get()
    }


@app.post("/admin/tasks/save")
def admin_tasks_save(req: AdminTaskSave):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    defs = db_task_defs_all()
    tasks = []
    for t in req.tasks:
        if t.id in defs:
            # 奖励回写到 defs
            d = dict(defs[t.id])
            d["reward"] = int(t.reward)
            db_task_def_upsert(t.id, d)
            tasks.append({"id": t.id, "reward": int(t.reward)})
    today = time.strftime("%Y-%m-%d")
    db_daily_set({"date": today, "tasks": tasks})
    return {"success": True, "message": "已保存"}


@app.post("/admin/tasks/clear")
def admin_tasks_clear(req: AdminAuth):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    today = time.strftime("%Y-%m-%d")
    db_daily_set({"date": today, "tasks": []})
    return {"success": True, "message": "已清空"}


@app.post("/admin/task/upsert")
def admin_task_upsert(req: AdminTaskUpsert):
    """新增/修改一个任务定义"""
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    data = {
        "name": req.name,
        "desc": req.desc,
        "metric": req.metric,
        "target": int(req.target),
        "reward": int(req.reward),
    }
    db_task_def_upsert(req.id, data)
    return {"success": True, "message": "已保存"}


@app.post("/admin/task/delete")
def admin_task_delete(req: AdminTaskDelete):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    db_task_def_delete(req.id)
    # 从今日布置里也移除
    cfg = db_daily_get()
    cfg["tasks"] = [t for t in cfg.get("tasks", []) if t.get("id") != req.id]
    db_daily_set(cfg)
    return {"success": True, "message": "已删除"}


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

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM chat_messages ORDER BY time DESC LIMIT 50")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    for r in reversed(rows):
        try:
            await websocket.send_json({
                "user": r["username"], "text": r["text"],
                "time": r["time"], "msg_id": r["msg_id"], "type": r["type"]
            })
        except Exception:
            break

    await broadcast_ws(room_id, {
        "type": "system", "user": "系统",
        "text": f"{user_name} 加入了聊天",
        "time": time.time(),
        "msg_id": f"sys_{int(time.time()*1000)}_{user_name}"
    })

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            text = payload.get("text", "").strip()
            if not text:
                continue
            msg = {
                "type": "chat", "user": user_name, "text": text,
                "time": time.time(),
                "msg_id": f"{user_name}_{int(time.time()*1000)}"
            }
            conn = get_conn()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO chat_messages (msg_id, username, text, time, type) VALUES (%s,%s,%s,%s,%s)",
                (msg["msg_id"], msg["user"], msg["text"], msg["time"], msg["type"])
            )
            conn.commit()
            cur.close()
            conn.close()
            await broadcast_ws(room_id, msg)
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in rooms_ws.get(room_id, []):
            rooms_ws[room_id].remove(websocket)
        conn_user.pop(websocket, None)
        if not rooms_ws.get(room_id):
            rooms_ws.pop(room_id, None)


@app.get("/")
def root():
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))
    uvicorn.run(app, host="0.0.0.0", port=port)
