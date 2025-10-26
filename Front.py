import asyncio
import json
import os
from tailer import follow
from datetime import datetime
import qrcode
import websockets

# 全局状态
FrontClientId = ""
TargetAPPId = ""
ws_conn = None

# ==============================================
# 用户输入配置（新增部分）
# ==============================================
def get_user_config():
    """获取用户输入的服务器IP和二维码保存路径"""
    # 提示用户输入，支持默认值
    default_ip = "192.168.137.1"
    default_qr_path = "../../../DGForts_qrcode.png"
    default_logfile_path = "D:/ROBERT/Steam/steamapps/common/Forts/users/76561198359266518/log.txt"
    print(f"\n请配置服务器信息（直接回车使用默认值）")
    server_ip = input(f"服务器IP（默认：{default_ip}）：").strip()
    qr_path = input(f"二维码保存路径（默认：{default_qr_path}）：").strip()
    logfile_path = input(f"Forts日志文件路径（默认：{default_logfile_path}）：").strip()
    # 处理默认值
    server_ip = server_ip if server_ip else default_ip
    qr_path = qr_path if qr_path else default_qr_path
    logfile_path = logfile_path if logfile_path else default_logfile_path
    # 确保二维码保存目录存在
    qr_dir = os.path.dirname(qr_path)
    if qr_dir and not os.path.exists(qr_dir):
        os.makedirs(qr_dir, exist_ok=True)
        print(f"已自动创建二维码保存目录：{qr_dir}")
    
    return server_ip, qr_path , logfile_path

# 获取用户配置
SERVER_IP, QR_CODE_PATH ,LOG_FILE_PATH= get_user_config()

# 其他配置
WS_SERVER_PORT = 8765
DATA_PREFIX = "[DGFortsRemoteData]"
WS_URL = f"ws://{SERVER_IP}:{WS_SERVER_PORT}/ws"  # 使用用户输入的IP

def log(message: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")


def generate_qrcode(content: str, save_path: str):
    try:
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L,
                           box_size=10, border=4)
        qr.add_data(content)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(save_path)
        log(f"二维码生成成功：{save_path}")
    except Exception as e:
        log(f"生成二维码失败：{str(e)}")


async def websocket_message_handler(websocket):
    global FrontClientId, TargetAPPId, ws_conn
    ws_conn = websocket

    async for message in websocket:
        try:
            data = json.loads(message)
            log(f"收到WebSocket消息：{data}")

            if data.get("type") == "bind":
                if not data.get("targetId") and data.get("clientId"):
                    FrontClientId = data["clientId"]
                    log(f"已获取前端ID：{FrontClientId}")
                    # 二维码内容使用用户输入的IP
                    qr_content = f"https://www.dungeon-lab.com/app-download.php#DGLAB-SOCKET#ws://{SERVER_IP}:{WS_SERVER_PORT}/{FrontClientId}"
                    generate_qrcode(qr_content, QR_CODE_PATH)
                elif data.get("targetId") and data.get("clientId") == FrontClientId:
                    TargetAPPId = data["targetId"]
                    log(f"已绑定APP ID：{TargetAPPId}")
            elif data.get("type") == "break":
                TargetAPPId = ""
                FrontClientId = ""
                log(f"目标APP已离线")
        except Exception as e:
            log(f"处理WebSocket消息出错：{str(e)}")


async def connect_websocket():
    global ws_conn
    log("=== 开始执行WebSocket连接流程 ===")

    while True:
        try:
            if ws_conn and not ws_conn.closed:
                log("WebSocket已连接，无需重复连接")
                return

            log(f"尝试连接：{WS_URL}（超时10秒）")
            ws_conn = await asyncio.wait_for(
                websockets.connect(WS_URL),
                timeout=10
            )
            log(f"✅ 连接成功！")
            await websocket_message_handler(ws_conn)
        except asyncio.TimeoutError:
            log(f"❌ 连接超时（10秒未响应）")
        except ConnectionRefusedError:
            log(f"❌ 连接被拒绝（后端未启动？）")
        except Exception as e:
            log(f"❌ 连接失败：{str(e)}")
        await asyncio.sleep(5)


async def send_command_to_app(command: str):
    global FrontClientId, TargetAPPId, ws_conn
    if not TargetAPPId or not FrontClientId or not ws_conn or ws_conn.closed:
        log(f"无法发送指令：未绑定或连接断开")
        return False

    try:
        message = {
            "type": "msg",
            "clientId": FrontClientId,
            "targetId": TargetAPPId,
            "message": command
        }
        await ws_conn.send(json.dumps(message))
        log(f"已发送指令：{command}")
        return True
    except Exception as e:
        log(f"发送失败：{str(e)}")
        return False


async def monitor_game_log():
    log("=== 开始监控游戏日志 ===")
    loop = asyncio.get_running_loop()
    f = None

    try:
        f = open(LOG_FILE_PATH, "r", encoding="utf-16", errors="ignore")
        log_follower = follow(f)

        while True:
            line = await loop.run_in_executor(None, next, log_follower)

            if line.startswith(DATA_PREFIX):
                game_data = line[len(DATA_PREFIX):].strip()
                log(f"提取到游戏数据：{game_data}")

                if game_data == "StartConnectWebSocketServer":
                    if not ws_conn or ws_conn.closed:
                        log("收到启动指令，创建WebSocket任务...")
                        asyncio.create_task(connect_websocket())
                    else:
                        log("WebSocket已连接")
                elif TargetAPPId and FrontClientId and ws_conn and not ws_conn.closed:
                    if game_data.startswith("StrengthSet:"):
                        strength = game_data[len("StrengthSet:"):].strip()
                        await send_command_to_app(f'strength-1+2+{strength}')
                        await send_command_to_app(f'strength-2+2+{strength}')
    except StopIteration:
        log("日志迭代结束")
    except FileNotFoundError:
        log(f"日志文件不存在：{LOG_FILE_PATH}")
    except Exception as e:
        log(f"日志监控错误：{str(e)}")
    finally:
        if f:
            f.close()
        await asyncio.sleep(10)
        await monitor_game_log()


async def main():
    await monitor_game_log()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("程序已退出")
    except Exception as e:
        log(f"程序崩溃：{str(e)}")