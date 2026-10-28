# Share of Answer India

When shoppers ask ChatGPT, Gemini or Perplexity what to buy, which brands get named? This kit measures it across four Indian categories of different involvement: face wash, kids' protein drinks, electric scooters and 2-wheeler accessories.

Working prototype, India only. **First results (26 Oct 2026) are in [FINDINGS.md](FINDINGS.md).** How the questions are chosen is in [METHOD.md](METHOD.md).

Free to use and adapt (MIT licence).

## What it measures

| Metric | Meaning |
| --- | --- |
| Share of mentions | % of answers that name the brand at all |
| Pick rate | % of answers that name it as the recommendation ("best overall", "I'd go with") |
| First named | % of answers where it is the first brand named |
| Weighted share | Pick = 3 points, listed = 2, passing mention = 1; brand's share of all points |
| Citations | Which websites the engines quote (YouTube, Reddit, Amazon, Nykaa...) |
| Channels | Where the answer sends the shopper to buy (Blinkit, Amazon, pharmacy, dealer...) |
| Claims to check | Kids' nutrition answers that mention height, immunity or brain claims next to a brand |

Head-to-head questions ("X vs Y") are kept but left out of the scorecard, since they force the brand names in.

## Where each brand wants to win

Every brand is scored on every question, but a gap only matters where the brand means to compete. A Chetak missing from "best after-sales service" matters; missing from "best scooter for 100 km a day of delivery work" may not, if delivery riders aren't its target.

Say where a brand competes in the `wants_to_win` column of `data/brands.csv`, using question ids, stages or need-state words separated by `|`:

| You write | Meaning |
| --- | --- |
| `consideration\|review` | Only questions at those stages |
| `EV01\|EV04\|EV08` | Only those questions |
| `charging\|battery` | Questions whose need state mentions those words |
| `all\|-EV11` | Everything except EV11 |

Left blank, two default rules decide: a bike brand's own accessories compete only after purchase (repeat stage), and every other brand competes on every question in its category.

`results/where_to_win.csv` then reads each brand on each question: **Strong** (named in 50%+ of answers), **Contested** (25–49%), **Gap that matters** (under 25%), **Named with warnings** (named, but mostly to warn against it), or **Not a target**. `where_to_win_summary.csv` counts these per brand. The thresholds sit at the top of `soa.py`.

## Run it (about 15 minutes to set up)

**Keep your API keys out of this folder.** Set them in the terminal as below, never in a file here, so they can't end up on GitHub. Each provider is pay-as-you-go: load a small prepaid amount ($5–10) and leave auto-recharge off. A full run of 40 questions × 3 runs cost about $5 on ChatGPT with web search, less on Perplexity.

You need Python 3.9 or later (Mac: already installed; type `python3 --version` in Terminal). No other installs.

1. Get API keys: OpenAI (platform.openai.com), Google Gemini (aistudio.google.com), Perplexity (perplexity.ai/settings/api).
2. In Terminal, in this folder:
   ```
   export OPENAI_API_KEY=...
   export GEMINI_API_KEY=...
   export PERPLEXITY_API_KEY=...
   ```
3. Pick the questions (no keys needed): `python3 soa.py questions` scores every candidate in `data/question_bank.csv` and explains each decision in `results/question_review.csv`. Change anything you disagree with (see below), then `python3 soa.py questions --apply`.
4. Test with fake answers first (no keys, no cost): `python3 soa.py run --dry-run && python3 soa.py score`, then delete the `results` folder.
5. Small real test: `python3 soa.py run --category kids_protein --runs 1`, then `python3 soa.py score`.
6. Full run (each question x 3 engines x 3 runs): `python3 soa.py run`, then `python3 soa.py score`.

## Choosing the questions

The full method, and why it works this way, is in [METHOD.md](METHOD.md). In short: questions start from shopper need states (occasions, benefits, barriers) across the journey, are written the way shoppers type, including Hinglish, price, city and persona variants, and are then filtered by rules.

