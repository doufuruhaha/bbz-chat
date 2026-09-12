import json
import os
import time
import hashlib
import uuid
import threading
from typing import Dict, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
import uvicorn

app = FastAPI()

# ============ 管理员密钥（改成你自己的） ============
ADMIN_KEY = "bbz_admin_2026_change_me"

# ============ 数据存储（JSON 文件） ============
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)

USERS_FILE = os.path.join(DATA_DIR, "users.json")
ROOMS_FILE = os.path.join(DATA_DIR, "rooms.json")
CHAT_FILE = os.path.join(DATA_DIR, "chat_history.json")

_data_lock = threading.Lock()


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_users():
    return load_json(USERS_FILE, {"users": {}})


def set_users(data):
    save_json(USERS_FILE, data)


def get_rooms():
    return load_json(ROOMS_FILE, {"rooms": {}})


def set_rooms(data):
    save_json(ROOMS_FILE, data)


def get_chat_history():
    return load_json(CHAT_FILE, {"messages": []})


def set_chat_history(data):
    save_json(CHAT_FILE, data)


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


# ============ HTTP API（游戏客户端用） ============
@app.post("/api/register")
def api_register(req: RegisterReq):
    with _data_lock:
        data = get_users()
        users = data.get("users", {})
        if req.user in users:
            return {"success": False, "message": "用户名已被注册"}
        hashed = hashlib.sha256(req.password.encode()).hexdigest()
        users[req.user] = {
            "password": hashed, "money": 0, "armor": 0, "dmg": 0, "ammo": 0, "cans": 0,
            "tasks": {"kill": 0, "collect": 0, "extract": 0}
        }
        data["users"] = users
        set_users(data)
    return {"success": True, "message": "注册成功"}


@app.post("/api/login")
def api_login(req: LoginReq):
    data = get_users()
    users = data.get("users", {})
    if req.user not in users:
        return {"success": False, "message": "用户名不存在"}
    hashed = hashlib.sha256(req.password.encode()).hexdigest()
    if users[req.user].get("password") != hashed:
        return {"success": False, "message": "密码错误"}
    u = users[req.user]
    return {
        "success": True, "message": "登录成功",
        "money": u.get("money", 0), "armor": u.get("armor", 0),
        "dmg": u.get("dmg", 0), "ammo": u.get("ammo", 0),
        "tasks": u.get("tasks", {"kill": 0, "collect": 0, "extract": 0})
    }


@app.post("/api/save")
def api_save(req: SaveReq):
    with _data_lock:
        data = get_users()
        users = data.get("users", {})
        if req.user not in users:
            return {"success": False, "message": "用户不存在"}
        users[req.user].update({
            "money": req.money, "armor": req.armor,
            "dmg": req.dmg, "ammo": req.ammo, "tasks": req.tasks
        })
        data["users"] = users
        set_users(data)
    return {"success": True}


@app.get("/api/rooms")
def api_rooms():
    data = get_rooms()
    rooms = data.get("rooms", {})
    now = time.time()
    available = {}
    for rid, room in rooms.items():
        players = [p for p in room.get("players", []) if now - p.get("last_update", 0) < 15]
        room["players"] = players
        if room.get("status") == "waiting" and len(players) < room.get("max_players", 10):
            available[rid] = room
    return {"success": True, "rooms": available}


