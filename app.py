"""
Band Rehearsal Scheduler - Streamlit Web App
===========================================

밴드부 합주 일정을 팀별 링크로 조율할 수 있는 Streamlit 웹앱입니다.

핵심 기능
- 운영진이 팀/곡/팀원/날짜/시간/필수 파트를 입력해 팀 생성
- 팀별 제출 링크 생성
- 팀원이 링크에 접속해 이름/파트/가능 시간을 체크하고 제출
- 운영진 결과 페이지에서 제출 현황, 미제출자, 파트 충족 여부, 추천 합주 시간 확인
- 공지용 요약문 자동 생성
- CSV 다운로드

실행 방법
1) 필요한 패키지 설치
   pip install streamlit

2) 이 파일을 app.py로 저장

3) 실행
   streamlit run app.py

주의
- 현재 버전은 SQLite 파일을 사용하는 간단한 웹앱입니다.
- 로컬에서 실행하면 같은 컴퓨터에서만 localhost 링크가 열립니다.
- 실제 부원들이 접속하려면 Streamlit Community Cloud 등에 배포해야 합니다.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import streamlit as st

DB_PATH = Path("band_schedule_web.db")
APP_BASE_URL = "https://dailyparty-band-scheduler.streamlit.app"
PARTS = ["보컬", "기타1", "기타2", "베이스", "드럼", "건반", "기타/그 외"]
DEFAULT_REQUIRED_PARTS = {
    "보컬": 1,
    "기타1": 1,
    "기타2": 1,
    "베이스": 1,
    "드럼": 1,
    "건반": 0,
    "기타/그 외": 0,
}


@dataclass
class Team:
    id: str
    name: str
    songs: str
    members: List[str]
    start_date: date
    end_date: date
    start_time: time
    end_time: time
    slot_minutes: int
    required_parts: Dict[str, int]
    created_at: str


@dataclass
class Response:
    member_name: str
    part: str
    selected_slots: List[str]
    submitted_at: str


# -----------------------------
# Database
# -----------------------------
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS teams (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                songs TEXT,
                members TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                slot_minutes INTEGER NOT NULL,
                required_parts TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_id TEXT NOT NULL,
                member_name TEXT NOT NULL,
                part TEXT NOT NULL,
                selected_slots TEXT NOT NULL,
                submitted_at TEXT NOT NULL,
                UNIQUE(team_id, member_name),
                FOREIGN KEY(team_id) REFERENCES teams(id)
            )
            """
        )


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def parse_time(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def create_team(
    name: str,
    songs: str,
    members: Sequence[str],
    start_date: date,
    end_date: date,
    start_time: time,
    end_time: time,
    slot_minutes: int,
    required_parts: Dict[str, int],
) -> str:
    team_id = uuid.uuid4().hex[:8]
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO teams (
                id, name, songs, members, start_date, end_date, start_time, end_time,
                slot_minutes, required_parts, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                team_id,
                name.strip(),
                songs.strip(),
                json.dumps(list(members), ensure_ascii=False),
                start_date.isoformat(),
                end_date.isoformat(),
                start_time.strftime("%H:%M"),
                end_time.strftime("%H:%M"),
                int(slot_minutes),
                json.dumps(required_parts, ensure_ascii=False),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
    return team_id


def get_team(team_id: str) -> Optional[Team]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id.strip(),)).fetchone()
    if not row:
        return None
    return Team(
        id=row["id"],
        name=row["name"],
        songs=row["songs"] or "",
        members=json.loads(row["members"] or "[]"),
        start_date=parse_date(row["start_date"]),
        end_date=parse_date(row["end_date"]),
        start_time=parse_time(row["start_time"]),
        end_time=parse_time(row["end_time"]),
        slot_minutes=int(row["slot_minutes"]),
        required_parts=json.loads(row["required_parts"]),
        created_at=row["created_at"],
    )


def list_teams() -> List[Team]:
    with get_conn() as conn:
        rows = conn.execute("SELECT id FROM teams ORDER BY created_at DESC").fetchall()
    teams = []
    for row in rows:
        team = get_team(row["id"])
        if team:
            teams.append(team)
    return teams


