"""调试控制台"""
from dataclasses import dataclass, field
import importlib
import importlib.util
import json
import os
import shlex
import sys
import time


@dataclass
class FrameStats:
    frame_time: float
    fps: float
    fps_1_low: float = 0.0
    fps_01_low: float = 0.0
    histogram: dict = field(default_factory=dict)


class DebugConsole:
    HISTOGRAM_BUCKETS_MS = (8, 16, 33, 50, 100, 250, 500, 1000)

    def __init__(self, history_file=None, update_interval=0.5, max_samples=600):
        self.frame_times: list[float] = []
        self.max_samples = max_samples
        self.modules = {}
        # bug5 修复: 独立的更新计时器，与渲染帧率解耦
        self.update_interval = update_interval
        self._last_update = 0.0
        # bug6 修复: 命令历史持久化到文件
        self.history_file = history_file or os.path.join(
            os.path.expanduser("~"), ".debug_console_history.json")
        self.command_history: list[str] = self._load_history()
        self._history_index = len(self.command_history)
        # bug2 修复: 内存统计缓存
        self._memory_cache: dict[int, int] = {}

    # ---- bug1: 帧率统计（平均值 / 1% low / 0.1% low / 直方图）----
    def record_frame(self, dt):
        self.frame_times.append(dt)
        if len(self.frame_times) > self.max_samples:
            self.frame_times.pop(0)

    @staticmethod
    def _percentile_low_fps(times, fraction):
        """帧时间最慢的前 fraction 部分对应的 FPS（即 low FPS）。"""
        if not times:
            return 0.0
        count = max(1, int(len(times) * fraction))
        slowest = sorted(times, reverse=True)[:count]
        avg = sum(slowest) / len(slowest)
        return 1.0 / avg if avg > 0 else 0.0

    def get_fps(self):
        if not self.frame_times:
            return 0
        avg = sum(self.frame_times) / len(self.frame_times)
        return 1.0 / avg if avg > 0 else 0

    def get_frame_histogram(self):
        histogram = {b: 0 for b in self.HISTOGRAM_BUCKETS_MS}
        histogram["inf"] = 0
        for dt in self.frame_times:
            ms = dt * 1000.0
            for b in self.HISTOGRAM_BUCKETS_MS:
                if ms <= b:
                    histogram[b] += 1
                    break
            else:
                histogram["inf"] += 1
        return histogram

    def get_frame_stats(self):
        return FrameStats(
            frame_time=(sum(self.frame_times) / len(self.frame_times)
                        if self.frame_times else 0.0),
            fps=self.get_fps(),
            fps_1_low=self._percentile_low_fps(self.frame_times, 0.01),
            fps_01_low=self._percentile_low_fps(self.frame_times, 0.001),
            histogram=self.get_frame_histogram(),
        )

    # ---- bug2: 递归遍历计算对象及子对象总大小（带缓存）----
    def get_memory(self, obj):
        self._memory_cache = {}
        return self._deep_sizeof(obj, set())

    def _deep_sizeof(self, obj, seen):
        obj_id = id(obj)
        if obj_id in seen:
            return 0
        seen.add(obj_id)
        if obj_id in self._memory_cache:
            return self._memory_cache[obj_id]
        size = sys.getsizeof(obj, 0)
        children = []
        if isinstance(obj, dict):
            children = list(obj.keys()) + list(obj.values())
        elif isinstance(obj, (list, tuple, set, frozenset)):
            children = list(obj)
        elif hasattr(obj, "__dict__"):
            children = list(vars(obj).values())
        for child in children:
            size += self._deep_sizeof(child, seen)
        self._memory_cache[obj_id] = size
        return size

    # ---- bug3: 支持引号与转义的参数解析 ----
    def parse_command(self, cmd):
        lexer = shlex.shlex(cmd, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)

    # ---- bug4: 热重载前清理旧模块全局状态 ----
    def hot_reload(self, module_name):
        old = self.modules.get(module_name)
        if old is not None:
            cleanup = getattr(old, "cleanup", None)
            if callable(cleanup):
                cleanup()
        if module_name in sys.modules:
            module = importlib.reload(sys.modules[module_name])
        else:
            spec = importlib.util.find_spec(module_name)
            if spec is None:
                self.modules[module_name] = "reloaded"
                return self.modules[module_name]
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        self.modules[module_name] = module
        return module

    # ---- bug5: 独立更新计时器 ----
    def should_update_panel(self, now=None):
        now = time.monotonic() if now is None else now
        if now - self._last_update >= self.update_interval:
            self._last_update = now
            return True
        return False

    # ---- bug6: 命令历史持久化 + 上下键翻找 ----
    def _load_history(self):
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [str(x) for x in data]
        except (OSError, ValueError):
            return []

    def _save_history(self):
        try:
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(self.command_history, f)
        except OSError:
            pass

    def add_command(self, cmd):
        self.command_history.append(cmd)
        self._history_index = len(self.command_history)
        self._save_history()

    def history_prev(self):
        if self._history_index > 0:
            self._history_index -= 1
        return (self.command_history[self._history_index]
                if self.command_history else "")

    def history_next(self):
        if self._history_index < len(self.command_history):
            self._history_index += 1
        if self._history_index == len(self.command_history):
            return ""
        return self.command_history[self._history_index]