| Step | Command or file |
| --- | --- |
| See what shoppers type about a topic | `python3 soa.py ideas "electric scooter"` → `results/question_ideas_electric_scooter.csv` |
| Add or reword candidate questions | `data/question_bank.csv` (`{city}` / `{price}` slots fill from `data/variants.csv`) |
| Search volume, automatic | Share of Google autocomplete suggestions (India) for the category's main terms in `data/categories.csv` that mention the question's keywords; on by default |
| Search volume, your own numbers | `data/volume.csv`: `search_phrase,volume` with High / Medium / Low / None or monthly searches from Keyword Planner. Wins over automatic |
| What's being talked about now | Put last-30-days research files (`.md` or `.txt`) in `data/signals/<category>/`, e.g. `data/signals/e2w/` for scooters (files directly in `data/signals/` count for every category) |
| Force a question in or out | `keep` column in the bank: `yes` or `no` |
| Change the rules | Limits at the top of `soa.py`, or `--max-per-stage 5` |

**On Windows:** install Python from python.org and tick "Add python.exe to PATH" in the installer. Open the unzipped folder, type `cmd` in the File Explorer address bar and press Enter. Use `python` instead of `python3`, and set keys with `set OPENAI_API_KEY=...` (no quotes, no spaces around `=`). Keys set this way last until you close that window.

If it stops, run the same command again; it skips answers already saved. Missing one key? Add `--engines chatgpt,perplexity`.

## Files

- `data/brands.csv`: brands per category, with the names to look for (edit freely)
- `METHOD.md`: how the shopper questions are built and filtered
- `data/question_bank.csv`: every candidate question, with need state, type, variant, search phrase and source
- `data/variants.csv`: values for `{city}` and `{price}` slots; `data/volume.csv`: your own search volumes (optional)
- `data/questions.csv`: the questions the run uses, written by `soa.py questions --apply` with the reason for each
- `results/question_review.csv`: every candidate, kept or dropped, and why
- `results/scorecard.csv`: the headline table (open in Excel or Google Sheets)
- `results/where_to_win.csv` and `where_to_win_summary.csv`: each brand's gaps, read only where it means to compete
- `results/scorecard_by_stage.csv`, `citations.csv`, `channels.csv`, `claims_to_check.csv`
- `results/hand_check_sample.csv`: about 10% of answers with the script's reading; fill in `my_reading` and `agree_y_n` to check the scoring

## Limitations

- Answers from the developer versions (APIs) can differ from the consumer apps. Web search is on to narrow the gap, and every question adds "I'm shopping in India."
- Answers vary run to run; each question is asked 3 times.
- Brand detection and "pick" detection are rules, not judgement. Check the hand sample before quoting a number.
- Amazon Rufus and Flipkart's assistant have no public API, so they are checked by hand.
- One week, India, about 35-40 questions: a directional read, not a market study. No claim of sales impact.

- APIs change: Perplexity moved from Sonar chat completions to its Agent API in September 2026, and Google retired older Gemini models for new users. Model names sit at the top of `soa.py` (or set `SOA_OPENAI_MODEL`, `SOA_GEMINI_MODEL`, `SOA_PERPLEXITY_MODEL`).
- Perplexity's Agent API has no country setting; the "I'm shopping in India" line does that job.
- The warning rule (a brand named only to warn against it) was tuned on kids' sugar warnings; extend `WARN_WORDS` in `soa.py` for other categories.
- Search volume comes from Google autocomplete, a free stand-in: no AI engine publishes how often a question is asked.

## Models used in the first run (26 Oct 2026)

| Engine | Model | Notes |
| --- | --- | --- |
| ChatGPT | `gpt-4.1`, Responses API with web search, location India | 113 answers |
| Perplexity | `perplexity/sonar`, Agent API with web search | 120 answers |
| Gemini | `gemini-3.8-flash`, then `gemini-flash-latest` | Not run: "high demand" errors and timeouts all afternoon |
