import json
import time


def time_to_min(t):
    h, m = map(int, t.split(':'))
    return h * 60 + m


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

    # 목표 학점 근접 (최대 20점)
    target = constraints.get('target_credits', 18)
    score += max(0, 20 - abs(total_credits - target) * 4)

    # 아침 수업 적을수록 (최대 15점)
    morning_count = sum(1 for s in all_slots if time_to_min(s['start']) < 600)
    score += max(0, 15 - morning_count * 5)

    # 선호 교수 보너스
    for lec in schedule:
        if lec.get('professor', '') in constraints.get('preferred_profs', []):
            score += 5

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
            if l.get('professor', '') in constraints.get('avoided_profs', []):
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
                        'category': l.get('category', '')
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

    results.sort(key=lambda x: x['score'], reverse=True)
    return results[:max_results]
