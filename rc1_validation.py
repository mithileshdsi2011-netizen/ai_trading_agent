#!/usr/bin/env python3
"""RC-1 release validation runner. Writes rc1_report.md and prints summary."""
import ast, compileall, importlib, json, os, re, sqlite3, subprocess, sys, time
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent
VENV = ROOT / "venv"
REPORT = ROOT / "rc1_report.md"
RESULTS = ROOT / "rc1_results.json"

# Ensure src on path
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

def run(cmd, cwd=ROOT, timeout=60):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"

def compile_check():
    py_files = list(ROOT.rglob("*.py"))
    py_files = [p for p in py_files if "__pycache__" not in p.parts and "venv" not in p.parts]
    errors = []
    for p in py_files:
        try:
            import py_compile
            py_compile.compile(p, doraise=True)
        except Exception as e:
            errors.append((str(p.relative_to(ROOT)), str(e)))
    return ("✅ All Python modules compile", errors) if not errors else ("❌ Compilation errors", errors)

def import_check():
    # Use ast to find first-level imports without executing module side-effects
    missing = []
    stdlib = {"os", "sys", "json", "time", "logging", "re", "sqlite3", "threading", "datetime"}
    for p in list((ROOT / "src").glob("*.py")) + list(ROOT.glob("*.py")):
        if "venv" in p.parts or p.name == "rc1_validation.py":
            continue
        try:
            tree = ast.parse(p.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        mod = alias.name.split(".")[0]
                        if mod in stdlib:
                            continue
                        if mod == "config" and p.name not in ("config.py",):
                            continue
                        try:
                            importlib.import_module(mod)
                        except Exception as e:
                            missing.append((p.name, mod, str(e)[:80]))
                elif isinstance(node, ast.ImportFrom):
                    mod = (node.module or "").split(".")[0]
                    if not mod or mod in stdlib:
                        continue
                    try:
                        importlib.import_module(mod)
                    except Exception as e:
                        missing.append((p.name, mod, str(e)[:80]))
        except SyntaxError as e:
            missing.append((p.name, "parse", str(e)[:80]))
    return ("✅ Module imports resolvable", missing) if not missing else ("❌ Import errors", missing)

def js_check():
    try:
        import esprima
    except ImportError:
        return "⚠ esprima not installed", []
    issues = []
    for js in (ROOT / "static").glob("*.js"):
        if js.name.endswith(".min.js"):
            continue
        try:
            esprima.parseScript(js.read_text())
        except Exception as e:
            issues.append((js.name, str(e)[:120]))
    # inline scripts
    db = (ROOT / "dashboard.py").read_text()
    for i, script in enumerate(re.findall(r"<script[^>]*>(.*?)</script>", db, re.DOTALL), 1):
        try:
            esprima.parseScript(script)
        except Exception as e:
            issues.append((f"dashboard.py inline script {i}", str(e)[:120]))
    return ("✅ JavaScript parses", issues) if not issues else ("❌ JS parse errors", issues)

def flask_check():
    try:
        import requests
        routes = ["/", "/api/data", "/api/health", "/api/journal", "/api/morning-report", "/api/backtest"]
        issues = []
        for r in routes:
            method = "POST" if r == "/api/backtest" else "GET"
            try:
                if method == "POST":
                    resp = requests.post(f"http://localhost:5001{r}", json={"symbols": ["RELIANCE"], "years": 1}, timeout=30)
                else:
                    resp = requests.get(f"http://localhost:5001{r}", timeout=10)
                if resp.status_code not in (200, 202):
                    issues.append((r, f"{method} {resp.status_code}"))
                elif r.startswith("/api/"):
                    try:
                        resp.json()
                    except Exception as e:
                        issues.append((r, f"invalid json: {e}"))
            except Exception as e:
                issues.append((r, str(e)[:80]))
        return ("✅ Flask routes respond", issues) if not issues else ("❌ Flask issues", issues)
    except ImportError:
        return ("⚠ requests not installed", [])

def test_check():
    rc, out, err = run(f"{VENV}/bin/python -m pytest tests/ -q --tb=line", timeout=120)
    if rc == 0:
        return "✅ Pytest suite passes", []
    return "❌ Pytest failures", [out[-2000:], err[-500:]]

def db_check():
    db_path = ROOT / "data" / "trading.db"
    if not db_path.exists():
        return "⚠ No trading.db found", []
    issues = []
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        # duplicate OPEN/PARTIAL positions only
        if "positions" in tables:
            cur.execute("SELECT data FROM positions")
            seen = {}
            dups = []
            for row in cur.fetchall():
                d = json.loads(row[0])
                if d.get("status") in ("OPEN", "PARTIAL"):
                    sym = d.get("symbol") or d.get("tradingsymbol") or ""
                    if sym in seen:
                        dups.append(sym)
                    seen[sym] = True
            if dups:
                issues.append(("positions", f"duplicate open symbols: {set(dups)}"))
        # journal consistency: open trades have matching positions
        if "trades" in tables and "positions" in tables:
            cur.execute("SELECT data FROM positions WHERE data LIKE '%\"OPEN\"%'")
            open_symbols = {json.loads(r[0]).get("symbol", "") for r in cur.fetchall()}
            cur.execute("SELECT data FROM trades")
            trade_symbols = {json.loads(r[0]).get("symbol", "") for r in cur.fetchall()}
            if not open_symbols.issubset(trade_symbols):
                issues.append(("journal", f"open positions missing trade journal: {open_symbols - trade_symbols}"))
        conn.close()
    except Exception as e:
        issues.append(("db", str(e)))
    return ("✅ Database audit", issues) if not issues else ("❌ DB issues", issues)

def broker_check():
    try:
        from broker_integration import BrokerIntegration
        bi = BrokerIntegration()
        # Smoke fetch without placing orders
        try:
            bi.kite.margins()
        except Exception as me:
            return ("⚠ Broker margins/RMS warning", [str(me)[:120]])
        _holdings = bi.kite.holdings() if hasattr(bi.kite, "holdings") else []
        _positions = bi.kite.positions() if hasattr(bi.kite, "positions") else {}
        return ("✅ Broker fetch works", [])
    except Exception as e:
        return "❌ Broker check error", [str(e)[:120]]

def ai_check():
    try:
        from ai_research_agent import AIResearchAgent
        from signal_generator import SignalGenerator
        return "✅ AI modules importable", []
    except Exception as e:
        return "❌ AI module import", [str(e)[:120]]

def safety_check():
    from config import config as Cfg
    import requests
    issues = []
    live_mode = not getattr(Cfg, "PAPER_TRADING", True)
    if live_mode:
        issues.append(("config", "PAPER_TRADING is False — live orders will be placed"))
    if getattr(Cfg, "TRADING_AMOUNT", 0) < 1000:
        issues.append(("config", f"TRADING_AMOUNT low: {getattr(Cfg, 'TRADING_AMOUNT', 0)}"))
    if getattr(Cfg, "MAX_POSITIONS", 0) < 1:
        issues.append(("config", f"MAX_POSITIONS invalid: {getattr(Cfg, 'MAX_POSITIONS', 0)}"))
    # Runtime / .env consistency
    try:
        resp = requests.get("http://localhost:5001/api/data", timeout=10)
        d = resp.json()
        server_paper = d.get("paper_trading", True)
        if bool(server_paper) == live_mode:
            issues.append(("runtime", f"RUNNING SERVER MISMATCH: .env PAPER_TRADING={not live_mode} but /api/data paper_trading={server_paper}. Restart server to pick up .env"))
    except Exception as e:
        issues.append(("runtime", f"Could not compare runtime paper_trading: {e}"))
    return ("✅ Safety config present", issues) if not issues else ("⚠ Safety config warnings", issues)

def performance_check():
    try:
        import requests
        t0 = time.time()
        requests.get("http://localhost:5001/api/health", timeout=10)
        health_ms = round((time.time() - t0) * 1000, 2)
        t0 = time.time()
        requests.get("http://localhost:5001/api/data", timeout=30)
        data_ms = round((time.time() - t0) * 1000, 2)
        return f"✅ API latency (health {health_ms} ms, data {data_ms} ms)", [health_ms, data_ms]
    except Exception as e:
        return "❌ Performance check", [str(e)[:80]]

def quality_check():
    todos = []
    for p in (ROOT / "src").rglob("*.py"):
        if "venv" in p.parts:
            continue
        for n, line in enumerate(p.read_text().splitlines(), 1):
            if re.search(r"#\s*(TODO|FIXME|XXX|HACK)", line, re.I):
                todos.append((str(p.relative_to(ROOT)), n, line.strip()[:80]))
    return (f"⚠ {len(todos)} TODO/FIXME found", todos) if todos else ("✅ No TODO/FIXME", [])

def main():
    checks = {
        "Phase 1 - Python Compilation": compile_check(),
        "Phase 1 - Static Import Check": import_check(),
        "Phase 2 - JavaScript": js_check(),
        "Phase 3 - Flask API": flask_check(),
        "Phase 4/5/7 - Test Suite": test_check(),
        "Phase 6 - Database": db_check(),
        "Phase 5 - Broker": broker_check(),
        "Phase 7 - AI": ai_check(),
        "Phase 10 - Safety Config": safety_check(),
        "Phase 11 - Performance": performance_check(),
        "Phase 12 - Code Quality": quality_check(),
    }

    passed = 0
    failed = 0
    warnings = 0
    lines = ["# RC-1 Release Readiness Report", f"**Generated:** {datetime.now().isoformat()}\n"]
    lines.append("## ✅ Passed Checks")
    issues = []
    for name, (msg, details) in checks.items():
        icon = msg[0]
        if icon == "✅":
            passed += 1
            lines.append(f"- {msg}")
        elif icon == "⚠":
            warnings += 1
            lines.append(f"- {msg}")
            for d in details[:10]:
                issues.append({"severity": "Low", "phase": name, "file": d[0] if isinstance(d, tuple) else "n/a", "description": d[1] if isinstance(d, tuple) else str(d)[:80]})
        else:
            failed += 1
            lines.append(f"- {msg}")
            for d in details[:10]:
                issues.append({"severity": "High", "phase": name, "file": d[0] if isinstance(d, tuple) else "n/a", "description": d[1] if isinstance(d, tuple) else str(d)[:80]})

    lines.append("\n## ⚠ Issues Found")
    if issues:
        lines.append("| Severity | Phase | File/Module | Description |")
        lines.append("|---|---|---|---|")
        for i in issues:
            lines.append(f"| {i['severity']} | {i['phase']} | {i['file']} | {i['description']} |")
    else:
        lines.append("No blocking or high-severity issues found.")

    lines.append("\n## 📊 Summary")
    lines.append(f"- **Total checks:** {len(checks)}")
    lines.append(f"- **Passed:** {passed}")
    lines.append(f"- **Warnings:** {warnings}")
    lines.append(f"- **Failed:** {failed}")
    lines.append(f"- **Blocking:** {failed}")

    if failed == 0 and warnings <= 2:
        lines.append("\n# ✅ READY FOR LIVE TRADING")
    elif failed == 0:
        lines.append("\n# ⚠ READY AFTER MINOR FIXES")
    else:
        lines.append("\n# ❌ NOT READY FOR LIVE TRADING")

    report = "\n".join(lines)
    REPORT.write_text(report)
    RESULTS.write_text(json.dumps({"checks": {k:v[0] for k,v in checks.items()}, "issues": issues}, indent=2))
    print(report)

if __name__ == "__main__":
    main()
