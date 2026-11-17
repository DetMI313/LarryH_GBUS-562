# 03 - Megan Turner, "Family trip"

## Email
> I'd like to take 10 business days off in a row for a family trip, about two months from now. Do I have enough vacation days for that? If not, what are my options?

## Tool calls
1. `get_leave_balance("megan.turner@hartwellfoods.example")`
   -> Department: Marketing, Manager: Lena Fischer, Vacation days left: 7, Sick days left: 5, Personal days left: 0
2. `search_policy("requesting vacation advance notice approval consecutive days")`
   -> leave-policy.html, "Requesting vacation" (0.73): requests of more than 5 consecutive business days need manager approval at least 10 business days ahead.
   -> leave-policy.html, "When you don't have enough vacation days" (0.45): balance cannot go below zero; up to 5 days of unpaid leave can cover the difference, with approval from both manager and HR.
3. `search_policy("vacation days accrual earned per month")`
   -> leave-policy.html, "How much vacation you earn" (0.63): 15 days a year for the first five years, 20 after. The passage doesn't say how often days are added.

## needs_human_review
false

## Reply
Hi Megan,

Thanks for planning ahead. You currently have **7 vacation days** left, so 10 business days is 3 more than your balance covers.

Your options:
- **Unpaid leave to cover the difference.** If a planned absence is longer than your balance, you can request up to 5 days of unpaid leave. You'd use your 7 vacation days plus 3 unpaid days. Unpaid leave needs approval from both your manager and Human Resources. *(leave-policy.html, "When you don't have enough vacation days")*
- **Shorten the trip** to 7 business days or fewer so your vacation balance covers it. *(Your vacation days can't be used before they're earned, and your balance can't go below zero. Source: leave-policy.html)*

**Timing:** Requests of more than 5 business days in a row need your manager's approval at least 10 business days ahead *(leave-policy.html, "Requesting vacation")*. Two months out gives you plenty of time. Please start with your manager, Lena Fischer.

Best regards,
Hartwell HR

## Note
The policy says how many days are earned per year but not how often they're added, so the reply does not suggest Megan will have more days in two months.
