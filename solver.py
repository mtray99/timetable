import json
import math
import os
import re
import time


def time_to_min(t):
    h, m = map(int, t.split(':'))
    return h * 60 + m


def load_building_coordinates():
    path = os.path.join(os.path.dirname(__file__), 'building_coordinates.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


BUILDING_COORDS = {
    k.strip().lower(): v
    for k, v in load_building_coordinates().items()
}


def get_building_name(room):
    if not room:
        return None
    room = room.strip()
    match = re.match(r'^(.+?)(?:\s+\d|\s*\(|$)', room)
    if match:
        return match.group(1).strip()
    return room


def get_building_coords(building_name):
    if not building_name:
        return None
    return BUILDING_COORDS.get(building_name.strip().lower())


def haversine_distance(coord_a, coord_b):
    if not coord_a or not coord_b:
        return None
    lat1, lon1 = coord_a['lat'], coord_a['lon']
    lat2, lon2 = coord_b['lat'], coord_b['lon']
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def slots_overlap(s1, s2):
    if s1['day'] != s2['day']:
        return False
    return time_to_min(s1['start']) < time_to_min(s2['end']) and \
           time_to_min(s2['start']) < time_to_min(s1['end'])


def lectures_conflict(lec1, lec2):
    for s1 in lec1['time_slots']:
        for s2 in lec2['time_slots']:
            if slots_overlap(s1, s2):
                return True
    return False


def check_constraints(schedule, constraints):
    all_slots = []
    total_credits = 0
    for lec in schedule:
        total_credits += (lec['credits'] or 0)
        all_slots.extend(lec['time_slots'])

    if constraints.get('max_credits') and total_credits > constraints['max_credits']:
        return False

    for day in constraints.get('free_days', []):
        if any(s['day'] == day for s in all_slots):
            return False

    if constraints.get('no_morning'):
        cutoff = time_to_min(constraints.get('morning_cutoff', '10:00'))
        if any(time_to_min(s['start']) < cutoff for s in all_slots):
            return False

    if constraints.get('lunch_break'):
        days_with_class = set(s['day'] for s in all_slots)
        for day in days_with_class:
            day_slots = [s for s in all_slots if s['day'] == day]
            lunch_occupied = 0
            for s in day_slots:
                os_ = max(time_to_min(s['start']), 690)  # 11:30
                oe = min(time_to_min(s['end']), 780)      # 13:00
                if os_ < oe:
                    lunch_occupied += (oe - os_)
            if lunch_occupied > 40:
                return False

    return True


def normalize_professor_names(professor_text):
    parts = []
    for line in str(professor_text).splitlines():
        for part in line.replace('/', ',').split(','):
            part = part.strip().lower()
            if part:
                parts.append(part)
    return parts


def professor_matches(lec, name_set):
    if not name_set:
        return False
    lec_names = normalize_professor_names(lec.get('professor', ''))
    for name in name_set:
        normalized = name.strip().lower()
        if not normalized:
            continue
        if any(normalized == ln or normalized in ln for ln in lec_names):
            return True
    return False


def score_schedule(schedule, constraints):
    score = 0
    all_slots = []
    for lec in schedule:
        all_slots.extend(lec['time_slots'])

    total_credits = sum(lec['credits'] or 0 for lec in schedule)
    days_with_class = set(s['day'] for s in all_slots)

    # 공강일 보너스 (최대 20점)
    score += (5 - len(days_with_class)) * 10

    # 빈 시간 최소화 (최대 25점)
    total_gap = 0
    for day in days_with_class:
        day_slots = sorted(
            [s for s in all_slots if s['day'] == day],
            key=lambda x: time_to_min(x['start'])
        )
        for i in range(1, len(day_slots)):
            gap = time_to_min(day_slots[i]['start']) - time_to_min(day_slots[i-1]['end'])
            if gap > 10:
                total_gap += gap
    score += max(0, 25 - (total_gap / 300) * 25)

    # 목표 학점 근접 스코어링 제거 (사용자 요청)

    # 아침 수업 적을수록 (최대 15점)
    morning_count = sum(1 for s in all_slots if time_to_min(s['start']) < 600)
    score += max(0, 15 - morning_count * 5)

    # 연강일 경우 건물 간 거리 보너스
    consecutive_distance_bonus = 0
    for day in days_with_class:
        day_slots = sorted(
            [s for s in all_slots if s['day'] == day],
            key=lambda x: time_to_min(x['start'])
        )
        for i in range(1, len(day_slots)):
            gap = time_to_min(day_slots[i]['start']) - time_to_min(day_slots[i - 1]['end'])
            if gap <= 15:
                building_a = get_building_name(day_slots[i - 1].get('room', ''))
                building_b = get_building_name(day_slots[i].get('room', ''))
                coord_a = get_building_coords(building_a)
                coord_b = get_building_coords(building_b)
                distance = haversine_distance(coord_a, coord_b)
                if distance is not None:
                    consecutive_distance_bonus += max(0, 10 - distance / 150)
    score += min(consecutive_distance_bonus, 20)

    # 선호 교수 보너스
    preferred_set = set(p.strip() for p in constraints.get('preferred_profs', []) if p.strip())
    preferred_count = sum(1 for lec in schedule if professor_matches(lec, preferred_set))
    score += preferred_count * 10

    # 수업 시간 균등 분배 (최대 10점)
    if days_with_class:
        hpd = [len([s for s in all_slots if s['day'] == d]) for d in days_with_class]
        avg = sum(hpd) / len(hpd)
        var = sum((h - avg)**2 for h in hpd) / len(hpd)
        score += max(0, 10 - var * 2)

    return round(score, 1)


def solve_timetable(course_groups, constraints, max_results=5):
    """
    백트래킹으로 최적 시간표 조합을 탐색합니다.

    Parameters:
        course_groups: [{ 'name': str, 'category': str, 'required': bool, 'lectures': [lecture, ...] }, ...]
        constraints: {
            'free_days': ['금'],          # 공강 요일
            'no_morning': True,           # 아침 수업 제외
            'morning_cutoff': '10:00',    # 이 시간 이전 수업 제외
            'lunch_break': True,          # 점심시간 확보
            'min_credits': 12,            # 최소 학점
            'max_credits': 18,            # 최대 학점
            'target_credits': 15,         # 목표 학점 (스코어링용)
            'preferred_profs': [],        # 선호 교수
            'avoided_profs': [],          # 기피 교수
            'timeout': 10,               # 탐색 제한 시간(초)
        }
        max_results: 반환할 최대 시간표 수

    Returns:
        [{ 'lectures': [...], 'total_credits': float, 'score': float }, ...]
    """
    preferred_set = set(p.strip() for p in constraints.get('preferred_profs', []) if p.strip())
    avoided_set = set(p.strip() for p in constraints.get('avoided_profs', []) if p.strip())

    # 1단계: 그룹 단위 필터링
    filtered_groups = []
    for group in course_groups:
        filtered_lectures = []
        for l in group['lectures']:
            if constraints.get('no_morning'):
                cutoff = time_to_min(constraints.get('morning_cutoff', '10:00'))
                if any(time_to_min(s['start']) < cutoff for s in l['time_slots']):
                    continue
            if constraints.get('free_days'):
                if any(s['day'] in constraints['free_days'] for s in l['time_slots']):
                    continue
            if professor_matches(l, avoided_set):
                continue
            filtered_lectures.append(l)
        if not filtered_lectures:
            return []
        filtered_groups.append(filtered_lectures)

    results = []
    min_credits = constraints.get('min_credits', 12)
    max_credits = constraints.get('max_credits', 21)
    start_time = time.time()
    timeout = constraints.get('timeout', 10)

    def backtrack(chosen, group_idx, total_credits):
        if time.time() - start_time > timeout:
            return
        if len(results) >= max_results * 10:
            return

        if group_idx >= len(filtered_groups):
            if total_credits >= min_credits and total_credits <= max_credits and check_constraints(chosen, constraints):
                sc = score_schedule(chosen, constraints)
                results.append({
                    'lectures': [{
                        'id': l['id'], 'name': l['name'],
                        'professor': l.get('professor', ''),
                        'credits': l['credits'], 'hours': l.get('hours'),
                        'time_slots': l['time_slots'],
                        'category': l.get('source_category') or l.get('dataset_type') or ''
                    } for l in chosen],
                    'total_credits': total_credits,
                    'score': sc
                })
            return

        for lec in filtered_groups[group_idx]:
            nc = total_credits + (lec['credits'] or 0)
            if nc > max_credits:
                continue
            if any(lectures_conflict(c, lec) for c in chosen):
                continue
            backtrack(chosen + [lec], group_idx + 1, nc)

    backtrack([], 0, 0)

    def has_preferred(schedule):
        return any(professor_matches(l, preferred_set) for l in schedule['lectures'])

    preferred_results = [r for r in results if has_preferred(r)]
    if constraints.get('force_preferred') and preferred_set:
        if preferred_results:
            preferred_results.sort(key=lambda x: x['score'], reverse=True)
            return preferred_results[:max_results]
        return []

    if preferred_set and preferred_results:
        preferred_results.sort(key=lambda x: x['score'], reverse=True)
        return preferred_results[:max_results]

    results.sort(key=lambda x: x['score'], reverse=True)
    return results[:max_results]
