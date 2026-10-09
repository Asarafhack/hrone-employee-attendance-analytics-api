# HROne — Employee Attendance & Analytics API

A RESTful backend API built with **FastAPI, Python, and MongoDB** for managing employee records, attendance punch-in/punch-out, attendance regularization, and workforce analytics.

The project is designed around accurate attendance calculations, database-backed analytics, validation, concurrency safety, and query-performance inspection.

**Repository:** [hrone-employee-attendance-analytics-api](https://github.com/Asarafhack/hrone-employee-attendance-analytics-api)

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Technology Stack](#technology-stack)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Installation and Setup](#installation-and-setup)
- [Environment Configuration](#environment-configuration)
- [Running the Application](#running-the-application)
- [API Endpoints](#api-endpoints)
- [Attendance Business Rules](#attendance-business-rules)
- [Analytics](#analytics)
- [Database Design](#database-design)
- [Concurrency and Data Integrity](#concurrency-and-data-integrity)
- [Error Handling](#error-handling)
- [Testing and Verification](#testing-and-verification)
- [Performance and Explain Plans](#performance-and-explain-plans)
- [API Documentation](#api-documentation)
- [Troubleshooting](#troubleshooting)
- [Author](#author)

## Overview

The HROne Employee Attendance & Analytics API provides a backend service for tracking employee attendance and generating department-level and employee-level reports.

The application uses FastAPI for HTTP endpoints and request validation, and MongoDB for persistent storage and aggregation-based analytics.

It supports employee management, attendance tracking, regularization history, monthly attendance reports, department summaries, late-arrival rankings, and department attendance trends.

## Features

- **Employee management:** Create and retrieve employee records.
- **Attendance tracking:** Record punch-in and punch-out timestamps.
- **Attendance regularization:** Update attendance records while maintaining a history of changes.
- **Attendance calculations:** Calculate work hours, late minutes, overtime, and half-day status.
- **Monthly analytics:** Generate employee-level monthly attendance reports.
- **Department analytics:** Summarize headcount, attendance, leave, lateness, and work hours.
- **Late-arrival leaderboard:** Rank employees by accumulated late minutes.
- **Attendance trends:** Generate daily department attendance metrics and moving averages.
- **Pagination and filtering:** Retrieve attendance records with filters and bounded pagination.
- **Data validation:** Reject malformed requests and invalid field values.
- **Concurrency protection:** Handle competing punch-in and punch-out requests without allowing duplicate successful operations.
- **Database health checks:** Check MongoDB connectivity through the health endpoint.
- **Query-plan inspection:** Inspect MongoDB execution plans and execution statistics.
- **Interactive API documentation:** Explore and test endpoints using Swagger UI.

## Technology Stack

| Technology | Purpose |
|---|---|
| Python | Backend programming language |
| FastAPI | REST API framework |
| Pydantic | Request validation and data modelling |
| MongoDB | Persistent document database |
| PyMongo | MongoDB connectivity and queries |
| Uvicorn | ASGI application server |
| PowerShell | Local development and API testing |

## Project Structure

```text
candidate_kit/
├── app/
│   ├── __init__.py
│   └── main.py
├── sample_data/
│   ├── attendance_logs.json
│   └── employees.json
├── DATA_MODEL.md
├── DECISIONS.md
├── PROBLEM_STATEMENT.docx
├── README.md
├── REVIEW.md
├── openapi.yaml
├── requirements.txt
├── sample_seed.py
└── .gitignore
```

### Important files

- `app/main.py` — Application initialization, API routes, validation, database operations, attendance calculations, and analytics pipelines.
- `requirements.txt` — Python dependencies.
- `openapi.yaml` — API specification.
- `DATA_MODEL.md` — Database schema and data-model documentation.
- `DECISIONS.md` — Implementation decisions and design rationale.
- `REVIEW.md` — Review notes.
- `sample_data/` — Sample employee and attendance data.
- `sample_seed.py` — Sample-data seeding utility.

## Prerequisites

Before running the project, ensure you have:

- Python 3.11 or a compatible Python version.
- pip installed.
- A MongoDB deployment accessible from your machine.
- Git, if you want to clone the repository.
- Network access to the configured MongoDB deployment.

## Installation and Setup

### 1. Clone the repository

```bash
git clone https://github.com/Asarafhack/hrone-employee-attendance-analytics-api.git
cd hrone-employee-attendance-analytics-api
```

### 2. Create a virtual environment

**Windows PowerShell:**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, you can invoke the environment's Python directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**Linux/macOS:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Configure MongoDB

Create a local `.env` file in the project root and configure the MongoDB connection settings as described below.

Do not commit `.env` or expose database credentials in source code, screenshots, or public repositories.

## Environment Configuration

The application uses these environment variables:

| Variable | Description |
|---|---|
| `MONGO_URI` | MongoDB connection URI |
| `MONGO_DB` | MongoDB database name |

Example `.env` structure:

```dotenv
MONGO_URI=mongodb+srv://<username>:<password>@<cluster-host>/<database>?retryWrites=true&w=majority
MONGO_DB=hrone
```

Replace the placeholders with your own MongoDB configuration. Do not use these placeholders as actual credentials.

Ensure your MongoDB network access settings permit connections from your development environment and that the database user has the required permissions.

Environment variables take precedence over local `.env` settings when both are configured.

## Running the Application

From the project root, with the virtual environment activated:

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The API will be available at:

- **Base URL:** `http://127.0.0.1:8000`
- **Swagger UI:** `http://127.0.0.1:8000/docs`
- **OpenAPI JSON:** `http://127.0.0.1:8000/openapi.json`
- **Health check:** `http://127.0.0.1:8000/health`

The health endpoint checks database connectivity. A successful health response indicates that the API can reach MongoDB.

Stop the server using `Ctrl+C`.

## API Endpoints

All endpoints are served from the base URL `http://127.0.0.1:8000`.

### Health

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Check application and database health |

### Employee Management

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/employees` | Create an employee |
| GET | `/employees` | Retrieve employee records |

### Attendance Management

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/attendance/punch-in` | Record an employee punch-in |
| POST | `/attendance/punch-out` | Record an employee punch-out |
| GET | `/attendance` | Retrieve attendance records with filtering and pagination |
| PATCH | `/attendance/{emp_code}/{date}` | Regularize an attendance record |

The attendance listing endpoint supports filtering by employee, date range, and status, along with pagination.

Example:

```http
GET /attendance?from=2026-10-01&to=2026-10-31&page=1&page_size=20
```

### Employee Analytics

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/analytics/employees/{emp_code}/monthly` | Retrieve monthly employee attendance metrics |

Example:

```http
GET /analytics/employees/EMP0001/monthly?month=2026-10
```

### Department Analytics

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/analytics/departments/summary` | Retrieve department-level monthly metrics |
| GET | `/analytics/leaderboard/late` | Rank employees by late-arrival minutes |
| GET | `/analytics/departments/{department}/trend` | Retrieve daily department attendance trends |

Examples:

```http
GET /analytics/departments/summary?month=2026-10
```

```http
GET /analytics/leaderboard/late?month=2026-10
```

```http
GET /analytics/departments/IT/trend?from=2026-10-01&to=2026-10-09
```

### Query-plan inspection

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/admin/explain/{endpoint}` | Inspect MongoDB query plans and execution statistics |

Supported endpoint identifiers:

- `attendance_list`
- `employee_monthly`
- `department_summary`
- `late_leaderboard`
- `department_trend`

Example:

```http
GET /admin/explain/department_summary?month=2026-10
```

Supply the required query parameters for the selected endpoint. The explain endpoint is intended for inspecting database execution plans, not for returning normal business reports.

## Attendance Business Rules

The API follows the assignment's attendance calculation requirements.

### Timestamps and dates

- API timestamps are expressed as integer Unix epoch milliseconds.
- Attendance dates and shift schedules follow Indian Standard Time (IST).
- Attendance timestamps are truncated to whole seconds for the relevant calculations.
- Attendance records use the applicable attendance date, including the overnight-shift rule specified in the assignment.

### Presence and absence

Presence statuses:

- `PRESENT`
- `WFH`
- `ON_DUTY`

`ABSENT` and `LEAVE` are not presence statuses.

### Late arrivals

- A grace period of 10 minutes applies.
- Lateness is calculated in whole minutes according to the assignment's boundary rules.
- Late-arrival totals and counts feed into employee and department analytics.

### Overtime

- Overtime is calculated from time worked after the scheduled shift end.
- Overtime is counted only when the qualifying duration reaches at least 30 minutes.
- Overtime is reported in whole minutes.

### Work hours and half-days

- Work hours are calculated from punch-in and punch-out timestamps.
- Work-hour values are rounded to two decimal places using half-up rounding.
- A half-day is determined from the rounded work-hour value when it is below 4.50 hours.

### Monthly calculations

- Working days are Monday through Friday, adjusted for an employee's joining date.
- Present-day calculations consider the applicable weekday and presence rules.
- Weekend attendance records can still contribute to late-arrival and overtime totals.
- Attendance percentages and report metrics use the rounding precision defined by the assignment.

Refer to `DATA_MODEL.md` and the assignment specification for the complete data definitions and calculation requirements.

## Analytics

### Employee monthly report

The monthly report includes metrics such as:

- Working days
- Present days
- Leave days
- Late-arrival count
- Total late minutes
- Total overtime minutes
- Attendance percentage

### Department summary

The department summary includes:

- Department headcount
- Present-day totals
- Late-arrival count and total late minutes
- Leave count
- On-duty count
- Average work hours

Employees who joined by the end of the reporting month are included in headcount calculations, even if they have no attendance records.

### Late-arrival leaderboard

The leaderboard ranks employees by total late minutes. Tied employees share the same rank, and the next rank is skipped as specified by the assignment.

### Department attendance trend

The trend endpoint returns daily metrics across the requested date range, including days without attendance records. It also calculates the seven-day moving average according to the assignment's rules.

### MongoDB aggregation

Analytics endpoints use MongoDB aggregation pipelines to perform calculations in the database instead of loading all matching records into Python for manual aggregation.

## Database Design

The application uses MongoDB collections for employee information and attendance records.

### Employees

Employee records contain information such as:

- Employee code
- Name and email
- Department and designation
- Joining date
- Shift start and end times

### Attendance logs

Attendance records contain information such as:

- Employee code
- Attendance date
- Status
- Punch-in and punch-out timestamps
- Work hours
- Late minutes
- Overtime minutes
- Half-day flag
- Regularization history

The application initializes the required indexes at startup. Index definitions and data-model details should be reviewed in `DATA_MODEL.md` and `app/main.py`.

## Concurrency and Data Integrity

Attendance operations must remain consistent when multiple requests arrive simultaneously.

The implementation is designed to protect punch-in and punch-out operations against competing requests and to reject conflicting operations with an appropriate HTTP status.

Concurrent requests should be tested against the same employee and attendance date to verify that only one competing operation succeeds.

Regularization history is intended to preserve previous changes when attendance records are updated.

## Error Handling

The API uses HTTP status codes to communicate outcomes.

| Status | Meaning |
|---|---|
| `200 OK` | Request completed successfully |
| `201 Created` | Resource created successfully |
| `404 Not Found` | Requested employee, record, or resource was not found |
| `409 Conflict` | Request conflicts with the existing attendance state |
| `422 Unprocessable Entity` | Request parameters or body failed validation |
| `503 Service Unavailable` | Database unavailable for an operation requiring MongoDB |

Use the response body to identify the specific validation error or failure reason.

## Testing and Verification

During local development, the following checks were performed:

- Health and OpenAPI endpoint checks.
- Employee and attendance listing checks.
- Monthly employee and department analytics checks.
- Department trend and late-leaderboard checks.
- Explain endpoint requests with required query parameters.
- Invalid-date and valid-date-range checks.
- Concurrent punch-in requests.
- Concurrent punch-out requests.

The observed results included successful API responses, the expected invalid-date validation response, and the expected conflict behavior for simultaneous punch operations.

These checks are manual smoke tests; they do not replace a comprehensive automated test suite or prove that every business rule has been verified.

## Performance and Explain Plans

Use the explain endpoint to inspect the query planner and execution statistics for the main reporting operations.

Review:

- Whether the expected indexes are being used.
- Whether an unexpected collection scan occurs.
- The number of documents examined versus returned.
- Execution time and the stages used by the query plan.

A successful explain request alone does not prove that a query is efficiently indexed. Validate the actual plan against the assignment's performance expectations and representative data volumes.

## API Documentation

Interactive documentation is available after starting the application:

```text
http://127.0.0.1:8000/docs
```

The generated OpenAPI specification is available at:

```text
http://127.0.0.1:8000/openapi.json
```

The repository also includes `openapi.yaml` for reference.

## Troubleshooting

### MongoDB connection failure

Check:

1. `MONGO_URI` and `MONGO_DB` are configured correctly.
2. MongoDB is reachable from your current network.
3. Your IP address is allowed in MongoDB Atlas Network Access, if applicable.
4. The database user has the required permissions.
5. Your connection URI is valid and your dependencies are installed.

Do not publish connection strings or passwords when requesting help.

### Module not found

Activate the virtual environment and reinstall dependencies:

```bash
python -m pip install -r requirements.txt
```

### Port already in use

Start the application on another port:

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Then access Swagger UI at `http://127.0.0.1:8001/docs`.

### Invalid request returns 422

Check the endpoint's required fields, parameter names, date format, status values, and data types in Swagger UI or `openapi.yaml`.

## Author

**Asaraf Ali A.**

- GitHub: [@Asarafhack](https://github.com/Asarafhack)
- LinkedIn: [Asaraf Ali A.](https://www.linkedin.com/in/asaraf-ali-a-031729261)
- Portfolio: [Asaraf Portfolio](https://asaraf-portfolio.vercel.app/)

---

*Developed as an Employee Attendance & Analytics API assignment for HROne.*
