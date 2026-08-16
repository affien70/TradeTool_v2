from __future__ import annotations

from enum import Enum


class CandidateType(str, Enum):
    STABLE_LEADER = 'Stable Leader'
    EARLY_BREAKOUT = 'Early Breakout'
    EXTENDED_RUNNER = 'Extended Runner'
    REBOUND_CASE = 'Rebound Case'
    REJECT = 'Reject'


class TradeSignal(str, Enum):
    BUY = 'BUY'
    WATCH = 'WATCH'
    REVIEW = 'REVIEW'
    AVOID = 'AVOID'


class HoldingsSignal(str, Enum):
    HOLD = 'HOLD'
    SELL = 'SELL'
    FOLG_MED = 'FØLG MED'


class CostBasisStatus(str, Enum):
    KNOWN = 'KNOWN'
    PARTIAL = 'PARTIAL'
    UNKNOWN = 'UNKNOWN'
