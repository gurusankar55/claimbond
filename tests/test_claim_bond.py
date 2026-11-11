"""
Offline unit tests for ClaimBond.

These tests run with plain Python (no Docker, no GenLayer node). A tiny stub
of the `genlayer` module is installed first, so the real contract file is
imported unchanged. Web pages and LLM replies are mocked, which lets us test
the deterministic logic (validation, windows, resolution table, bond payouts)
exactly.

Run from the repo root:
    python -m unittest tests/test_claim_bond.py -v
"""

import os
import sys
import json
import types
import unittest
import importlib.util


# ---------------- genlayer stub ----------------

class _Addr:
    def __init__(self, hex_):
        self.as_hex = hex_


class _WriteDecorator:
    def __call__(self, fn):
        return fn

    def payable(self, fn):
        return fn


class _Transfers:
    log = []


class _ContractHandle:
    def __init__(self, addr):
        self.addr = addr

    def emit_transfer(self, value):
        _Transfers.log.append((self.addr.as_hex, int(value)))


gl = types.SimpleNamespace()
gl.Contract = object
gl.public = types.SimpleNamespace(write=_WriteDecorator(), view=lambda f: f)
gl.message = types.SimpleNamespace(sender_address=_Addr("0xA"), value=0)
gl.message_raw = {"datetime": "2026-11-11T10:00:00Z"}
gl.nondet = types.SimpleNamespace(
    web=types.SimpleNamespace(render=None),
    exec_prompt=None,
)
gl.eq_principle = types.SimpleNamespace(strict_eq=lambda fn: fn())
gl.get_contract_at = lambda addr: _ContractHandle(addr)

stub = types.ModuleType("genlayer")
stub.gl = gl
stub.TreeMap = dict
stub.u256 = int
stub.__all__ = ["gl", "TreeMap", "u256"]
sys.modules["genlayer"] = stub

HERE = os.path.dirname(os.path.abspath(__file__))
CONTRACT = os.path.join(HERE, "..", "contracts", "claim_bond.py")
spec = importlib.util.spec_from_file_location("claim_bond", CONTRACT)
cb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cb)


# ---------------- helpers ----------------

PAGES = {}   # url -> page text
ANSWERS = {} # url -> raw LLM reply for that page


def fake_render(url, mode="text"):
    if url not in PAGES:
        raise Exception("fetch failed")
    return PAGES[url]


def fake_prompt(prompt):
    for url, page in PAGES.items():
        if page in prompt:
            return ANSWERS.get(url, '{"value": ""}')
    return '{"value": ""}'


gl.nondet.web.render = fake_render
gl.nondet.exec_prompt = fake_prompt


def at(ts_iso):
    gl.message_raw = {"datetime": ts_iso}


def as_user(addr, value=0):
    gl.message.sender_address = _Addr(addr)
    gl.message.value = value


def page(text):
    return text + " " + ("filler text " * 10)


def new_contract(min_bond=10, window=3600):
    c = cb.ClaimBond.__new__(cb.ClaimBond)
    c.claims = {}
    c.credits = {}
    cb.ClaimBond.__init__(c, min_bond, window)
    return c


# ---------------- tests ----------------

class NormalizationTests(unittest.TestCase):
    def test_yes_no(self):
        self.assertEqual(cb._norm_yes_no(" yes. "), "YES")
        self.assertEqual(cb._norm_yes_no("False"), "NO")
        self.assertEqual(cb._norm_yes_no("maybe"), "")

    def test_number(self):
        self.assertEqual(cb._norm_number("1,000,000"), "1000000")
        self.assertEqual(cb._norm_number("$1e3"), "1000")
        self.assertEqual(cb._norm_number("2.50"), "2.5")
        self.assertEqual(cb._norm_number("-0.0"), "0")
        self.assertEqual(cb._norm_number("1.5 billion"), "")
        self.assertEqual(cb._norm_number("NaN"), "")

    def test_parse_llm_value(self):
        self.assertEqual(cb._parse_llm_value('Sure! {"value": "YES"} done'), "YES")
        self.assertEqual(cb._parse_llm_value("no json"), "")
        self.assertEqual(cb._parse_llm_value({"value": 42}), "42")

    def test_url(self):
        self.assertTrue(cb._valid_url("https://example.com/a"))
        self.assertFalse(cb._valid_url("http://example.com"))
        self.assertFalse(cb._valid_url("https://bad url.com"))
        self.assertFalse(cb._valid_url("https://localhost"))


