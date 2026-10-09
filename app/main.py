"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
from __future__ import annotations

import os
import re
from calendar import monthrange
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal, Optional

from bson import ObjectId, json_util
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError

load_dotenv()  # existing process environment variables take precedence
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "attendance_db")
client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000, connectTimeoutMS=3000)
db = client[MONGO_DB]
IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
MIN_EPOCH_MS = 100_000_000_000
MAX_EPOCH_MS = 4_102_444_800_000
PRESENCE_STATUSES = ("PRESENT", "WFH", "ON_DUTY")
ALL_STATUSES = ("PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY")

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


# ---------------------------------------------------------------------------
# Validation models
# ---------------------------------------------------------------------------
class EmployeeIn(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str

    @field_validator("joined_on")
    @classmethod
    def valid_joined_on(cls, value: str) -> str:
        try:
            date.fromisoformat(value)
        except (TypeError, ValueError):
            raise ValueError("joined_on must be YYYY-MM-DD")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("joined_on must be YYYY-MM-DD")
        return value


class PunchInIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emp_code: str
    punched_at: Optional[StrictInt] = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"

    @field_validator("punched_at")
    @classmethod
    def valid_punched_at(cls, value: Optional[int]) -> Optional[int]:
        validate_epoch_ms(value)
        return value


class PunchOutIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emp_code: str
    punched_at: Optional[StrictInt] = None

    @field_validator("punched_at")
    @classmethod
    def valid_punched_at(cls, value: Optional[int]) -> Optional[int]:
        validate_epoch_ms(value)
        return value


class RegularizeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None
    punch_in: Optional[StrictInt] = None
    punch_out: Optional[StrictInt] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

    @field_validator("punch_in", "punch_out")
    @classmethod
    def valid_instants(cls, value: Optional[int]) -> Optional[int]:
        validate_epoch_ms(value)
        return value

def validate_epoch_ms(value: Optional[int]) -> None:
    if value is None:
        return
    if type(value) is not int or not MIN_EPOCH_MS <= value <= MAX_EPOCH_MS:
        raise ValueError(f"timestamp must be integer epoch milliseconds between {MIN_EPOCH_MS} and {MAX_EPOCH_MS}")


# ---------------------------------------------------------------------------
# Time and serialization helpers
# ---------------------------------------------------------------------------
def truncate_seconds(value: datetime) -> datetime:
    return value.replace(microsecond=0)


def as_utc(value: datetime) -> datetime:
    # PyMongo returns naive UTC datetimes by default.
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def from_epoch_ms(value: Optional[int]) -> Optional[datetime]:
    if value is None:
        return None
    validate_epoch_ms(value)
    return truncate_seconds(datetime.fromtimestamp(value / 1000, tz=UTC))


def to_epoch_ms(value: Optional[datetime]) -> Optional[int]:
    if value is None:
        return None
    return int(as_utc(value).timestamp() * 1000)


def now_utc() -> datetime:
    return truncate_seconds(datetime.now(UTC))


def round_half_up(value: float, places: int = 2) -> float:
    quantum = Decimal(1).scaleb(-places)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def is_overnight(employee: dict[str, Any]) -> bool:
    return employee["shift_end"] <= employee["shift_start"]


def attendance_date_for(punch_in: datetime, employee: dict[str, Any]) -> str:
    local = as_utc(truncate_seconds(punch_in)).astimezone(IST)
    day = local.date()
    # Early-morning punch-ins before an overnight shift's end belong to the
    # shift that began on the previous calendar day.
    if is_overnight(employee) and local.strftime("%H:%M") < employee["shift_end"]:
        day -= timedelta(days=1)
    return day.isoformat()


def shift_start_instant(attendance_day: str, employee: dict[str, Any]) -> datetime:
    day = date.fromisoformat(attendance_day)
    hour, minute = map(int, employee["shift_start"].split(":"))
    return datetime.combine(day, time(hour, minute), tzinfo=IST).astimezone(UTC)


def shift_end_instant(attendance_day: str, employee: dict[str, Any]) -> datetime:
    day = date.fromisoformat(attendance_day)
    if is_overnight(employee):
        day += timedelta(days=1)
    hour, minute = map(int, employee["shift_end"].split(":"))
    return datetime.combine(day, time(hour, minute), tzinfo=IST).astimezone(UTC)


def compute_late_minutes(punch_in: datetime, shift_start: str, attendance_day: Optional[str] = None) -> int:
    punch = as_utc(truncate_seconds(punch_in))
    if attendance_day is None:
        attendance_day = punch.astimezone(IST).date().isoformat()
    hour, minute = map(int, shift_start.split(":"))
    start_local = datetime.combine(date.fromisoformat(attendance_day), time(hour, minute), tzinfo=IST)
    elapsed_seconds = int((punch - start_local.astimezone(UTC)).total_seconds())
    return elapsed_seconds // 60 if elapsed_seconds > 600 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = int((as_utc(truncate_seconds(punch_out)) - as_utc(truncate_seconds(punch_in))).total_seconds())
    return round_half_up(seconds / 3600.0, 2)


def compute_overtime(punch_out: datetime, shift_end: str, attendance_day: str, shift_start: Optional[str] = None) -> int:
    day = date.fromisoformat(attendance_day)
    if shift_start is not None and shift_end <= shift_start:
        day += timedelta(days=1)
    hour, minute = map(int, shift_end.split(":"))
    end = datetime.combine(day, time(hour, minute), tzinfo=IST).astimezone(UTC)
    elapsed_seconds = int((as_utc(truncate_seconds(punch_out)) - end).total_seconds())
    minutes = elapsed_seconds // 60
    return minutes if minutes >= 30 else 0


def weekday_expr(date_string_expr: Any) -> dict[str, Any]:
    safe_date = {"$dateFromString": {"dateString": {"$ifNull": [date_string_expr, "1970-01-01"]}}}
    return {"$in": [{"$dayOfWeek": safe_date}, [2, 3, 4, 5, 6]]}


def round_expr(expression: Any, places: int) -> dict[str, Any]:
    scale = 10 ** places
    return {"$divide": [{"$floor": {"$add": [{"$multiply": [expression, scale]}, 0.5]}}, scale]}


def working_days_expr(joined_on_expr: Any, month_start: str, month_end: str) -> dict[str, Any]:
    joined_dt = {"$dateFromString": {"dateString": joined_on_expr}}
    start_dt = {"$dateFromString": {"dateString": month_start}}
    end_dt = {"$dateFromString": {"dateString": month_end}}
    effective_start = {"$cond": [{"$gt": [joined_dt, start_dt]}, joined_dt, start_dt]}
    day_count = {"$add": [{"$dateDiff": {"startDate": effective_start, "endDate": end_dt, "unit": "day"}}, 1]}
    generated_days = {"$map": {
        "input": {"$range": [0, {"$max": [0, day_count]}]},
        "as": "offset",
        "in": {"$dateAdd": {"startDate": effective_start, "unit": "day", "amount": "$$offset"}},
    }}
    return {"$cond": [
        {"$gt": [joined_dt, end_dt]}, 0,
        {"$size": {"$filter": {
            "input": generated_days,
            "as": "workday",
            "cond": {"$in": [{"$dayOfWeek": "$$workday"}, [2, 3, 4, 5, 6]]},
        }}},
    ]}


def serialize_history(history: Optional[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    result = []
    for entry in history or []:
        item = dict(entry)
        item["at"] = to_epoch_ms(item.get("at"))
        changes = {}
        for key, pair in (item.get("changes") or {}).items():
            pair = dict(pair)
            if key in ("punch_in", "punch_out"):
                pair["from"] = to_epoch_ms(pair.get("from")) if isinstance(pair.get("from"), datetime) else pair.get("from")
                pair["to"] = to_epoch_ms(pair.get("to")) if isinstance(pair.get("to"), datetime) else pair.get("to")
            changes[key] = pair
        item["changes"] = changes
        result.append(item)
    return result


def serialize_attendance(document: dict[str, Any]) -> dict[str, Any]:
    result = {key: value for key, value in document.items() if key != "_id"}
    result.setdefault("punch_in", None)
    result.setdefault("punch_out", None)
    result.setdefault("work_hours", None)
    result.setdefault("late_minutes", 0)
    result.setdefault("overtime_minutes", 0)
    result.setdefault("half_day", False)
    result["punch_in"] = to_epoch_ms(result.get("punch_in"))
    result["punch_out"] = to_epoch_ms(result.get("punch_out"))
    result["history"] = serialize_history(result.get("history"))
    return result


def next_month(month: str) -> str:
    year, mon = map(int, month.split("-"))
    if mon == 12:
        return f"{year + 1:04d}-01"
    return f"{year:04d}-{mon + 1:02d}"


def month_bounds(month: str) -> tuple[str, str, str]:
    year, mon = map(int, month.split("-"))
    last_day = monthrange(year, mon)[1]
    return f"{month}-01", f"{month}-{last_day:02d}", f"{next_month(month)}-01"


def ensure_month(month: str) -> str:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(422, "month must be YYYY-MM")
    return month


def attendance_filter(emp_code: Optional[str], date_from: Optional[str], date_to: Optional[str], status: Optional[str]) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if emp_code is not None:
        query["emp_code"] = emp_code
    if date_from is not None or date_to is not None:
        date_query: dict[str, str] = {}
        if date_from is not None:
            date_query["$gte"] = date_from
        if date_to is not None:
            date_query["$lte"] = date_to
        query["date"] = date_query
    if status is not None:
        query["status"] = status
    return query


def validate_date_range(start: str, end: str) -> None:
    try:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
    except ValueError:
        raise HTTPException(422, "dates must be YYYY-MM-DD")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end):
        raise HTTPException(422, "dates must be YYYY-MM-DD")
    if start_date > end_date:
        raise HTTPException(422, "from must be on or before to")




def json_safe(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)

    if isinstance(value, datetime):
        return value.isoformat()

    # Handle BSON timestamps in MongoDB explain output.
    if type(value).__module__ == "bson.timestamp" and type(value).__name__ == "Timestamp":
        return {"time": value.time, "inc": value.inc}

    # Convert binary values to hexadecimal strings for JSON serialization.
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()

    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]

    return value



# ---------------------------------------------------------------------------
# Indexes and readiness
# ---------------------------------------------------------------------------
@app.on_event("startup")
def create_indexes() -> None:
    """Create idempotent indexes. If MongoDB is temporarily unavailable, keep
    the process alive so /health can correctly report 503 rather than lying."""
    try:
        client.admin.command("ping")
        db.employees.create_index([("emp_code", ASCENDING)], unique=True, name="uq_employees_emp_code")
        db.employees.create_index([("department", ASCENDING), ("emp_code", ASCENDING)], name="ix_employees_department_emp_code")
        db.employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING)], name="ix_employees_department_joined")
        db.employees.create_index([("joined_on", ASCENDING)], name="ix_employees_joined_on")
        db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True, name="uq_attendance_emp_date")
        db.attendance_logs.create_index([("date", DESCENDING), ("emp_code", ASCENDING), ("status", ASCENDING)], name="ix_attendance_date_emp_status")
        db.attendance_logs.create_index([("date", ASCENDING), ("late_minutes", DESCENDING), ("emp_code", ASCENDING)], name="ix_attendance_date_late_emp")
    except PyMongoError:
        # Readiness returns 503 while the database is unavailable.
        return


