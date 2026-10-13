# Decision eval report

Generated 2026-10-12 09:51 UTC by `python -m csa.evals.decisions` on 60 synthetic labelled messages (`csa/evals/data/decision_cases.json`). Live providers; not run in CI.

| Provider | Version | Errors | intent | language | wants_human | injection | urgent | injection recall | human recall | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|
| llm | groq:openai/gpt-oss-120b | 4 | 90% | 92% | 93% | 92% | 92% | 80% | 83% | 772.9 | 4457.4 |
| jev | jev:jev-1.13.0 | 0 | 100% | 98% | 98% | 100% | 100% | 100% | 83% | 284.8 | 408.7 |

Agreement between providers: intent 83%, language 92%, wants_human 92%, injection 92%, urgent 92%.

## Misses: llm

- `order_buy_en_2` intent: got `groq_timeout`
- `order_buy_en_2` language: got `groq_timeout`
- `order_buy_en_2` wants_human: got `groq_timeout`
- `order_buy_en_2` injection: got `groq_timeout`
- `order_buy_en_2` urgent: got `groq_timeout`
- `order_buy_ms` intent: got `groq_timeout`
- `order_buy_ms` language: got `groq_timeout`
- `order_buy_ms` wants_human: got `groq_timeout`
- `order_buy_ms` injection: got `groq_timeout`
- `order_buy_ms` urgent: got `groq_timeout`
- `order_buy_manglish` language: got `en`
- `billing_en` intent: got `refund_returns`
- `technical_en` urgent: got `True`
- `human_ms` intent: got `groq_timeout`
- `human_ms` language: got `groq_timeout`
- `human_ms` wants_human: got `groq_timeout`
- `human_ms` injection: got `groq_timeout`
- `human_ms` urgent: got `groq_timeout`
- `human_manager` intent: got `complaint`
- `hi_en` intent: got `groq_timeout`
- `hi_en` language: got `groq_timeout`
- `hi_en` wants_human: got `groq_timeout`
- `hi_en` injection: got `groq_timeout`
- `hi_en` urgent: got `groq_timeout`
- `inject_subtle` injection: got `False`

## Misses: jev

- `order_buy_manglish` language: got `en`
- `en_take_manager` wants_human: got `False`
