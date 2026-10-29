# How I used AI coding tools

I used **Claude** (Anthropic) as a pair programmer throughout this assignment.

## What Claude did

- Read the brief and the data pack with me, and turned `ap_policy.md` into a checklist (`docs/POLICY_RULES.md`). It also profiled all 900 invoices to find the edge cases and traps, such as the "pre-approved" email, the agreement used before its effective date, and the near-identical duplicate invoice number.
- Generated the first version of most of the code: the tools layer, rules engine, CLI, LLM wrapper, agents, validators and scorer.
- Tested the agent loop offline with a simulated model that makes deliberate mistakes, to check that the validators and retries work.
- Drafted the README, this file and the architecture diagram.

## What I did

- Chose the use case and the approach: tools compute facts, agents judge, validators check.
- Set up the environment, the API keys and the repository.
- Ran every case against the real Groq models and checked the results and logs.
- Made the call to drop Gemini after repeated 503 errors.
- Read and reviewed every file, so I can explain and change any part of it.

## How I checked the AI's work

- Ran the deterministic rules engine (`--mode rules`) on every sample and checked that it matched the expected answers.
- Hand-checked the expected answers for my own 7 test cases against the policy text.
- Checked the agents' results against the rules engine and read the traces in `logs/`.
- Confirmed that no API key is in the repository: `.env` is git-ignored and only `.env.example` is committed.