def save_response(team_id: str, member_name: str, part: str, selected_slots: Sequence[str]) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO responses (team_id, member_name, part, selected_slots, submitted_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(team_id, member_name)
            DO UPDATE SET
                part = excluded.part,
                selected_slots = excluded.selected_slots,
                submitted_at = excluded.submitted_at
            """,
            (
                team_id,
                member_name.strip(),
                part,
                json.dumps(list(selected_slots), ensure_ascii=False),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )


def get_responses(team_id: str) -> List[Response]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT member_name, part, selected_slots, submitted_at
            FROM responses
            WHERE team_id = ?
            ORDER BY submitted_at DESC
            """,
            (team_id.strip(),),
        ).fetchall()
    return [
        Response(
            member_name=row["member_name"],
            part=row["part"],
            selected_slots=json.loads(row["selected_slots"]),
            submitted_at=row["submitted_at"],
        )
        for row in rows
    ]


# -----------------------------
# Scheduling logic
# -----------------------------
def weekday_kr(d: date) -> str:
    return ["월", "화", "수", "목", "금", "토", "일"][d.weekday()]


def generate_slots(team: Team) -> List[Dict[str, object]]:
    slots: List[Dict[str, object]] = []
    current_date = team.start_date
    while current_date <= team.end_date:
        current = datetime.combine(current_date, team.start_time)
        end_dt = datetime.combine(current_date, team.end_time)
        while current + timedelta(minutes=team.slot_minutes) <= end_dt:
            next_time = current + timedelta(minutes=team.slot_minutes)
            slot_id = current.isoformat(timespec="minutes")
            label = f"{current_date.strftime('%m/%d')}({weekday_kr(current_date)}) {current.strftime('%H:%M')}-{next_time.strftime('%H:%M')}"
            slots.append(
                {
                    "id": slot_id,
                    "label": label,
                    "date": current_date.isoformat(),
                    "date_label": f"{current_date.strftime('%m/%d')}({weekday_kr(current_date)})",
                    "time_label": f"{current.strftime('%H:%M')}-{next_time.strftime('%H:%M')}",
                    "start_hour": current.hour,
                }
            )
            current = next_time
        current_date += timedelta(days=1)
    return slots


def time_preference_score(slot: Dict[str, object]) -> int:
    hour = int(slot.get("start_hour", 0))
    if 18 <= hour <= 21:
        return 10
    if 15 <= hour < 18:
        return 6
    if 12 <= hour < 15:
        return 3
    return 0


def analyze_availability(team: Team, responses: Sequence[Response]) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    slots = generate_slots(team)

    for slot in slots:
        available = [response for response in responses if str(slot["id"]) in response.selected_slots]
        part_counts = {part: 0 for part in PARTS}
        for response in available:
            part_counts[response.part] = part_counts.get(response.part, 0) + 1

        missing = []
        for part, required_count in team.required_parts.items():
            current_count = part_counts.get(part, 0)
            if required_count > 0 and current_count < required_count:
                missing.append(f"{part} {required_count - current_count}명")

        part_ok = not missing
        member_score = len(available) * 10
        part_score = 50 if part_ok else 0
        time_score = time_preference_score(slot)
        total_score = member_score + part_score + time_score

        rows.append(
            {
                "시간대": slot["label"],
                "추천 점수": total_score,
                "가능 인원": len(available),
                "가능 멤버": ", ".join(f"{r.member_name}({r.part})" for r in available),
                "파트 충족": "충족" if part_ok else "부족",
                "부족한 파트": "없음" if part_ok else ", ".join(missing),
                "점수 산식": f"인원 {member_score} + 파트 {part_score} + 시간대 {time_score}",
            }
        )

    rows.sort(key=lambda row: (int(row["추천 점수"]), int(row["가능 인원"])), reverse=True)
    return rows


def get_missing_members(team: Team, responses: Sequence[Response]) -> List[str]:
    submitted = {response.member_name.strip() for response in responses}
    return [member for member in team.members if member.strip() and member.strip() not in submitted]