@app.get("/health")
def health() -> dict[str, str]:
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except PyMongoError:
        raise HTTPException(status_code=503, detail="MongoDB unavailable")


# ---------------------------------------------------------------------------
# Employees
# ---------------------------------------------------------------------------
@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn) -> dict[str, Any]:
    document = body.model_dump()
    document["created_at"] = now_utc()
    try:
        db.employees.insert_one(document)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="emp_code already exists")
    except PyMongoError:
        raise HTTPException(status_code=503, detail="MongoDB unavailable")
    document.pop("_id", None)
    document["created_at"] = to_epoch_ms(document["created_at"])
    return document


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if department is not None:
        query["department"] = department
    total = db.employees.count_documents(query)
    cursor = db.employees.find(query, {"_id": 0}).sort("emp_code", ASCENDING).skip((page - 1) * page_size).limit(page_size)
    items = list(cursor)
    for item in items:
        item["created_at"] = to_epoch_ms(item.get("created_at"))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ---------------------------------------------------------------------------
# Attendance write endpoints
# ---------------------------------------------------------------------------
@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn) -> dict[str, Any]:
    employee = db.employees.find_one({"emp_code": body.emp_code}, {"_id": 0})
    if employee is None:
        raise HTTPException(status_code=404, detail="employee not found")
    punch = from_epoch_ms(body.punched_at) if body.punched_at is not None else now_utc()
    assert punch is not None
    punch = truncate_seconds(punch)
    day = attendance_date_for(punch, employee)
    document = {
        "emp_code": body.emp_code,
        "date": day,
        "status": body.status,
        "punch_in": punch,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(punch, employee["shift_start"], day),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        db.attendance_logs.insert_one(document)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="already punched in for this date")
    except PyMongoError:
        raise HTTPException(status_code=503, detail="MongoDB unavailable")
    return serialize_attendance(document)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn) -> dict[str, Any]:
    employee = db.employees.find_one({"emp_code": body.emp_code}, {"_id": 0})
    if employee is None:
        raise HTTPException(status_code=404, detail="employee not found")
    punch = from_epoch_ms(body.punched_at) if body.punched_at is not None else now_utc()
    assert punch is not None
    punch = truncate_seconds(punch)

    record = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$ne": None}, "punch_out": None},
        sort=[("date", DESCENDING)],
    )
    if record is None:
        if db.attendance_logs.find_one({"emp_code": body.emp_code}) is not None:
            raise HTTPException(status_code=409, detail="no open attendance record to punch out")
        raise HTTPException(status_code=404, detail="attendance record not found")

    if punch < as_utc(record["punch_in"]):
        raise HTTPException(status_code=422, detail="punch_out cannot precede punch_in")
    work_hours = compute_work_hours(record["punch_in"], punch)
    overtime = compute_overtime(punch, employee["shift_end"], record["date"], employee["shift_start"])
    half_day = work_hours < 4.50
    updated = db.attendance_logs.find_one_and_update(
        {"emp_code": body.emp_code, "date": record["date"], "punch_out": None, "punch_in": {"$ne": None}},
        {"$set": {"punch_out": punch, "work_hours": work_hours, "overtime_minutes": overtime, "half_day": half_day}},
        return_document=True,
    )
    if updated is None:
        raise HTTPException(status_code=409, detail="attendance record already punched out")
    return serialize_attendance(updated)


