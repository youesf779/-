#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔════════════════════════════════════════════════════════════════╗
║            𝑺𝑰𝑫𝑹𝑨 Bot — NanoBanana PRO  v4  (FIXED)          ║
╚════════════════════════════════════════════════════════════════╝
  ✅ txt2img  → POST /generate  (prompt + aspect_ratio)
  ✅ img2img  → POST /edit      (prompt + image_urls  max 5)
  ✅ Auto-save file_id to library on every generation
  ✅ Export users  (/admin → 📥 Export Users)
  ✅ No proxies
  ✅ Single message UI
  ✅ My Library with navigation
  ✅ Admin panel

  🔧 FIXES v4.1:
     - _api_generate: flexible response parsing (image_url / imageUrl / url / output / images[])
     - _api_edit: send BOTH image_url (singular) AND image_urls (plural) for max compatibility
     - Better timeout error logging (Timeout vs generic Exception)
     - All API errors now log the full response body for debugging
"""

import base64
import io
import json
import logging
import os
import random
import re
import string
import threading
import time
from datetime import datetime, timezone

import requests
import telebot
from telebot import types

# ================================================================
#  ⚙️  MAIN CONFIG
# ================================================================
BOT_TOKEN  = "8188915534:AAFKj8mwdaQasQGH-7Z0yaK70YgPkPP9tY0"
OWNER_URL  = "https://t.me/Ok_Sidra"
DATA_FILE  = "users.json"

ADMIN_IDS: list[int] = [7902097354]

# ── NanoBanana PRO API ─────────────────────────────────────────
NANABANA_API = "https://nanobananapro-api.up.railway.app"

ASPECTS = ["1:1", "16:9", "9:16", "4:3", "3:4", "4:5", "5:4", "2:3", "3:2", "21:9"]

MAX_IMAGES      = 5
UPLOAD_DEBOUNCE = 2.0
BROADCAST_DELAY = 0.05

HOME_IMAGE_FILE = "home.jpg"
HOME_IMAGE_URL  = "https://i.ibb.co/RTZPDPJV/photo-2026-02-19-14-01-01.jpg"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)

FORCE_CHANNELS: list[dict] = [
    {"id": -1002245110782, "name": "𝑺𝑨𝑹𝟏𝟑™", "url": "https://t.me/x1_v5"},
]

# ================================================================
#  🖼  HOME IMAGE CACHE
# ================================================================
_home_file_id: str | None = None
_home_bytes:   bytes | None = None

def _load_home_image() -> bytes | None:
    global _home_bytes
    if _home_bytes:
        return _home_bytes
    if os.path.exists(HOME_IMAGE_FILE):
        with open(HOME_IMAGE_FILE, "rb") as f:
            _home_bytes = f.read()
        return _home_bytes
    try:
        r = requests.get(HOME_IMAGE_URL, timeout=20)
        r.raise_for_status()
        _home_bytes = r.content
        with open(HOME_IMAGE_FILE, "wb") as f:
            f.write(_home_bytes)
        return _home_bytes
    except Exception as ex:
        log.error("Failed to load home image: %s", ex)
        return None

# ================================================================
#  💾  DATA STORAGE
# ================================================================
_lock = threading.Lock()

def _load() -> dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def _save(data: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _defaults() -> dict:
    return {
        "state":              "idle",
        "home_msg_id":        None,
        "display_name":       "User",
        "username":           None,
        "library":            [],
        "lib_index":          0,
        "upload_msg_id":      None,
        "is_new":             True,
        "pending_prompt_t2i": None,
        "aspect_msg_id":      None,
        "pro_mode":           False,
        "pro_reply_chat_id":  None,
        "pro_reply_msg_id":   None,
    }

def get_user(uid: int) -> dict:
    key = str(uid)
    with _lock:
        d = _load()
        if key not in d:
            d[key] = _defaults()
            _save(d)
        u = d[key]
        for k, v in _defaults().items():
            if k not in u:
                u[k] = v
        return dict(u)

def set_user(uid: int, **kw):
    key = str(uid)
    with _lock:
        d = _load()
        if key not in d:
            d[key] = _defaults()
        d[key].update(kw)
        _save(d)

def is_new_user(uid: int) -> bool:
    key = str(uid)
    with _lock:
        d = _load()
        if key not in d:
            return True
        return d[key].get("is_new", True)

def mark_user_seen(uid: int):
    key = str(uid)
    with _lock:
        d = _load()
        if key not in d:
            d[key] = _defaults()
        d[key]["is_new"] = False
        _save(d)

def add_to_library(uid: int, prompt: str, file_id: str | None = None):
    key = str(uid)
    with _lock:
        d = _load()
        if key not in d:
            d[key] = _defaults()
        lib = d[key].get("library", [])
        lib.append({
            "prompt":  prompt,
            "ts":      time.time(),
            "file_id": file_id,
            "uid":     uid,
        })
        d[key]["library"]   = lib
        d[key]["lib_index"] = len(lib) - 1
        _save(d)

def update_library_file_id(uid: int, index: int, file_id: str):
    key = str(uid)
    with _lock:
        d = _load()
        if key in d:
            lib = d[key].get("library", [])
            if 0 <= index < len(lib):
                lib[index]["file_id"] = file_id
                d[key]["library"] = lib
                _save(d)

def get_all_images() -> list[dict]:
    with _lock:
        d = _load()
    all_imgs = []
    for k, v in d.items():
        if k.startswith("__") or not k.isdigit():
            continue
        name = v.get("display_name", "User")
        for img in v.get("library", []):
            all_imgs.append({
                "prompt":       img.get("prompt", ""),
                "ts":           img.get("ts", 0),
                "file_id":      img.get("file_id"),
                "uid":          int(k),
                "display_name": name,
            })
    all_imgs.sort(key=lambda x: x["ts"], reverse=True)
    return all_imgs

def _get_cfg(key: str, default=None):
    with _lock:
        d = _load()
        return d.get("__cfg__", {}).get(key, default)

def _set_cfg(key: str, value):
    with _lock:
        d = _load()
        if "__cfg__" not in d:
            d["__cfg__"] = {}
        d["__cfg__"][key] = value
        _save(d)

_model_status = {"txt2img": True, "img2img": True}

def _load_model_status():
    _model_status["txt2img"] = _get_cfg("txt2img_enabled", True)
    _model_status["img2img"] = _get_cfg("img2img_enabled", True)

def _save_model_status():
    _set_cfg("txt2img_enabled", _model_status["txt2img"])
    _set_cfg("img2img_enabled", _model_status["img2img"])

def _force_enabled() -> bool:
    return bool(_get_cfg("force_enabled", True))

def _set_force_enabled(val: bool):
    _set_cfg("force_enabled", val)

def _get_stats() -> dict:
    with _lock:
        d = _load()
    users      = [k for k in d if not k.startswith("__")]
    total_imgs = sum(len(d[k].get("library", [])) for k in users)
    active     = sum(1 for k in users if d[k].get("library"))
    return {"total_users": len(users), "active_users": active,
            "total_images": total_imgs}

def _get_all_user_ids() -> list[int]:
    with _lock:
        d = _load()
    return [int(k) for k in d if not k.startswith("__") and k.isdigit()]

# ================================================================
#  📤  EXPORT USERS
# ================================================================
def _export_users_file() -> bytes:
    with _lock:
        d = _load()
    lines = []
    lines.append("# قائمة المستخدمين\n")
    lines.append(f"# تاريخ التصدير: {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")
    lines.append("-" * 40 + "\n\n")
    count = 0
    for k, v in d.items():
        if k.startswith("__") or not k.isdigit():
            continue
        name     = v.get("display_name", "User")
        username = v.get("username")
        uid      = k
        imgs     = len(v.get("library", []))
        label    = f"@{username}" if username else name
        lines.append(f"{label}: {uid}  [{imgs} images]\n")
        count += 1
    lines.append(f"\n# إجمالي: {count} مستخدم\n")
    return "".join(lines).encode("utf-8")

# ================================================================
#  🗂️  ADMIN STATE
# ================================================================
_admin_states: dict[int, dict] = {}

def _admin_set(uid: int, step: str | None = None, **data):
    if step is None:
        _admin_states.pop(uid, None)
    else:
        _admin_states[uid] = {"step": step, **data}

def _admin_get(uid: int) -> dict | None:
    return _admin_states.get(uid)

_admin_glib_index: dict[int, int] = {}

# ================================================================
#  📦  IN-MEMORY IMAGE STORE + DEBOUNCE
# ================================================================
_img_store:     dict[int, list[tuple[str, bytes]]] = {}
_upload_timers: dict[int, threading.Timer]          = {}

def _cancel_upload_timer(uid: int):
    t = _upload_timers.pop(uid, None)
    if t:
        t.cancel()

def _fire_upload_counter(chat_id: int, uid: int):
    _upload_timers.pop(uid, None)
    imgs      = _img_store.get(uid, [])
    uploaded  = len(imgs)
    if uploaded == 0:
        return
    remaining = MAX_IMAGES - uploaded
    old_id    = get_user(uid).get("upload_msg_id")
    if old_id:
        _delete_message(chat_id, old_id)
    new_id = _send_message_raw(chat_id, cap_upload_counter(uploaded, remaining))
    set_user(uid, upload_msg_id=new_id, state="waiting_img2img_prompt")

def _schedule_upload_counter(chat_id: int, uid: int):
    _cancel_upload_timer(uid)
    t = threading.Timer(UPLOAD_DEBOUNCE, _fire_upload_counter,
                        args=(chat_id, uid))
    t.daemon = True
    t.start()
    _upload_timers[uid] = t

# ================================================================
#  ✨  PREMIUM EMOJI HELPER
# ================================================================
def tge(eid: str, fb: str) -> str:
    return f'<tg-emoji emoji-id="{eid}">{fb}</tg-emoji>'

H1   = lambda: tge("6298356878573307709", "❤️")
H2   = lambda: tge("6298505110779594363", "❤️")
FR   = lambda: tge("5409143496902716934", "🖼")
CK   = lambda: tge("6296577138615125756", "✅")
ST   = lambda: tge("5409101350388643254", "🌟")
KS   = lambda: tge("6298412927896520857", "😘")
SM   = lambda: tge("6298440608960742886", "😄")
CR1  = lambda: tge("6298671811345254603", "😭")
HND  = lambda: tge("5408900479063175258", "✋")
PSN  = lambda: tge("5870695289714643076", "👤")
PIN  = lambda: tge("5409337058193847247", "📌")
HL   = lambda: tge("5913704697079799593", "❤️")
CLK  = lambda: tge("5408910404732595664", "🕐")
UPL  = lambda: tge("5195149092136710159", "⭐")
HND2 = lambda: tge("5208467927455523557", "🤚")

# ================================================================
#  🎹  KEYBOARD BUILDER
# ================================================================
def _btn(text: str, *, cb: str | None = None, url: str | None = None,
         icon: str | None = None) -> dict:
    b: dict = {"text": text}
    if cb:   b["callback_data"] = cb
    if url:  b["url"] = url
    if icon: b["icon_custom_emoji_id"] = icon
    return b

def _markup(rows: list) -> str:
    return json.dumps({"inline_keyboard": rows}, ensure_ascii=False)

ICON_HEART   = "5913304638056045068"
ICON_LIBRARY = "5409143496902716934"
ICON_OWNER   = "5409194306365829029"
ICON_HOME    = "5873147866364514353"
ICON_NEXT    = "5456119091817384146"
ICON_BACK    = "5454404927419877032"

# ================================================================
#  ⌨️  KEYBOARDS — USER
# ================================================================
def kb_home() -> str:
    return _markup([
        [_btn("𝒕𝒆𝒙𝒕 𝒕𝒐 𝒊𝒎𝒂𝒈𝒆", cb="TXT2IMG", icon=ICON_HEART),
         _btn("𝒊𝒎𝒂𝒈𝒆 𝒕𝒐 𝒊𝒎𝒂𝒈𝒆", cb="IMG2IMG", icon=ICON_HEART)],
        [_btn("𝑴𝒚 𝑳𝒊𝒃𝒓𝒂𝒓𝒚", cb="LIBRARY",  icon=ICON_LIBRARY)],
        [_btn("𝒕𝒉𝒆 𝒐𝒘𝒆𝒓",   url=OWNER_URL, icon=ICON_OWNER)],
    ])

def kb_txt2img() -> str:
    return _markup([[_btn("𝑯𝒐𝒎𝒆", cb="HOME", icon=ICON_HOME)]])

def kb_img2img() -> str:
    return _markup([[_btn("𝑯𝒐𝒎𝒆", cb="HOME", icon=ICON_HOME)]])

def kb_error() -> str:
    return _markup([
        [_btn("𝒕𝒉𝒆 𝒐𝒘𝒆𝒓", url=OWNER_URL, icon=ICON_OWNER)],
        [_btn("𝑯𝒐𝒎𝒆",     cb="HOME",     icon=ICON_HOME)],
    ])

def kb_library(prev: bool, nxt: bool) -> str:
    rows: list = []
    nav = []
    if prev: nav.append(_btn("𝑩𝒂𝒄𝒌", cb="LIB_BACK", icon=ICON_BACK))
    if nxt:  nav.append(_btn("𝑵𝒆𝒙𝒕", cb="LIB_NEXT", icon=ICON_NEXT))
    if nav:  rows.append(nav)
    rows.append([_btn("𝑯𝒐𝒎𝒆", cb="HOME", icon=ICON_HOME)])
    return _markup(rows)

def kb_success() -> str:
    return _markup([
        [_btn("𝑴𝒚 𝑳𝒊𝒃𝒓𝒂𝒓𝒚", cb="LIBRARY", icon=ICON_LIBRARY)],
        [_btn("𝑯𝒐𝒎𝒆",         cb="HOME",    icon=ICON_HOME)],
    ])

def kb_disabled() -> str:
    return _markup([[_btn("𝑯𝒐𝒎𝒆", cb="HOME", icon=ICON_HOME)]])

def kb_force_channel() -> str:
    rows: list = []
    for ch in FORCE_CHANNELS:
        rows.append([_btn(ch["name"], url=ch["url"])])
    rows.append([_btn("𝑽𝒆𝒓𝒊𝒇𝒚", cb="FC_VERIFY",
                       icon="6296577138615125756")])
    return _markup(rows)

def kb_aspect_selection() -> str:
    rows: list = []
    row:  list = []
    for i, asp in enumerate(ASPECTS):
        row.append(_btn(asp, cb=f"ASPECT:{asp}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([_btn("𝑯𝒐𝒎𝒆", cb="HOME", icon=ICON_HOME)])
    return _markup(rows)

# ================================================================
#  ⌨️  KEYBOARDS — ADMIN
# ================================================================
def kb_admin() -> str:
    return _markup([
        [_btn("📊 𝑺𝒕𝒂𝒕𝒔",        cb="ADM:STATS"),
         _btn("📢 𝑪𝒂𝒔𝒕",          cb="ADM:CAST")],
        [_btn("💌 𝑷𝒓𝒗 𝑪𝒂𝒔𝒕",     cb="ADM:PRV_CAST"),
         _btn("📨 𝑭𝒘𝒅 𝑪𝒂𝒔𝒕",    cb="ADM:FWD_CAST")],
        [_btn("🔒 𝑪𝒍𝒐𝒔𝒆 𝑴𝒐𝒅𝒆𝒍", cb="ADM:CLOSE_MODEL")],
        [_btn("📌 𝑭𝒐𝒓𝒄𝒆 𝑪𝒉𝒂𝒏𝒏𝒆𝒍", cb="ADM:FORCE_CH")],
        [_btn("🖼 𝑨𝒍𝒍 𝑳𝒊𝒃𝒓𝒂𝒓𝒚",  cb="ADM:ALL_LIB")],
        [_btn("📥 𝑬𝒙𝒑𝒐𝒓𝒕 𝑼𝒔𝒆𝒓𝒔", cb="ADM:EXPORT")],
        [_btn("✖️ 𝑪𝒍𝒐𝒔𝒆",         cb="ADM:CLOSE")],
    ])

def kb_admin_model_status() -> str:
    t2i = _model_status["txt2img"]
    i2i = _model_status["img2img"]
    return _markup([
        [_btn(f"{'✅' if t2i else '❌'} 𝑻𝒙𝒕→𝑰𝒎𝒈", cb="ADM:TOGGLE_T2I"),
         _btn(f"{'✅' if i2i else '❌'} 𝑰𝒎𝒈→𝑰𝒎𝒈", cb="ADM:TOGGLE_I2I")],
        [_btn("𝑩𝒂𝒄𝒌", cb="ADM:BACK", icon=ICON_BACK)],
    ])

def kb_admin_force_ch() -> str:
    enabled = _force_enabled()
    toggle  = _btn(
        f"{'🟢 𝑫𝒊𝒔𝒂𝒃𝒍𝒆' if enabled else '🔴 𝑬𝒏𝒂𝒃𝒍𝒆'}",
        cb="ADM:TOGGLE_FORCE",
    )
    return _markup([[toggle], [_btn("𝑩𝒂𝒄𝒌", cb="ADM:BACK",
                                    icon=ICON_BACK)]])

def kb_admin_glib(prev: bool, nxt: bool, total: int) -> str:
    rows: list = []
    nav = []
    if prev: nav.append(_btn("𝑩𝒂𝒄𝒌", cb="ADM:GLIB_BACK", icon=ICON_BACK))
    if nxt:  nav.append(_btn("𝑵𝒆𝒙𝒕", cb="ADM:GLIB_NEXT", icon=ICON_NEXT))
    if nav:  rows.append(nav)
    rows.append([_btn("𝑨𝒅𝒎𝒊𝒏", cb="ADM:BACK", icon=ICON_BACK)])
    return _markup(rows)

def cap_admin_back() -> str:
    return _markup([[_btn("𝑩𝒂𝒄𝒌", cb="ADM:BACK", icon=ICON_BACK)]])

# ================================================================
#  💬  CAPTIONS
# ================================================================
def cap_home(uid: int = 0, name: str = "User") -> str:
    safe = name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        f'{H1()}-𝑾𝑬𝑳𝑪𝑶𝑴𝑬 <a href="tg://user?id={uid}">{safe}</a>\n'
        f"{H2()}-𝒀𝒐𝒖 𝒄𝒂𝒏 𝒈𝒆𝒏𝒓𝒂𝒕𝒆 &amp; 𝒆𝒅𝒊𝒕 𝒖𝒏𝒍𝒊𝒎𝒊𝒕𝒆𝒅 𝒊𝒎𝒂𝒈𝒆𝒔\n"
        f"{H1()}-𝒘𝒆 𝒖𝒔𝒆 𝒐𝒏𝒍𝒚 𝒕𝒉𝒆 𝒃𝒆𝒔𝒕 𝒎𝒐𝒅𝒆𝒍𝒔 𝒏𝒂𝒏𝒐 𝒃𝒂𝒏𝒂𝒏𝒂\n"
        f"{H2()}-𝑶𝑼𝑹 𝑺𝑰𝑻𝑬 𝑺𝑶𝑶𝑵"
    )

def cap_txt2img() -> str:
    return (
        f"{H1()}-𝑺𝒆𝒏𝒅 𝒕𝒉𝒆 𝒑𝒓𝒐𝒎𝒑𝒕 𝒚𝒐𝒖 𝒘𝒂𝒏𝒕 𝒕𝒐 𝒖𝒔𝒆 𝒕𝒐 𝒈𝒆𝒏𝒆𝒓𝒂𝒕𝒆 𝒕𝒉𝒆 𝒊𝒎𝒂𝒈𝒆.\n\n"
        f"{CK()}-𝑴𝒂𝒙 𝒍𝒆𝒏𝒈𝒕𝒉: 𝟐𝟎𝟎𝟎 𝒄𝒉𝒂𝒓𝒂𝒄𝒕𝒆𝒓𝒔."
    )

def cap_img2img() -> str:
    return (
        f"{H1()}-𝑺𝒆𝒏𝒅 𝒂 𝒑𝒉𝒐𝒕𝒐 𝒘𝒊𝒕𝒉 𝒂 𝒄𝒂𝒑𝒕𝒊𝒐𝒏 𝒅𝒆𝒔𝒄𝒓𝒊𝒃𝒊𝒏𝒈 𝒕𝒉𝒆 𝒆𝒅𝒊𝒕.\n\n"
        f"{CK()}-𝑺𝒆𝒏𝒅 𝒚𝒐𝒖𝒓 𝒊𝒎𝒂𝒈𝒆 + 𝒄𝒂𝒑𝒕𝒊𝒐𝒏 (𝒑𝒓𝒐𝒎𝒑𝒕) 𝒕𝒐𝒈𝒆𝒕𝒉𝒆𝒓.\n\n"
        f"{ST()}-𝒀𝒐𝒖 𝒄𝒂𝒏 𝒖𝒑𝒍𝒐𝒂𝒅 𝒖𝒑 𝒕𝒐 𝟓 𝒊𝒎𝒂𝒈𝒆𝒔."
    )

def cap_generating() -> str:
    return (
        f"𝑮𝑬𝑵𝑹𝑨𝑻𝑰𝑵𝑮 𝑰𝑴𝑨𝑮𝑬 {KS()}\n"
        f"                          ⁿᵃⁿᵃⁿᵃ{SM()}"
    )

def cap_wait() -> str:
    return f"{PIN()}-𝑻𝒉𝒊𝒔 𝒕𝒂𝒔𝒌 𝒘𝒊𝒍𝒍 𝒕𝒂𝒌𝒆 𝟏 𝒎𝒊𝒏𝒖𝒕𝒆… 𝒑𝒍𝒆𝒂𝒔𝒆 𝒘𝒂𝒊𝒕{CLK()}"

def cap_error() -> str:
    return (
        f"{CR1()} 𝑺𝑶𝑴𝑬𝑻𝑯𝑰𝑵𝑮 𝑾𝑬𝑵𝑻 𝑾𝑹𝑶𝑵𝑮 {CR1()}\n\n"
        f"{HND()}𝑻𝒓𝒚 𝒂𝒈𝒂𝒊𝒏 𝒐𝒓 𝒄𝒐𝒏𝒕𝒂𝒄𝒕 𝒕𝒉𝒆 𝒐𝒘𝒏𝒆𝒓.{PSN()}"
    )

def cap_success(uid: int, name: str) -> str:
    safe = name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        f"{FR()}𝒊𝒎𝒂𝒈𝒆 𝒔𝒖𝒄𝒄𝒆𝒆𝒅𝒆𝒅 𝒈𝒆𝒏{FR()}\n"
        f'{PIN()}𝑰𝒕 𝒈𝒆𝒏𝒆𝒓𝒂𝒕𝒆𝒅 𝒃𝒚 <a href="tg://user?id={uid}">{safe}</a>'
    )

def cap_library(index: int, total: int, prompt: str) -> str:
    return (
        f"{HL()}𝑴𝒚 𝑳𝒊𝒃𝒓𝒂𝒓𝒚{HL()}\n\n"
        f"{PIN()} [{index + 1}/{total}]\n"
        f"<i>{prompt[:200]}</i>"
    )

def cap_library_empty() -> str:
    return (
        f"{HL()}𝑴𝒚 𝑳𝒊𝒃𝒓𝒂𝒓𝒚{HL()}\n\n"
        "📭 𝒀𝒐𝒖𝒓 𝒍𝒊𝒃𝒓𝒂𝒓𝒚 𝒊𝒔 𝒆𝒎𝒑𝒕𝒚 — 𝒈𝒆𝒏𝒆𝒓𝒂𝒕𝒆 𝒚𝒐𝒖𝒓 𝒇𝒊𝒓𝒔𝒕 𝒊𝒎𝒂𝒈𝒆!"
    )

def cap_upload_counter(uploaded: int, remaining: int) -> str:
    return (
        f"{UPL()}- 𝒘𝒆 𝒖𝒑𝒍𝒐𝒂𝒅𝒆𝒅 {uploaded} 𝒊𝒎𝒂𝒈𝒆𝒔 "
        f"𝒚𝒐𝒖 𝒄𝒂𝒏 𝒔𝒆𝒏𝒅 {remaining} 𝒎𝒐𝒓𝒆 {UPL()}"
    )

def cap_feature_disabled() -> str:
    return (
        f"{CR1()} 𝑻𝒉𝒊𝒔 𝒇𝒆𝒂𝒕𝒖𝒓𝒆 𝒊𝒔 𝒕𝒆𝒎𝒑𝒐𝒓𝒂𝒓𝒊𝒍𝒚 𝒅𝒊𝒔𝒂𝒃𝒍𝒆𝒅 {CR1()}\n\n"
        f"{HND()} 𝑷𝒍𝒆𝒂𝒔𝒆 𝒘𝒂𝒊𝒕 𝒖𝒏𝒕𝒊𝒍 𝒊𝒕 𝒊𝒔 𝒓𝒆𝒔𝒕𝒐𝒓𝒆𝒅."
    )

def cap_force_channel() -> str:
    return (
        f"{UPL()}-𝒘𝒆𝒍𝒄𝒐𝒎𝒆 𝒕𝒐 𝒏𝒂𝒏𝒐 𝒃𝒂𝒏𝒂𝒏𝒂 𝒊𝒎𝒂𝒈𝒆 𝒃𝒐𝒕\n"
        f"{UPL()}-𝒚𝒐𝒖 𝒉𝒂𝒗𝒆 𝒕𝒐 𝒋𝒐𝒊𝒏 𝒉𝒆𝒓𝒆 𝒇𝒊𝒓𝒔𝒕 {HND2()}"
    )

def cap_aspect_selection(prompt: str) -> str:
    safe = prompt[:200].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        f"{ST()}-𝑺𝒆𝒕𝒕𝒊𝒏𝒈𝒔 — 𝑪𝒉𝒐𝒐𝒔𝒆 𝒕𝒉𝒆 𝒊𝒎𝒂𝒈𝒆 𝒔𝒊𝒛𝒆:\n\n"
        f"{PIN()} <i>{safe}</i>"
    )

def cap_admin() -> str:
    return f"{ST()}𝑨𝒅𝒎𝒊𝒏 𝑷𝒂𝒏𝒆𝒍{ST()}\n\n{PIN()} 𝑺𝒆𝒍𝒆𝒄𝒕 𝒂𝒏 𝒐𝒑𝒕𝒊𝒐𝒏:"

def cap_admin_stats() -> str:
    s = _get_stats()
    return (
        f"{ST()}𝑺𝒕𝒂𝒕𝒊𝒔𝒕𝒊𝒄𝒔{ST()}\n\n"
        f"{PIN()} 𝑻𝒐𝒕𝒂𝒍 𝑼𝒔𝒆𝒓𝒔: <b>{s['total_users']}</b>\n"
        f"{CK()} 𝑨𝒄𝒕𝒊𝒗𝒆 𝑼𝒔𝒆𝒓𝒔: <b>{s['active_users']}</b>\n"
        f"{FR()} 𝑻𝒐𝒕𝒂𝒍 𝑰𝒎𝒂𝒈𝒆𝒔: <b>{s['total_images']}</b>"
    )

def cap_admin_model() -> str:
    t2i = "✅ 𝑬𝒏𝒂𝒃𝒍𝒆𝒅" if _model_status["txt2img"] else "❌ 𝑫𝒊𝒔𝒂𝒃𝒍𝒆𝒅"
    i2i = "✅ 𝑬𝒏𝒂𝒃𝒍𝒆𝒅" if _model_status["img2img"] else "❌ 𝑫𝒊𝒔𝒂𝒃𝒍𝒆𝒅"
    return (
        f"{ST()}𝑴𝒐𝒅𝒆𝒍 𝑺𝒕𝒂𝒕𝒖𝒔{ST()}\n\n"
        f"{PIN()} 𝑻𝒆𝒙𝒕 𝒕𝒐 𝑰𝒎𝒂𝒈𝒆: {t2i}\n"
        f"{PIN()} 𝑰𝒎𝒂𝒈𝒆 𝒕𝒐 𝑰𝒎𝒂𝒈𝒆: {i2i}"
    )

def cap_admin_force_ch() -> str:
    status   = "🟢 𝑬𝒏𝒂𝒃𝒍𝒆𝒅" if _force_enabled() else "🔴 𝑫𝒊𝒔𝒂𝒃𝒍𝒆𝒅"
    ch_count = len(FORCE_CHANNELS)
    return (
        f"{ST()}𝑭𝒐𝒓𝒄𝒆 𝑪𝒉𝒂𝒏𝒏𝒆𝒍{ST()}\n\n"
        f"{PIN()} 𝑺𝒕𝒂𝒕𝒖𝒔: {status}\n"
        f"{CK()} 𝑪𝒉𝒂𝒏𝒏𝒆𝒍𝒔: <b>{ch_count}</b>"
    )

def cap_admin_glib(index: int, total: int, item: dict) -> str:
    prompt  = item.get("prompt", "")
    uid_str = item.get("uid", "?")
    name    = item.get("display_name", "User")
    ts      = item.get("ts", 0)
    dt_str  = datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M") if ts else "—"
    safe = name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        f"{FR()}𝑨𝒍𝒍 𝑳𝒊𝒃𝒓𝒂𝒓𝒚 — [{index + 1}/{total}]{FR()}\n\n"
        f'{PSN()} <a href="tg://user?id={uid_str}">{safe}</a> '
        f"(<code>{uid_str}</code>)\n"
        f"{CLK()} {dt_str}\n\n"
        f"<i>{prompt[:250]}</i>"
    )

def cap_admin_glib_empty() -> str:
    return (
        f"{FR()}𝑨𝒍𝒍 𝑳𝒊𝒃𝒓𝒂𝒓𝒚{FR()}\n\n"
        "📭 𝑵𝒐 𝒊𝒎𝒂𝒈𝒆𝒔 𝒉𝒂𝒗𝒆 𝒃𝒆𝒆𝒏 𝒈𝒆𝒏𝒆𝒓𝒂𝒕𝒆𝒅 𝒚𝒆𝒕."
    )

# ================================================================
#  🔔  ADMIN NEW USER NOTIFICATION
# ================================================================
def _notify_admins_new_user(uid: int, name: str, username: str | None):
    if not ADMIN_IDS:
        return
    safe  = name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    uname = f"@{username}" if username else "—"
    text  = (
        f"{ST()} 𝑵𝒆𝒘 𝑼𝒔𝒆𝒓 𝑱𝒐𝒊𝒏𝒆𝒅! {ST()}\n\n"
        f'{PSN()} <a href="tg://user?id={uid}">{safe}</a>\n'
        f"{PIN()} 𝑰𝑫: <code>{uid}</code>\n"
        f"{CK()} 𝑼𝒔𝒆𝒓𝒏𝒂𝒎𝒆: {uname}"
    )
    for admin_id in ADMIN_IDS:
        try:
            _send_message_raw(admin_id, text)
        except Exception as ex:
            log.warning("Admin notify failed: %s", ex)

# ================================================================
#  📡  RAW API HELPERS
# ================================================================
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

def _api(method: str, **kwargs) -> dict:
    try:
        r = requests.post(f"{API_BASE}/{method}", json=kwargs, timeout=30)
        return r.json()
    except Exception as ex:
        log.error("API %s error: %s", method, ex)
        return {"ok": False}

def _send_photo_raw(
    chat_id: int, photo, caption: str, markup_json: str,
    reply_to_message_id: int | None = None,
) -> tuple[int | None, str | None]:
    markup_dict = json.loads(markup_json)
    d: dict = {}
    if isinstance(photo, bytes):
        try:
            extra = {}
            if reply_to_message_id:
                extra["reply_to_message_id"] = str(reply_to_message_id)
            r = requests.post(
                f"{API_BASE}/sendPhoto",
                data={
                    "chat_id":      chat_id,
                    "caption":      caption,
                    "parse_mode":   "HTML",
                    "reply_markup": json.dumps(markup_dict, ensure_ascii=False),
                    **extra,
                },
                files={"photo": ("photo.jpg", photo, "image/jpeg")},
                timeout=60,
            )
            d = r.json()
        except Exception as ex:
            log.error("_send_photo_raw error: %s", ex)
            return None, None
    else:
        params = dict(chat_id=chat_id, photo=photo, caption=caption,
                      parse_mode="HTML", reply_markup=markup_dict)
        if reply_to_message_id:
            params["reply_to_message_id"] = reply_to_message_id
        d = _api("sendPhoto", **params)
    if d.get("ok"):
        msg_id  = d["result"]["message_id"]
        photos  = d["result"].get("photo", [])
        file_id = photos[-1]["file_id"] if photos else None
        return msg_id, file_id
    log.error("sendPhoto failed: %s", d)
    return None, None

def _send_message_raw(chat_id: int, text: str,
                      markup_json: str | None = None,
                      reply_to_message_id: int | None = None) -> int | None:
    params: dict = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if markup_json:
        params["reply_markup"] = json.loads(markup_json)
    if reply_to_message_id:
        params["reply_to_message_id"] = reply_to_message_id
    d = _api("sendMessage", **params)
    if d.get("ok"):
        return d["result"]["message_id"]
    log.error("sendMessage failed: %s", d)
    return None

def _edit_message_text_raw(chat_id: int, msg_id: int,
                           text: str, markup_json: str | None = None) -> bool:
    params: dict = {
        "chat_id": chat_id, "message_id": msg_id,
        "text": text, "parse_mode": "HTML",
    }
    if markup_json:
        params["reply_markup"] = json.loads(markup_json)
    return bool(_api("editMessageText", **params).get("ok"))

def _edit_caption_raw(chat_id: int, msg_id: int,
                      caption: str, markup_json: str) -> bool:
    d = _api("editMessageCaption",
             chat_id=chat_id, message_id=msg_id,
             caption=caption, parse_mode="HTML",
             reply_markup=json.loads(markup_json))
    return bool(d.get("ok"))

def _edit_media_raw(chat_id: int, msg_id: int,
                    photo, caption: str, markup_json: str) -> bool:
    markup_dict = json.loads(markup_json)
    if isinstance(photo, bytes):
        try:
            r = requests.post(
                f"{API_BASE}/editMessageMedia",
                data={
                    "chat_id":    chat_id,
                    "message_id": msg_id,
                    "media": json.dumps({
                        "type": "photo", "media": "attach://photo",
                        "caption": caption, "parse_mode": "HTML",
                    }, ensure_ascii=False),
                    "reply_markup": json.dumps(markup_dict, ensure_ascii=False),
                },
                files={"photo": ("photo.jpg", photo, "image/jpeg")},
                timeout=60,
            )
            return r.json().get("ok", False)
        except Exception as ex:
            log.error("_edit_media_raw error: %s", ex)
            return False
    else:
        d = _api("editMessageMedia",
                 chat_id=chat_id, message_id=msg_id,
                 media={"type": "photo", "media": photo,
                        "caption": caption, "parse_mode": "HTML"},
                 reply_markup=markup_dict)
        return bool(d.get("ok"))

def _delete_message(chat_id: int, msg_id: int):
    _api("deleteMessage", chat_id=chat_id, message_id=msg_id)

def _copy_message(chat_id: int, from_chat_id: int, msg_id: int) -> bool:
    return bool(_api("copyMessage", chat_id=chat_id,
                     from_chat_id=from_chat_id, message_id=msg_id).get("ok"))

def _forward_message(chat_id: int, from_chat_id: int, msg_id: int) -> bool:
    return bool(_api("forwardMessage", chat_id=chat_id,
                     from_chat_id=from_chat_id, message_id=msg_id).get("ok"))

def _send_document_raw(chat_id: int, file_bytes: bytes,
                       filename: str, caption: str):
    try:
        requests.post(
            f"{API_BASE}/sendDocument",
            data={"chat_id": chat_id, "caption": caption,
                  "parse_mode": "HTML"},
            files={"document": (filename, file_bytes, "text/plain")},
            timeout=30,
        )
    except Exception as ex:
        log.error("sendDocument error: %s", ex)

# ================================================================
#  🏠  HOME MESSAGE HELPERS
# ================================================================
def send_home(chat_id: int, uid: int) -> int | None:
    global _home_file_id
    user   = get_user(uid)
    name   = user.get("display_name", "User")
    cap    = cap_home(uid, name)
    markup = kb_home()
    if _home_file_id:
        msg_id, _ = _send_photo_raw(chat_id, _home_file_id, cap, markup)
    else:
        photo = _load_home_image()
        if photo is None:
            msg_id = _send_message_raw(chat_id, cap, markup)
        else:
            msg_id, fid = _send_photo_raw(chat_id, photo, cap, markup)
            if fid:
                _home_file_id = fid
                log.info("Home file_id cached: %s…", fid[:20])
    if msg_id:
        set_user(uid, home_msg_id=msg_id, state="idle")
    return msg_id

def edit_caption_safe(uid: int, chat_id: int,
                      caption: str, markup_json: str) -> bool:
    msg_id = get_user(uid).get("home_msg_id")
    if not msg_id:
        return False
    return _edit_caption_raw(chat_id, msg_id, caption, markup_json)

def edit_media_safe(uid: int, chat_id: int,
                    photo, caption: str, markup_json: str) -> bool:
    msg_id = get_user(uid).get("home_msg_id")
    if not msg_id:
        return False
    return _edit_media_raw(chat_id, msg_id, photo, caption, markup_json)

def go_home(uid: int, chat_id: int):
    user   = get_user(uid)
    msg_id = user.get("home_msg_id")
    name   = user.get("display_name", "User")
    if not msg_id:
        send_home(chat_id, uid)
        return
    ok = edit_caption_safe(uid, chat_id, cap_home(uid, name), kb_home())
    if not ok:
        _delete_message(chat_id, msg_id)
        set_user(uid, home_msg_id=None)
        send_home(chat_id, uid)
        return
    set_user(uid, state="idle")

# ================================================================
#  📌  FORCE CHANNEL HELPERS
# ================================================================
def _check_all_channels(uid: int) -> list[dict]:
    if not FORCE_CHANNELS:
        return []
    not_subbed = []
    for ch in FORCE_CHANNELS:
        try:
            d = _api("getChatMember", chat_id=ch["id"], user_id=uid)
            if not d.get("ok"):
                not_subbed.append(ch)
                continue
            status = d["result"].get("status", "left")
            if status not in ("member", "administrator", "creator"):
                not_subbed.append(ch)
        except Exception as ex:
            log.warning("getChatMember error: %s", ex)
            not_subbed.append(ch)
    return not_subbed

def _require_subscription(uid: int, chat_id: int) -> bool:
    if uid in ADMIN_IDS:
        return True
    if not _force_enabled():
        return True
    if not FORCE_CHANNELS:
        return True
    not_subbed = _check_all_channels(uid)
    if not not_subbed:
        return True
    _send_message_raw(chat_id, cap_force_channel(), kb_force_channel())
    return False

# ================================================================
#  🧹  CLEANUP HELPERS
# ================================================================
def _cleanup_upload(uid: int, chat_id: int):
    _cancel_upload_timer(uid)
    upload_msg = get_user(uid).get("upload_msg_id")
    if upload_msg:
        _delete_message(chat_id, upload_msg)
    _img_store.pop(uid, None)
    set_user(uid, upload_msg_id=None)

def _cleanup_aspect(uid: int, chat_id: int):
    asp_id = get_user(uid).get("aspect_msg_id")
    if asp_id:
        _delete_message(chat_id, asp_id)
    set_user(uid, aspect_msg_id=None, pending_prompt_t2i=None,
             pro_mode=False, pro_reply_chat_id=None, pro_reply_msg_id=None)

# ================================================================
#  🖼  ADMIN GLOBAL LIBRARY DISPLAY
# ================================================================
def show_admin_glib(admin_uid: int, chat_id: int,
                    msg_id: int | None = None):
    all_imgs = get_all_images()
    idx      = _admin_glib_index.get(admin_uid, 0)
    idx      = max(0, min(idx, len(all_imgs) - 1))
    _admin_glib_index[admin_uid] = idx
    mk_empty = _markup([[_btn("𝑨𝒅𝒎𝒊𝒏", cb="ADM:BACK", icon=ICON_BACK)]])
    if not all_imgs:
        if msg_id:
            _edit_message_text_raw(chat_id, msg_id,
                                   cap_admin_glib_empty(), mk_empty)
        else:
            _send_message_raw(chat_id, cap_admin_glib_empty(), mk_empty)
        return
    item  = all_imgs[idx]
    total = len(all_imgs)
    cap   = cap_admin_glib(idx, total, item)
    mk    = kb_admin_glib(idx > 0, idx < total - 1, total)
    fid   = item.get("file_id")
    if msg_id:
        if fid:
            ok = _edit_media_raw(chat_id, msg_id, fid, cap, mk)
            if ok:
                return
        _edit_message_text_raw(chat_id, msg_id, cap, mk)
    else:
        if fid:
            new_id, _ = _send_photo_raw(chat_id, fid, cap, mk)
            if new_id:
                return
        _send_message_raw(chat_id, cap, mk)

# ================================================================
#  🍌  NANOBANANA PRO — GENERATE (txt2img)   ✅ FIXED
# ================================================================
def _api_generate(prompt: str, aspect_ratio: str = "1:1") -> bytes | str:
    """
    POST /generate
    Returns: raw image bytes on success, error string on failure.
    """
    log.info("NanaBanaPro /generate → prompt=%s aspect=%s",
             prompt[:60], aspect_ratio)
    try:
        r = requests.post(
            f"{NANABANA_API}/generate",
            json={"prompt": prompt, "aspect_ratio": aspect_ratio},
            timeout=240,
        )
        log.info("Generate HTTP status: %d", r.status_code)

        # ── Parse JSON safely ──
        try:
            data = r.json()
        except Exception:
            msg = f"Response not JSON (HTTP {r.status_code}):\n{r.text[:400]}"
            log.error("Generate: %s", msg)
            return msg

        log.info("Generate response: %s", str(data)[:400])

        # ── فحص خطأ صريح من الـ API ──
        if data.get("error") or data.get("message") and not data.get("success", True):
            api_err = data.get("error") or data.get("message") or str(data)
            return f"API error: {api_err}"

        # ── Extract image_url flexibly ──
        image_url = (
            data.get("image_url")
            or data.get("imageUrl")
            or data.get("url")
            or data.get("output")
        )
        if not image_url and isinstance(data.get("images"), list):
            imgs = data["images"]
            image_url = imgs[0] if imgs else None

        if not image_url:
            return f"No image_url in response:\n{str(data)[:400]}"

        log.info("Generate image_url: %s", str(image_url)[:120])
        img_r = requests.get(image_url, timeout=60)
        img_r.raise_for_status()
        log.info("Image downloaded (%d bytes)", len(img_r.content))
        return img_r.content

    except requests.exceptions.Timeout:
        msg = "TIMEOUT — الـ API لم يرد خلال 240 ثانية"
        log.error("_api_generate: %s", msg)
        return msg
    except Exception as ex:
        log.error("_api_generate error: %s", ex)
        return f"Exception: {ex}"


# ================================================================
#  🍌  NANOBANANA PRO — EDIT (img2img)   ✅ FIXED
# ================================================================
def _api_edit(prompt: str, image_urls: list[str]) -> bytes | str:
    """
    POST /edit
    Returns: raw image bytes on success, error string on failure.
    """
    log.info("NanaBanaPro /edit → prompt=%s images=%d",
             prompt[:60], len(image_urls))

    if len(image_urls) == 1:
        payload = {
            "prompt":     prompt,
            "image_url":  image_urls[0],
            "image_urls": image_urls,
        }
    else:
        payload = {
            "prompt":     prompt,
            "image_urls": image_urls,
        }

    log.info("Edit payload keys: %s", list(payload.keys()))

    try:
        r = requests.post(
            f"{NANABANA_API}/edit",
            json=payload,
            timeout=240,
        )
        log.info("Edit HTTP status: %d", r.status_code)

        try:
            data = r.json()
        except Exception:
            msg = f"Response not JSON (HTTP {r.status_code}):\n{r.text[:400]}"
            log.error("Edit: %s", msg)
            return msg

        log.info("Edit response: %s", str(data)[:400])

        # ── فحص خطأ صريح من الـ API ──
        if data.get("error") or data.get("message") and not data.get("success", True):
            api_err = data.get("error") or data.get("message") or str(data)
            return f"API error: {api_err}"

        image_url = (
            data.get("image_url")
            or data.get("imageUrl")
            or data.get("url")
            or data.get("output")
        )
        if not image_url and isinstance(data.get("images"), list):
            imgs = data["images"]
            image_url = imgs[0] if imgs else None

        if not image_url:
            return f"No image_url in response:\n{str(data)[:400]}"

        log.info("Edit image_url: %s", str(image_url)[:120])
        img_r = requests.get(image_url, timeout=60)
        img_r.raise_for_status()
        log.info("Edited image downloaded (%d bytes)", len(img_r.content))
        return img_r.content

    except requests.exceptions.Timeout:
        msg = "TIMEOUT — الـ API لم يرد خلال 240 ثانية"
        log.error("_api_edit: %s", msg)
        return msg
    except Exception as ex:
        log.error("_api_edit error: %s", ex)
        return f"Exception: {ex}"


# ================================================================
#  ⚙️  GENERATION WORKERS
# ================================================================
def _worker_txt2img(chat_id: int, uid: int, prompt: str,
                    aspect: str = "1:1",
                    pro_mode: bool = False,
                    pro_chat_id: int | None = None,
                    pro_reply_msg_id: int | None = None,
                    gen_msg_id_pro: int | None = None):
    user       = get_user(uid)
    name       = user.get("display_name", "User")
    target_cid = pro_chat_id if (pro_mode and pro_chat_id) else chat_id

    wait_msg_id = None
    if not pro_mode:
        wait_msg_id = _send_message_raw(target_cid, cap_wait())
        if wait_msg_id:
            def _del_wait():
                time.sleep(30)
                _delete_message(target_cid, wait_msg_id)
            threading.Thread(target=_del_wait, daemon=True).start()

    def err(detail: str = ""):
        # ── بناء رسالة الخطأ ──
        safe_detail = detail.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = cap_error()
        if safe_detail:
            text += f"\n\n<code>{safe_detail[:600]}</code>"
        if pro_mode:
            if gen_msg_id_pro:
                _delete_message(target_cid, gen_msg_id_pro)
            _send_message_raw(target_cid, text,
                              reply_to_message_id=pro_reply_msg_id)
        else:
            user_now = get_user(uid)
            g_id     = user_now.get("home_msg_id")
            if g_id:
                _delete_message(chat_id, g_id)
            err_id = _send_message_raw(chat_id, text, kb_error())
            if err_id:
                set_user(uid, home_msg_id=err_id, state="idle")
            else:
                set_user(uid, state="idle")

    try:
        result = _api_generate(prompt, aspect)
        # isinstance str = error message, bytes = success
        if isinstance(result, str):
            err(result); return
        data = result

        if pro_mode:
            if gen_msg_id_pro:
                _delete_message(target_cid, gen_msg_id_pro)
            msg_id, file_id = _send_photo_raw(
                target_cid, data,
                cap_success(uid, name),
                _markup([]),
                reply_to_message_id=pro_reply_msg_id,
            )
            add_to_library(uid, prompt, file_id=file_id)
            set_user(uid, state="idle")
        else:
            user2 = get_user(uid)
            g_id  = user2.get("home_msg_id")
            if g_id:
                _delete_message(chat_id, g_id)
            success_id, success_fid = _send_photo_raw(
                chat_id, data, cap_success(uid, name), kb_success()
            )
            add_to_library(uid, prompt, file_id=success_fid)
            set_user(uid, state="idle",
                     home_msg_id=success_id if success_id else None)

    except Exception as ex:
        log.exception("_worker_txt2img unhandled")
        try:
            err(f"Unhandled: {ex}")
        except Exception:
            pass


def _worker_img2img(chat_id: int, uid: int, prompt: str,
                    file_paths: list[str]):
    user = get_user(uid)
    name = user.get("display_name", "User")

    wait_msg_id = _send_message_raw(chat_id, cap_wait())
    if wait_msg_id:
        def _del_wait():
            time.sleep(30)
            _delete_message(chat_id, wait_msg_id)
        threading.Thread(target=_del_wait, daemon=True).start()

    def err(detail: str = ""):
        safe_detail = detail.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = cap_error()
        if safe_detail:
            text += f"\n\n<code>{safe_detail[:600]}</code>"
        user_now = get_user(uid)
        g_id     = user_now.get("home_msg_id")
        if g_id:
            _delete_message(chat_id, g_id)
        err_id = _send_message_raw(chat_id, text, kb_error())
        if err_id:
            set_user(uid, home_msg_id=err_id, state="idle",
                     upload_msg_id=None)
        else:
            set_user(uid, state="idle", upload_msg_id=None)

    try:
        image_urls = [
            f"https://api.telegram.org/file/bot{BOT_TOKEN}/{fp}"
            for fp in file_paths
        ]

        result = _api_edit(prompt, image_urls)
        if isinstance(result, str):
            err(result); return
        data = result

        user2 = get_user(uid)
        g_id  = user2.get("home_msg_id")
        if g_id:
            _delete_message(chat_id, g_id)

        success_id, success_fid = _send_photo_raw(
            chat_id, data, cap_success(uid, name), kb_success()
        )
        add_to_library(uid, prompt, file_id=success_fid)
        set_user(uid, state="idle",
                 home_msg_id=success_id if success_id else None,
                 upload_msg_id=None)

    except Exception as ex:
        log.exception("_worker_img2img unhandled")
        try:
            err(f"Unhandled: {ex}")
        except Exception:
            pass

# ================================================================
#  📚  LIBRARY DISPLAY
# ================================================================
def show_library(uid: int, chat_id: int):
    user = get_user(uid)
    lib  = user.get("library", [])

    if not lib:
        ok = edit_caption_safe(uid, chat_id, cap_library_empty(),
                               kb_library(False, False))
        if not ok:
            new_id = _send_message_raw(chat_id, cap_library_empty(),
                                       kb_library(False, False))
            if new_id:
                set_user(uid, home_msg_id=new_id)
        return

    idx    = max(0, min(user.get("lib_index", len(lib) - 1), len(lib) - 1))
    item   = lib[idx]
    prompt = item.get("prompt", "")
    cap    = cap_library(idx, len(lib), prompt)
    mk     = kb_library(idx > 0, idx < len(lib) - 1)

    fid = item.get("file_id")
    if fid:
        ok = edit_media_safe(uid, chat_id, fid, cap, mk)
        if ok:
            return

    ok = edit_caption_safe(uid, chat_id, cap, mk)
    if not ok:
        old_id = get_user(uid).get("home_msg_id")
        if old_id:
            _delete_message(chat_id, old_id)
            set_user(uid, home_msg_id=None)
        new_id = _send_message_raw(chat_id, cap, mk)
        if new_id:
            set_user(uid, home_msg_id=new_id)

# ================================================================
#  📢  ADMIN BROADCAST HELPERS
# ================================================================
def _broadcast_all(from_chat_id: int, message_id: int,
                   method: str = "copy") -> tuple[int, int]:
    user_ids = _get_all_user_ids()
    ok_n = fail_n = 0
    for uid in user_ids:
        try:
            if method == "copy":
                sent = _copy_message(uid, from_chat_id, message_id)
            else:
                sent = _forward_message(uid, from_chat_id, message_id)
            if sent: ok_n += 1
            else:    fail_n += 1
        except Exception:
            fail_n += 1
        time.sleep(BROADCAST_DELAY)
    return ok_n, fail_n

def _run_broadcast(admin_chat_id: int, admin_uid: int, admin_msg_id: int,
                   method: str, notify_msg_id: int | None = None):
    ok_n, fail_n = _broadcast_all(admin_chat_id, admin_msg_id, method)
    result = (
        f"{CK()} 𝑩𝒓𝒐𝒂𝒅𝒄𝒂𝒔𝒕 𝒅𝒐𝒏𝒆!\n\n"
        f"{PIN()} 𝑺𝒆𝒏𝒕: <b>{ok_n}</b>\n"
        f"{CR1()} 𝑭𝒂𝒊𝒍𝒆𝒅: <b>{fail_n}</b>"
    )
    back_mk = cap_admin_back()
    if notify_msg_id:
        _edit_message_text_raw(admin_chat_id, notify_msg_id, result, back_mk)
    else:
        _send_message_raw(admin_chat_id, result, back_mk)
    _admin_set(admin_uid)

def _run_prv_cast(admin_chat_id: int, admin_uid: int, admin_msg_id: int,
                  target_uid: int, notify_msg_id: int | None = None):
    sent = _copy_message(target_uid, admin_chat_id, admin_msg_id)
    result = (
        f"{CK()} 𝑴𝒆𝒔𝒔𝒂𝒈𝒆 𝒔𝒆𝒏𝒕 𝒕𝒐 <code>{target_uid}</code>"
        if sent else
        f"{CR1()} 𝑭𝒂𝒊𝒍𝒆𝒅 𝒕𝒐 𝒔𝒆𝒏𝒅 𝒕𝒐 <code>{target_uid}</code>"
    )
    back_mk = cap_admin_back()
    if notify_msg_id:
        _edit_message_text_raw(admin_chat_id, notify_msg_id, result, back_mk)
    else:
        _send_message_raw(admin_chat_id, result, back_mk)
    _admin_set(admin_uid)

# ================================================================
#  🎯  HANDLERS
# ================================================================

@bot.message_handler(commands=["start"])
def on_start(msg: types.Message):
    uid      = msg.from_user.id
    cid      = msg.chat.id
    name     = msg.from_user.first_name or "User"
    username = msg.from_user.username
    new      = is_new_user(uid)
    _cleanup_upload(uid, cid)
    _cleanup_aspect(uid, cid)
    _admin_set(uid)
    set_user(uid, state="idle", display_name=name,
             username=username,
             home_msg_id=None, lib_index=0, upload_msg_id=None)
    mark_user_seen(uid)
    if new:
        threading.Thread(
            target=_notify_admins_new_user,
            args=(uid, name, username),
            daemon=True,
        ).start()
    if not _require_subscription(uid, cid):
        return
    send_home(cid, uid)

@bot.message_handler(commands=["admin"])
def on_admin_cmd(msg: types.Message):
    uid = msg.from_user.id
    cid = msg.chat.id
    if uid not in ADMIN_IDS:
        return
    _admin_set(uid)
    _send_message_raw(cid, cap_admin(), kb_admin())

@bot.message_handler(commands=["cancel"])
def on_cancel(msg: types.Message):
    uid = msg.from_user.id
    cid = msg.chat.id
    if uid in ADMIN_IDS and _admin_get(uid):
        _admin_set(uid)
        _send_message_raw(cid, f"{CK()} 𝑪𝒂𝒏𝒄𝒆𝒍𝒍𝒆𝒅.", kb_admin())

@bot.message_handler(commands=["pro"])
def on_pro_cmd(msg: types.Message):
    uid  = msg.from_user.id
    cid  = msg.chat.id
    name = msg.from_user.first_name or "User"

    parts  = (msg.text or "").split(None, 1)
    prompt = parts[1].strip() if len(parts) > 1 else ""
    if not prompt and msg.reply_to_message:
        reply  = msg.reply_to_message
        prompt = (reply.text or reply.caption or "").strip()

    if not prompt:
        _send_message_raw(
            cid,
            f"{CR1()} 𝑷𝒍𝒆𝒂𝒔𝒆 𝒑𝒓𝒐𝒗𝒊𝒅𝒆 𝒂 𝒑𝒓𝒐𝒎𝒑𝒕:\n"
            f"<code>/pro your prompt here</code>",
            reply_to_message_id=msg.message_id,
        )
        return

    set_user(uid, display_name=name, username=msg.from_user.username)

    if not _require_subscription(uid, cid):
        return
    if not _model_status["txt2img"]:
        _send_message_raw(cid, cap_feature_disabled(),
                          reply_to_message_id=msg.message_id)
        return

    prompt = prompt[:2000]
    set_user(uid, pending_prompt_t2i=prompt, state="waiting_aspect_t2i",
             pro_mode=True, pro_reply_chat_id=cid,
             pro_reply_msg_id=msg.message_id)

    asp_id = _send_message_raw(
        cid, cap_aspect_selection(prompt), kb_aspect_selection(),
        reply_to_message_id=msg.message_id,
    )
    set_user(uid, aspect_msg_id=asp_id)


@bot.callback_query_handler(func=lambda c: True)
def on_callback(call: types.CallbackQuery):
    uid  = call.from_user.id
    cid  = call.message.chat.id
    mid  = call.message.message_id
    data = call.data

    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    log.info("CALLBACK uid=%s data=%s", uid, data)

    # ── ADMIN CALLBACKS ──────────────────────────────────────────
    if data.startswith("ADM:") and uid in ADMIN_IDS:
        action = data[4:]

        if action == "GLIB_NEXT":
            all_imgs = get_all_images()
            idx      = _admin_glib_index.get(uid, 0)
            idx      = min(idx + 1, max(0, len(all_imgs) - 1))
            _admin_glib_index[uid] = idx
            show_admin_glib(uid, cid, mid)
            return

        if action == "GLIB_BACK":
            idx = max(_admin_glib_index.get(uid, 0) - 1, 0)
            _admin_glib_index[uid] = idx
            show_admin_glib(uid, cid, mid)
            return

        if action == "ALL_LIB":
            _admin_glib_index[uid] = 0
            show_admin_glib(uid, cid, mid)
            return

        _admin_set(uid)

        if action == "BACK":
            _edit_message_text_raw(cid, mid, cap_admin(), kb_admin())

        elif action == "CLOSE":
            _delete_message(cid, mid)

        elif action == "STATS":
            _edit_message_text_raw(cid, mid, cap_admin_stats(),
                                   cap_admin_back())

        elif action == "EXPORT":
            stats     = _get_stats()
            file_data = _export_users_file()
            cap_exp   = (
                f"{ST()} 𝑼𝒔𝒆𝒓𝒔 𝑳𝒊𝒔𝒕\n\n"
                f"{PIN()} 𝑻𝒐𝒕𝒂𝒍: <b>{stats['total_users']}</b> 𝒖𝒔𝒆𝒓𝒔\n"
                f"{CK()} 𝑨𝒄𝒕𝒊𝒗𝒆: <b>{stats['active_users']}</b>"
            )
            _send_document_raw(cid, file_data, "users_list.txt", cap_exp)

        elif action == "CAST":
            prompt_id = _send_message_raw(
                cid,
                f"{PIN()}-𝑺𝒆𝒏𝒅 𝒕𝒉𝒆 𝒎𝒆𝒔𝒔𝒂𝒈𝒆 𝒕𝒐 𝒃𝒓𝒐𝒂𝒅𝒄𝒂𝒔𝒕:\n"
                f"<i>/cancel 𝒕𝒐 𝒂𝒃𝒐𝒓𝒕</i>",
            )
            _admin_set(uid, step="cast_wait", notify_id=prompt_id)

        elif action == "FWD_CAST":
            prompt_id = _send_message_raw(
                cid,
                f"{PIN()}-𝑺𝒆𝒏𝒅 𝒎𝒆𝒔𝒔𝒂𝒈𝒆 𝒕𝒐 𝒇𝒐𝒓𝒘𝒂𝒓𝒅:\n"
                f"<i>/cancel 𝒕𝒐 𝒂𝒃𝒐𝒓𝒕</i>",
            )
            _admin_set(uid, step="fwd_cast_wait", notify_id=prompt_id)

        elif action == "PRV_CAST":
            prompt_id = _send_message_raw(
                cid,
                f"{PIN()}-𝑺𝒆𝒏𝒅 𝒕𝒉𝒆 𝒕𝒂𝒓𝒈𝒆𝒕 𝒖𝒔𝒆𝒓 𝑰𝑫:\n"
                f"<i>/cancel 𝒕𝒐 𝒂𝒃𝒐𝒓𝒕</i>",
            )
            _admin_set(uid, step="prv_id_wait", notify_id=prompt_id)

        elif action == "CLOSE_MODEL":
            _edit_message_text_raw(cid, mid, cap_admin_model(),
                                   kb_admin_model_status())

        elif action == "TOGGLE_T2I":
            _model_status["txt2img"] = not _model_status["txt2img"]
            _save_model_status()
            _edit_message_text_raw(cid, mid, cap_admin_model(),
                                   kb_admin_model_status())

        elif action == "TOGGLE_I2I":
            _model_status["img2img"] = not _model_status["img2img"]
            _save_model_status()
            _edit_message_text_raw(cid, mid, cap_admin_model(),
                                   kb_admin_model_status())

        elif action == "FORCE_CH":
            _edit_message_text_raw(cid, mid, cap_admin_force_ch(),
                                   kb_admin_force_ch())

        elif action == "TOGGLE_FORCE":
            _set_force_enabled(not _force_enabled())
            _edit_message_text_raw(cid, mid, cap_admin_force_ch(),
                                   kb_admin_force_ch())
        return

    # ── FORCE CHANNEL VERIFY ─────────────────────────────────────
    if data == "FC_VERIFY":
        not_subbed = _check_all_channels(uid)
        if not not_subbed:
            try:
                _delete_message(cid, mid)
            except Exception:
                pass
            send_home(cid, uid)
        else:
            try:
                bot.answer_callback_query(call.id,
                    text="❌ You are not subscribed yet!",
                    show_alert=True)
            except Exception:
                pass
        return

    # ── USER CALLBACKS ───────────────────────────────────────────
    if not _require_subscription(uid, cid):
        return

    if data.startswith("ASPECT:"):
        aspect = data[7:]
        user   = get_user(uid)
        prompt = user.get("pending_prompt_t2i", "")
        if not prompt:
            go_home(uid, cid)
            return

        pro_mode         = user.get("pro_mode", False)
        pro_chat_id      = user.get("pro_reply_chat_id")
        pro_reply_msg_id = user.get("pro_reply_msg_id")

        _delete_message(cid, mid)
        set_user(uid, aspect_msg_id=None, pending_prompt_t2i=None,
                 pro_mode=False, pro_reply_chat_id=None,
                 pro_reply_msg_id=None, state="generating")

        gen_msg_id_pro = None
        if pro_mode:
            gen_msg_id_pro = _send_message_raw(
                pro_chat_id, cap_generating(),
                reply_to_message_id=pro_reply_msg_id,
            )
        else:
            gen_id = _send_message_raw(cid, cap_generating())
            if gen_id:
                set_user(uid, home_msg_id=gen_id)

        threading.Thread(
            target=_worker_txt2img,
            args=(cid, uid, prompt, aspect,
                  pro_mode,
                  pro_chat_id if pro_mode else cid,
                  pro_reply_msg_id,
                  gen_msg_id_pro),
            daemon=True,
        ).start()
        return

    set_user(uid, home_msg_id=mid)
    user = get_user(uid)

    if data == "HOME":
        _cleanup_upload(uid, cid)
        _cleanup_aspect(uid, cid)
        go_home(uid, cid)

    elif data == "TXT2IMG":
        _cleanup_upload(uid, cid)
        _cleanup_aspect(uid, cid)
        if not _model_status["txt2img"]:
            edit_caption_safe(uid, cid, cap_feature_disabled(), kb_disabled())
            return
        set_user(uid, state="waiting_txt2img")
        edit_caption_safe(uid, cid, cap_txt2img(), kb_txt2img())

    elif data == "IMG2IMG":
        _cleanup_upload(uid, cid)
        _cleanup_aspect(uid, cid)
        if not _model_status["img2img"]:
            edit_caption_safe(uid, cid, cap_feature_disabled(), kb_disabled())
            return
        set_user(uid, state="waiting_img2img")
        edit_caption_safe(uid, cid, cap_img2img(), kb_img2img())

    elif data == "LIBRARY":
        _cleanup_upload(uid, cid)
        _cleanup_aspect(uid, cid)
        lib = user.get("library", [])
        idx = max(0, len(lib) - 1)
        set_user(uid, lib_index=idx)
        show_library(uid, cid)

    elif data == "LIB_NEXT":
        lib = user.get("library", [])
        idx = min(user.get("lib_index", 0) + 1, max(0, len(lib) - 1))
        set_user(uid, lib_index=idx)
        show_library(uid, cid)

    elif data == "LIB_BACK":
        idx = max(user.get("lib_index", 0) - 1, 0)
        set_user(uid, lib_index=idx)
        show_library(uid, cid)


@bot.message_handler(func=lambda m: True, content_types=["text"])
def on_text(msg: types.Message):
    uid  = msg.from_user.id
    cid  = msg.chat.id
    text = msg.text.strip() if msg.text else ""

    if msg.chat.type in ("group", "supergroup", "channel"):
        return

    a_state = _admin_get(uid)
    if uid in ADMIN_IDS and a_state:
        step      = a_state.get("step")
        notify_id = a_state.get("notify_id")

        if step == "cast_wait":
            if notify_id:
                _edit_message_text_raw(cid, notify_id,
                    f"{CLK()} 𝑩𝒓𝒐𝒂𝒅𝒄𝒂𝒔𝒕𝒊𝒏𝒈…")
            threading.Thread(target=_run_broadcast,
                args=(cid, uid, msg.message_id, "copy", notify_id),
                daemon=True).start()

        elif step == "fwd_cast_wait":
            if notify_id:
                _edit_message_text_raw(cid, notify_id,
                    f"{CLK()} 𝑭𝒐𝒓𝒘𝒂𝒓𝒅𝒊𝒏𝒈…")
            threading.Thread(target=_run_broadcast,
                args=(cid, uid, msg.message_id, "forward", notify_id),
                daemon=True).start()

        elif step == "prv_id_wait":
            m = re.search(r'\d{5,12}', text)
            if not m:
                _send_message_raw(cid,
                    f"{CR1()} 𝑰𝒏𝒗𝒂𝒍𝒊𝒅 𝑰𝑫 — 𝒔𝒆𝒏𝒅 𝒂 𝒗𝒂𝒍𝒊𝒅 𝒖𝒔𝒆𝒓 𝑰𝑫:")
                return
            target_id = int(m.group(0))
            prompt_id = _send_message_raw(
                cid,
                f"{PIN()}-𝑻𝒂𝒓𝒈𝒆𝒕: <code>{target_id}</code>\n"
                f"𝑵𝒐𝒘 𝒔𝒆𝒏𝒅 𝒕𝒉𝒆 𝒎𝒆𝒔𝒔𝒂𝒈𝒆:\n"
                f"<i>/cancel 𝒕𝒐 𝒂𝒃𝒐𝒓𝒕</i>",
            )
            _admin_set(uid, step="prv_msg_wait",
                       target=target_id, notify_id=prompt_id)

        elif step == "prv_msg_wait":
            target_id = a_state.get("target")
            if notify_id:
                _edit_message_text_raw(cid, notify_id,
                    f"{CLK()} 𝑺𝒆𝒏𝒅𝒊𝒏𝒈 𝒕𝒐 <code>{target_id}</code>…")
            threading.Thread(target=_run_prv_cast,
                args=(cid, uid, msg.message_id, target_id, notify_id),
                daemon=True).start()
        return

    user  = get_user(uid)
    state = user.get("state", "idle")

    if not _require_subscription(uid, cid):
        return

    if state == "waiting_txt2img":
        if not _model_status["txt2img"]:
            return
        prompt = text[:2000]
        if not prompt:
            return
        set_user(uid, pending_prompt_t2i=prompt, state="waiting_aspect_t2i",
                 pro_mode=False, pro_reply_chat_id=None, pro_reply_msg_id=None)
        asp_id = _send_message_raw(cid, cap_aspect_selection(prompt),
                                   kb_aspect_selection())
        set_user(uid, aspect_msg_id=asp_id)

    elif state == "waiting_img2img_prompt":
        if not _model_status["img2img"]:
            return
        prompt = text[:2000]
        if not prompt:
            return
        imgs_data = _img_store.pop(uid, [])
        if not imgs_data:
            go_home(uid, cid)
            return
        _cancel_upload_timer(uid)
        file_paths = [fp for fp, _ in imgs_data]
        _start_i2i(cid, uid, prompt, file_paths)

    else:
        if not user.get("home_msg_id"):
            send_home(cid, uid)
        else:
            go_home(uid, cid)


@bot.message_handler(content_types=["photo"])
def on_photo(msg: types.Message):
    uid    = msg.from_user.id
    cid    = msg.chat.id
    prompt = (msg.caption or "").strip()

    if msg.chat.type in ("group", "supergroup", "channel"):
        if uid not in ADMIN_IDS or not _admin_get(uid):
            return

    a_state = _admin_get(uid)
    if uid in ADMIN_IDS and a_state:
        step      = a_state.get("step")
        notify_id = a_state.get("notify_id")
        if step == "cast_wait":
            if notify_id:
                _edit_message_text_raw(cid, notify_id,
                    f"{CLK()} 𝑩𝒓𝒐𝒂𝒅𝒄𝒂𝒔𝒕𝒊𝒏𝒈…")
            threading.Thread(target=_run_broadcast,
                args=(cid, uid, msg.message_id, "copy", notify_id),
                daemon=True).start()
            return
        elif step == "fwd_cast_wait":
            if notify_id:
                _edit_message_text_raw(cid, notify_id,
                    f"{CLK()} 𝑭𝒐𝒓𝒘𝒂𝒓𝒅𝒊𝒏𝒈…")
            threading.Thread(target=_run_broadcast,
                args=(cid, uid, msg.message_id, "forward", notify_id),
                daemon=True).start()
            return
        elif step == "prv_msg_wait":
            target_id = a_state.get("target")
            if notify_id:
                _edit_message_text_raw(cid, notify_id,
                    f"{CLK()} 𝑺𝒆𝒏𝒅𝒊𝒏𝒈 𝒕𝒐 <code>{target_id}</code>…")
            threading.Thread(target=_run_prv_cast,
                args=(cid, uid, msg.message_id, target_id, notify_id),
                daemon=True).start()
            return

    user  = get_user(uid)
    state = user.get("state", "idle")

    if not _require_subscription(uid, cid):
        return

    if state not in ("waiting_img2img", "waiting_img2img_prompt"):
        go_home(uid, cid)
        return

    if not _model_status["img2img"]:
        return

    imgs_so_far = _img_store.get(uid, [])
    if len(imgs_so_far) >= MAX_IMAGES:
        if prompt:
            imgs_data  = _img_store.pop(uid, [])
            _cancel_upload_timer(uid)
            file_paths = [fp for fp, _ in imgs_data]
            _start_i2i(cid, uid, prompt, file_paths)
        return

    try:
        fi        = bot.get_file(msg.photo[-1].file_id)
        file_path = fi.file_path
    except Exception as ex:
        bot.reply_to(msg, f"❌ 𝑭𝒂𝒊𝒍𝒆𝒅 𝒕𝒐 𝒈𝒆𝒕 𝒇𝒊𝒍𝒆: {ex}")
        return

    if uid not in _img_store:
        _img_store[uid] = []
    _img_store[uid].append((file_path, b""))

    if prompt:
        imgs_data  = _img_store.pop(uid, [])
        _cancel_upload_timer(uid)
        file_paths = [fp for fp, _ in imgs_data]
        _start_i2i(cid, uid, prompt, file_paths)
        return

    _schedule_upload_counter(cid, uid)


@bot.message_handler(
    func=lambda m: m.from_user.id in ADMIN_IDS and
                   _admin_get(m.from_user.id) is not None and
                   _admin_get(m.from_user.id).get("step") in
                   ("cast_wait", "fwd_cast_wait", "prv_msg_wait"),
    content_types=["sticker", "video", "document", "audio",
                   "voice", "animation", "video_note"],
)
def on_admin_media(msg: types.Message):
    uid  = msg.from_user.id
    cid  = msg.chat.id
    a_st = _admin_get(uid)
    step = a_st.get("step")
    nid  = a_st.get("notify_id")
    if step == "cast_wait":
        if nid:
            _edit_message_text_raw(cid, nid, f"{CLK()} 𝑩𝒓𝒐𝒂𝒅𝒄𝒂𝒔𝒕𝒊𝒏𝒈…")
        threading.Thread(target=_run_broadcast,
            args=(cid, uid, msg.message_id, "copy", nid),
            daemon=True).start()
    elif step == "fwd_cast_wait":
        if nid:
            _edit_message_text_raw(cid, nid, f"{CLK()} 𝑭𝒐𝒓𝒘𝒂𝒓𝒅𝒊𝒏𝒈…")
        threading.Thread(target=_run_broadcast,
            args=(cid, uid, msg.message_id, "forward", nid),
            daemon=True).start()
    elif step == "prv_msg_wait":
        target_id = a_st.get("target")
        if nid:
            _edit_message_text_raw(cid, nid,
                f"{CLK()} 𝑺𝒆𝒏𝒅𝒊𝒏𝒈 𝒕𝒐 <code>{target_id}</code>…")
        threading.Thread(target=_run_prv_cast,
            args=(cid, uid, msg.message_id, target_id, nid),
            daemon=True).start()


def _start_i2i(chat_id: int, uid: int, prompt: str,
               file_paths: list[str]):
    upload_msg = get_user(uid).get("upload_msg_id")
    if upload_msg:
        _delete_message(chat_id, upload_msg)
    set_user(uid, state="generating", upload_msg_id=None)
    gen_id = _send_message_raw(chat_id, cap_generating())
    if gen_id:
        set_user(uid, home_msg_id=gen_id)
    threading.Thread(
        target=_worker_img2img,
        args=(chat_id, uid, prompt, file_paths),
        daemon=True,
    ).start()


# ================================================================
#  🚀  LAUNCH
# ================================================================
if __name__ == "__main__":
    log.info("🍌 SIDRA Bot v4.1 (FIXED) — NanoBanana PRO API starting…")
    _load_home_image()
    _load_model_status()
    bot.infinity_polling(timeout=60, long_polling_timeout=30)
