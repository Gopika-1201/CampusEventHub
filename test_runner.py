#!/usr/bin/env python3
"""
SAT II Automated Verification Test Runner
Industry Use Case 38: College Event Registration System

Executes all 12 mandatory test cases from the specification and
outputs a clean terminal table formatted for the SAT II submission report.
"""

import sys
import os
import time

# Add current folder to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import run_all_tests, init_db

def format_cell(text, width):
    text = str(text)
    if len(text) > width:
        return text[:width - 3] + "..."
    return text.ljust(width)

def main():
    print("=" * 115)
    print(" COLLEGE EVENT REGISTRATION SYSTEM (USE CASE 38) - FORMAL TEST SUITE")
    print(" Specification: SAT II Industry Verification & Validation Standard")
    print("=" * 115)
    print(" Initializing isolated database environment and seeding demo records...")
    
    start_time = time.time()
    results = run_all_tests()
    elapsed = time.time() - start_time

    # Header definition
    col_w = {
        "id": 6,
        "name": 28,
        "input": 22,
        "expected": 28,
        "actual": 18,
        "verdict": 8
    }

    sep_line = "+" + "+".join(["-" * (w + 2) for w in col_w.values()]) + "+"

    print("\n" + sep_line)
    header = (
        f"| {format_cell('ID', col_w['id'])} "
        f"| {format_cell('TEST CASE / RULE', col_w['name'])} "
        f"| {format_cell('TEST INPUTS', col_w['input'])} "
        f"| {format_cell('EXPECTED RESULT', col_w['expected'])} "
        f"| {format_cell('ACTUAL OUTCOME', col_w['actual'])} "
        f"| {format_cell('VERDICT', col_w['verdict'])} |"
    )
    print(header)
    print(sep_line)

    passed_count = 0
    failed_count = 0

    for t in results:
        v = "PASS" if t["passed"] else "FAIL"
        if t["passed"]:
            passed_count += 1
        else:
            failed_count += 1

        row = (
            f"| {format_cell(t['test_id'], col_w['id'])} "
            f"| {format_cell(t['name'], col_w['name'])} "
            f"| {format_cell(t['inputs'], col_w['input'])} "
            f"| {format_cell(t['expected'], col_w['expected'])} "
            f"| {format_cell(t['actual'], col_w['actual'])} "
            f"| {format_cell(v, col_w['verdict'])} |"
        )
        print(row)

    print(sep_line)

    print("\n" + "=" * 115)
    print(f" TEST EXECUTION SUMMARY:")
    print(f" Total Tests Executed : {len(results)}")
    print(f" Tests Passed         : {passed_count}")
    print(f" Tests Failed         : {failed_count}")
    print(f" Execution Duration   : {elapsed:.3f} seconds")
    print(f" Final System Verdict : {'ALL TESTS PASSED [100% SAT II COMPLIANT]' if failed_count == 0 else 'TEST FAILURES DETECTED'}")
    print("=" * 115 + "\n")

    # Re-seed DB after test run for normal user UI usage
    init_db(force_reseed=True)

    if failed_count > 0:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    main()
