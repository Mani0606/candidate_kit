"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""

import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Annotated

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, field_validator, Field, model_validator
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from enum import Enum
import math
from pymongo import ASCENDING, DESCENDING

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def ensure_indexes():
    db.employees.create_index("emp_code", unique=True)
    db.employees.create_index([("department", 1), ("joined_on", 1)])
    db.employees.create_index([("department", 1), ("emp_code", 1)])
    db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
    db.attendance_logs.create_index([("date", -1), ("emp_code", 1)])
    db.attendance_logs.create_index([("status", 1), ("date", 1)])
    db.attendance_logs.create_index([("emp_code", 1), ("punch_out", 1), ("status", 1)])
    db.attendance_logs.create_index([("late_minutes", 1)])


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_indexes()
    yield


app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
    lifespan=lifespan,
)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    h, m = map(int, shift_start.split(":"))
    start = punch_in.replace(hour=h, minute=m, second=0, microsecond=0)
    minutes = (punch_in - start).total_seconds() / 60
    return math.floor(minutes) if minutes > 10 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    return round((punch_out - punch_in).total_seconds() / 3600, 2)


def compute_overtime(punch_out: datetime, shift_end: str, date_str: datetime) -> int:
    h, m = map(int, shift_end.split(":"))
    end = date_str.replace(hour=h, minute=m, second=0, microsecond=0)
    return max(0, int((punch_out - end).total_seconds() / 60))


def compute_half_day(work_hours) -> bool:
    return work_hours < 4.5


def to_epoch_millis(dt: datetime | str) -> int:
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("datetime must be timezone-aware (include Z or an offset)")

    ms = (dt.astimezone(timezone.utc) - EPOCH) // timedelta(milliseconds=1)
    return ms


def _ms_to_dt(ms: int):
    return EPOCH + timedelta(milliseconds=ms)


def check_emp_code(value: str) -> str:
    value = value.strip()

    if value[0:3] != "EMP":
        raise ValueError("EMP Code must start with EMP")
    elif not value[3:].isdigit:
        raise ValueError("After EMP only numerics should present")

    return value


def _attendance_date_for_punch_in(punch_in_value, shift_start, shift_end):
    if punch_in_value is None:
        return None
    if isinstance(punch_in_value, str):
        punch_in_dt = _ms_to_dt(int(punch_in_value))
    else:
        punch_in_dt = punch_in_value
    if not isinstance(punch_in_dt, datetime):
        return None
    local_dt = punch_in_dt.astimezone(IST)
    return local_dt.date().isoformat()


def _round_half_up(value, digits):
    if value is None:
        return None
    quantum = Decimal("1").scaleb(-digits)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def _record_derived_values(emp, record):
    status = record.get("status")
    punch_in = record.get("punch_in")
    punch_out = record.get("punch_out")
    if status in {"ABSENT", "LEAVE"}:
        return {
            "status": status,
            "punch_in": None,
            "punch_out": None,
            "work_hours": None,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
        }
    if punch_in is None:
        return {
            "status": status,
            "punch_in": None,
            "punch_out": punch_out,
            "work_hours": None,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
        }
    late = compute_late_minutes(punch_in, emp["shift_start"])
    work_hours = None
    overtime = 0
    half_day = False
    if punch_out is not None:
        work_hours = compute_work_hours(punch_in, punch_out)
        overtime = compute_overtime(punch_out, emp["shift_end"], punch_in)
        half_day = compute_half_day(work_hours)
    return {
        "status": status,
        "punch_in": to_epoch_millis(punch_in),
        "punch_out": to_epoch_millis(punch_out),
        "work_hours": work_hours,
        "late_minutes": late,
        "overtime_minutes": overtime,
        "half_day": half_day,
    }


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #


class Department(str, Enum):
    engineering = "Engineering"
    sales = "Sales"
    support = "Support"
    manager = "manager"
    admin = "Admin"
    none = "NONE"


