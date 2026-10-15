# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | cerate Employee | Not Email if exist when creating employee | When understanding the given code| By adding check for email|
| 2 | All the classes | Validation is not there for every class used for post path | When understanding the given code | By Pydantic|
| 3 | PunchIn | The given name for PunchIn class is PunchInIn | When understanding the given code |Removed the 2nd In | 
| 4 | compute_late_mintues | The function is not considering seconds | Test | By removing floor operation |
| 5 | compute_overtime | The function is overcomplicated the process of computing end variable | Test | By changing parameter  datatype and passing recorded punch_in as argument|
| 6 | list_employees | Corrected Page nation formula during the running the code add to the list | Test | By decrementing the page number |
| 7 | list_employees | Corrected no of documents according selected document. Add it to list | When understanding the given code | By adding given department |
| 8 | punch_in | The employee existent didn't check add it to the list | When understanding the given code | By adding document check |
| 9 | punch_in | The input punch_in didn't transfred into datetime from epoch milliseconds | Test |By wirting a custom function |
| 10 | list_attendance | The retrived documents were looping. | Test | I build a pipline with correct pageniation. |
| 11 | list_attendance | The emp_code sorting isn't wasn't computed | When understanding the given code | By changing the pipline |

Also note anything you looked at and decided was **not** a defect, and why.