class ResolutionTableTests(unittest.TestCase):
    def test_both_agree_with_proposer(self):
        self.assertEqual(cb._resolve("yes_no", "YES", "YES", "YES", 0), ("CONFIRMED", "YES"))

    def test_both_disagree_with_proposer(self):
        self.assertEqual(cb._resolve("yes_no", "YES", "NO", "NO", 0), ("CORRECTED", "NO"))

    def test_pages_contradict(self):
        self.assertEqual(cb._resolve("yes_no", "YES", "YES", "NO", 0), ("UNRESOLVABLE", ""))

    def test_one_page_unreadable(self):
        self.assertEqual(cb._resolve("number", "100", "", "100", 0), ("CONFIRMED", "100"))
        self.assertEqual(cb._resolve("number", "100", "200", "", 0), ("CORRECTED", "200"))

    def test_nothing_readable(self):
        self.assertEqual(cb._resolve("number", "100", "", "", 0), ("UNRESOLVABLE", ""))

    def test_number_tolerance(self):
        # 1% tolerance: 99.5 vs 100 is fine, 98 vs 100 is not
        self.assertEqual(cb._resolve("number", "99.5", "100", "100", 100)[0], "CONFIRMED")
        self.assertEqual(cb._resolve("number", "98", "100", "100", 100)[0], "CORRECTED")

    def test_zero_truth(self):
        self.assertEqual(cb._resolve("number", "0", "0", "0", 500)[0], "CONFIRMED")
        self.assertEqual(cb._resolve("number", "1", "0", "0", 500)[0], "CORRECTED")