# ---------------------------------------------------------------------------
# Attendance list
# ---------------------------------------------------------------------------
@app.get("/attendance")

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[date] = Query(default=None, alias="from"),
    date_to: Optional[date] = Query(default=None, alias="to"),
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:

    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be on or before date_to")
    query = attendance_filter(emp_code, date_from.isoformat() if date_from else None, date_to.isoformat() if date_to else None, status)
    total = db.attendance_logs.count_documents(query)
    docs = list(db.attendance_logs.find(query).sort("date", DESCENDING).skip((page - 1) * page_size).limit(page_size))
    return {"items": [serialize_attendance(doc) for doc in docs], "total": total, "page": page, "page_size": page_size}


# ---------------------------------------------------------------------------
# Regularization / audit trail
# ---------------------------------------------------------------------------
@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: date, body: RegularizeIn) -> dict[str, Any]:
    day = date.isoformat()
    if ("status" in body.model_fields_set and body.status is None) or ("punch_in" in body.model_fields_set and body.punch_in is None) or ("punch_out" in body.model_fields_set and body.punch_out is None):
        raise HTTPException(status_code=422, detail="provided status and punch timestamps cannot be null")
    employee = db.employees.find_one({"emp_code": emp_code}, {"_id": 0})
    if employee is None:
        raise HTTPException(status_code=404, detail="employee not found")
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": day})
    if record is None:
        raise HTTPException(status_code=404, detail="attendance record not found")

    before = {key: record.get(key) for key in ("status", "punch_in", "punch_out", "work_hours", "late_minutes", "overtime_minutes", "half_day")}
    status = body.status if body.status is not None else record.get("status", "PRESENT")
    if status in ("ABSENT", "LEAVE"):
        punch_in = punch_out = None
    else:
        punch_in = from_epoch_ms(body.punch_in) if body.punch_in is not None else record.get("punch_in")
        punch_out = from_epoch_ms(body.punch_out) if body.punch_out is not None else record.get("punch_out")
        if punch_in is None:
            raise HTTPException(status_code=422, detail="presence status requires punch_in")
        if punch_out is not None and as_utc(punch_out) < as_utc(punch_in):
            raise HTTPException(status_code=422, detail="punch_out cannot precede punch_in")
    if punch_in is not None:
        punch_in = truncate_seconds(punch_in)
    if punch_out is not None:
        punch_out = truncate_seconds(punch_out)

    if status in ("ABSENT", "LEAVE"):
        work_hours, late_minutes, overtime_minutes, half_day = None, 0, 0, False
    else:
        late_minutes = compute_late_minutes(punch_in, employee["shift_start"], day)
        if punch_out is None:
            work_hours, overtime_minutes, half_day = None, 0, False
        else:
            work_hours = compute_work_hours(punch_in, punch_out)
            overtime_minutes = compute_overtime(punch_out, employee["shift_end"], day, employee["shift_start"])
            half_day = work_hours < 4.50

    after = {"status": status, "punch_in": punch_in, "punch_out": punch_out, "work_hours": work_hours,
             "late_minutes": late_minutes, "overtime_minutes": overtime_minutes, "half_day": half_day}
    changes: dict[str, Any] = {}
    for key, new_value in after.items():
        old_value = before.get(key)
        if key in ("punch_in", "punch_out"):
            old_value = as_utc(old_value) if isinstance(old_value, datetime) else old_value
            new_value = as_utc(new_value) if isinstance(new_value, datetime) else new_value
        if old_value != new_value:
            changes[key] = {"from": before.get(key), "to": after[key]}
    if not changes:
        # Successful no-op corrections still append an audit entry with no changes.
        changes = {}

    history_entry = {"at": now_utc(), "by": body.regularized_by, "reason": body.reason, "changes": changes}
    update_fields = {key: after[key] for key in after}
    # $push is atomic and does not discard history entries from concurrent PATCH calls.
    updated = db.attendance_logs.find_one_and_update(
        {"emp_code": emp_code, "date": day},
        {"$set": update_fields, "$push": {"history": history_entry}},
        return_document=True,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="attendance record not found")
    return serialize_attendance(updated)