class AttendenceStatus(str, Enum):
    present = "PRESENT"
    absent = "ABSENT"
    leave = "LEAVE"
    wfh = "WFH"
    on_duty = "ON_DUTY"
    none = "NONE"


class EmployeeIn(BaseModel):
    emp_code: str = Field(min_length=4)
    name: str = Field(min_length=4, max_length=20, pattern=r"^[A-Za-z]+$")
    email: str = Field(
        max_length=256, pattern=r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"
    )
    department: Department = Department.support
    shift_start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", default="09:30")
    shift_end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", default="18:30")
    joined_on: str
    validate_emp_code = field_validator("emp_code")(check_emp_code)

    @field_validator("joined_on")
    def check_joined_on(cls, value: str) -> str:
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Work date must be a valid date in YYYY-MM-DD format")
        return value

    @model_validator(mode="after")
    def check_order(self):
        start = datetime.combine(datetime.today(), self.shift_start)
        end = datetime.combine(datetime.today(), self.shift_end)

        if self.shift_end == self.shift_start:
            raise ValueError("shift end must be different from shift start")

        if end <= start:
            end += timedelta(days=1)

        if end - start < timedelta(hours=9):
            raise ValueError("Shift duration must be at least 9 hours")

        return self


class PunchIn(BaseModel):
    emp_code: str = Field(min_length=4)
    punched_at: Optional[int]
    status: AttendenceStatus = AttendenceStatus.present
    validate_emp_code = field_validator("emp_code")(check_emp_code)


class PunchOut(BaseModel):
    emp_code: str = Field(min_length=4)
    punched_at: Optional[int]
    validate_emp_code = field_validator("emp_code")(check_emp_code)


class ListAttendanceDTO(BaseModel):
    emp_code: Optional[str] = (None,)
    date_from: Optional[str] = (None,)
    date_to: Optional[str] = (None,)
    status: Optional[AttendenceStatus] = AttendenceStatus.none
    page: int = (1,)
    page_size: int = (20,)


class RegularizeRequest(BaseModel):
    status: Optional[AttendenceStatus] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    reason: str = Field(..., min_length=5, max_length=200)
    regularized_by: str = Field(..., min_length=1, max_length=50)


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    if client.admin.command("ping"):
        return {"status": "ok"}
    else:
        return {"status": "503"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one(
        {"emp_code": body.emp_code, "email": body.email}
    ):  # Add Email unique check
        raise HTTPException(409, "EMP code already exists")
    doc = body.model_dump()
    doc["created_at"] = datetime.now()
    db.employees.insert_one(doc)
    doc.pop("_id", None)
    return doc


@app.get("/employees")
def list_employees(
    department: Optional[Department] = None, page: int = 1, page_size: int = 20
):
    q = {}
    if department:
        q["department"] = department.value
    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)
    items = list(db.employees.find(q, {"_id": 0}).skip(skip).limit(page_size))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(409, "Employee doesn't exist")

    ts = _ms_to_dt(body.punched_at) if body.punched_at else datetime.now()
    d = ts.date().isoformat()
    if db.attendance_logs.find_one({"emp_code": body.emp_code, "date": d}):
        raise HTTPException(409, "already punched in for this date")
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": to_epoch_millis(ts),
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        res = db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")
    doc["id"] = str(res.inserted_id)
    doc.pop("_id", None)
    return doc


@app.get("/attendance")
def list_attendance(body: Annotated[ListAttendanceDTO, Query()]):

    if body.emp_code:
        emp = db.employees.find_one({"emp_code": body.emp_code})
        if not emp:
            raise HTTPException(409, "Employee doesn't exist")

    q = {}
    if body.emp_code:
        q["emp_code"] = body.emp_code
    if body.date_from or body.date_to:
        q["date"] = {}
        if body.date_from:
            q["date"]["$gte"] = body.date_from
        if body.date_to:
            q["date"]["$lte"] = body.date_to
    if body.status != AttendenceStatus.none:
        q["status"] = body.status.value

    skip = (body.page - 1) * body.page_size
    docs = list(
        db.attendance_logs.find(q, {"_id": 0})
        .skip(skip)
        .limit(body.page_size)
        .sort([("date", DESCENDING), ("emp_code", ASCENDING)])
    )
    total = len(docs)
    return {
        "items": docs,
        "total": total,
        "page": body.page,
        "page_size": body.page_size,
    }


