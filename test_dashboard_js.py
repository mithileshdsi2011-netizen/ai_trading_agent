#!/usr/bin/env python3
"""
Test if the dashboard HTML has valid JavaScript
"""
import re

def check_js_syntax():
    with open('dashboard.py', 'r') as f:
        content = f.read()
    
    # Extract the main JavaScript update function
    start_marker = "// Positions table with enhanced data"
    end_marker = "// AI Opportunities — BUY signals only"
    
    start_idx = content.find(start_marker)
    end_idx = content.find(end_marker)
    
    if start_idx == -1 or end_idx == -1:
        print("❌ Could not find JavaScript section")
        return
    
    js_section = content[start_idx:end_idx]
    
    # Check for common syntax errors
    issues = []
    
    # Check for unclosed template literals
    backtick_count = js_section.count('`')
    if backtick_count % 2 != 0:
        issues.append("Unclosed template literal (backtick)")
    
    # Check for unclosed parentheses in template literals
    template_literals = re.findall(r'`([^`]*)`', js_section)
    for literal in template_literals:
        if literal.count('(') != literal.count(')'):
            issues.append(f"Mismatched parentheses in template literal: {literal[:50]}...")
    
    # Check for undefined variables
    if 'document.getElementById(\'d-total-qty\')' in js_section:
        print("✅ Found dashboard total elements")
    else:
        issues.append("Missing dashboard total elements")
    
    # Check for duplicate IDs
    ids = re.findall(r"getElementById\('([^']+)'\)", js_section)
    duplicate_ids = [id for id in set(ids) if ids.count(id) > 1]
    if duplicate_ids:
        issues.append(f"Duplicate element IDs: {duplicate_ids}")
    
    if issues:
        print("❌ JavaScript issues found:")
        for issue in issues:
            print(f"  - {issue}")
    else:
        print("✅ JavaScript syntax appears valid")
    
    print(f"\n📊 Found {len(ids)} element ID references")
    print(f"📊 Found {len(template_literals)} template literals")

if __name__ == "__main__":
    check_js_syntax()