# ---------------------------------------------------------------------------
# Analytics aggregation pipelines
# ---------------------------------------------------------------------------
def monthly_employee_pipeline(emp_code: str, month: str) -> list[dict[str, Any]]:
    start, end, next_start = month_bounds(month)
    return [
        {"$match": {"emp_code": emp_code}},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"code": "$emp_code"},
            "pipeline": [{"$match": {"$expr": {"$and": [
                {"$eq": ["$emp_code", "$$code"]}, {"$gte": ["$date", start]}, {"$lt": ["$date", next_start]},
            ]}}}],
            "as": "logs",
        }},
        {"$addFields": {"working_days": working_days_expr("$joined_on", start, end)}},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {"$group": {
            "_id": "$emp_code", "emp_code": {"$first": "$emp_code"}, "month": {"$first": {"$literal": month}},
            "working_days": {"$first": "$working_days"},
            "present_days": {"$sum": {"$cond": [
                {"$and": [{"$in": ["$logs.status", list(PRESENCE_STATUSES)]}, weekday_expr("$logs.date")]},
                {"$cond": [{"$eq": [{"$ifNull": ["$logs.half_day", False]}, True]}, 0.5, 1]}, 0,
            ]}},
            "leave_days": {"$sum": {"$cond": [{"$and": [{"$eq": ["$logs.status", "LEAVE"]}, weekday_expr("$logs.date")]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$ifNull": ["$logs.late_minutes", 0]}},
            "total_overtime_minutes": {"$sum": {"$ifNull": ["$logs.overtime_minutes", 0]}},
        }},
        {"$project": {
            "_id": 0, "emp_code": 1, "month": 1, "working_days": 1, "present_days": 1, "leave_days": 1,
            "late_count": 1, "total_late_minutes": 1, "total_overtime_minutes": 1,
            "attendance_pct": {"$cond": [
                {"$gt": ["$working_days", 0]}, round_expr({"$multiply": [{"$divide": ["$present_days", "$working_days"]}, 100]}, 4), None,
            ]},
        }},
    ]


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$")) -> dict[str, Any]:
    ensure_month(month)
    if db.employees.find_one({"emp_code": emp_code}, {"_id": 1}) is None:
        raise HTTPException(status_code=404, detail="employee not found")
    rows = list(db.employees.aggregate(monthly_employee_pipeline(emp_code, month)))
    if not rows:
        raise HTTPException(status_code=404, detail="employee not found")
    return rows[0]


def department_summary_pipeline(month: str, department: Optional[str] = None) -> list[dict[str, Any]]:
    start, end, next_start = month_bounds(month)
    match: dict[str, Any] = {"joined_on": {"$lte": end}}
    if department is not None:
        match["department"] = department
    return [
        {"$match": match},
        {"$lookup": {
            "from": "attendance_logs", "let": {"code": "$emp_code"},
            "pipeline": [{"$match": {"$expr": {"$and": [
                {"$eq": ["$emp_code", "$$code"]}, {"$gte": ["$date", start]}, {"$lt": ["$date", next_start]},
            ]}}}], "as": "logs",
        }},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {"$group": {
            "_id": {"department": "$department", "emp_code": "$emp_code"}, "department": {"$first": "$department"},
            "present_days": {"$sum": {"$cond": [
                {"$and": [{"$in": ["$logs.status", list(PRESENCE_STATUSES)]}, weekday_expr("$logs.date")]},
                {"$cond": [{"$eq": [{"$ifNull": ["$logs.half_day", False]}, True]}, 0.5, 1]}, 0,
            ]}},
            "work_hours_sum": {"$sum": {"$cond": [{"$ne": [{"$ifNull": ["$logs.work_hours", None]}, None]}, "$logs.work_hours", 0]}},
            "work_hours_count": {"$sum": {"$cond": [{"$ne": [{"$ifNull": ["$logs.work_hours", None]}, None]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$ifNull": ["$logs.late_minutes", 0]}},
            "leave_count": {"$sum": {"$cond": [{"$and": [{"$eq": ["$logs.status", "LEAVE"]}, weekday_expr("$logs.date")]}, 1, 0]}},
            "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0]}},
        }},
        {"$group": {
            "_id": "$department", "department": {"$first": "$department"}, "headcount": {"$sum": 1},
            "present_days": {"$sum": "$present_days"}, "work_hours_sum": {"$sum": "$work_hours_sum"},
            "work_hours_count": {"$sum": "$work_hours_count"}, "late_count": {"$sum": "$late_count"},
            "total_late_minutes": {"$sum": "$total_late_minutes"}, "leave_count": {"$sum": "$leave_count"},
            "on_duty_count": {"$sum": "$on_duty_count"},
        }},
        {"$project": {
            "_id": 0, "department": 1, "headcount": 1, "present_days": 1,
            "avg_work_hours": {"$cond": [
                {"$gt": ["$work_hours_count", 0]}, round_expr({"$divide": ["$work_hours_sum", "$work_hours_count"]}, 2), None,
            ]},
            "late_count": 1, "total_late_minutes": 1, "leave_count": 1, "on_duty_count": 1,
        }},
        {"$sort": {"department": 1}},
    ]


@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
) -> dict[str, Any]:
    ensure_month(month)
    return {"month": month, "items": list(db.employees.aggregate(department_summary_pipeline(month, department)))}


def late_leaderboard_pipeline(month: str, limit: int, department: Optional[str] = None) -> list[dict[str, Any]]:
    start, _end, next_start = month_bounds(month)
    pipeline: list[dict[str, Any]] = [
        {"$match": {"date": {"$gte": start, "$lt": next_start}}},
        {"$group": {
            "_id": "$emp_code", "emp_code": {"$first": "$emp_code"},
            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
        }},
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {"$lookup": {"from": "employees", "localField": "emp_code", "foreignField": "emp_code", "as": "employee"}},
        {"$unwind": "$employee"},
    ]
    if department is not None:
        pipeline.append({"$match": {"employee.department": department}})
    pipeline.extend([
        {"$setWindowFields": {"sortBy": {"total_late_minutes": DESCENDING}, "output": {"rank": {"$rank": {}}}}},
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"rank": 1, "emp_code": 1}},
        {"$project": {"_id": 0, "rank": 1, "emp_code": 1, "name": "$employee.name", "department": "$employee.department", "total_late_minutes": 1, "late_count": 1}},
    ])
    return pipeline


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(default=10, ge=1, le=50),
    department: Optional[str] = None,
) -> dict[str, Any]:
    ensure_month(month)
    items = list(db.attendance_logs.aggregate(late_leaderboard_pipeline(month, limit, department)))
    return {"month": month, "items": items}


def department_trend_pipeline(department: str, start: str, end: str) -> list[dict[str, Any]]:
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    days = (end_date - start_date).days + 1
    return [
        {"$match": {"department": department}},
        {"$group": {"_id": None, "employee_codes": {"$push": "$emp_code"},
                    "employees": {"$push": {"emp_code": "$emp_code", "joined_on": "$joined_on"}}}},
        {"$project": {
            "_id": 0, "employee_codes": 1, "employees": 1,
            "days": {"$map": {"input": {"$range": [0, days]}, "as": "offset", "in": {
                "$dateToString": {"format": "%Y-%m-%d", "date": {"$dateAdd": {
                    "startDate": {"$dateFromString": {"dateString": start}}, "unit": "day", "amount": "$$offset"
                }}}
            }}},
        }},
        {"$unwind": "$days"},
        {"$set": {"date": "$days"}},
        {"$set": {"headcount": {"$size": {"$filter": {"input": "$employees", "as": "employee", "cond": {"$lte": ["$$employee.joined_on", "$date"]}}}}}},
        {"$lookup": {
            "from": "attendance_logs", "let": {"day": "$date", "codes": "$employee_codes"},
            "pipeline": [{"$match": {"$expr": {"$and": [{"$eq": ["$date", "$$day"]}, {"$in": ["$emp_code", "$$codes"]}]}}}],
            "as": "logs",
        }},
        {"$set": {"is_working_day": weekday_expr("$date")}},
        {"$set": {
            "present_count": {"$sum": {"$map": {"input": {"$filter": {"input": "$logs", "as": "log", "cond": {
                "$and": [{"$in": ["$$log.status", list(PRESENCE_STATUSES)]}, "$is_working_day", {"$lte": ["$$log.date", "$date"]}]
            }}}, "as": "log", "in": {"$cond": [{"$eq": [{"$ifNull": ["$$log.half_day", False]}, True]}, 0.5, 1]}}}},
            "late_count": {"$size": {"$filter": {"input": "$logs", "as": "log", "cond": {"$gt": [{"$ifNull": ["$$log.late_minutes", 0]}, 0]}}}},
        }},
        {"$set": {"attendance_rate": {"$cond": [
            {"$gt": ["$headcount", 0]}, round_expr({"$multiply": [{"$divide": ["$present_count", "$headcount"]}, 100]}, 4), None
        ]}}},
        {"$setWindowFields": {"sortBy": {"date": 1}, "output": {
            "moving_avg_7d": {"$avg": "$attendance_rate", "window": {"documents": [-6, 0]}}
        }}},
        {"$project": {"_id": 0, "date": 1, "is_working_day": 1, "headcount": 1, "present_count": 1,
                      "late_count": 1, "attendance_rate": 1, "moving_avg_7d": {"$cond": [
                          {"$eq": [{"$type": "$moving_avg_7d"}, "missing"]}, None, round_expr("$moving_avg_7d", 4)
                      ]}}},
        {"$sort": {"date": 1}},
    ]


@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, from_date: str = Query(..., alias="from"), to_date: str = Query(..., alias="to")) -> dict[str, Any]:
    validate_date_range(from_date, to_date)
    if db.employees.find_one({"department": department}, {"_id": 1}) is None:
        raise HTTPException(status_code=404, detail="department not found")
    items = list(db.employees.aggregate(department_trend_pipeline(department, from_date, to_date)))
    return {"department": department, "items": items}


# ---------------------------------------------------------------------------
# Explain endpoint: raw executionStats for the endpoint's principal query.
# ---------------------------------------------------------------------------
@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: Literal["attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"],
    emp_code: Optional[str] = None,
    month: Optional[str] = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
    limit: int = Query(default=10, ge=1, le=50),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    from_date: Optional[str] = Query(default=None, alias="from"),
    to_date: Optional[str] = Query(default=None, alias="to"),
) -> dict[str, Any]:
    try:
        if endpoint == "attendance_list":
            query = attendance_filter(emp_code, date_from.isoformat() if date_from else None, date_to.isoformat() if date_to else None, status)
            command = {"explain": {"find": "attendance_logs", "filter": query, "sort": {"date": -1}, "skip": (page - 1) * page_size, "limit": page_size}, "verbosity": "executionStats"}
            result = db.command(command)
            collection = "attendance_logs"
        elif endpoint == "employee_monthly":
            if not emp_code or not month:
                raise HTTPException(status_code=422, detail="emp_code and month are required")
            ensure_month(month)
            command = {"explain": {"aggregate": "employees", "pipeline": monthly_employee_pipeline(emp_code, month), "cursor": {}}, "verbosity": "executionStats"}
            result = db.command(command)
            collection = "employees"
        elif endpoint == "department_summary":
            if not month:
                raise HTTPException(status_code=422, detail="month is required")
            ensure_month(month)
            command = {"explain": {"aggregate": "employees", "pipeline": department_summary_pipeline(month, department), "cursor": {}}, "verbosity": "executionStats"}
            result = db.command(command)
            collection = "employees"
        elif endpoint == "late_leaderboard":
            if not month:
                raise HTTPException(status_code=422, detail="month is required")
            ensure_month(month)
            command = {"explain": {"aggregate": "attendance_logs", "pipeline": late_leaderboard_pipeline(month, limit, department), "cursor": {}}, "verbosity": "executionStats"}
            result = db.command(command)
            collection = "attendance_logs"
        else:
            if not department or not from_date or not to_date:
                raise HTTPException(status_code=422, detail="department, from and to are required")
            validate_date_range(from_date, to_date)
            command = {"explain": {"aggregate": "employees", "pipeline": department_trend_pipeline(department, from_date, to_date), "cursor": {}}, "verbosity": "executionStats"}
            result = db.command(command)
            collection = "employees"
        # Return the explain document itself (not the wrapper command response).
        explain_doc = result.get("queryPlanner", result)
        if "executionStats" in result:
            explain_doc = result
        return {"endpoint": endpoint, "collection": collection, "explain": json_safe(explain_doc)}
    except HTTPException:
        raise
    except PyMongoError:
        raise HTTPException(status_code=503, detail="MongoDB unavailable")