@app.post("/attedance/punch-out", status_code=201)
def punch_out(body: PunchOut):
    ts = _ms_to_dt(body.punched_at) if body.punched_at else datetime.now()
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if not emp:
        raise HTTPException(409, "Employee doesn't exist")

    record = db.attendance_logs.find_one(
        {
            "emp_code": emp["emp_code"],
            "punch_out": None,
            "status": AttendenceStatus.present,
        }
    )

    if not record:
        raise HTTPException(409, "already punched out for this date")

    punch_in = _ms_to_dt(record["punch_in"])
    if ts <= punch_in:
        raise HTTPException(409, "Punch out can't appear before punch In")

    work_hours = compute_work_hours(punch_in, ts)
    update_result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
        },
        {
            "$set": {
                "punch_out": to_epoch_millis(ts),
                "work_hours": work_hours,
                "overtime_minutes": compute_overtime(ts, emp["shift_end"], punch_in),
                "half_day": compute_half_day(work_hours),
            }
        },
    )

    if update_result.matched_count == 0:
        raise HTTPException(409, "Attendance record was already punched out")

    updated_record = db.attendance_logs.find_one({"_id": record["_id"]})
    updated_record["id"] = str(updated_record.pop("_id"))
    return updated_record


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_attendance_summary(
    emp_code: str,
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
):
    emp = db.employees.find_one({"emp_code": emp_code})

    if not emp:
        raise HTTPException(404, "Employee doesn't exist")

    month_start = datetime.strptime(month, "%Y-%m").date()
    if month_start.month == 12:
        next_month = month_start.replace(year=month_start.year + 1, month=1)
    else:
        next_month = month_start.replace(month=month_start.month + 1)
    month_end = next_month - timedelta(days=1)
    joined_on = datetime.strptime(emp["joined_on"], "%Y-%m-%d").date()
    day = max(month_start, joined_on)
    working_days = 0
    while day <= month_end:
        if day.weekday() < 5:
            working_days += 1
        day += timedelta(days=1)

    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lt": next_month.isoformat(),
                },
            }
        },
        {
            "$addFields": {
                "_attendance_date": {
                    "$dateFromString": {"dateString": "$date", "onError": None}
                }
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$gte": [{"$dayOfWeek": "$_attendance_date"}, 2]},
                                    {"$lte": [{"$dayOfWeek": "$_attendance_date"}, 6]},
                                ]
                            },
                            {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]},
                            0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
                "total_overtime_minutes": {
                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                },
            }
        },
    ]
    results = list(db.attendance_logs.aggregate(pipeline))
    totals = results[0] if results else {}
    present_days = totals.get("present_days", 0)

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": totals.get("leave_days", 0),
        "late_count": totals.get("late_count", 0),
        "total_late_minutes": totals.get("total_late_minutes", 0),
        "total_overtime_minutes": totals.get("total_overtime_minutes", 0),
        "attendance_pct": (
            _round_half_up(present_days / working_days * 100, 2)
            if working_days
            else None
        ),
    }


