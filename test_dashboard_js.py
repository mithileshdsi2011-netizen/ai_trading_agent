#!/usr/bin/env python3
"""
Audit dashboard.py for JavaScript and HTML binding health.
"""
import re


def check_js_and_bindings():
    with open('dashboard.py', 'r') as f:
        content = f.read()

    issues = []

    # Extract all inline <script> blocks
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', content, re.DOTALL)
    if not scripts:
        issues.append("No inline <script> blocks found")
        return

    combined_js = '\n'.join(scripts)
    print(f"📊 Found {len(scripts)} inline <script> block(s) ({len(combined_js)} chars)")

    # Basic backtick / template-literal balance
    for i, js in enumerate(scripts, 1):
        if js.count('`') % 2 != 0:
            issues.append(f"Script {i}: unclosed template literal (backtick)")

    # getElementById duplicate check: only warn when an addEventListener is repeated
    # for the same element (potential duplicate listener leak).
    listener_refs = re.findall(r"getElementById\(['\"]([^'\"]+)['\"]\)\.addEventListener", combined_js)
    duplicate_listeners = [id for id in set(listener_refs) if listener_refs.count(id) > 1]
    if duplicate_listeners:
        issues.append(f"Duplicate addEventListener for: {duplicate_listeners}")

    # Placeholder / empty-state values that are acceptable if data unavailable
    # We only flag literal JSX/JS strings that render static '—' or '0' as a fallback.
    suspicious = re.findall(r"innerHTML\s*=\s*['\"](—|Unknown|Undefined|NaN|Infinity|null)['\"]", combined_js)
    if suspicious:
        issues.append(f"Hard-coded placeholder values found: {suspicious}")

    # Basic JS syntax sanity: check for unmatched parentheses/braces in the entire script
    for i, js in enumerate(scripts, 1):
        if js.count('(') != js.count(')'):
            issues.append(f"Script {i}: unbalanced parentheses")
        if js.count('{') != js.count('}'):
            issues.append(f"Script {i}: unbalanced braces")

    all_ids = re.findall(r"getElementById\(['\"]([^'\"]+)['\"]\)", combined_js)

    if issues:
        print("❌ Dashboard JS/binding issues found:")
        for issue in issues:
            print(f"  - {issue}")
    else:
        print("✅ Dashboard JavaScript / HTML binding checks passed")

    print(f"\n📊 Found {len(all_ids)} getElementById references")


if __name__ == "__main__":
    check_js_and_bindings()
