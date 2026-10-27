"""
Verified Angel One intraday equity charge schedule.

Source: Angel One official pricing / exchange transaction charges pages
(angelone.in/exchange-transaction-charges, pricing update effective
Nov 17 2025). Equity intraday product on NSE:

- Brokerage: lower of Rs 20 or 0.1% per executed order, Rs 5 minimum
- STT: 0.025% sell side only
- NSE transaction charges: 0.0030699% both sides
- Stamp duty: 0.003% buy side only
- SEBI charges: Rs 10 per crore of turnover
- IPFT: 0.0000001% of turnover
- GST: 18% on brokerage + transaction + SEBI charges

Account-specific discounts (e.g. onboarding plans) are NOT assumed —
charges are computed at the published standard schedule.
"""
from typing import Dict

BROKERAGE_PCT = 0.001          # 0.1% per executed order
BROKERAGE_MAX = 20.0           # Rs 20 cap
BROKERAGE_MIN = 5.0            # Rs 5 minimum per executed order
STT_SELL_PCT = 0.00025         # 0.025% sell side (intraday equity)
TXN_PCT = 0.000030699          # NSE 0.0030699% both sides
STAMP_BUY_PCT = 0.00003        # 0.003% buy side
SEBI_PCT = 0.000001            # Rs 10 per crore
IPFT_PCT = 0.000000001         # 0.0000001%
GST_PCT = 0.18                 # on brokerage + txn + SEBI


def per_order_brokerage(order_value: float) -> float:
    """Brokerage for a single executed order: 0.1% capped at Rs 20,
    floored at Rs 5."""
    return max(BROKERAGE_MIN, min(BROKERAGE_MAX, BROKERAGE_PCT * order_value))


def angel_intraday_charges(buy_value: float, sell_value: float) -> Dict[str, float]:
    """Itemised Angel One intraday charges for a round trip."""
    brokerage = per_order_brokerage(buy_value) + per_order_brokerage(sell_value)
    stt = STT_SELL_PCT * sell_value
    txn = TXN_PCT * (buy_value + sell_value)
    stamp = STAMP_BUY_PCT * buy_value
    sebi = SEBI_PCT * (buy_value + sell_value)
    ipft = IPFT_PCT * (buy_value + sell_value)
    gst = GST_PCT * (brokerage + txn + sebi)
    total = brokerage + stt + txn + stamp + sebi + ipft + gst
    return {'brokerage': brokerage, 'stt': stt, 'txn': txn, 'stamp': stamp,
            'sebi': sebi, 'ipft': ipft, 'gst': gst, 'total': total}
