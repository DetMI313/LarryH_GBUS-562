# Week 2 Writeup: Prompt Lab

## What I Built

I built Prompt Lab (`prompt-lab.html`), a single-page web app that sends prompts to Claude (`claude-sonnet-5`) through the Anthropic API. The API key is typed in at the start of each session and kept only in the open page, so it is never saved to a file. The app supports an optional system prompt, multi-turn conversations, and an Export button that downloads the conversation as Markdown with the model name on the first line and each message under `### System`, `### You`, or `### Claude`. I used it to complete four marketing tasks for a fictional coffee company, Wildframe Coffee Roasters, each with a different prompting technique, and saved the exports in `runs/`. `task-1.md` generates loyalty perks for long-term subscribers, `task-2.md` writes coffee bean cards, `task-3.md` replies to customer comments in the brand voice, and `task-4.md` analyzes where to place a spring pop-up stand.

## Why This Approach

**Task 1 - Technique: zero-shot**

Brainstorming subscriber perks is an open-ended creative task, and Claude already knows what loyalty rewards look like, so it did not need an example to understand the job. Leaving examples out also avoided anchoring the ideas to one pattern, while the brand description and the strict format rules (exactly 6 ideas, one sentence each, 25 words or fewer) were enough to keep the output usable.

**Task 2 - Technique: one-shot**

The bean cards had to follow an exact layout with fixed labels in a fixed order, which is much easier to show than to describe. One complete example (Huila, Colombia) defined the structure, the all-caps heading, and the joking tone of the "Real talk" line, so additional examples would have added length without adding information.

**Task 3 - Technique: few-shot**

Brand voice is hard to pin down with adjectives alone, and replying to customers covers very different situations: skepticism, praise, and a how-to question. Three example replies showed how Wildframe sounds in each of those cases, which gave Claude a range of tone to match for a complaint, a product question, and a compliment.

**Task 4 - Technique: chain-of-thought**

Choosing a pop-up location involved several dependent calculations (customers, revenue, four-week totals, rent) plus a budget constraint, and a recommendation made without that work would be easy to get wrong. Asking Claude to reason step by step made every number visible and checkable, and it surfaced the deciding factors: Union Station breaks the $5,000 rent budget, and the library cannot beat Riverside even with free rent.

## Example Run

The full contents of `runs/task-4.md`:

````markdown
# Prompt Lab Run — claude-sonnet-5

Exported: 11/2/2026, 7:12:42 PM

### System

(none)

### You

You are helping Wildframe Coffee Roasters decide where to place a four-week spring pop-up stand.

Think through the decision step by step before making a recommendation.

Use the following data:

Riverside Farmers Market:
- 9,000 visitors per week
- 2% buy
- Average sale $14
- Rent $900 per week

Union Station concourse:
- 30,000 visitors per week
- 0.5% buy
- Average sale $6
- Rent $2,000 per week

Campus library atrium:
- 6,000 visitors per week
- 3% buy
- Average sale $5
- Rent budget fits within the overall limit

The total rent budget for the four weeks is $5,000.

Calculate and compare:
- Expected weekly customers
- Expected weekly revenue
- Expected four-week revenue
- Rent costs
- Overall opportunity

Show your reasoning and then make a recommendation.

Required format:
The response must end with a single line that starts with:

Recommendation:

### Claude

# Pop-Up Stand Location Analysis

## Step 1: Expected Weekly Customers

| Location | Visitors/Week | Buy Rate | Weekly Customers |
|---|---|---|---|
| Riverside Farmers Market | 9,000 | 2% | **180** |
| Union Station Concourse | 30,000 | 0.5% | **150** |
| Campus Library Atrium | 6,000 | 3% | **180** |

Riverside and Campus Library tie on customer count, despite very different visitor volumes — Riverside's higher buy rate makes up for lower foot traffic, while Union Station's high traffic is undercut by a very low conversion rate.

## Step 2: Expected Weekly Revenue

| Location | Customers | Avg Sale | Weekly Revenue |
|---|---|---|---|
| Riverside | 180 | $14 | **$2,520** |
| Union Station | 150 | $6 | **$900** |
| Campus Library | 180 | $5 | **$900** |

Riverside pulls far ahead here because it combines solid customer count *with* the highest average sale price.

## Step 3: Expected Four-Week Revenue

| Location | Weekly Revenue × 4 | 4-Week Revenue |
|---|---|---|
| Riverside | $2,520 × 4 | **$10,080** |
| Union Station | $900 × 4 | **$3,600** |
| Campus Library | $900 × 4 | **$3,600** |

## Step 4: Rent Costs vs. Budget ($5,000 total)

| Location | Weekly Rent | 4-Week Rent | Fits Budget? |
|---|---|---|---|
| Riverside | $900 | **$3,600** | ✅ Yes ($1,400 to spare) |
| Union Station | $2,000 | **$8,000** | ❌ No — exceeds budget by $3,000 |
| Campus Library | Unspecified | ≤ $5,000 (stated to fit) | ✅ Yes |

Union Station is **immediately disqualified** — it costs more in rent alone than the entire four-week budget allows, before even accounting for revenue.

## Step 5: Net Opportunity (Revenue − Rent)

