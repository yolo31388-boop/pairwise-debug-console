"""调试控制台 - 带6个bug"""
from dataclasses import dataclass, field
import time

@dataclass
class FrameStats:
    frame_time: float
    fps: float

class DebugConsole:
    def __init__(self):
        self.frame_times: list[float] = []
        self.command_history: list[str] = []  # bug6: 不持久化
        self.modules = {}
        self.update_interval = 0  # bug5: 和渲染绑定

    def record_frame(self, dt):
        self.frame_times.append(dt)
        if len(self.frame_times) > 60:
            self.frame_times.pop(0)

    def get_fps(self):
        # bug1: 简单平均
        if not self.frame_times:
            return 0
        avg = sum(self.frame_times) / len(self.frame_times)
        return 1.0 / avg if avg > 0 else 0

    def get_memory(self, obj):
        # bug2: 只算对象本身
        return sys.getsizeof(obj)

    def parse_command(self, cmd):
        # bug3: 简单split
        return cmd.split()

    def hot_reload(self, module_name):
        # bug4: 不清理旧状态
        if module_name in self.modules:
            del self.modules[module_name]
        self.modules[module_name] = "reloaded"