class FlowTests(unittest.TestCase):
    def setUp(self):
        PAGES.clear()
        ANSWERS.clear()
        _Transfers.log.clear()
        at("2026-11-11T10:00:00Z")

    def test_constructor_limits(self):
        with self.assertRaises(Exception):
            new_contract(-1, 60)
        with self.assertRaises(Exception):
            new_contract(0, 31 * 24 * 3600)

    def test_propose_validation(self):
        c = new_contract()
        as_user("0xA", 5)
        with self.assertRaises(Exception):
            c.propose("Is mainnet live today?", "yes_no", "YES", "https://x.io/a", 0)  # bond low
        as_user("0xA", 10)
        for args in [
            ("short", "yes_no", "YES", "https://x.io/a", 0),
            ("Is mainnet live today?", "text", "YES", "https://x.io/a", 0),
            ("Is mainnet live today?", "yes_no", "maybe", "https://x.io/a", 0),
            ("Is mainnet live today?", "yes_no", "YES", "http://x.io/a", 0),
            ("What is the max supply?", "number", "100", "https://x.io/a", 5000),
        ]:
            with self.assertRaises(Exception):
                c.propose(*args)

    def test_unchallenged_claim_finalizes_without_ai(self):
        c = new_contract()
        gl.nondet.web.render = lambda *a, **k: self.fail("no web call expected")
        try:
            as_user("0xA", 10)
            cid = c.propose("Is mainnet live today?", "yes_no", "yes", "https://x.io/a", 0)
            self.assertEqual(c.get_status(cid), "PROPOSED")
            self.assertFalse(c.is_final(cid))
            with self.assertRaises(Exception):
                c.finalize(cid)  # window still open
            at("2026-11-11T11:00:01Z")
            as_user("0xZ", 0)
            self.assertEqual(c.finalize(cid), "ACCEPTED")
            self.assertTrue(c.is_final(cid))
            self.assertEqual(c.get_final_answer(cid), "YES")
            self.assertEqual(c.get_credit("0xA"), 10)
            with self.assertRaises(Exception):
                c.finalize(cid)
        finally:
            gl.nondet.web.render = fake_render

    def test_dispute_corrects_wrong_answer(self):
        c = new_contract()
        PAGES["https://x.io/a"] = page("Total supply page A")
        PAGES["https://y.io/b"] = page("Total supply page B")
        ANSWERS["https://x.io/a"] = '{"value": "1,000,000,000"}'
        ANSWERS["https://y.io/b"] = '{"value": "1000000000"}'
        as_user("0xA", 10)
        cid = c.propose("What is the token max supply?", "number", "2000000000", "https://x.io/a", 100)
        as_user("0xA", 10)
        with self.assertRaises(Exception):
            c.dispute(cid, "https://y.io/b")  # proposer cannot dispute
        as_user("0xB", 5)
        with self.assertRaises(Exception):
            c.dispute(cid, "https://y.io/b")  # bond too low
        as_user("0xB", 10)
        self.assertEqual(c.dispute(cid, "https://y.io/b"), "CORRECTED")
        self.assertEqual(c.get_final_answer(cid), "1000000000")
        self.assertEqual(c.get_credit("0xB"), 20)
        self.assertEqual(c.get_credit("0xA"), 0)
        claim = json.loads(c.get_claim(cid))
        self.assertEqual(claim["read_values"], {"evidence": "1000000000", "counter": "1000000000"})
        with self.assertRaises(Exception):
            c.dispute(cid, "https://y.io/b")  # only one dispute

    def test_dispute_confirms_right_answer(self):
        c = new_contract()
        PAGES["https://x.io/a"] = page("Mainnet launched page")
        ANSWERS["https://x.io/a"] = '{"value": "YES"}'
        as_user("0xA", 10)
        cid = c.propose("Is the mainnet live now?", "yes_no", "YES", "https://x.io/a", 0)
        as_user("0xB", 15)
        # counter page unreachable -> evidence page decides
        self.assertEqual(c.dispute(cid, "https://dead.io/x"), "CONFIRMED")
        self.assertEqual(c.get_credit("0xA"), 25)

    def test_contradicting_pages_refund_both(self):
        c = new_contract()
        PAGES["https://x.io/a"] = page("Page says yes")
        PAGES["https://y.io/b"] = page("Page says no")
        ANSWERS["https://x.io/a"] = '{"value": "YES"}'
        ANSWERS["https://y.io/b"] = '{"value": "NO"}'
        as_user("0xA", 10)
        cid = c.propose("Is the mainnet live now?", "yes_no", "YES", "https://x.io/a", 0)
        as_user("0xB", 12)
        self.assertEqual(c.dispute(cid, "https://y.io/b"), "UNRESOLVABLE")
        self.assertFalse(c.is_final(cid))
        self.assertEqual(c.get_credit("0xA"), 10)
        self.assertEqual(c.get_credit("0xB"), 12)

    def test_dispute_after_window_rejected(self):
        c = new_contract()
        as_user("0xA", 10)
        cid = c.propose("Is the mainnet live now?", "yes_no", "YES", "https://x.io/a", 0)
        at("2026-11-11T12:00:00Z")
        as_user("0xB", 10)
        with self.assertRaises(Exception):
            c.dispute(cid, "https://y.io/b")

    def test_withdraw(self):
        c = new_contract()
        as_user("0xA", 10)
        cid = c.propose("Is the mainnet live now?", "yes_no", "NO", "https://x.io/a", 0)
        at("2026-11-11T11:00:01Z")
        c.finalize(cid)
        as_user("0xA", 0)
        self.assertEqual(c.withdraw(), 10)
        self.assertEqual(_Transfers.log, [("0xA", 10)])
        with self.assertRaises(Exception):
            c.withdraw()


if __name__ == "__main__":
    unittest.main()
