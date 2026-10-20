"""A hard spending cap for paid guardian bench runs, priced per provider per call (#246).

Used by `scripts/skeptic_arms.py` and `scripts/guardian_bench.py`, so a cap
means the same thing in both. Prices are keyed by the name the run uses for a
provider (an arm, or a provider's `name`), and charged per call from
`BaseProvider.call_usages`, because a vendor price can depend on one request's
size and a sum cannot say which request crossed the line.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass

from cgis.guardian.providers.base import ProviderUsage

__all__ = ["Budget", "Price", "make_budget", "parse_prices"]


@dataclass(frozen=True)
class Price:
    """One provider's USD per million tokens, with an optional long-prompt tier.

    The tier is decided per request: a call whose prompt exceeds `above` is
    priced whole at the long rate. Claude Haiku 5.5 bills $0.10/$0.50 up to 100K
    input tokens and $0.50/$2.50 beyond, so a flat counter would under-price
    every long call by 5x. Pricing the whole call long errs high if the vendor
    tiers only the excess — the safe direction for a cap.
    """

    per_in: float
    per_out: float
    above: int | None = None
    long_in: float = 0.0
    long_out: float = 0.0

    def cost(self, usage: ProviderUsage) -> float:
        """The USD cost of one call."""
        long = self.above is not None and usage.prompt_tokens > self.above
        per_in, per_out = (self.long_in, self.long_out) if long else (self.per_in, self.per_out)
        return (usage.prompt_tokens * per_in + usage.completion_tokens * per_out) / 1_000_000


@dataclass
class Budget:
    """A hard spending cap, priced per provider from the calls each step reports.

    Priced per provider because they are different vendors at different prices:
    one pair applied to all would under-count the dearer one and let the cap pass
    untripped. The cap is only as good as the provider's own usage counts.

    The caller checks `exhausted` before each unit of work (a skeptic_arms row,
    a guardian_bench run), so a run overshoots by at most one unit — the honest
    bound, since a unit's cost is not known until it returns. A run that reaches
    the cap on its last unit has nothing left to skip and is complete. A stop is
    reported as a stop, never scored: half a comparison read as a whole one.
    """

    cap_usd: float
    prices: dict[str, Price]
    spent_usd: float = 0.0
    stopped: bool = False

    @property
    def exhausted(self) -> bool:
        """Whether the cap has been reached."""
        return self.spent_usd >= self.cap_usd

    def charge(self, name: str, calls: list[ProviderUsage]) -> None:
        """Add calls, each at `name`'s price for its own size."""
        price = self.prices[name]
        self.spent_usd += sum(price.cost(call) for call in calls)


_PRICE_FORMAT = (
    "expected ARM=IN:OUT or ARM=IN:OUT;ABOVE=IN:OUT (USD per million tokens; ABOVE in input tokens)"
)


def _rate_pair(text: str, item: str) -> tuple[float, float]:
    """`"0.10:0.50"` -> (0.10, 0.50); ValueError naming the whole --price item."""
    per_in, _, per_out = text.partition(":")
    try:
        return float(per_in), float(per_out)
    except ValueError:
        _msg = f"--price {item!r}: {_PRICE_FORMAT}"
        raise ValueError(_msg) from None


def parse_prices(items: list[str]) -> dict[str, Price]:
    """`["anthropic=0.10:0.50;100000=0.50:2.50"]` -> {"anthropic": Price(...)}."""
    prices: dict[str, Price] = {}
    for item in items:
        arm, _, rest = item.partition("=")
        short, _, tier = rest.partition(";")
        above, _, long_rates = tier.partition("=")
        if not arm or (tier and not above.isdigit()):
            _msg = f"--price {item!r}: {_PRICE_FORMAT}"
            raise ValueError(_msg)
        per_in, per_out = _rate_pair(short, item)
        if not tier:
            prices[arm] = Price(per_in, per_out)
            continue
        long_in, long_out = _rate_pair(long_rates, item)
        prices[arm] = Price(per_in, per_out, int(above), long_in, long_out)
    return prices


def make_budget(cap: float | None, price_items: list[str], names: Iterable[str]) -> Budget | None:
    """The run's Budget from its flags, or None; ValueError on a cap that cannot price.

    `names` are the providers the run will charge. A cap missing one of their
    prices would never count that provider, and a price without a cap stops
    nothing, so both are refused rather than run.
    """
    prices = parse_prices(price_items)
    for name, price in prices.items():
        rates = (price.per_in, price.per_out, price.long_in, price.long_out)
        if not all(math.isfinite(r) and r >= 0 for r in rates):
            # A negative price pays the cap back; nan never compares to anything.
            _msg = f"--price {name}: rates must be non-negative, finite numbers"
            raise ValueError(_msg)
    if cap is not None and not (math.isfinite(cap) and cap > 0):
        # nan >= cap is always False, so a nan cap would never trip.
        _msg = f"--budget-usd {cap}: must be a positive, finite number"
        raise ValueError(_msg)
    if cap is None:
        if prices:
            _msg = "--price only means something with --budget-usd"
            raise ValueError(_msg)
        return None
    unpriced = sorted(set(names) - set(prices))
    if unpriced:
        _msg = f"--budget-usd needs a --price for every provider it charges; missing: {unpriced}"
        raise ValueError(_msg)
    return Budget(cap, prices)