@app.post("/api/room/create")
def api_room_create(req: RoomCreateReq):
    with _data_lock:
        data = get_rooms()
        rooms = data.get("rooms", {})
        for rid, room in rooms.items():
            if room.get("name") == req.name:
                return {"success": False, "message": "房间名已存在"}
        room_id = f"room_{int(time.time())}_{uuid.uuid4().hex[:4]}"
        rooms[room_id] = {
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
        data["rooms"] = rooms
        set_rooms(data)
    return {"success": True, "message": "创建成功", "room_id": room_id, "room": rooms[room_id]}


@app.post("/api/room/join")
def api_room_join(req: RoomJoinReq):
    with _data_lock:
        data = get_rooms()
        rooms = data.get("rooms", {})
        if req.room_id not in rooms:
            return {"success": False, "message": "房间不存在"}
        room = rooms[req.room_id]
        if room.get("password") and room["password"] != req.password:
            return {"success": False, "message": "密码错误"}
        if len(room.get("players", [])) >= room.get("max_players", 10):
            return {"success": False, "message": "房间已满"}
        for p in room.get("players", []):
            if p.get("name") == req.user:
                return {"success": True, "message": "已在房间", "room": room}
        room["players"].append({
            "name": req.user, "x": 300, "y": 300, "angle": 0,
            "hp": req.hp, "maxhp": req.hp, "dmg": req.dmg,
            "res": req.res, "last_update": time.time()
        })
        data["rooms"] = rooms
        set_rooms(data)
    return {"success": True, "message": "加入成功", "room": room}


@app.post("/api/room/leave")
def api_room_leave(req: RoomLeaveReq):
    with _data_lock:
        data = get_rooms()
        rooms = data.get("rooms", {})
        if req.room_id not in rooms:
            return {"success": False}
        room = rooms[req.room_id]
        room["players"] = [p for p in room.get("players", []) if p.get("name") != req.user]
        if room.get("host") == req.user:
            if room.get("players"):
                room["host"] = room["players"][0].get("name")
            else:
                del rooms[req.room_id]
                data["rooms"] = rooms
                set_rooms(data)
                return {"success": True}
        data["rooms"] = rooms
        set_rooms(data)
    return {"success": True}


@app.post("/api/room/update")
def api_room_update(req: RoomUpdateReq):
    with _data_lock:
        data = get_rooms()
        rooms = data.get("rooms", {})
        if req.room_id not in rooms:
            return {"success": False}
        room = rooms[req.room_id]
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
        data["rooms"] = rooms
        set_rooms(data)
    return {"success": True}


@app.get("/api/room/{room_id}")
def api_room_info(room_id: str):
    data = get_rooms()
    rooms = data.get("rooms", {})
    if room_id not in rooms:
        return {"success": False, "message": "房间不存在"}
    room = rooms[room_id]
    now = time.time()
    room["players"] = [p for p in room.get("players", []) if now - p.get("last_update", 0) < 15]
    return {"success": True, "room": room}


# ============ 管理后台 API ============
@app.post("/admin/users/list")
def admin_users_list(req: AdminAuth):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    data = get_users()
    users = data.get("users", {})
    result = {}
    for name, u in users.items():
        result[name] = {
            "money": u.get("money", 0),
            "armor": u.get("armor", 0),
            "dmg": u.get("dmg", 0),
            "ammo": u.get("ammo", 0),
            "cans": u.get("cans", 0),
            "tasks": u.get("tasks", {"kill": 0, "collect": 0, "extract": 0})
        }
    return {"success": True, "users": result}


@app.post("/admin/user/add")
def admin_user_add(req: AdminUserAdd):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    with _data_lock:
        data = get_users()
        users = data.get("users", {})
        if req.user in users:
            return {"success": False, "message": "已存在"}
        hashed = hashlib.sha256(req.password.encode()).hexdigest()
        users[req.user] = {
            "password": hashed,
            "money": req.money, "armor": req.armor, "dmg": req.dmg, "ammo": req.ammo, "cans": req.cans,
            "tasks": {"kill": 0, "collect": 0, "extract": 0}
        }
        data["users"] = users
        set_users(data)
    return {"success": True, "message": "添加成功"}


@app.post("/admin/user/edit")
def admin_user_edit(req: AdminUserEdit):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    with _data_lock:
        data = get_users()
        users = data.get("users", {})
        if req.user not in users:
            return {"success": False, "message": "用户不存在"}
        users[req.user].update({
            "money": req.money, "armor": req.armor,
            "dmg": req.dmg, "ammo": req.ammo, "cans": req.cans
        })
        data["users"] = users
        set_users(data)
    return {"success": True, "message": "修改成功"}


@app.post("/admin/user/delete")
def admin_user_delete(req: AdminUserDelete):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    with _data_lock:
        data = get_users()
        users = data.get("users", {})
        if req.user in users:
            del users[req.user]
        data["users"] = users
        set_users(data)
    return {"success": True, "message": "删除成功"}


@app.post("/admin/chat/list")
def admin_chat_list(req: AdminAuth):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    hist = get_chat_history().get("messages", [])
    return {"success": True, "messages": hist}


@app.post("/admin/chat/clear")
def admin_chat_clear(req: AdminAuth):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    with _data_lock:
        set_chat_history({"messages": [{
            "user": "系统", "text": "💬 聊天记录已被管理员清空",
            "time": time.time(), "msg_id": f"clear_{int(time.time())}"
        }]})
    return {"success": True, "message": "已清空"}


@app.post("/admin/chat/send")
async def admin_chat_send(req: AdminChatSend):
    if req.admin_key != ADMIN_KEY:
        return {"success": False, "message": "密钥错误"}
    msg = {
        "user": "后台管理员", "text": req.text,
        "time": time.time(),
        "msg_id": f"admin_{int(time.time()*1000)}"
    }
    with _data_lock:
        h = get_chat_history()
        h["messages"].append(msg)
        if len(h["messages"]) > 200:
            h["messages"] = h["messages"][-200:]
        set_chat_history(h)
    for rid in list(rooms_ws.keys()):
        await broadcast_ws(rid, msg)
    return {"success": True, "message": "发送成功"}


# ============ WebSocket 聊天 ============
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

    hist = get_chat_history().get("messages", [])[-50:]
    for m in hist:
        try:
            await websocket.send_json(m)
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
            with _data_lock:
                h = get_chat_history()
                h["messages"].append(msg)
                if len(h["messages"]) > 200:
                    h["messages"] = h["messages"][-200:]
                set_chat_history(h)
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
