"""Phase 1 核心逻辑单元测试（纯逻辑 + 假 SSH 池，无 paramiko 依赖）。

运行：
    cd mcp && python -m tests
（目标机器上也可用 pytest：uv run --group dev pytest mcp/tests）
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from buganalyzer_mcp import env_registry, log_tools, policy
from buganalyzer_mcp.paths import config_dir
from buganalyzer_mcp.server import (
    _State,
    fetch_logs_core,
    kb_search_core,
    list_environments_core,
    ssh_run_core,
)

ENVS_EXAMPLE = config_dir() / "envs.example.toml"
POLICY_EXAMPLE = config_dir() / "policy.example.toml"


class FakePool:
    """假 SSH 池：记录调用，返回固定输出。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def run(self, env, argv, timeout=None):
        self.calls.append(("run", env.name, list(argv), timeout))
        return "jps output\n12345 OrderServiceMain\n"

    def run_command(self, env, cmd, timeout=None):
        self.calls.append(("run_command", env.name, cmd, timeout))
        return (
            "2026-08-30 09:59:59 INFO startup ok\n"
            "2026-08-30 10:00:01 ERROR ConnectException: conn refused\n"
            "2026-08-30 10:00:02 ERROR boom\n"
        )


def make_state() -> _State:
    st = _State()
    st.envs = env_registry.load_envs(ENVS_EXAMPLE)
    st.policy = policy.load_policy(POLICY_EXAMPLE)
    st.pool = FakePool()
    return st


def _tmp_dir() -> str:
    """沙箱内临时目录：建在工作区里（系统临时目录不可写）。"""
    base = Path(__file__).resolve().parents[1] / ".test-tmp"
    base.mkdir(exist_ok=True)
    return str(base)


# ---------------------------------------------------------------- env_registry

def test_env_registry_loads_example():
    envs = env_registry.load_envs(ENVS_EXAMPLE)
    assert "test-order" in envs
    e = envs["test-order"]
    assert e.tier == "test" and e.mode == "docker"
    assert e.timeout == 15 and e.timezone == "Asia/Shanghai"
    assert e.log_path_for("order-service") == "/var/log/order-service/app.log"
    assert envs["prod-order"].is_prod


def test_env_registry_rejects_bad_tier():
    with tempfile.TemporaryDirectory(dir=_tmp_dir()) as td:
        p = Path(td) / "envs.toml"
        p.write_text('[env.x]\ntier = "danger"\nssh = "a@b"\n', encoding="utf-8")
        try:
            env_registry.load_envs(p)
        except env_registry.EnvConfigError as exc:
            assert "tier 非法" in str(exc)
        else:
            raise AssertionError("应当拒绝非法 tier")


# ---------------------------------------------------------------- policy

def test_policy_decisions_from_example():
    pol = policy.load_policy(POLICY_EXAMPLE)
    assert pol.decision("list_environments", None, "test") == "allow"
    assert pol.decision("ssh_run", "readonly", "test") == "allow"
    assert pol.decision("ssh_run", "confirm", "test") == "confirm"
    assert pol.decision("ssh_run", "readonly", "prod") == "confirm"
    assert pol.decision("fetch_logs", None, "prod") == "confirm"
    assert pol.decision("ssh_run_raw", None, "prod") == "deny"
    assert pol.decision("whatever", None, "test") == "deny"  # allowlist 默认拒绝


def test_policy_ssh_level():
    pol = policy.load_policy(POLICY_EXAMPLE)
    assert pol.ssh_level(["tail", "-n", "100"]) == "readonly"
    assert pol.ssh_level(["docker", "ps"]) == "readonly"      # docker 在 readonly
    assert pol.ssh_level(["docker", "exec", "app", "ls"]) == "confirm"  # 更具体条目优先
    assert pol.ssh_level(["rm", "-rf", "/"]) is None          # 不在白名单


# ---------------------------------------------------------------- log_tools

def test_log_filter_and_truncate():
    lines = [
        "2026-08-30 09:59:59 INFO ok",
        "2026-08-30 10:00:01 ERROR ConnectException",
        "2026-08-30 10:00:02 ERROR boom",
        "no timestamp line",
    ]
    out, matched, truncated = log_tools.filter_and_truncate(
        lines, pattern="ERROR", since="2026-08-30 10:00:02", timezone="Asia/Shanghai"
    )
    assert matched == 1  # since 过滤掉 10:00:01 那条 ERROR
    assert out == ["2026-08-30 10:00:02 ERROR boom"]
    assert truncated is False

    out2, matched2, _ = log_tools.filter_and_truncate(
        lines, pattern=None, since=None, timezone="UTC", max_lines=2
    )
    assert matched2 == 4 and len(out2) == 2  # 截断到最近 2 行
    assert out2 == lines[-2:]


