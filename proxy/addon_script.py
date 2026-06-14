"""
mitmdump addon脚本 - 在子进程中运行

职责：
- 拦截WebSocket消息（文本+二进制）
- 读取共享状态文件获取 target_dice / mode
- 修改骰子数据包
- 通过stdout输出JSON行通知主进程

共享状态文件: ~/.mitmproxy/dice_state.json
格式: {"target_dice": null|1-6, "mode": "K"|"R"|"D"|"J"|"Q"}
"""
import json
import os
import sys
import time
import random

from mitmproxy import http
from mitmproxy.websocket import WebSocketMessage

# === 编解码（内联，避免import路径问题）===

ALPHABETS = {
    "K": "QWERTYUIOPASDFGHJKLZXCVBNMabcdefghijklmnopqrstuvwxyz0123456789-_",
    "R": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_",
    "D": "-_0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "J": "0123456789-_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
    "Q": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_",
}


def decode(payload: str, mode: str = "K") -> dict:
    alphabet = ALPHABETS.get(mode, ALPHABETS["K"])
    values = [alphabet.index(c) for c in payload]
    packed = values[0] * 64 * 64 + values[1] * 64 + values[2]
    dice = (packed % 8) + 1
    operator = ((packed // 8) % 4) + 1
    time_part = packed // 32
    if dice > 6:
        dice = 6
    return {"dice": dice, "operator": operator, "time_part": time_part}


def encode(dice: int, operator: int, time_part: int, mode: str = "K") -> str:
    alphabet = ALPHABETS.get(mode, ALPHABETS["K"])
    packed = time_part * 32 + (operator - 1) * 8 + (dice - 1)
    c2 = packed % 64
    packed //= 64
    c1 = packed % 64
    c0 = packed // 64
    return alphabet[c0] + alphabet[c1] + alphabet[c2]


# === 防封逻辑 ===
_last_was_six = False


def anti_ban_check(dice: int) -> int:
    """防封：上一次发出6，这次绝对不能再是6（连续两个6=封号）"""
    global _last_was_six
    if _last_was_six and dice == 6:
        # 强制替换为非6
        dice = random.choice([1, 2, 3, 4, 5])
    return dice


def anti_ban_record(dice: int):
    """记录本次实际发出的点数，更新状态"""
    global _last_was_six
    _last_was_six = (dice == 6)


# === 状态读取 ===
STATE_FILE = os.path.join(os.path.expanduser("~"), ".mitmproxy", "dice_state.json")
_state_cache = {"target_dice": None, "mode": "K"}
_state_mtime = 0


def read_state() -> dict:
    """读取共享状态文件（带mtime缓存避免频繁IO）"""
    global _state_cache, _state_mtime
    try:
        mtime = os.path.getmtime(STATE_FILE)
        if mtime != _state_mtime:
            _state_mtime = mtime
            with open(STATE_FILE, "r") as f:
                _state_cache = json.load(f)
    except:
        pass
    return _state_cache


def write_state_clear_dice():
    """修改完毕后清除target_dice"""
    try:
        state = read_state().copy()
        state["target_dice"] = None
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except:
        pass


# === 输出到主进程 ===
def emit(msg_type: str, **kwargs):
    """输出JSON行到stdout，主进程读取"""
    data = {"type": msg_type, **kwargs}
    print(json.dumps(data, ensure_ascii=False), flush=True)


# === 数据包处理 ===
def process_dice_text(data: str) -> tuple:
    """处理文本WebSocket消息（微信模式），返回 (modified_data|None, info|None)"""
    if "randomNumber" not in data:
        return None, None

    state = read_state()
    mode = state.get("mode", "K")
    target_dice = state.get("target_dice")

    # 4层JSON解析
    try:
        outer = json.loads(data)
        data_obj = json.loads(outer.get("data", ""))
        if data_obj.get("cmd") != 6:
            return None, None
        content_obj = json.loads(data_obj.get("content", ""))
        msg_obj = json.loads(content_obj.get("msg", ""))
    except:
        return None, None

    update_value = msg_obj.get("updateValueObj", {})
    random_number = update_value.get("randomNumber")
    if not random_number or len(random_number) != 3:
        return None, None

    # 解码
    try:
        info = decode(random_number, mode)
    except:
        return None, None

    original_dice = info["dice"]

    # 决定最终点数
    if target_dice is not None and 1 <= target_dice <= 6:
        final_dice = anti_ban_check(target_dice)
        # 用完清除
        write_state_clear_dice()
    else:
        # 透传也要防封
        final_dice = anti_ban_check(original_dice)

    # 记录实际发出的点数（更新防封状态）
    anti_ban_record(final_dice)

    # 编码
    new_payload = encode(final_dice, info["operator"], info["time_part"], mode)
    update_value["randomNumber"] = new_payload

    # 重建
    msg_str = json.dumps(msg_obj, ensure_ascii=False, separators=(',', ':'))
    content_obj["msg"] = msg_str
    content_str = json.dumps(content_obj, ensure_ascii=False, separators=(',', ':'))
    data_obj["content"] = content_str
    data_str = json.dumps(data_obj, ensure_ascii=False, separators=(',', ':'))
    outer["data"] = data_str
    modified = json.dumps(outer, ensure_ascii=False, separators=(',', ':'))

    room_id = msg_obj.get("roomId", "")
    result_info = {
        "original_dice": original_dice,
        "final_dice": final_dice,
        "modified": original_dice != final_dice,
        "room_id": room_id,
        "had_target": target_dice is not None,
    }

    if original_dice != final_dice:
        return modified, result_info
    else:
        return None, result_info


def process_dice_binary(raw: bytes) -> tuple:
    """
    处理二进制WebSocket消息（QK模式 - 华为protobuf包裹扁平JSON）
    返回 (modified_raw|None, info|None)

    Q版数据结构：protobuf二进制帧内嵌扁平JSON：
    {"roomId":"...","updatePlayerId":"...","updateKey":304,
     "updateValueObj":{"currentOperator":3,"randomNumber":"MO1"}}

    策略：randomNumber始终3字符，直接在原始bytes中替换，
    不改变长度，无需重建protobuf长度前缀。
    """
    if b"randomNumber" not in raw:
        return None, None

    state = read_state()
    mode = state.get("mode", "Q")  # 二进制默认Q模式
    target_dice = state.get("target_dice")

    # 用正则直接从bytes中提取randomNumber值（避免JSON解析+重建导致长度变化）
    import re as _re
    # 匹配 "randomNumber":"XXX" (恰好3字符)
    pattern = b'"randomNumber":"([A-Za-z0-9_-]{3})"'
    match = _re.search(pattern, raw)
    if not match:
        return None, None

    original_payload = match.group(1).decode("ascii")

    # 解码
    try:
        info = decode(original_payload, mode)
    except:
        return None, None

    original_dice = info["dice"]

    # 决定最终点数
    if target_dice is not None and 1 <= target_dice <= 6:
        final_dice = anti_ban_check(target_dice)
        write_state_clear_dice()
    else:
        # 透传也要防封
        final_dice = anti_ban_check(original_dice)

    # 记录实际发出的点数（更新防封状态）
    anti_ban_record(final_dice)

    # 编码新载荷
    new_payload = encode(final_dice, info["operator"], info["time_part"], mode)

    # 提取roomId用于日志
    room_id = ""
    room_match = _re.search(b'"roomId":"([^"]*)"', raw)
    if room_match:
        room_id = room_match.group(1).decode("utf-8", errors="ignore")

    result_info = {
        "original_dice": original_dice,
        "final_dice": final_dice,
        "modified": original_dice != final_dice,
        "room_id": room_id,
        "had_target": target_dice is not None,
        "payload": original_payload,
        "new_payload": new_payload if original_dice != final_dice else original_payload,
    }

    if original_dice != final_dice:
        # 直接替换3字节的randomNumber值（长度不变，protobuf前缀无需修改）
        old_fragment = f'"randomNumber":"{original_payload}"'.encode("ascii")
        new_fragment = f'"randomNumber":"{new_payload}"'.encode("ascii")
        new_raw = raw.replace(old_fragment, new_fragment, 1)
        return new_raw, result_info
    else:
        return None, result_info


# === mitmproxy Addon ===
class DiceAddon:
    def __init__(self):
        self._flow_count = 0
        self._ws_count = 0
        self._last_report = 0

    def request(self, flow: http.HTTPFlow):
        """任何HTTP请求经过都计数（诊断用）"""
        self._flow_count += 1
        now = time.time()
        # 每10秒或前5个请求报告一次
        if self._flow_count <= 5 or (now - self._last_report > 10):
            self._last_report = now
            emit("diag_flow",
                 count=self._flow_count,
                 host=flow.request.pretty_host,
                 path=flow.request.path[:80])

    def websocket_start(self, flow: http.HTTPFlow):
        """WebSocket连接建立时通知"""
        self._ws_count += 1
        emit("diag_ws",
             count=self._ws_count,
             host=flow.request.pretty_host,
             path=flow.request.path[:80])

    def websocket_message(self, flow: http.HTTPFlow):
        msg: WebSocketMessage = flow.websocket.messages[-1]
        if not msg.from_client:
            return

        info = None

        if msg.is_text:
            # 文本消息（微信）
            modified, info = process_dice_text(msg.text)
            if modified is not None:
                msg.content = modified.encode("utf-8")
        else:
            # 二进制消息（QK/模拟器）
            modified_raw, info = process_dice_binary(msg.content)
            if modified_raw is not None:
                msg.content = modified_raw

        # 通知主进程
        if info:
            emit("dice",
                 original=info["original_dice"],
                 final=info["final_dice"],
                 modified=info["modified"],
                 room=info.get("room_id", ""),
                 had_target=info.get("had_target", False),
                 payload=info.get("payload", ""),
                 new_payload=info.get("new_payload", ""),
                 six_locked=_last_was_six)


addons = [DiceAddon()]

# 启动时通知主进程
emit("ready")