def make_notice(team: Team, responses: Sequence[Response], results: Sequence[Dict[str, object]], missing_members: Sequence[str]) -> str:
    if not responses:
        return f"[{team.name} 합주 일정 조율 현황]\n아직 제출된 응답이 없습니다. 팀원들에게 가능 시간 제출을 요청해 주세요."

    if not results:
        return f"[{team.name} 합주 일정 조율 현황]\n추천 가능한 시간대가 없습니다."

    best = results[0]
    submitted_count = len(responses)
    total_members = len(team.members)

    if best["파트 충족"] == "충족":
        decision = (
            f"현재 기준 가장 적합한 합주 시간은 {best['시간대']}입니다. "
            f"해당 시간에는 필수 파트 조건이 충족되며, 가능 인원은 {best['가능 인원']}명입니다."
        )
    else:
        decision = (
            f"현재 기준 가장 가능 인원이 많은 시간은 {best['시간대']}입니다. "
            f"다만 {best['부족한 파트']}이 부족하여 추가 조율이 필요합니다."
        )

    missing_text = f"\n아직 미제출 인원은 {', '.join(missing_members)}입니다." if missing_members else ""

    return (
        f"[{team.name} 합주 일정 조율 현황]\n"
        f"현재 총 {total_members}명 중 {submitted_count}명이 가능 시간을 제출했습니다.\n"
        f"{decision}\n"
        f"가능 멤버: {best['가능 멤버']}{missing_text}"
    )


def to_csv_bytes(rows: Sequence[Dict[str, object]]) -> bytes:
    if not rows:
        return "".encode("utf-8-sig")
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


def response_rows(responses: Sequence[Response]) -> List[Dict[str, object]]:
    return [
        {
            "이름": response.member_name,
            "파트": response.part,
            "선택한 시간 수": len(response.selected_slots),
            "제출 시각": response.submitted_at,
        }
        for response in responses
    ]


# -----------------------------
# UI helpers
# -----------------------------
def get_query_param(name: str, default: str = "") -> str:
    value = st.query_params.get(name, default)
    if isinstance(value, list):
        return value[0] if value else default
    return value or default


def build_links(team_id: str) -> Dict[str, str]:
    base_url = APP_BASE_URL.rstrip("/")
    submit_link = f"{base_url}/?page=submit&team={team_id}"
    result_link = f"{base_url}/?page=result&team={team_id}"
    return {"제출 링크": submit_link, "결과 링크": result_link}


def show_team_summary(team: Team) -> None:
    st.markdown(f"### {team.name}")
    if team.songs:
        st.write("**곡명**")
        st.write(team.songs)
    st.write(f"**기간:** {team.start_date} ~ {team.end_date}")
    st.write(f"**시간:** {team.start_time.strftime('%H:%M')} ~ {team.end_time.strftime('%H:%M')} / {team.slot_minutes}분 단위")
    st.write("**필수 파트 조건**")
    st.write(", ".join(f"{part} {count}명" for part, count in team.required_parts.items() if count > 0) or "없음")


# -----------------------------
# Pages
# -----------------------------
def page_create_team() -> None:
    st.header("1. 팀 만들기")
    st.caption("운영진이 팀 정보를 입력하면 팀원 제출 링크와 결과 확인 링크가 생성됩니다.")

    with st.form("create_team_form"):
        col1, col2 = st.columns(2)
        with col1:
            name = st.text_input("팀명", placeholder="예: 2026 봄 정기공연 A팀")
            songs = st.text_area("곡명", placeholder="예: Ditto\nSupernova")
            members_raw = st.text_area(
                "팀원 명단",
                placeholder="한 줄에 한 명씩 입력\n서영\n민지\n지훈",
                help="이 명단을 기준으로 미제출자를 자동 확인합니다.",
            )
        with col2:
            start_date = st.date_input("시작 날짜", value=date.today())
            end_date = st.date_input("종료 날짜", value=date.today() + timedelta(days=7))
            start_time = st.time_input("하루 시작 시간", value=time(18, 0), step=1800)
            end_time = st.time_input("하루 종료 시간", value=time(22, 0), step=1800)
            slot_minutes = st.selectbox("시간 단위", [30, 60], index=1)

        st.markdown("#### 필수 파트 조건")
        st.caption("합주가 성립하려면 필요한 최소 인원을 설정하세요. 곡에 따라 0명으로 조정할 수 있습니다.")
        required_parts: Dict[str, int] = {}
        part_cols = st.columns(4)
        for idx, part in enumerate(PARTS):
            with part_cols[idx % 4]:
                required_parts[part] = st.number_input(
                    part,
                    min_value=0,
                    max_value=5,
                    value=DEFAULT_REQUIRED_PARTS.get(part, 0),
                    step=1,
                )

        submitted = st.form_submit_button("팀 생성", type="primary")

    if submitted:
        members = [line.strip() for line in members_raw.splitlines() if line.strip()]
        if not name.strip():
            st.error("팀명을 입력해 주세요.")
            return
        if not members:
            st.error("팀원 명단을 최소 1명 이상 입력해 주세요.")
            return
        if end_date < start_date:
            st.error("종료 날짜는 시작 날짜보다 빠를 수 없습니다.")
            return
        if end_time <= start_time:
            st.error("종료 시간은 시작 시간보다 늦어야 합니다.")
            return

        team_id = create_team(name, songs, members, start_date, end_date, start_time, end_time, slot_minutes, required_parts)
        links = build_links(team_id)
        st.success("팀이 생성되었습니다!")
        st.write(f"**팀 ID:** `{team_id}`")
        st.markdown("#### 팀원에게 보낼 제출 링크")
        st.code(links["제출 링크"])
        st.markdown("#### 운영진 결과 확인 링크")
        st.code(links["결과 링크"])
        st.info("로컬 실행 중에는 같은 컴퓨터에서만 위 링크가 열립니다. 실제 공유용 링크는 Streamlit Cloud 배포 후 생성됩니다.")


