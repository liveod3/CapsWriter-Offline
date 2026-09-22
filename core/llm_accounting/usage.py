"""Capture allowlisted accounting metadata without retaining response content."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation


def amount(value) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and 0 <= number <= Decimal('1e12') else None
    except InvalidOperation:
        return None


def tokens(value) -> int | None:
    return value if type(value) is int and 0 <= value <= 10**10 else None


def mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def estimate_tokens(text: str) -> int:
    # Deliberately a labelled heuristic, not a model tokenizer or billing guarantee.
    return (len(text.encode('utf-8')) + 2) // 3


@dataclass
class UsageObservation:
    sent: bool = False
    http_status: int | None = None
    usage: dict = field(default_factory=dict)
    reported_cost: str | None = None
    cost_currency: str | None = None
    cost_field: str | None = None
    invalid_usage: bool = False
    output_estimate: int | None = None

    def capture(self, body, provider, policy=None):
        body = mapping(body)
        usage = mapping(body.get('usage'))
        native = mapping(body.get('usageMetadata'))
        prompt = mapping(usage.get('prompt_tokens_details'))
        completion = mapping(usage.get('completion_tokens_details'))
        values = {
            'input_tokens': usage.get('prompt_tokens'),
            'output_tokens': usage.get('completion_tokens'),
            'cached_tokens': prompt.get('cached_tokens'),
            'cache_write_tokens': prompt.get('cache_write_tokens'),
            'reasoning_tokens': completion.get('reasoning_tokens'),
            'total_tokens': usage.get('total_tokens'),
        }
        if provider.kind == 'ollama':
            values.update(input_tokens=body.get('prompt_eval_count'),
                          output_tokens=body.get('eval_count'))
        elif native:
            values.update(input_tokens=native.get('promptTokenCount'),
                          output_tokens=native.get('candidatesTokenCount'),
                          cached_tokens=native.get('cachedContentTokenCount'),
                          reasoning_tokens=native.get('thoughtsTokenCount'),
                          total_tokens=native.get('totalTokenCount'))
            # Native Gemini reports candidate and thought tokens separately.
            if tokens(values['output_tokens']) is not None:
                values['output_tokens'] += tokens(values['reasoning_tokens']) or 0
        elif provider.base_url.rstrip('/') == 'https://generativelanguage.googleapis.com/v1beta/openai':
            # Google's compatible layer may expose thoughts at the top level instead.
            thoughts = tokens(values['reasoning_tokens'])
            separate_thoughts = thoughts is None
            if thoughts is None:
                thoughts = tokens(usage.get('reasoning_tokens'))
            if thoughts is not None:
                values['reasoning_tokens'] = thoughts
                inp, out, total = (tokens(values[k]) for k in
                                   ('input_tokens', 'output_tokens', 'total_tokens'))
                if inp is not None and out is not None and total is not None:
                    if total == inp + out + thoughts:
                        values['output_tokens'] = out + thoughts
                    elif total != inp + out:
                        self.invalid_usage = True
                elif separate_thoughts:
                    # A top-level extension needs a total to disambiguate its inclusion.
                    self.invalid_usage = True
        for name, value in values.items():
            if value is not None:
                count = tokens(value)
                if count is None:
                    self.invalid_usage = True
                else:
                    self.usage[name] = count
        inp, out = (self.usage.get(k) for k in ('input_tokens', 'output_tokens'))
        total = self.usage.get('total_tokens')
        if inp is not None and out is not None and total is not None and total != inp + out:
            self.invalid_usage = True
        cache = self.usage.get('cached_tokens', 0) + self.usage.get('cache_write_tokens', 0)
        if (inp is not None and cache > inp) or (
            out is not None and self.usage.get('reasoning_tokens', 0) > out
        ):
            self.invalid_usage = True
        # Only documented endpoint semantics or an explicit local mapping can assign money.
        field_name, currency = None, None
        if provider.base_url.rstrip('/') == 'https://openrouter.ai/api/v1':
            field_name, currency = 'usage.cost', 'USD'
        elif policy:
            field_name, currency = policy.get('reported_cost_field'), policy.get('currency')
        if field_name and currency:
            value = body
            for key in field_name.split('.'):
                value = mapping(value).get(key)
            cost = amount(value)
            if cost is not None:
                self.reported_cost = str(cost)
                self.cost_currency = currency
                self.cost_field = field_name
        # Count discarded/incomplete output in memory; never store its content.
        message = mapping(body.get('message'))
        choices = body.get('choices')
        if isinstance(choices, list) and choices:
            message = mapping(mapping(choices[0]).get('message'))
        content = message.get('content')
        if isinstance(content, str):
            self.output_estimate = estimate_tokens(content)


observation: ContextVar[tuple[UsageObservation, dict | None] | None] = ContextVar(
    'llm_usage_observation', default=None
)