| Location | Revenue | Rent | Net Profit (4 weeks) |
|---|---|---|---|
| Riverside | $10,080 | $3,600 | **$6,480** |
| Union Station | $3,600 | $8,000 | **–$4,400 (infeasible)** |
| Campus Library | $3,600 | ≤ $5,000 | **≤ $3,600 (at best, if rent were $0)** |

**Key insight:** Even in the best-case scenario where Campus Library's rent is $0, its maximum possible profit ($3,600) still falls short of Riverside's profit ($6,480). Riverside wins on revenue generation alone, independent of exact rent figures for the library site. Union Station fails on budget feasibility before profitability is even relevant.

## Step 6: Overall Opportunity Summary

- **Riverside Farmers Market:** Highest revenue, healthy profit margin, well within budget, lowest risk.
- **Union Station:** High traffic is a mirage — poor conversion and high rent make it a clear loss, and it's disqualified by budget alone.
- **Campus Library Atrium:** Decent conversion rate, but low average sale price caps revenue potential regardless of rent savings.

Riverside dominates on every meaningful metric: revenue, profit, and budget safety margin.

Recommendation: Choose Riverside Farmers Market — it generates the highest four-week revenue ($10,080) and profit (~$6,480), comfortably fits within the $5,000 rent budget, and outperforms both alternatives even under best-case assumptions for the other sites.

### You

Please revise the final line so it contains only:

Recommendation: [location name]

and nothing else after the location name.

### Claude

# Pop-Up Stand Location Analysis

## Step 1: Expected Weekly Customers

| Location | Visitors/Week | Buy Rate | Weekly Customers |
|---|---|---|---|
| Riverside Farmers Market | 9,000 | 2% | **180** |
| Union Station Concourse | 30,000 | 0.5% | **150** |
| Campus Library Atrium | 6,000 | 3% | **180** |

Riverside and Campus Library tie on customer count, despite very different visitor volumes — Riverside's higher buy rate makes up for lower foot traffic, while Union Station's high traffic is undercut by a very low conversion rate.

## Step 2: Expected Weekly Revenue

| Location | Customers | Avg Sale | Weekly Revenue |
|---|---|---|---|
| Riverside | 180 | $14 | **$2,520** |
| Union Station | 150 | $6 | **$900** |
| Campus Library | 180 | $5 | **$900** |

Riverside pulls far ahead here because it combines solid customer count *with* the highest average sale price.

## Step 3: Expected Four-Week Revenue

| Location | Weekly Revenue × 4 | 4-Week Revenue |
|---|---|---|
| Riverside | $2,520 × 4 | **$10,080** |
| Union Station | $900 × 4 | **$3,600** |
| Campus Library | $900 × 4 | **$3,600** |

## Step 4: Rent Costs vs. Budget ($5,000 total)

| Location | Weekly Rent | 4-Week Rent | Fits Budget? |
|---|---|---|---|
| Riverside | $900 | **$3,600** | ✅ Yes ($1,400 to spare) |
| Union Station | $2,000 | **$8,000** | ❌ No — exceeds budget by $3,000 |
| Campus Library | Unspecified | ≤ $5,000 (stated to fit) | ✅ Yes |

Union Station is **immediately disqualified** — it costs more in rent alone than the entire four-week budget allows, before even accounting for revenue.

## Step 5: Net Opportunity (Revenue − Rent)

| Location | Revenue | Rent | Net Profit (4 weeks) |
|---|---|---|---|
| Riverside | $10,080 | $3,600 | **$6,480** |
| Union Station | $3,600 | $8,000 | **–$4,400 (infeasible)** |
| Campus Library | $3,600 | ≤ $5,000 | **≤ $3,600 (at best, if rent were $0)** |

**Key insight:** Even in the best-case scenario where Campus Library's rent is $0, its maximum possible profit ($3,600) still falls short of Riverside's profit ($6,480). Riverside wins on revenue generation alone, independent of exact rent figures for the library site. Union Station fails on budget feasibility before profitability is even relevant.

## Step 6: Overall Opportunity Summary

- **Riverside Farmers Market:** Highest revenue, healthy profit margin, well within budget, lowest risk.
- **Union Station:** High traffic is a mirage — poor conversion and high rent make it a clear loss, and it's disqualified by budget alone.
- **Campus Library Atrium:** Decent conversion rate, but low average sale price caps revenue potential regardless of rent savings.

Riverside dominates on every meaningful metric: revenue, profit, and budget safety margin.

Recommendation: Riverside Farmers Market
````

## Reflection

Each technique fit its task, but they made different kinds of difference. Zero-shot was enough for brainstorming perks, and the one-shot example locked the bean cards into the exact format on the first try. Few-shot gave the customer replies a consistent voice across a complaint, a question, and a compliment. Chain-of-thought made the biggest difference. The analysis showed every calculation, caught that Union Station's $8,000 rent breaks the $5,000 budget, and argued that the library could not beat Riverside even with free rent, which made the recommendation easy to trust and verify. It was not perfect, though. The first answer added an explanation after "Recommendation:", so I needed a follow-up turn to get a clean final line. The choice I was least certain about was zero-shot for Task 1, because the brand voice was described only with adjectives; the perks were on-format, but a few lines, like "loyalty deserves a discount," sound more generic than Wildframe's examples in Task 3. In the future I would add one sample perk to Task 1, give Claude the real decaf product name in Task 3 so it does not invent one (it named a "decaf Colombia" I never provided), and put the exact final-line rule in the system prompt for Task 4 so the format is right on the first attempt.