def page_submit() -> None:
    st.header("2. 가능 시간 제출")
    team_id = get_query_param("team") or st.text_input("팀 ID 입력")
    if not team_id:
        st.warning("팀 링크로 접속하거나 팀 ID를 입력해 주세요.")
        return

    team = get_team(team_id)
    if not team:
        st.error("해당 팀을 찾을 수 없습니다. 링크 또는 팀 ID를 확인해 주세요.")
        return

    show_team_summary(team)
    slots = generate_slots(team)

    st.markdown("---")
    member_name = st.selectbox("이름", [""] + team.members)
    part = st.selectbox("파트", PARTS)

    st.markdown("#### 가능한 시간을 체크해 주세요")
    st.caption("날짜와 시간이 만나는 칸을 체크하면 됩니다. 같은 이름으로 다시 제출하면 기존 응답이 수정됩니다.")

    # When2meet-like grid: rows = time ranges, columns = dates.
    date_labels: List[str] = []
    time_labels: List[str] = []
    slot_map: Dict[tuple, str] = {}

    for slot in slots:
        date_label = str(slot["date_label"])
        time_label = str(slot["time_label"])
        if date_label not in date_labels:
            date_labels.append(date_label)
        if time_label not in time_labels:
            time_labels.append(time_label)
        slot_map[(time_label, date_label)] = str(slot["id"])

    selected_slots: List[str] = []

    if not date_labels or not time_labels:
        st.error("선택 가능한 시간대가 없습니다. 팀 생성 시 날짜와 시간을 다시 확인해 주세요.")
        return

    # Header row
    header_cols = st.columns([1.25] + [1 for _ in date_labels])
    with header_cols[0]:
        st.markdown("**시간**")
    for idx, date_label in enumerate(date_labels, start=1):
        with header_cols[idx]:
            st.markdown(f"**{date_label}**")

    # Checkbox grid
    for time_label in time_labels:
        row_cols = st.columns([1.25] + [1 for _ in date_labels])
        with row_cols[0]:
            st.markdown(f"**{time_label}**")
        for idx, date_label in enumerate(date_labels, start=1):
            slot_id = slot_map.get((time_label, date_label))
            with row_cols[idx]:
                if slot_id:
                    checked = st.checkbox(
                        "가능",
                        key=f"grid_{team.id}_{member_name}_{part}_{slot_id}",
                        label_visibility="collapsed",
                    )
                    if checked:
                        selected_slots.append(slot_id)
                else:
                    st.write("-")

    submitted = st.button("제출하기", type="primary")

    if submitted:
        if not member_name:
            st.error("이름을 선택해 주세요.")
            return
        if not selected_slots:
            st.error("가능한 시간을 최소 1개 이상 선택해 주세요.")
            return
        save_response(team.id, member_name, part, selected_slots)
        st.success("제출 완료! 같은 이름으로 다시 제출하면 기존 응답이 수정됩니다.")


