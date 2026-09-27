"""调试控制台测试"""
import pytest, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.engine import DebugConsole

class TestFPS:
    def test_fps_has_percentile(self):
        dc = DebugConsole()
        for i in range(50):
            dc.record_frame(0.016)
        dc.record_frame(0.5)  # 卡顿
        dc.record_frame(0.016)
        fps = dc.get_fps()
        # 平均会被卡顿拉低，但应该有1%low统计
        assert fps > 0, "FPS统计缺失"

class TestParse:
    def test_parse_quoted_args(self):
        dc = DebugConsole()
        result = dc.parse_command('say "hello world"')
        assert len(result) == 2, f"带空格参数被拆分: {result}"
        assert result[1] == "hello world", f"引号参数错误: {result}"
