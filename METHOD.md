# How the shopper questions are built

A Share of Answer number is only as good as the questions behind it. Ask the wrong questions and you measure what the brand team wanted to hear, not what shoppers ask. This kit builds questions with a fixed method and fixed rules, writes down why each question is in, and lets you change any of it.

## 1. Start from the shopper, not the brand

For each category, write down who decides and what makes them start looking. A parent worried about a fussy eater, a commuter hit by petrol prices, a teenager with acne.

Then list **need states**, the reason behind the purchase, in three kinds:

| Kind | What it is | Kids' protein | Electric scooter |
| --- | --- | --- | --- |
| Occasion | When or where it's used | A bored child, switching drinks | Family use, city commute, switching from petrol |
| Benefit | What the shopper wants | Protein, height, what doctors recommend | Range, removable battery |
| Barrier | What stops the purchase | Sugar, daily safety | Charging in an apartment, battery fire, service, EMI |

Barriers matter most in high-value categories: they are where shoppers drop out, and where AI answers differ most.

## 2. Place them on the shopper journey

Consideration, review, purchase, repeat. A category only needs questions where shoppers actually choose. A scooter is bought every 6 to 8 years, so its "repeat" stage is switching from petrol and buying accessories.

## 3. Write each question the way a shopper types it

- No brand names, except one head-to-head question per category, which is scored separately.
- One real-life detail each: a price, a city, family use, a time limit ("in 10 minutes").
- **Variants** cover how India actually asks: Hinglish, price tiers, cities, personas (a delivery rider, a new rider). A question can carry a `{city}` or `{price}` slot; `data/variants.csv` fills it in.

Where the wording comes from: Google autocomplete and "People also ask" (`python soa.py ideas "electric scooter"` collects these), YouTube review comments, Reddit and owner forums, dealer FAQs, 1-star reviews, and the follow-up questions the AI apps suggest. Each question's `source` column says where it came from.

## 4. Filter with rules

`python soa.py questions` scores every candidate in `data/question_bank.csv`:

| Rule | Why | How |
| --- | --- | --- |
| It must ask for a choice | If no brand can be picked, no brand can win | "which", "best", "suggest", "kaunsa"… |
| Shoppers must ask it | Don't measure questions nobody asks | **Search volume, automatic:** collect everything Google autocomplete suggests in India for the category's main terms (`data/categories.csv`, a few hundred suggestions), then count the share that mention the question's keywords. 4%+ is High, 1–4% Medium, under 1% Low. **Manual:** your own numbers in `data/volume.csv` from Google Trends or Keyword Planner, which win over automatic |
| Is it being talked about now? | Recent talk shows what is on shoppers' minds | **Recency:** mentions of the question's keywords in last-30-days research files: `data/signals/<category>/` (for example `data/signals/e2w/`) counts for that category only, files directly in `data/signals/` count for all |
| Balance across the journey | One stage shouldn't drown out the rest | At most 4 questions per stage, 2 per need state, 1 head-to-head per category |
| Cover how India asks | Hinglish and price-led shoppers get different answers | Each category keeps at least one Hinglish, one price and one city question, if the bank has one |

Score = volume points (High 3, Medium 2, Low 1) + recency points (High 2, Medium 1). The highest-scoring questions fill each stage. A question with no Google suggestions and no recent talk is dropped.

No AI engine publishes how often a question is asked. Google search demand is the best free stand-in, so volume is used to rank and filter, not as a precise weight.

## 5. Review, change, apply

Every decision and its reason goes to `results/question_review.csv`. To change the outcome:

- set `keep` to `yes` or `no` in `data/question_bank.csv` (we pin the three hand-tested kids' questions this way)
- reword a question, or add your own rows
- change the limits at the top of `soa.py`, or use `--max-per-stage`

Then run `python soa.py questions --apply` to write `data/questions.csv`. The old list is saved as a backup first.
