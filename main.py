"""
AstrBot 定时暂停服务插件

功能：
- 在指定时间段内暂停 LLM 对话调用（指令和其他功能不受影响）
- 支持跨天时间设置（如 23:00 ~ 08:00）
- 配置持久化存储（WebUI 可视化配置）
- 管理员权限控制
- 暂停期间自定义回复消息
"""

import asyncio
from datetime import datetime, time
from typing import Optional

import pytz

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star


class PauseSchedulePlugin(Star):
    """定时暂停服务插件"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 当前是否处于暂停状态
        self._paused: bool = False
        # 后台检查任务
        self._check_task: Optional[asyncio.Task] = None
        # 通知状态追踪（避免重复通知）
        self._notified_pause: bool = False

        # 启动后台时间检查任务
        self._start_check_task()
        # 注册 Web API（供 WebUI Settings Page 使用）
        self._register_web_apis()

        logger.info(
            f"[PauseSchedule] 插件已加载 | "
            f"启用: {self.config.get('enabled', True)} | "
            f"暂停时段: {self.config.get('pause_start', '23:00')} ~ "
            f"{self.config.get('pause_end', '08:00')}"
        )

    def _register_web_apis(self):
        """注册 Web API 端点，供 Settings Page 调用"""
        from astrbot.api.web import json_response

        plugin_name = "astrbot_plugin_pause_schedule"

        async def handle_config():
            from astrbot.api.web import request

            if request.method == "GET":
                return json_response({"code": 0, "data": dict(self.config)})

            # POST: 保存配置
            payload = await request.json(default={})
            for key in (
                "enabled",
                "pause_start",
                "pause_end",
                "pause_message",
                "admin_only",
                "exclude_admin",
                "notify_on_pause",
                "timezone",
            ):
                if key in payload:
                    self.config[key] = payload[key]
            self.config.save_config()
            self._update_pause_state()
            return json_response({"code": 0, "msg": "ok"})

        async def get_status():
            tz = self._get_tz()
            now = datetime.now(tz)
            return json_response(
                {
                    "code": 0,
                    "data": {
                        "paused": self._paused,
                        "enabled": self.config.get("enabled", True),
                        "current_time": now.strftime("%Y-%m-%d %H:%M:%S"),
                        "pause_start": self.config.get("pause_start", "23:00"),
                        "pause_end": self.config.get("pause_end", "08:00"),
                    },
                }
            )

        self.context.register_web_api(
            f"/{plugin_name}/config",
            handle_config,
            ["GET", "POST"],
            "Get or save pause schedule config",
        )
        self.context.register_web_api(
            f"/{plugin_name}/status", get_status, ["GET"], "Get pause schedule status"
        )

    def _start_check_task(self):
        """启动后台定时检查任务"""
        if self._check_task is not None and not self._check_task.done():
            self._check_task.cancel()
        self._check_task = asyncio.create_task(self._time_check_loop())

    async def _time_check_loop(self):
        """后台循环：每 30 秒检查一次当前时间是否在暂停区间内"""
        try:
            while True:
                try:
                    self._update_pause_state()
                except Exception as e:
                    logger.error(f"[PauseSchedule] 时间检查异常: {e}")
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            pass

    def _get_tz(self):
        """获取配置的时区对象"""
        tz_name = self.config.get("timezone", "Asia/Shanghai")
        try:
            return pytz.timezone(tz_name)
        except pytz.UnknownTimeZoneError:
            logger.warning(f"[PauseSchedule] 未知时区 '{tz_name}'，使用 Asia/Shanghai")
            return pytz.timezone("Asia/Shanghai")

    def _parse_time(self, time_str: str) -> time:
        """解析 HH:MM 格式的时间字符串"""
        try:
            parts = time_str.strip().split(":")
            return time(int(parts[0]), int(parts[1]))
        except (ValueError, IndexError):
            logger.warning(f"[PauseSchedule] 无效的时间格式: '{time_str}'，使用默认值")
            return time(0, 0)

    def _is_in_pause_window(self, now_time: time) -> bool:
        """
        判断当前时间是否在暂停窗口内
        支持跨天：如 23:00 ~ 08:00 表示从23点到次日8点
        """
        start = self._parse_time(self.config.get("pause_start", "23:00"))
        end = self._parse_time(self.config.get("pause_end", "08:00"))

        if start <= end:
            # 不跨天：start <= now < end
            return start <= now_time < end
        else:
            # 跨天：now >= start 或 now < end
            return now_time >= start or now_time < end

    def _update_pause_state(self):
        """更新暂停状态，处理通知"""
        if not self.config.get("enabled", True):
            if self._paused:
                self._paused = False
                self._notified_pause = False
                logger.info("[PauseSchedule] 定时暂停已禁用，退出暂停状态")
            return

        tz = self._get_tz()
        now_time = datetime.now(tz).time()
        was_paused = self._paused
        self._paused = self._is_in_pause_window(now_time)

        # 状态变化时记录日志
        if self._paused and not was_paused:
            logger.info("[PauseSchedule] ⏸️ 进入暂停时段，LLM 服务已暂停")
            if self.config.get("notify_on_pause", False) and not self._notified_pause:
                self._notified_pause = True
                asyncio.create_task(self._send_pause_notification(entering=True))
        elif not self._paused and was_paused:
            logger.info("[PauseSchedule] ▶️ 退出暂停时段，LLM 服务已恢复")
            self._notified_pause = False
            if self.config.get("notify_on_pause", False):
                asyncio.create_task(self._send_pause_notification(entering=False))

    async def _send_pause_notification(self, entering: bool):
        """向管理员发送暂停/恢复通知"""
        try:
            pause_msg = self._format_pause_message()
            if entering:
                text = f"⏸️ LLM 服务已进入暂停时段\n{pause_msg}"
            else:
                text = "▶️ LLM 服务已恢复运行"
            # 通过 context 发送通知给管理员
            # 这里通过 logger 输出，管理员可在日志中查看
            logger.info(f"[PauseSchedule] 通知: {text}")
        except Exception as e:
            logger.error(f"[PauseSchedule] 发送通知失败: {e}")

    def _format_pause_message(self) -> str:
        """格式化暂停提示消息"""
        template = self.config.get(
            "pause_message",
            "⏰ 当前为休息时间，LLM 服务已暂停。\n"
            "服务恢复时间：{end_time}\n"
            "请稍后再试～",
        )
        return template.format(
            start_time=self.config.get("pause_start", "23:00"),
            end_time=self.config.get("pause_end", "08:00"),
        )

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        """检查消息发送者是否为管理员"""
        try:
            from astrbot.api.event.filter import PermissionType

            # 通过 AstrBot 内置权限系统判断
            role = event.get_sender_role()
            return role == "admin"
        except Exception:
            # 降级方案：检查消息对象中的权限字段
            try:
                msg_obj = event.message_obj
                if hasattr(msg_obj, "sender") and hasattr(msg_obj.sender, "role"):
                    return msg_obj.sender.role == "admin"
            except Exception:
                pass
            return False

    # ==================== LLM 请求拦截 ====================

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req):
        """
        仅拦截 LLM 对话调用。
        指令、工具调用、其他插件功能不受影响。
        """
        # 未启用或未暂停，放行
        if not self.config.get("enabled", True) or not self._paused:
            return

        # 管理员免除暂停限制
        if self.config.get("exclude_admin", True) and self._is_admin(event):
            return

        # 暂停中：阻止 LLM 调用，向用户发送提示
        await event.send(event.plain_result(self._format_pause_message()))
        event.stop_event()

    # ==================== 管理员指令 ====================

    @filter.command("pause_status")
    async def pause_status(self, event: AstrMessageEvent):
        """查看当前暂停状态"""
        if self.config.get("admin_only", True) and not self._is_admin(event):
            yield event.plain_result("❌ 仅管理员可使用此指令")
            return

        status = "⏸️ 暂停中" if self._paused else "▶️ 运行中"
        enabled = "✅ 已启用" if self.config.get("enabled", True) else "❌ 已禁用"

        tz = self._get_tz()
        now_str = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

        lines = [
            "📋 定时暂停服务状态",
            "━━━━━━━━━━━━━━━",
            f"当前状态: {status}",
            f"功能开关: {enabled}",
            f"暂停时段: {self.config.get('pause_start', '23:00')} ~ {self.config.get('pause_end', '08:00')}",
            f"时区: {self.config.get('timezone', 'Asia/Shanghai')}",
            f"当前时间: {now_str}",
            f"管理员免除: {'✅' if self.config.get('exclude_admin', True) else '❌'}",
        ]
        yield event.plain_result("\n".join(lines))

    @filter.command("pause_toggle")
    async def pause_toggle(self, event: AstrMessageEvent):
        """切换定时暂停功能的启用/禁用状态"""
        if self.config.get("admin_only", True) and not self._is_admin(event):
            yield event.plain_result("❌ 仅管理员可使用此指令")
            return

        current = self.config.get("enabled", True)
        self.config["enabled"] = not current
        self.config.save_config()

        if not current:
            # 从禁用变为启用，重新启动检查任务
            self._start_check_task()
            yield event.plain_result("✅ 定时暂停功能已启用")
        else:
            # 从启用变为禁用
            self._paused = False
            yield event.plain_result("❌ 定时暂停功能已禁用")

    @filter.command("pause_set")
    async def pause_set(self, event: AstrMessageEvent, start: str, end: str):
        """
        设置暂停时间段

        用法: /pause_set 23:00 08:00
        """
        if self.config.get("admin_only", True) and not self._is_admin(event):
            yield event.plain_result("❌ 仅管理员可使用此指令")
            return

        # 验证时间格式
        try:
            self._parse_time(start)
            self._parse_time(end)
        except Exception:
            yield event.plain_result("❌ 时间格式错误，请使用 HH:MM 格式（如 23:00）")
            return

        self.config["pause_start"] = start
        self.config["pause_end"] = end
        self.config.save_config()

        # 立即更新暂停状态
        self._update_pause_state()

        cross_day = "（跨天）" if self._parse_time(start) > self._parse_time(end) else ""
        yield event.plain_result(
            f"✅ 暂停时段已更新\n"
            f"⏸️ {start} ~ {end} {cross_day}\n"
            f"状态将在下一个检查周期生效"
        )

    # ==================== 生命周期 ====================

    async def terminate(self):
        """插件卸载/停用时清理资源"""
        if self._check_task is not None and not self._check_task.done():
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass
        self._paused = False
        logger.info("[PauseSchedule] 插件已卸载")
