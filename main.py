from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import json
import os
import sqlite3

from solver import solve_timetable

app = FastAPI(title="AI 시간표 생성기", version="2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DATA_PATH = os.path.join(os.path.dirname(__file__), "internal_data.json")
DB_PATH = os.path.join(os.path.dirname(__file__), "timetable.db")


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def load_db_lectures():
    conn = get_db_connection()
    try:
        lectures = {}
        lecture_rows = conn.execute(
            "SELECT l.id, l.source_id, l.source_category, l.name, l.professor, l.hours, l.credits, l.required, d.type AS dataset_type, d.name AS dataset_name "
            "FROM lecture l JOIN dataset d ON l.dataset_id = d.id"
        ).fetchall()
        for row in lecture_rows:
            lectures[row["id"]] = {
                "id": row["id"],
                "source_id": row["source_id"],
                "source_category": row["source_category"],
                "dataset_type": row["dataset_type"],
                "dataset_name": row["dataset_name"],
                "name": row["name"],
                "professor": row["professor"],
                "hours": row["hours"],
                "credits": row["credits"],
                "required": bool(row["required"]),
                "time_slots": [],
            }

        for slot in conn.execute(
            "SELECT lecture_id, day, start_time AS start, end_time AS end, room FROM time_slot"
        ).fetchall():
            lecture = lectures.get(slot["lecture_id"])
            if lecture is not None:
                lecture["time_slots"].append({
                    "day": slot["day"],
                    "start": slot["start"],
                    "end": slot["end"],
                    "room": slot["room"],
                })
        return lectures
    finally:
        conn.close()


def load_major_curriculum(all_lectures):
    conn = get_db_connection()
    try:
        curriculum = {}
        for row in conn.execute(
            "SELECT grade_semester, course_name, category, required FROM curriculum ORDER BY grade_semester"
        ).fetchall():
            grade_semester = row["grade_semester"]
            name = row["course_name"]
            sections = [
                {
                    "id": lec["id"],
                    "professor": lec.get("professor", ""),
                    "credits": lec.get("credits"),
                    "time_slots": lec.get("time_slots", []),
                    "category": lec.get("source_category") or lec.get("dataset_type"),
                }
                for lec in all_lectures.values()
                if lec["name"] == name and lec.get("dataset_type") == "전공"
            ]
            curriculum.setdefault(grade_semester, []).append({
                "name": name,
                "category": row["category"],
                "required": bool(row["required"]),
                "section_count": len(sections),
                "sections": sections,
            })
        return curriculum
    finally:
        conn.close()


def build_general_education_groups(all_lectures):
    result = {"교양필수": {}, "교양선택": {}, "교양": {}}
    for lecture in all_lectures.values():
        dataset_type = lecture.get("dataset_type")
        if dataset_type not in result:
            continue
        name = lecture.get("name")
        if not name:
            continue
        groups = result[dataset_type]
        if name not in groups:
            groups[name] = {
                "name": name,
                "category": dataset_type,
                "required": lecture.get("required", False),
                "sections": [],
                "section_count": 0,
            }
        groups[name]["sections"].append({
            "id": lecture["id"],
            "professor": lecture.get("professor", ""),
            "credits": lecture.get("credits"),
            "time_slots": lecture.get("time_slots", []),
            "category": dataset_type,
            "source_category": lecture.get("source_category"),
        })
        groups[name]["section_count"] = len(groups[name]["sections"])

    return {
        "교양필수": list(result["교양필수"].values()),
        "교양선택": list(result["교양선택"].values()),
        "교양": list(result["교양"].values()),
    }


def load_db_data():
    all_lectures = load_db_lectures()
    major_curriculum = load_major_curriculum(all_lectures)
    general_education = build_general_education_groups(all_lectures)
    return major_curriculum, general_education, all_lectures


class SolveRequest(BaseModel):
    grade_semester: str  # "2-1", "3-1" 등
    selected_courses: list[str] = []  # 수강할 과목명 리스트
    free_days: list[str] = []
    no_morning: bool = False
    morning_cutoff: str = "10:00"
    lunch_break: bool = False
    preferred_profs: list[str] = []
    force_preferred: bool = False
    avoided_profs: list[str] = []
    timeout: int = 10
    max_results: int = 5


@app.get("/api/curriculum")
async def get_curriculum():
    """학년/학기별 커리큘럼 및 교양 과목 반환"""
    major_curriculum, general_education, _ = load_db_data()
    combined = {}
    for grade_semester, courses in major_curriculum.items():
        combined[grade_semester] = (
            courses
            + general_education.get("교양필수", [])
            + general_education.get("교양선택", [])
            + general_education.get("교양", [])
        )
    return {"curriculum": combined}


@app.post("/api/solve")
async def solve(req: SolveRequest):
    major_curriculum, general_education, all_lectures = load_db_data()
    key = req.grade_semester
    selected = req.selected_courses

    if not selected:
        return {"success": False, "error": "선택된 과목이 없습니다. 수강할 과목을 최소 하나 선택해주세요."}

    if key not in major_curriculum:
        # major curriculum이 없더라도 교양 과목만 선택해서 시간표 생성 가능
        major_curriculum[key] = []

    course_groups = []
    for course_name in selected:
        group_lectures = [lec for lec in all_lectures.values() if lec["name"] == course_name]
        if not group_lectures:
            return {"success": False, "error": f"선택한 과목 '{course_name}'에 대한 강의 데이터가 없습니다."}
        category = group_lectures[0].get("source_category") or group_lectures[0].get("dataset_type") or ""
        required = any(lec.get("required") for lec in group_lectures)
        course_groups.append({
            "name": course_name,
            "category": category,
            "required": required,
            "lectures": group_lectures,
        })

    constraints = {
        "free_days": req.free_days,
        "no_morning": req.no_morning,
        "morning_cutoff": req.morning_cutoff,
        "lunch_break": req.lunch_break,
        "min_credits": 0,
        "max_credits": 21,
        "target_credits": 18,
        "preferred_profs": req.preferred_profs,
        "force_preferred": req.force_preferred,
        "avoided_profs": req.avoided_profs,
        "timeout": req.timeout,
    }

    results = solve_timetable(course_groups, constraints, max_results=req.max_results)
    return {"success": True, "count": len(results), "results": results}


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    base = os.path.dirname(__file__)
    preferred_path = os.path.join(base, "index (1).html")
    fallback_path = os.path.join(base, "index.html")
    html_path = preferred_path if os.path.exists(preferred_path) else fallback_path
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>index.html not found</h1>"


@app.get("/api/professors")
async def get_professors():
    _, _, all_lectures = load_db_data()
    profs = set()
    for l in all_lectures.values():
        p = l.get('professor')
        if p:
            for line in str(p).splitlines():
                for part in line.replace('/', ',').split(','):
                    part = part.strip()
                    if part:
                        profs.add(part)
    prof_list = sorted(profs)
    return {"professors": prof_list}

if __name__ == "__main__":
    import uvicorn
    # 로컬 네트워크: 0.0.0.0으로 설정하면 다른 기기에서도 접근 가능
    # 배포 후: RENDER_EXTERNAL_URL 등의 환경변수 사용 가능
    uvicorn.run(app, host="0.0.0.0", port=8000)
