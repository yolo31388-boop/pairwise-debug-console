"""调试控制台"""
from dataclasses import dataclass, field
import importlib
import importlib.util
import json
import os
import shlex
import sys
import time
import types


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
        # bug6 修复: 命令历史持久化到文件
        self.history_file = history_file or os.path.join(
            os.path.expanduser("~"), ".debug_console_history")
        self.command_history: list[str] = self._load_history()
        self._history_index = len(self.command_history)
        self.modules = {}
        # bug5 修复: 性能面板使用独立计时器, 与渲染帧率解耦
        self.update_interval = update_interval
        self._last_panel_update = 0.0
        self._cached_panel = None
        # bug2 修复: 内存统计的递归大小缓存 {id(obj): size}
        self._sizeof_cache: dict[int, int] = {}

    # ---------- bug1: 帧率统计 (平均 / 1% low / 0.1% low / 直方图) ----------
    def record_frame(self, dt):
        self.frame_times.append(dt)
        if len(self.frame_times) > self.max_samples:
            self.frame_times.pop(0)

    @staticmethod
    def _low_fps(samples, fraction):
        """帧时间最长的 fraction 部分对应的 FPS (如 1% low)。"""
        if not samples:
            return 0.0
        count = max(1, int(len(samples) * fraction + 0.5))
        worst = sorted(samples, reverse=True)[:count]
        avg = sum(worst) / len(worst)
        return 1.0 / avg if avg > 0 else 0.0

    def get_fps(self):
        if not self.frame_times:
            return 0
        avg = sum(self.frame_times) / len(self.frame_times)
        return 1.0 / avg if avg > 0 else 0

    def get_frame_stats(self):
        """返回平均值、1% low、0.1% low 和帧时间直方图。"""
        samples = self.frame_times
        histogram = {}
        for dt in samples:
            ms = dt * 1000.0
            label = f">{self.HISTOGRAM_BUCKETS_MS[-1]}ms"
            for bucket in self.HISTOGRAM_BUCKETS_MS:
                if ms <= bucket:
                    label = f"<= {bucket}ms"
                    break
            histogram[label] = histogram.get(label, 0) + 1
        return FrameStats(
            frame_time=samples[-1] if samples else 0.0,
            fps=self.get_fps(),
            fps_1_low=self._low_fps(samples, 0.01),
            fps_01_low=self._low_fps(samples, 0.001),
            histogram=histogram,
        )

    # ---------- bug2: 递归内存统计 (带缓存) ----------
    def get_memory(self, obj):
        """递归遍历对象图, 计算对象及其引用的子对象总字节数。"""
        self._sizeof_cache.clear()
        seen = set()
        total = 0
        stack = [obj]
        while stack:
            current = stack.pop()
            oid = id(current)
            if oid in seen:
                continue
            seen.add(oid)
            size = self._sizeof_cache.get(oid)
            if size is None:
                size = sys.getsizeof(current, 0)
                self._sizeof_cache[oid] = size
            total += size
            stack.extend(self._iter_referents(current))
        return total

    @staticmethod
    def _iter_referents(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                yield key
                yield value
        elif isinstance(obj, (list, tuple, set, frozenset)):
            yield from obj
        elif isinstance(obj, (types.ModuleType, types.FunctionType,
                              types.BuiltinFunctionType, type)):
            return  # 不深入模块/函数/类, 避免把整个世界算进来
        elif hasattr(obj, "__dict__"):
            yield vars(obj)
        if hasattr(obj, "__slots__") and not isinstance(obj, type):
            for slot in obj.__slots__:
                if hasattr(obj, slot):
                    yield getattr(obj, slot)

    # ---------- bug3: 支持引号和转义的参数解析 ----------
    def parse_command(self, cmd):
        lexer = shlex.shlex(cmd, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)

    # ---------- bug4: 热重载前清理旧模块全局状态 ----------
    def hot_reload(self, module_name):
        old = self.modules.get(module_name)
        if old is not None:
            cleanup = getattr(old, "cleanup", None)
            if callable(cleanup):
                cleanup()  # 让旧模块自己清理全局状态
            if isinstance(old, types.ModuleType):
                # 沙箱隔离: 在全新命名空间中重新执行模块代码
                spec = importlib.util.find_spec(module_name)
                if spec and spec.loader:
                    fresh = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(fresh)
                    self.modules[module_name] = fresh
                    sys.modules[module_name] = fresh
                    return fresh
                self.modules[module_name] = importlib.reload(old)
                return self.modules[module_name]
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            module = "reloaded"
        self.modules[module_name] = module
        return module

    # ---------- bug5: 独立计时器驱动的性能面板 ----------
    def should_update_panel(self, now=None):
        now = time.monotonic() if now is None else now
        return now - self._last_panel_update >= self.update_interval

    def update_panel(self, now=None):
        """按自身计时器刷新面板, 与渲染帧率无关; 未到间隔返回缓存。"""
        now = time.monotonic() if now is None else now
        if self._cached_panel is not None and not self.should_update_panel(now):
            return self._cached_panel
        self._last_panel_update = now
        stats = self.get_frame_stats()
        self._cached_panel = {
            "fps": stats.fps,
            "fps_1_low": stats.fps_1_low,
            "fps_01_low": stats.fps_01_low,
            "histogram": dict(stats.histogram),
        }
        return self._cached_panel

    # ---------- bug6: 命令历史持久化 + 上下翻找 ----------
    def _load_history(self):
        try:
            with open(self.history_file, "r", encoding="utf-8") as fh:
                return [line for line in fh.read().splitlines() if line]
        except (OSError, UnicodeDecodeError):
            return []

    def _save_history(self):
        try:
            with open(self.history_file, "w", encoding="utf-8") as fh:
                fh.write("\n".join(self.command_history))
                if self.command_history:
                    fh.write("\n")
        except OSError:
            pass

    def add_command(self, cmd):
        if cmd:
            self.command_history.append(cmd)
            self._history_index = len(self.command_history)
            self._save_history()

    def history_prev(self):
        """上键: 返回更早的历史命令。"""
        if self._history_index > 0:
            self._history_index -= 1
        return self.command_history[self._history_index] if self.command_history else ""

    def history_next(self):
        """下键: 返回更晚的历史命令, 越过最新返回空串。"""
        if self._history_index < len(self.command_history):
            self._history_index += 1
        if self._history_index == len(self.command_history):
            return ""
        return self.command_history[self._history_index]
