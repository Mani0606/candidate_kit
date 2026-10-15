# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?
2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.
3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?
4. **Headcount.** How does your department summary make sure employees with zero logs are counted?
5. **One thing you would change** if this had to serve 100x the data.


Answers:-

1. Created indexes of emp_code, department, joined_on, date, status, late_minutes, punch_out because those are the mosted used when building the paths. I have consider to create for email but emp_code also serveres same purpose.

2. When both requestes are arrived at same time the inital check but when 1st request insereted the and return 201 but when the 2nd requestes cames it throw 409 error becuase the kay already created because of index creation 

3. If there are 2 employee have same rank, emp_code is considered for sorting.

4. I have grouped the employees with department and joined_on and searched the emp_codes in attendance_logs and aggregated the results. So, I can include the employees even with no logs.

5. I would add Redis. So, I can store frequently requested data and serve them when required and query the DB when rare data is used.   