# Customer bot conversation retest checklist

Run this after every staging rollout that touches the customer bot's conversation handling (memory,
no-repeat, Malay register, handoff and holding texts). Use the staging test company and synthetic
customers only. Rows marked "manual" depend on staging state or on the console, so only a person can
check them. Tick each row when the result matches.

## The replay

The replay sends a synthetic chat through the real classify and compose model calls, with the
conversation memory a real turn would carry. It uses synthetic facts only, touches no database and
needs only the model key (see the docstring of `scripts/customer_bot_replay.py`):

```bash
docker exec csa-staging-gateway-1 python -m scripts.customer_bot_replay --help
docker exec csa-staging-gateway-1 python -m scripts.customer_bot_replay --dry-run
docker exec csa-staging-gateway-1 python -m scripts.customer_bot_replay --language ms
```

- `--script PATH` replays another script file (default `scripts/customer_bot_replay_cases.json`),
  `--language en|ms|zh|ta` and `--conversation ID` pick conversations, `--dry-run` only checks the
  script file and calls no model.
- The shipped script has two conversations: `ms-plans` (Malay) and `en-plans` (English).
- It prints each customer message and reply (synthetic text, on purpose), the classifier's intent,
  language and wants_human, the brand tone used, then per conversation the counts of the formal
  Malay markers (`anda`, `sila`, `pihak kami`, `menemukan`, `dimaklumkan`) and of sentences repeated
  across replies. The key is never printed.
- Exit 0: every message answered, no formal marker in a Malay conversation and no repeated
  sentence. 1: a failure. 2: a setup error (no model key, the model unavailable on the first call,
  a bad script file or an unknown conversation id).
- The replay does not run the graph (routing, agents, gate, delivery); the golden cases cover that.
  The counts are a floor, not a verdict: read every reply yourself.

## Rows

| Check | Setup | Expected | Kind |
| --- | --- | --- | --- |
| Malay replay read-through | `--conversation ms-plans` on the shipped script | Exit 0. Read every reply: casual, natural Malay; no `anda`, `sila`, `pihak kami`, `menemukan` or `dimaklumkan`; no sentence said twice; a list is never longer than the cap; asking for a person is acknowledged once, in one or two short sentences | manual |
| English twin | `--conversation en-plans` | Exit 0. Read every reply: the same facts as the Malay run, warm and plain, no repeated sentence, no list over the cap | manual |
| Register with a professional tone | Console, Customer replies, Tone set to `professional` for the staging test company; then the replay (the script's brand tone can be changed in a copy passed with `--script`) or a real Telegram chat in Malay | The Malay stays casual: `professional` is not a request for formal Malay | manual |
| Register with an explicit formal tone | The same, with Tone set to `formal Malay` | The Malay goes formal (`anda`, `sila` and so on are now allowed); setting the Tone back to empty returns it to casual | manual |
| Handoff after "AI resumed" | A Telegram chat on the staging test company: ask for a person; take over in the console (or note the staff alert) and hand back with Resume AI ("AI resumed."); ask for a person again | The first ask gets a short acknowledgement and the staff alert. After the hand back, the second ask is acknowledged once, in different words from the first, with no repeated sentence | manual |
| Provider-failure holding text | The golden cases only (`fail_safe` expects the localized holding text, and a review error degrades to grounding). Do not break a staging key to test it | In a real outage the customer gets the short holding text in their own language; a handoff reply never fails the turn | golden only |
| Memory kill switch | Staging test company; the runbook SQL under "Customer bot conversation memory" in [RUNBOOK.md](RUNBOOK.md). In a chat, say your name and a plan you want, then switch memory off, then ask "what did I ask for?" | With memory off the bot does not know the earlier turn and asks again, without an error. Switch it back on (or clear the key): the next message uses the earlier turns again. Read the setting back with the runbook SELECT each time | manual |

## Calibration

The Malay register (a glossary and 12 examples in `_MS_GLOSSARY` and `_MS_EXAMPLES`, in
`csa/decide/llm.py`) has not been read by a native speaker against live model output. After the first
staging rollout:

1. The owner runs the replay on the Malay script and reads every reply.
2. The owner lists the phrases that sound wrong, too stiff or over-used (the examples use "ya"
   often, so watch for it in every reply).
3. One follow-up change then edits ONLY `_MS_GLOSSARY` and `_MS_EXAMPLES` in `csa/decide/llm.py` and
   re-runs `pytest tests/test_llm_provider.py -k register`.

The register is not finished until that pass is done.

## Known limits

- The Chinese and Tamil handoff, holding and follow-up texts have not been reviewed by a native
  speaker.
- The approval-outcome follow-up text uses the contact's stored language. It can still be English
  for a Malay customer when the draft is too short for the word-list detector.
- The second-provider fallback for classify and plan (Gemini JSON mode) has not been exercised on a
  live model, and staging has no Google key.
- When the semantic review is down, an answer with no unsupported figures or links is sent
  unreviewed.