@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[Department] = Department.none,
):
    try:
        month_start_date = datetime.strptime(month, "%Y-%m")
    except ValueError:
        raise HTTPException(422, "month must be in YYYY-MM format")

    if month_start_date.month == 12:
        next_month_date = month_start_date.replace(
            year=month_start_date.year + 1, month=1
        )
    else:
        next_month_date = month_start_date.replace(month=month_start_date.month + 1)

    month_start = month_start_date.strftime("%Y-%m-%d")
    next_month = next_month_date.strftime("%Y-%m-%d")
    month_end = (next_month_date - timedelta(days=1)).strftime("%Y-%m-%d")
    employee_match = {"joined_on": {"$lte": month_end}}
    if department is not Department.none:
        employee_match["department"] = department

    pipeline = [
        {"$match": employee_match},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"employee_code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$employee_code"]},
                                    {"$gte": ["$date", month_start]},
                                    {"$lt": ["$date", next_month]},
                                ]
                            }
                        }
                    }
                ],
                "as": "attendance",
            }
        },
        {
            "$unwind": {
                "path": "$attendance",
                "preserveNullAndEmptyArrays": True,
            }
        },
        {
            "$group": {
                "_id": "$department",
                "employees": {"$addToSet": "$emp_code"},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {"$eq": [{"$ifNull": ["$attendance.date", None]}, None]},
                            0,
                            {
                                "$cond": [
                                    {
                                        "$and": [
                                            {
                                                "$in": [
                                                    "$attendance.status",
                                                    ["PRESENT", "WFH", "ON_DUTY"],
                                                ]
                                            },
                                            {
                                                "$in": [
                                                    {
                                                        "$dayOfWeek": {
                                                            "$dateFromString": {
                                                                "dateString": "$attendance.date"
                                                            }
                                                        }
                                                    },
                                                    [2, 3, 4, 5, 6],
                                                ]
                                            },
                                        ]
                                    },
                                    {
                                        "$cond": [
                                            {"$eq": ["$attendance.half_day", True]},
                                            0.5,
                                            1,
                                        ]
                                    },
                                    0,
                                ]
                            },
                        ]
                    }
                },
                "work_hours_total": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$attendance.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {
                                        "$ne": [
                                            {
                                                "$ifNull": [
                                                    "$attendance.work_hours",
                                                    None,
                                                ]
                                            },
                                            None,
                                        ]
                                    },
                                ]
                            },
                            "$attendance.work_hours",
                            0,
                        ]
                    }
                },
                "work_hours_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$attendance.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {
                                        "$ne": [
                                            {
                                                "$ifNull": [
                                                    "$attendance.work_hours",
                                                    None,
                                                ]
                                            },
                                            None,
                                        ]
                                    },
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$attendance.late_minutes", 0]}, 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$attendance.late_minutes", 0]}
                },
                "leave_count": {
                    "$sum": {"$cond": [{"$eq": ["$attendance.status", "LEAVE"]}, 1, 0]}
                },
                "on_duty_count": {
                    "$sum": {
                        "$cond": [{"$eq": ["$attendance.status", "ON_DUTY"]}, 1, 0]
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": {"$size": "$employees"},
                "present_days": 1,
                "work_hours_total": 1,
                "work_hours_count": 1,
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1,
            }
        },
        {"$sort": {"department": 1}},
    ]

    rows = list(db.employees.aggregate(pipeline))
    for row in rows:
        if row["work_hours_count"]:
            row["avg_work_hours"] = float(
                (
                    Decimal(str(row["work_hours_total"])) / row["work_hours_count"]
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            )
        else:
            row["avg_work_hours"] = None
        row.pop("work_hours_total")
        row.pop("work_hours_count")

    return {"month": month, "items": rows}


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeRequest):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not record:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    old_status = record.get("status")
    old_punch_in = record.get("punch_in")
    old_punch_out = record.get("punch_out")
    new_status = body.status.value if body.status is not None else old_status
    new_punch_in = (
        _ms_to_dt(body.punch_in)
        if body.punch_in is not None
        else _ms_to_dt(old_punch_in)
    )
    new_punch_out = (
        _ms_to_dt(body.punch_out)
        if body.punch_out is not None
        else _ms_to_dt(old_punch_out)
    )

    if new_status in ["ABSENT", "LEAVE"]:
        new_punch_in = None
        new_punch_out = None
    else:
        if new_punch_in is None and old_punch_in is None:
            raise HTTPException(
                status_code=422, detail="Presence status requires a punch_in"
            )
        if new_punch_in is not None:
            expected_date = _attendance_date_for_punch_in(
                new_punch_in, emp["shift_start"], emp["shift_end"]
            )
            if expected_date != date:
                raise HTTPException(
                    status_code=422, detail="punch_in must stay on the attendance date"
                )

    if new_punch_in is not None and new_punch_out is not None:
        if new_punch_out <= new_punch_in:
            raise HTTPException(
                status_code=422, detail="punch_out must be after punch_in"
            )
        if new_punch_out - new_punch_in > timedelta(hours=24):
            raise HTTPException(
                status_code=422, detail="punch_out must be within 24 hours of punch_in"
            )

    final = _record_derived_values(
        emp,
        {
            "status": new_status,
            "punch_in": new_punch_in,
            "punch_out": new_punch_out,
        },
    )
    next_record = {**record, **final}

    changes = {}
    for field in [
        "status",
        "punch_in",
        "punch_out",
        "work_hours",
        "late_minutes",
        "overtime_minutes",
        "half_day",
    ]:
        old_value = record.get(field)
        new_value = next_record.get(field)
        if old_value != new_value:
            changes[field] = {"from": old_value, "to": new_value}

    if not changes:
        raise HTTPException(status_code=422, detail="Request changes nothing")

    entry = {
        "at": datetime.now(timezone.utc),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }
    history = list(record.get("history") or [])
    history.append(entry)

    result = db.attendance_logs.update_one(
        {"_id": record["_id"]},
        {
            "$set": {
                "status": next_record["status"],
                "punch_in": next_record["punch_in"],
                "punch_out": next_record["punch_out"],
                "work_hours": next_record["work_hours"],
                "late_minutes": next_record["late_minutes"],
                "overtime_minutes": next_record["overtime_minutes"],
                "half_day": next_record["half_day"],
                "history": history,
            }
        },
    )
    if result.matched_count == 0:
        raise HTTPException(
            status_code=409, detail="Attendance record changed concurrently"
        )

    updated = db.attendance_logs.find_one({"_id": record["_id"]})
    updated["id"] = str(updated.pop("_id"))

    return updated


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(10, ge=1, le=50),
    department: Optional[Department] = Department.none,
):
    try:
        month_dt = datetime.strptime(month, "%Y-%m")
    except ValueError:
        raise HTTPException(status_code=422, detail="month must be YYYY-MM")
    next_month = (month_dt.replace(day=28) + timedelta(days=4)).replace(day=1)
    month_start = month_dt.strftime("%Y-%m-%d")
    next_month_str = next_month.strftime("%Y-%m-%d")

    pipeline = []
    if department.value != Department.none:
        pipeline.append({"$match": {"department": department.value}})

    pipeline.extend(
        [
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "let": {"employee_code": "$emp_code"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$emp_code", "$$employee_code"]},
                                        {"$gte": ["$date", month_start]},
                                        {"$lt": ["$date", next_month_str]},
                                        {"$gt": ["$late_minutes", 0]},
                                    ]
                                }
                            }
                        }
                    ],
                    "as": "late_records",
                }
            },
            {
                "$addFields": {
                    "total_late_minutes": {"$sum": "$late_records.late_minutes"},
                    "late_count": {"$size": "$late_records"},
                }
            },
            {"$match": {"late_count": {"$gt": 0}}},
            {
                "$setWindowFields": {
                    "sortBy": {"total_late_minutes": -1},
                    "output": {"rank": {"$rank": {}}},
                }
            },
            {"$match": {"rank": {"$lte": limit}}},
            {"$sort": {"rank": 1, "emp_code": 1}},
            {
                "$project": {
                    "_id": 0,
                    "rank": 1,
                    "emp_code": 1,
                    "name": 1,
                    "department": 1,
                    "total_late_minutes": 1,
                    "late_count": 1,
                }
            },
        ]
    )
    items = list(db.employees.aggregate(pipeline))

    return {"month": month, "items": items}


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: Department,
    from_: str = Query(..., alias="from"),
    to: str = Query(...),
):
    try:
        start_dt = datetime.strptime(from_, "%Y-%m-%d").date()
        end_dt = datetime.strptime(to, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="from and to must be YYYY-MM-DD")

    if end_dt < start_dt:
        raise HTTPException(status_code=422, detail="to must be on or after from")

    if (end_dt - start_dt).days + 1 > 92:
        raise HTTPException(status_code=422, detail="range exceeds 92 days")

    pipeline = [
        {"$documents": [{"from": from_, "day_count": (end_dt - start_dt).days + 1}]},
        {
            "$lookup": {
                "from": "employees",
                "pipeline": [
                    {"$match": {"department": department.value}},
                    {"$project": {"_id": 0, "emp_code": 1, "joined_on": 1}},
                ],
                "as": "employees",
            }
        },
        {"$match": {"employees.0": {"$exists": True}}},
        {
            "$set": {
                "days": {
                    "$map": {
                        "input": {"$range": [0, "$day_count"]},
                        "as": "offset",
                        "in": {
                            "$dateAdd": {
                                "startDate": {
                                    "$dateFromString": {"dateString": "$from"}
                                },
                                "unit": "day",
                                "amount": "$$offset",
                            }
                        },
                    }
                }
            }
        },
        {"$unwind": "$days"},
        {
            "$set": {
                "date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$days"}},
                "day_of_week": {"$dayOfWeek": "$days"},
                "day_employees": {
                    "$filter": {
                        "input": "$employees",
                        "as": "employee",
                        "cond": {
                            "$lte": [
                                "$$employee.joined_on",
                                {
                                    "$dateToString": {
                                        "format": "%Y-%m-%d",
                                        "date": "$days",
                                    }
                                },
                            ]
                        },
                    }
                },
            }
        },
        {
            "$set": {
                "headcount": {"$size": "$day_employees"},
                "emp_codes": {
                    "$map": {
                        "input": "$day_employees",
                        "as": "employee",
                        "in": "$$employee.emp_code",
                    }
                },
                "is_working_day": {
                    "$and": [
                        {"$gte": ["$day_of_week", 2]},
                        {"$lte": ["$day_of_week", 6]},
                    ]
                },
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"day": "$date", "codes": "$emp_codes"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$date", "$$day"]},
                                    {"$in": ["$emp_code", "$$codes"]},
                                ]
                            }
                        }
                    },
                    {
                        "$project": {
                            "_id": 0,
                            "status": 1,
                            "half_day": 1,
                            "late_minutes": 1,
                        }
                    },
                ],
                "as": "records",
            }
        },
        {
            "$set": {
                "present_count": {
                    "$sum": {
                        "$map": {
                            "input": "$records",
                            "as": "record",
                            "in": {
                                "$cond": [
                                    {
                                        "$in": [
                                            "$$record.status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {
                                        "$cond": [
                                            {"$eq": ["$$record.half_day", True]},
                                            0.5,
                                            1.0,
                                        ]
                                    },
                                    0,
                                ]
                            },
                        }
                    }
                },
                "late_count": {
                    "$size": {
                        "$filter": {
                            "input": "$records",
                            "as": "record",
                            "cond": {
                                "$gt": [{"$ifNull": ["$$record.late_minutes", 0]}, 0]
                            },
                        }
                    }
                },
            }
        },
        {
            "$set": {
                "attendance_rate_raw": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]},
                            ]
                        },
                        {
                            "$divide": [
                                {
                                    "$floor": {
                                        "$add": [
                                            {
                                                "$multiply": [
                                                    {
                                                        "$divide": [
                                                            {
                                                                "$toDecimal": "$present_count"
                                                            },
                                                            {
                                                                "$toDecimal": "$headcount"
                                                            },
                                                        ]
                                                    },
                                                    {"$toDecimal": 10000},
                                                ]
                                            },
                                            {"$toDecimal": 0.5},
                                        ]
                                    }
                                },
                                {"$toDecimal": 10000},
                            ]
                        },
                        None,
                    ]
                },
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"date": 1},
                "output": {
                    "moving_avg_7d_raw": {
                        "$avg": "$attendance_rate_raw",
                        "window": {"documents": [-6, "current"]},
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": 1,
                "is_working_day": 1,
                "headcount": 1,
                "present_count": {"$toDouble": "$present_count"},
                "late_count": 1,
                "attendance_rate": {
                    "$cond": [
                        {"$eq": ["$attendance_rate_raw", None]},
                        None,
                        {"$toDouble": "$attendance_rate_raw"},
                    ]
                },
                "moving_avg_7d": {
                    "$cond": [
                        {"$eq": ["$moving_avg_7d_raw", None]},
                        None,
                        {
                            "$toDouble": {
                                "$divide": [
                                    {
                                        "$floor": {
                                            "$add": [
                                                {
                                                    "$multiply": [
                                                        "$moving_avg_7d_raw",
                                                        {"$toDecimal": 10000},
                                                    ]
                                                },
                                                {"$toDecimal": 0.5},
                                            ]
                                        }
                                    },
                                    {"$toDecimal": 10000},
                                ]
                            }
                        },
                    ]
                },
            }
        },
        {"$sort": {"date": 1}},
    ]

    items = list(db.aggregate(pipeline))
    if not items:
        raise HTTPException(status_code=404, detail="Unknown department")

    return {"department": department.value, "items": items}


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: Optional[int] = Query(None, ge=1, le=50),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[AttendenceStatus] = None,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    endpoint = endpoint.strip()
    if endpoint not in {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    }:
        raise HTTPException(status_code=422, detail="Unknown endpoint")

    if endpoint == "attendance_list":
        filt = {}
        if emp_code:
            filt["emp_code"] = emp_code
        if date_from or date_to:
            filt["date"] = {}
            if date_from:
                filt["date"]["$gte"] = date_from
            if date_to:
                filt["date"]["$lte"] = date_to
        if status:
            filt["status"] = status.value
        result = db.command(
            "explain",
            {
                "find": "attendance_logs",
                "filter": filt,
                "sort": {"date": -1, "emp_code": 1},
                "skip": (page - 1) * page_size,
                "limit": page_size,
                "verbosity": "executionStats",
            },
        )
        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": result,
        }

    if endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(
                status_code=422, detail="emp_code and month are required"
            )
        result = db.command(
            "explain",
            {
                "find": "attendance_logs",
                "filter": {"emp_code": emp_code},
                "verbosity": "executionStats",
            },
        )
        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": result,
        }

    if endpoint == "department_summary":
        if not month:
            raise HTTPException(status_code=422, detail="month is required")
        filter_doc = {"department": department} if department else {}
        result = db.command(
            "explain",
            {"find": "employees", "filter": filter_doc, "verbosity": "executionStats"},
        )
        return {"endpoint": endpoint, "collection": "employees", "explain": result}

    if endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(status_code=422, detail="month is required")
        filt = {"late_minutes": {"$gt": 0}}
        if department:
            emp_codes = [
                doc["emp_code"]
                for doc in db.employees.find(
                    {"department": department}, {"_id": 0, "emp_code": 1}
                )
            ]
            filt["emp_code"] = {"$in": emp_codes}
        result = db.command(
            "explain",
            {
                "find": "attendance_logs",
                "filter": filt,
                "sort": {"late_minutes": -1, "emp_code": 1},
                "limit": limit or 10,
                "verbosity": "executionStats",
            },
        )
        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": result,
        }

    if endpoint == "department_trend":
        if not department or not from_ or not to:
            raise HTTPException(
                status_code=422, detail="department, from and to are required"
            )
        emp_codes = [
            doc["emp_code"]
            for doc in db.employees.find(
                {"department": department}, {"_id": 0, "emp_code": 1}
            )
        ]
        result = db.command(
            "explain",
            {
                "find": "attendance_logs",
                "filter": {
                    "emp_code": {"$in": emp_codes},
                    "date": {"$gte": from_, "$lte": to},
                },
                "sort": {"date": 1, "emp_code": 1},
                "verbosity": "executionStats",
            },
        )
        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": result,
        }

    raise HTTPException(status_code=422, detail="Unsupported endpoint")