def test_log_path_validation():
    log_tools.validate_log_path("/var/log/app/*.log")  # 合法
    for bad in ("/var/log/a; rm -rf /", "/x > /etc/hosts", "/x && y", "/x `id`"):
        try:
            log_tools.validate_log_path(bad)
        except log_tools.LogError:
            pass
        else:
            raise AssertionError(f"应拒绝 log_path: {bad!r}")


# ---------------------------------------------------------------- server core

def test_ssh_run_whitelist_and_decision():
    st = make_state()
    res = ssh_run_core(st, "test-order", "jps")
    assert res["decision"] == "auto" and "12345" in res["output"]
    assert st.pool.calls[0][:3] == ("run", "test-order", ["jps"])

    res2 = ssh_run_core(st, "test-order", "docker", ["exec", "app", "ls"])
    assert res2["decision"] == "confirm"


def test_ssh_run_rejects_non_whitelist():
    st = make_state()
    try:
        ssh_run_core(st, "test-order", "rm", ["-rf", "/"])
    except ValueError as exc:
        assert "不在白名单" in str(exc)
    else:
        raise AssertionError("非白名单命令应被拒绝")


def test_ssh_run_rejects_prod_phase1():
    st = make_state()
    for cmd in ("jps",):
        try:
            ssh_run_core(st, "prod-order", cmd)
        except ValueError as exc:
            assert "Phase 1 不接入生产" in str(exc)
        else:
            raise AssertionError("Phase 1 必须拒绝 prod 环境连接")


def test_fetch_logs_core():
    st = make_state()
    res = fetch_logs_core(st, "test-order", "order-service", pattern="ERROR", tail=100)
    assert res["matched"] == 2
    assert res["returned"] == 2
    assert res["command"].startswith("tail -n 100")
    assert st.pool.calls[0][0] == "run_command"


def test_fetch_logs_requires_log_path():
    st = make_state()
    try:
        fetch_logs_core(st, "test-order", "unknown-service")
    except ValueError as exc:
        assert "不在环境" in str(exc) or "日志路径" in str(exc)
    else:
        raise AssertionError("未配置日志路径/未知服务应报错")


def test_list_environments_core():
    st = make_state()
    envs = list_environments_core(st)
    names = [e["name"] for e in envs]
    assert "test-order" in names and "prod-order" in names
    assert [e for e in envs if e["name"] == "prod-order"][0]["prod"] is True


# ---------------------------------------------------------------- kb_tools

def test_kb_search_and_tags():
    with tempfile.TemporaryDirectory(dir=_tmp_dir()) as td:
        kb = Path(td)
        cases = kb / "cases"
        cases.mkdir(parents=True)
        (cases / "a-conn-timeout.md").write_text(
            "---\ntitle: 连接超时 502\ntags: [java, http, timeout]\n"
            "symptom: 高峰期 502\nroot_cause: 连接池耗尽\n---\n## 现象\n连接池耗尽导致 502\n",
            encoding="utf-8",
        )
        (cases / "b-oom.md").write_text(
            "---\ntitle: OOM\nsymptom: 内存暴涨\n---\n## 现象\nOutOfMemoryError\n",
            encoding="utf-8",
        )
        hits = kb_search_core(None, "连接池", top_k=5, kb_dir=kb)
        assert hits and hits[0]["title"] == "连接超时 502"
        assert hits[0]["path"].startswith("cases/")
        tagged = kb_search_core(None, "连接池", tags=["java"], kb_dir=kb)
        assert len(tagged) == 1
        none = kb_search_core(None, "连接池", tags=["python"], kb_dir=kb)
        assert none == []


# ---------------------------------------------------------------- kb_import

def test_kb_import_idempotent_and_index():
    from kb_import.importer import import_csv
    from kb_import.index import rebuild_index

    with tempfile.TemporaryDirectory(dir=_tmp_dir()) as td:
        root = Path(td)
        csv_path = root / "tickets.csv"
        csv_path.write_text(
            "ticket_id,title,symptom,root_cause,service,env,severity,tags,related_mr,created\n"
            'TKT-1,订单服务连接超时,高峰期502,连接池耗尽,order-service,prod,P1,"java,http",https://git/x/1,2026-08-01\n'
            'TKT-2,支付超时,网关504,下游慢,payment-service,prod,P2,http,https://git/x/2,2026-08-02\n',
            encoding="utf-8",
        )
        out = root / "cases"
        r1 = import_csv(csv_path, out)
        assert len(r1.created) == 2 and not r1.failed
        files = list(out.glob("*.md"))
        assert len(files) == 2
        assert "ticket_id: TKT-1" in files[0].read_text(encoding="utf-8")

        r2 = import_csv(csv_path, out)  # 幂等：跳过
        assert len(r2.skipped) == 2 and not r2.created

        r3 = import_csv(csv_path, out, force=True)  # 覆盖
        assert len(r3.created) == 2

        n = rebuild_index(root)
        assert n == 2
        index_text = (root / "INDEX.md").read_text(encoding="utf-8")
        assert "订单服务连接超时" in index_text and "支付超时" in index_text
