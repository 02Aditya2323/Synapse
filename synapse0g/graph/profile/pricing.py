"""What measured tokens cost at each priced model in `models.yaml`.

Local models have no price, so their own cost stays `None`. This answers a different question:
what these same token counts would cost at a provider's listed price. The tokens are measured and
the price is listed, but no such call was made, and a provider's model might use different counts.
"""

from __future__ import annotations

from synapse0g.graph.runtime.providers import load_registry


def priced_at(tokens_in: float | None, tokens_out: float | None) -> list[dict]:
    """One row per priced model. Empty when a token count is unknown: it is never treated as zero."""
    if tokens_in is None or tokens_out is None:
        return []
    return [
        {
            "model": cfg.id,
            "provider": cfg.provider,
            "cost_usd": round((tokens_in * cfg.price_per_mtok["in"] + tokens_out * cfg.price_per_mtok["out"]) / 1e6, 8),
        }
        for cfg in load_registry().models.values()
        if cfg.price_per_mtok
    ]
