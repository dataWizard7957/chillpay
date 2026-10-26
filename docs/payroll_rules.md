# Payroll rules

These formulas are implemented in `app/services/payroll_engine.py`. The language model must not compute them.

- Daily rate = Monthly payment / 30.
- Final leaves = Total leaves − Comp-off.
- Final extra days = Extra work days − Comp-off.
- Final payment = Monthly payment − (Final leaves × daily rate) + (Final extra days × daily rate).
- Hourly equivalent = Monthly payment / 30 / 8.
- Overtime multiplier default 1.5.
- Contract TDS default 10% of gross. Employee and trainee TDS is 0.
- Amount paid = Gross − TDS.
- Net deposit = Amount paid − actual advance recovery.
- Recovery above amount paid is capped and flagged. Net deposit stays at or above 0.

`prorate_monthly_payment` exists and is not used. Eligible-day counting is not decided, so a partial month or a pay term that changes during the month cannot be saved.

Invoice billing periods are shown only for calendar months that have 30 days. September works. Other month lengths are rejected for invoice display only; the monthly formula still uses / 30.

Advance recovery starts the month after the date paid. The last installment is adjusted so the parts sum to the original amount. Examples: ₹12,000 / 6 = six × ₹2,000. ₹10,000 / 6 = five × ₹1,666.67 and last ₹1,666.65.

Financial-year TDS reports group already-saved amounts from 1 April to 31 March. That grouping is not a tax-policy decision.