def page_result() -> None:
    st.header("3. 결과 보기")
    team_id = get_query_param("team") or st.text_input("팀 ID 입력")
    if not team_id:
        st.warning("결과 링크로 접속하거나 팀 ID를 입력해 주세요.")
        return

    team = get_team(team_id)
    if not team:
        st.error("해당 팀을 찾을 수 없습니다. 링크 또는 팀 ID를 확인해 주세요.")
        return

    show_team_summary(team)
    responses = get_responses(team.id)
    results = analyze_availability(team, responses)
    missing_members = get_missing_members(team, responses)
    notice = make_notice(team, responses, results, missing_members)

    st.markdown("#### 제출 현황")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("전체 팀원", f"{len(team.members)}명")
    col2.metric("제출 인원", f"{len(responses)}명")
    col3.metric("미제출", f"{len(missing_members)}명")
    satisfied_count = sum(1 for row in results if row["파트 충족"] == "충족")
    col4.metric("파트 충족 시간대", f"{satisfied_count}개")

    if missing_members:
        st.warning("미제출자: " + ", ".join(missing_members))
    else:
        st.success("모든 팀원이 제출했습니다.")

    st.markdown("#### 운영진 공지용 요약")
    st.text_area("단톡방에 복사해서 보낼 수 있는 문장", value=notice, height=180)

    st.markdown("#### 추천 기준")
    st.info("추천 점수 = 가능 인원×10점 + 필수 파트 충족 50점 + 저녁 시간대 가중치 최대 10점")

    st.markdown("#### 추천 합주 시간")
    st.dataframe(results, use_container_width=True, hide_index=True)

    st.markdown("#### 제출자 목록")
    rows = response_rows(responses)
    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.markdown("#### 다운로드")
    col_a, col_b = st.columns(2)
    with col_a:
        st.download_button(
            "추천 결과 CSV 다운로드",
            data=to_csv_bytes(results),
            file_name=f"{team.name}_추천결과.csv",
            mime="text/csv",
        )
    with col_b:
        st.download_button(
            "제출 현황 CSV 다운로드",
            data=to_csv_bytes(rows),
            file_name=f"{team.name}_제출현황.csv",
            mime="text/csv",
        )


def page_team_list() -> None:
    st.header("팀 목록")
    teams = list_teams()
    if not teams:
        st.info("아직 생성된 팀이 없습니다.")
        return

    for team in teams:
        with st.container(border=True):
            st.markdown(f"### {team.name}")
            st.write(f"팀 ID: `{team.id}`")
            st.write(f"기간: {team.start_date} ~ {team.end_date}")
            links = build_links(team.id)
            st.write("제출 링크")
            st.code(links["제출 링크"])
            st.write("결과 링크")
            st.code(links["결과 링크"])


# -----------------------------
# App
# -----------------------------
def main() -> None:
    st.set_page_config(page_title="밴드부 합주 일정 조율", page_icon="🎸", layout="wide")
    init_db()

    st.title("🎸 밴드부 합주 일정 조율")
    st.caption("팀별 링크로 가능 시간을 수집하고, 파트 충족 여부와 최적 합주 시간을 자동으로 계산합니다.")

    page_from_url = get_query_param("page", "create")
    page_options = {
        "create": "팀 만들기",
        "submit": "가능 시간 제출",
        "result": "결과 보기",
        "list": "팀 목록",
    }
    reverse_options = {label: key for key, label in page_options.items()}

    default_label = page_options.get(page_from_url, "팀 만들기")
    with st.sidebar:
        selected_label = st.radio(
            "메뉴",
            list(reverse_options.keys()),
            index=list(reverse_options.keys()).index(default_label),
        )
        selected_page = reverse_options[selected_label]
        st.divider()
        st.write("실제 공유용으로 쓰려면 Streamlit Cloud 배포가 필요합니다.")

    if selected_page == "create":
        page_create_team()
    elif selected_page == "submit":
        page_submit()
    elif selected_page == "result":
        page_result()
    elif selected_page == "list":
        page_team_list()


if __name__ == "__main__":
    main()
