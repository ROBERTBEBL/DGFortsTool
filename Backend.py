import asyncio
import websockets
import json
import uuid
from datetime import datetime
from typing import Dict, Set, Tuple, Optional

# ==============================================
# 核心配置与常量（与JS代码保持一致）
# ==============================================
WS_SERVER_PORT = 8765  # 与JS代码相同的端口
PUNISHMENT_DURATION = 5  # 默认发送时间（秒）
PUNISHMENT_TIME = 1  # 每秒发送次数
HEARTBEAT_INTERVAL = 60  # 心跳间隔（秒）

# ==============================================
# 核心存储结构（对应JS的Map）
# ==============================================
# 客户端连接映射：client_id -> WebSocket对象
client_connections: Dict[str, websockets.WebSocketServerProtocol] = {}
# 绑定关系映射：client_id -> target_id（一对一）
binding_relations: Dict[str, str] = {}
# 波形计时器映射：{client_id}-{channel} -> 计时器任务
client_timers: Dict[str, asyncio.Task] = {}


def log(message: str):
    """带时间戳的日志输出"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")


async def send_heartbeat():
    """心跳机制：定期向所有客户端发送心跳包"""
    heartbeat_msg = {
        "type": "heartbeat",
        "clientId": "",
        "targetId": "",
        "message": "200"
    }
    while True:
        if client_connections:  # 有连接时才发送
            log(f"发送心跳包（当前连接数：{len(client_connections)}）")
            for client_id, ws in client_connections.items():
                if ws.open:  # 连接有效
                    heartbeat_msg["clientId"] = client_id
                    heartbeat_msg["targetId"] = binding_relations.get(client_id, "")
                    await ws.send(json.dumps(heartbeat_msg))
        await asyncio.sleep(HEARTBEAT_INTERVAL)


def validate_relation(client_id: str, target_id: str, ws: websockets.WebSocketServerProtocol) -> Tuple[bool, str]:
    """验证绑定关系合法性（对应JS的invalidRelation）"""
    # 检查客户端是否存在
    if client_id not in client_connections or target_id not in client_connections:
        return False, "404"  # 目标不存在
    # 检查连接是否匹配
    if client_connections[client_id] != ws and client_connections[target_id] != ws:
        return False, "404"  # 非法连接
    # 检查绑定关系
    if binding_relations.get(client_id) != target_id:
        return False, "402"  # 非绑定关系
    return True, "200"


async def cancel_timer(client_id: str, channel: str):
    """取消指定客户端+通道的波形计时器（对应JS的clearInterval）"""
    timer_key = f"{client_id}-{channel}"
    if timer_key in client_timers:
        task = client_timers[timer_key]
        if not task.done():
            task.cancel()  # 取消任务
        del client_timers[timer_key]
        log(f"已取消计时器：{timer_key}")


async def delay_send_msg(
    client_id: str,
    target_ws: websockets.WebSocketServerProtocol,
    send_data: dict,
    total_sends: int,
    time_space: float,
    channel: str
):
    """波形消息定时发送（对应JS的delaySendMsg）"""
    timer_key = f"{client_id}-{channel}"
    try:
        # 立即发送第一次
        if target_ws.open:
            await target_ws.send(json.dumps(send_data))
        total_sends -= 1
        log(f"发送波形消息（剩余：{total_sends}）：{send_data['message']}")

        # 循环发送剩余消息
        while total_sends > 0:
            await asyncio.sleep(time_space)  # 间隔发送
            if not target_ws.open:  # 目标连接已关闭
                log("目标连接已关闭，停止发送波形")
                break
            await target_ws.send(json.dumps(send_data))
            total_sends -= 1
            log(f"发送波形消息（剩余：{total_sends}）：{send_data['message']}")

        # 发送完毕
        log(f"波形消息发送完成（{timer_key}）")
    finally:
        if timer_key in client_timers:
            del client_timers[timer_key]


async def handle_client(ws: websockets.WebSocketServerProtocol):
    """处理客户端连接（对应JS的wss.on('connection')）"""
    client_id = str(uuid.uuid4())  # 生成唯一ID
    log(f"新连接建立，clientId：{client_id}")
    client_connections[client_id] = ws

    # 发送绑定ID给客户端（对应JS的ws.send(bind消息)）
    await ws.send(json.dumps({
        "type": "bind",
        "clientId": client_id,
        "targetId": "",
        "message": "targetId"
    }))

    try:
        async for message in ws:
            try:
                data = json.loads(message)
                log(f"收到消息：{data}（来自 {client_id}）")

                # 验证必填字段
                required = ["type", "clientId", "targetId", "message"]
                if not all(k in data for k in required):
                    await ws.send(json.dumps({
                        "type": "error",
                        "clientId": "",
                        "targetId": "",
                        "message": "403"  # 缺少字段
                    }))
                    continue

                type_ = data["type"]
                client_id = data["clientId"]
                target_id = data["targetId"]
                message = data["message"]

                # 处理APP绑定请求
                if type_ == "bind":
                    # 检查目标客户端是否存在
                    if target_id not in client_connections:
                        await ws.send(json.dumps({
                            "type": "bind",
                            "clientId": client_id,
                            "targetId": target_id,
                            "message": "401"  # 目标不存在
                        }))
                        continue

                    # 检查是否已绑定（防止重复绑定）
                    if (client_id in binding_relations or 
                        target_id in binding_relations.values()):
                        await ws.send(json.dumps({
                            "type": "bind",
                            "clientId": client_id,
                            "targetId": target_id,
                            "message": "400"  # 已绑定
                        }))
                        continue

                    # 建立双向绑定
                    binding_relations[client_id] = target_id
                    binding_relations[target_id] = client_id  # 反向绑定
                    # 通知双方绑定成功
                    bind_success = {
                        "type": "bind",
                        "clientId": client_id,
                        "targetId": target_id,
                        "message": "200"
                    }
                    await client_connections[client_id].send(json.dumps(bind_success))
                    await client_connections[target_id].send(json.dumps(bind_success))
                    log(f"绑定成功：{client_id} <-> {target_id}")
                    continue

                # 验证绑定关系（非bind消息需要验证）
                valid, err_code = validate_relation(client_id, target_id, ws)
                if not valid:
                    await ws.send(json.dumps({
                        "type": "error",
                        "clientId": client_id,
                        "targetId": target_id,
                        "message": err_code
                    }))
                    continue

                # 处理其他消息（默认情况）
                target_ws = client_connections[target_id]
                await target_ws.send(json.dumps({
                    "type": type_,
                    "clientId": client_id,
                    "targetId": target_id,
                    "message": message
                }))
                log(f"转发消息：{type_} -> {target_id}")

            except json.JSONDecodeError:
                # 非JSON格式消息
                await ws.send(json.dumps({
                    "type": "error",
                    "clientId": "",
                    "targetId": "",
                    "message": "403"  # 非标准JSON
                }))
                log("收到非JSON格式消息")
            except Exception as e:
                log(f"消息处理错误：{str(e)}")
                await ws.send(json.dumps({
                    "type": "error",
                    "clientId": data.get("clientId", ""),
                    "targetId": data.get("targetId", ""),
                    "message": "500"  # 服务器内部错误
                }))

    except websockets.exceptions.ConnectionClosed:
        log(f"连接关闭：{client_id}")
    finally:
        # 清理资源（对应JS的ws.on('close')）
        # 1. 移除客户端连接
        if client_id in client_connections:
            del client_connections[client_id]
        
        # 2. 处理绑定关系，通知对方
        if client_id in binding_relations:
            target_id = binding_relations[client_id]
            # 通知对方连接断开
            if target_id in client_connections and client_connections[target_id].open:
                await client_connections[target_id].send(json.dumps({
                    "type": "break",
                    "clientId": client_id,
                    "targetId": target_id,
                    "message": "209"  # 对方断开
                }))
                await client_connections[target_id].close()  # 关闭对方连接
            # 清除绑定关系
            del binding_relations[client_id]
            if target_id in binding_relations:
                del binding_relations[target_id]
            log(f"解除绑定：{client_id} <-> {target_id}")
        
        # 3. 取消相关计时器
        for key in list(client_timers.keys()):
            if key.startswith(f"{client_id}-"):
                await cancel_timer(client_id, key.split("-")[1])
        
        log(f"资源清理完成（{client_id}），当前连接数：{len(client_connections)}")


async def main():
    """启动服务（主入口）"""
    # 启动心跳任务
    asyncio.create_task(send_heartbeat())
    # 启动WebSocket服务
    async with websockets.serve(handle_client, "0.0.0.0", WS_SERVER_PORT):
        log(f"WebSocket服务启动，端口：{WS_SERVER_PORT}")
        await asyncio.Future()  # 保持服务运行


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("服务已手动停止")
    except Exception as e:
        log(f"服务崩溃：{str(e)}")