"""测试默认与本机环境隔离：不使用真实的 Codex 登录，也不读本机私有档。个别测试需要时自行覆盖。"""
import pytest


@pytest.fixture(autouse=True)
def _no_real_codex(monkeypatch):
    monkeypatch.setenv("SANSHENG_IMAGE_CODEX", "/nonexistent/codex")
    try:
        from stylebook.backends import providers
        providers._CODEX_LOGIN.clear()
    except Exception:  # 导入路径尚未就绪的测试文件里忽略
        pass


@pytest.fixture(autouse=True)
def _single_review_sample(monkeypatch):
    """历史测试按「一次请求一次结论」写成；多次采样聚合的行为在 test_qa_review_reliability.py 里显式开启。"""
    monkeypatch.setenv("SANSHENG_IMAGE_QA_SAMPLES", "1")
