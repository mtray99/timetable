from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import json
import os

from solver import solve_timetable

app = FastAPI(title="AI 시간표 생성기", version="2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# 내부 데이터 로드
DATA_PATH = os.path.join(os.path.dirname(__file__), "internal_data.json")

def load_internal_data():
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_internal_data():
    internal = load_internal_data()
    curriculum = internal["curriculum"]
    all_lectures = {l["id"]: l for l in internal["lectures"]}
    return curriculum, all_lectures


class SolveRequest(BaseModel):
    grade_semester: str  # "2-1", "3-1" 등
    selected_courses: list[str] = []  # 수강할 과목명 리스트
    free_days: list[str] = []
    no_morning: bool = False
    morning_cutoff: str = "10:00"
    lunch_break: bool = False
    preferred_profs: list[str] = []
    avoided_profs: list[str] = []
    timeout: int = 10
    max_results: int = 5


@app.get("/api/curriculum")
async def get_curriculum():
    """학년/학기별 커리큘럼 반환"""
    curriculum, all_lectures = get_internal_data()
    result = {}
    for key, courses in curriculum.items():
        result[key] = []
        for c in courses:
            sections = []
            for lid in c["lecture_ids"]:
                lec = all_lectures.get(lid)
                if lec:
                    sections.append({
                        "id": lec["id"],
                        "professor": lec.get("professor", ""),
                        "credits": lec.get("credits"),
                        "time_slots": lec.get("time_slots", []),
                    })
            result[key].append({
                "name": c["name"],
                "category": c["category"],
                "required": c["required"],
                "section_count": len(sections),
                "sections": sections,
            })
    return {"curriculum": result}


@app.post("/api/solve")
async def solve(req: SolveRequest):
    curriculum, all_lectures = get_internal_data()
    key = req.grade_semester
    if key not in curriculum:
        return {"success": False, "error": f"'{key}' 학년/학기 데이터가 없습니다."}

    courses = curriculum[key]
    selected = req.selected_courses

    if not selected:
        return {"success": False, "error": "선택된 과목이 없습니다. 수강할 과목을 최소 하나 선택해주세요."}

    course_groups = []
    for c in courses:
        if c["name"] not in selected:
            continue
        group_lectures = []
        for lid in c["lecture_ids"]:
            lec = all_lectures.get(lid)
            if lec:
                group_lectures.append(lec)
        if not group_lectures:
            return {"success": False, "error": f"선택한 과목 '{c['name']}'에 대한 강의 데이터가 없습니다."}
        course_groups.append({
            "name": c["name"],
            "category": c["category"],
            "required": c["required"],
            "lectures": group_lectures,
        })

    if not course_groups:
        return {"success": False, "error": "선택한 과목이 커리큘럼에 없습니다."}

    constraints = {
        "free_days": req.free_days,
        "no_morning": req.no_morning,
        "morning_cutoff": req.morning_cutoff,
        "lunch_break": req.lunch_break,
        "min_credits": 0,
        "max_credits": 21,
        "target_credits": 18,
        "preferred_profs": req.preferred_profs,
        "avoided_profs": req.avoided_profs,
        "timeout": req.timeout,
    }

    results = solve_timetable(course_groups, constraints, max_results=req.max_results)
    return {"success": True, "count": len(results), "results": results}


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>index.html not found</h1>"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
