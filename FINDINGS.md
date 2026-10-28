# First run: what AI tells Indian shoppers to buy

**AI names a category's established or expert brand, but the pick goes to whoever fits the question.** A brand can be named in most answers and recommended in almost none.

Run on 26 October 2026: 40 shopper questions chosen by the rules in [METHOD.md](METHOD.md), each asked 3 times on ChatGPT (`gpt-4.1` with web search) and Perplexity (`perplexity/sonar`, Agent API), 233 answers in all. Gemini could not be run that day (see limits below).

## By category

| Category | Most named (share of answers) | What stands out |
| --- | --- | --- |
| Electric scooters | TVS iQube 85%, Ather 76%, Bajaj Chetak 65%, Ola S1 64%, Hero Vida 49% | iQube is the pick: recommended in 32% of answers, 46% on Perplexity. Chetak is named often but recommended in 6%, and is absent from the apartment-charging question. Ola is still named in 95% of ChatGPT answers, and recommended in 18%, despite a fall to about 6% of sales |
| Kids' nutrition drinks | Horlicks 65%, PediaSure 44%, Complan 44%, Gritzo 26% | Horlicks is the pick on the low-sugar question (50%), so its No Added Sugar relaunch shows up in AI answers. It is absent on the protein question, where Gritzo is the pick in a third of answers. Two in three answers question whether a child needs the drink at all |
| Face wash | Cetaphil 60%, Himalaya 21%, Mamaearth 17%, Pond's 6%, Garnier 2% | Dermatologist-led brands win: Cetaphil is named first in 54% of answers, and derm brands outside the tracked list (CeraVe, Minimalist, La Roche-Posay) appear often. Mass MNC brands are nearly invisible |
| Two-wheeler accessories | Vega 54%, Steelbird 51%, Studds 49% | A bike brand's own accessories win only when the shopper names the bike: Chetak's own on "accessories for my new Chetak" (75%), Ather's Halo on the Ather helmet question (40%). On generic helmet or phone-holder questions they score 0% |

## Where AI sends the shopper

- **Kids' nutrition:** pharmacy or 1mg (30% of answers), then Zepto and BigBasket (15% each).
- **Face wash:** pharmacy (19%), Nykaa (15%), Zepto (13%).
- **Scooters:** the dealer (77%).

## What it means for a brand

1. **Track Share of Answer by need state, not as one number.** Horlicks wins low sugar and is absent on protein; Chetak is strong on service and missing on charging.
2. **The fix is often a product or proof point, not media.** The low-sugar answer moved with a reformulation; the charging gap needs a clear charging story where shoppers and forums look.
3. **Owned accessories leak at the first unbranded question.** The bike brand keeps accessory demand only while the shopper says its name.

## Earlier hand test (25 October, apps, fresh chats)

- 8 of 9 app answers on kids' nutrition put milk or home food first.
- The API is not the app: Perplexity's app picked brands where its API named none in 5 of 8 answers. What a shopper sees depends on the door they use.
- Paid placement has reached AI answers: a Gritzo ad under a ChatGPT answer, and Horlicks ads bought via Criteo landing on Blinkit.

## Read with care

- Scoring note: picks were rescored on 28 October so that a brand counts as the pick if any mention recommends it, not only its first mention. ChatGPT tends to list brands first and recommend at the end, so its picks had been undercounted.
- Two engines, not three. Gemini returned "high demand" or timed out all afternoon on 26 October.
- ChatGPT has 14 of 21 accessory answers (its credit ran out).
- The warning rule was tuned on kids' sugar warnings, so it misses negative scooter mentions (Ola shows no warnings).
- The brand list missed some brands seen in answers (CeraVe, Minimalist, Axor, SMK); counts above are for tracked brands only.
- One week, India, 40 questions: a directional read, not a market study, and no claim about sales.
