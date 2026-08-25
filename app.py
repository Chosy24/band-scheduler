from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Sequence, Set, Tuple

import requests
import streamlit as st

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
class SchedulePeriod:
    id: str
    name: str
    start_date: date
    end_date: date
    start_time: time
    end_time: time
    slot_minutes: int


@dataclass
class Member:
    id: str
    name: str
    part: str
    active: bool = True


@dataclass
class Team:
    id: str
    schedule_id: str
    name: str
    songs: str
    member_ids: List[str]
    required_parts: Dict[str, int]


@dataclass
class PersonalSchedule:
    schedule_id: str
    member_id: str
    selected_slots: List[str]
    submitted_at: str = ""


# -----------------------------------------------------------------------------
# Google Apps Script API
# -----------------------------------------------------------------------------
def get_gas_url() -> str:
    try:
        return str(st.secrets.get("GAS_WEB_APP_URL", "")).strip()
    except Exception:
        return ""


def get_admin_pin() -> str:
    try:
        return str(st.secrets.get("ADMIN_PIN", "")).strip()
    except Exception:
        return ""


def api_get(action: str, params: Optional[Dict[str, str]] = None) -> Dict[str, object]:
    url = get_gas_url()
    if not url:
        raise RuntimeError("GAS_WEB_APP_URL이 설정되지 않았습니다.")
    query = {"action": action}
    if params:
        query.update(params)
    response = requests.get(url, params=query, timeout=20)
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(str(data.get("error", "Google Apps Script API 오류")))
    return data


def api_post(payload: Dict[str, object]) -> Dict[str, object]:
    url = get_gas_url()
    if not url:
        raise RuntimeError("GAS_WEB_APP_URL이 설정되지 않았습니다.")
    response = requests.post(url, json=payload, timeout=20)
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(str(data.get("error", "Google Apps Script API 오류")))
    return data


# -----------------------------------------------------------------------------
# Parsing
# -----------------------------------------------------------------------------
def parse_date(value: object) -> date:
    text = str(value).strip().split("T", 1)[0]
    return datetime.strptime(text, "%Y-%m-%d").date()


def parse_time(value: object) -> time:
    text = str(value).strip()
    if "T" in text:
        text = text.split("T", 1)[1]
    return datetime.strptime(text[:5], "%H:%M").time()


def parse_json_list(value: object) -> List[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    text = str(value or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        return [str(v) for v in parsed] if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def parse_json_dict(value: object) -> Dict[str, int]:
    result = DEFAULT_REQUIRED_PARTS.copy()
    if isinstance(value, dict):
        for key, val in value.items():
            result[str(key)] = int(val)
        return result
    text = str(value or "").strip()
    if not text:
        return result
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            for key, val in parsed.items():
                result[str(key)] = int(val)
    except json.JSONDecodeError:
        pass
    return result


def row_to_period(row: Dict[str, object]) -> SchedulePeriod:
    return SchedulePeriod(
        id=str(row.get("schedule_id", "")).strip(),
        name=str(row.get("name", "")).strip(),
        start_date=parse_date(row.get("start_date", date.today().isoformat())),
        end_date=parse_date(row.get("end_date", date.today().isoformat())),
        start_time=parse_time(row.get("start_time", "18:00")),
        end_time=parse_time(row.get("end_time", "22:00")),
        slot_minutes=int(float(row.get("slot_minutes", 60) or 60)),
    )


def row_to_member(row: Dict[str, object]) -> Member:
    active_value = row.get("active", True)
    active = str(active_value).lower() not in {"false", "0", "no", "n"}
    return Member(
        id=str(row.get("member_id", "")).strip(),
        name=str(row.get("name", "")).strip(),
        part=str(row.get("part", "기타/그 외")).strip() or "기타/그 외",
        active=active,
    )


def row_to_team(row: Dict[str, object]) -> Team:
    return Team(
        id=str(row.get("team_id", "")).strip(),
        schedule_id=str(row.get("schedule_id", "")).strip(),
        name=str(row.get("name", "")).strip(),
        songs=str(row.get("songs", "")).strip(),
        member_ids=parse_json_list(row.get("member_ids", "[]")),
        required_parts=parse_json_dict(row.get("required_parts", "{}")),
    )


def row_to_personal_schedule(row: Dict[str, object]) -> PersonalSchedule:
    return PersonalSchedule(
        schedule_id=str(row.get("schedule_id", "")).strip(),
        member_id=str(row.get("member_id", "")).strip(),
        selected_slots=parse_json_list(row.get("selected_slots", "[]")),
        submitted_at=str(row.get("submitted_at", "")),
    )


# -----------------------------------------------------------------------------
# Data access
# -----------------------------------------------------------------------------
def get_current_period() -> Optional[SchedulePeriod]:
    data = api_get("getSettings")
    row = data.get("settings")
    if not isinstance(row, dict) or not row.get("schedule_id"):
        return None
    return row_to_period(row)


def save_period(name: str, start_date: date, end_date: date, start_time: time, end_time: time, slot_minutes: int) -> str:
    data = api_post({
        "action": "saveSettings",
        "name": name.strip(),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "start_time": start_time.strftime("%H:%M"),
        "end_time": end_time.strftime("%H:%M"),
        "slot_minutes": int(slot_minutes),
    })
    return str(data["schedule_id"])


def list_members(active_only: bool = True) -> List[Member]:
    data = api_get("getMembers", {"active_only": "true" if active_only else "false"})
    rows = data.get("members", [])
    return [row_to_member(row) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def add_members(rows: Sequence[Dict[str, str]]) -> None:
    api_post({"action": "addMembers", "members": list(rows)})


def save_personal_schedule(schedule_id: str, member_id: str, selected_slots: Sequence[str]) -> None:
    api_post({
        "action": "savePersonalSchedule",
        "schedule_id": schedule_id,
        "member_id": member_id,
        "selected_slots": list(selected_slots),
    })


def get_personal_schedule(schedule_id: str, member_id: str) -> Optional[PersonalSchedule]:
    data = api_get("getMemberSchedule", {"schedule_id": schedule_id, "member_id": member_id})
    row = data.get("schedule")
    return row_to_personal_schedule(row) if isinstance(row, dict) else None


def create_team(schedule_id: str, name: str, songs: str, member_ids: Sequence[str], required_parts: Dict[str, int]) -> str:
    data = api_post({
        "action": "createTeam",
        "schedule_id": schedule_id,
        "name": name.strip(),
        "songs": songs.strip(),
        "member_ids": list(member_ids),
        "required_parts": required_parts,
    })
    return str(data["team_id"])


def delete_team(team_id: str) -> None:
    api_post({"action": "deleteTeam", "team_id": team_id})


def list_teams(schedule_id: Optional[str] = None) -> List[Team]:
    params = {"schedule_id": schedule_id} if schedule_id else None
    data = api_get("getTeams", params)
    rows = data.get("teams", [])
    return [row_to_team(row) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def get_team_bundle(team_id: str) -> Tuple[Optional[Team], List[Member], Dict[str, PersonalSchedule]]:
    data = api_get("getTeamBundle", {"team_id": team_id})
    team_row = data.get("team")
    if not isinstance(team_row, dict):
        return None, [], {}
    team = row_to_team(team_row)
    members_raw = data.get("members", [])
    schedules_raw = data.get("schedules", [])
    members = [row_to_member(r) for r in members_raw if isinstance(r, dict)] if isinstance(members_raw, list) else []
    schedules_list = [row_to_personal_schedule(r) for r in schedules_raw if isinstance(r, dict)] if isinstance(schedules_raw, list) else []
    schedules = {s.member_id: s for s in schedules_list}
    return team, members, schedules


def save_final_schedule(schedule_id: str, team_id: str, start_dt: datetime, end_dt: datetime) -> None:
    api_post({
        "action": "saveFinalSchedule",
        "schedule_id": schedule_id,
        "team_id": team_id,
        "start_dt": start_dt.isoformat(timespec="minutes"),
        "end_dt": end_dt.isoformat(timespec="minutes"),
    })


def list_final_schedules(schedule_id: str) -> List[Dict[str, object]]:
    data = api_get("getFinalSchedules", {"schedule_id": schedule_id})
    rows = data.get("final_schedules", [])
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def delete_final_schedule(final_id: str) -> None:
    api_post({"action": "deleteFinalSchedule", "final_id": final_id})


# -----------------------------------------------------------------------------
# Scheduling logic
# -----------------------------------------------------------------------------
def weekday_kr(d: date) -> str:
    return ["월", "화", "수", "목", "금", "토", "일"][d.weekday()]


def generate_slots(period: SchedulePeriod) -> List[Dict[str, object]]:
    slots: List[Dict[str, object]] = []
    current_date = period.start_date
    while current_date <= period.end_date:
        current = datetime.combine(current_date, period.start_time)
        end_dt = datetime.combine(current_date, period.end_time)
        while current + timedelta(minutes=period.slot_minutes) <= end_dt:
            nxt = current + timedelta(minutes=period.slot_minutes)
            slot_id = current.isoformat(timespec="minutes")
            slots.append({
                "id": slot_id,
                "date": current_date,
                "date_label": f"{current_date.strftime('%m/%d')}({weekday_kr(current_date)})",
                "start": current,
                "end": nxt,
                "time_label": f"{current.strftime('%H:%M')}-{nxt.strftime('%H:%M')}",
            })
            current = nxt
        current_date += timedelta(days=1)
    return slots


def build_slot_maps(period: SchedulePeriod) -> Tuple[List[str], List[str], Dict[Tuple[str, str], str], Dict[str, Dict[str, object]]]:
    date_labels: List[str] = []
    time_labels: List[str] = []
    cell_map: Dict[Tuple[str, str], str] = {}
    by_id: Dict[str, Dict[str, object]] = {}
    for slot in generate_slots(period):
        date_label = str(slot["date_label"])
        time_label = str(slot["time_label"])
        if date_label not in date_labels:
            date_labels.append(date_label)
        if time_label not in time_labels:
            time_labels.append(time_label)
        slot_id = str(slot["id"])
        cell_map[(date_label, time_label)] = slot_id
        by_id[slot_id] = slot
    return date_labels, time_labels, cell_map, by_id


def analyze_team_slots(
    period: SchedulePeriod,
    team: Team,
    members: Sequence[Member],
    schedules: Dict[str, PersonalSchedule],
) -> List[Dict[str, object]]:
    member_by_id = {m.id: m for m in members}
    team_member_ids = [mid for mid in team.member_ids if mid in member_by_id]
    selected_sets: Dict[str, Set[str]] = {
        mid: set(schedules[mid].selected_slots) if mid in schedules else set()
        for mid in team_member_ids
    }

    rows: List[Dict[str, object]] = []
    for slot in generate_slots(period):
        slot_id = str(slot["id"])
        available_ids = [mid for mid in team_member_ids if slot_id in selected_sets[mid]]
        unavailable_ids = [mid for mid in team_member_ids if mid not in available_ids]

        part_counts: Dict[str, int] = {part: 0 for part in PARTS}
        for mid in available_ids:
            part = member_by_id[mid].part
            part_counts[part] = part_counts.get(part, 0) + 1

        missing_parts: List[str] = []
        for part, required in team.required_parts.items():
            shortage = max(0, int(required) - part_counts.get(part, 0))
            if shortage:
                missing_parts.append(f"{part} {shortage}명")

        rows.append({
            "slot_id": slot_id,
            "date": slot["date"],
            "date_label": slot["date_label"],
            "start": slot["start"],
            "end": slot["end"],
            "time_label": slot["time_label"],
            "available_ids": available_ids,
            "unavailable_ids": unavailable_ids,
            "available_count": len(available_ids),
            "total_count": len(team_member_ids),
            "unavailable_names": [member_by_id[mid].name for mid in unavailable_ids],
            "part_ok": not missing_parts,
            "missing_parts": missing_parts,
        })
    return rows


def group_rehearsal_blocks(rows: Sequence[Dict[str, object]], max_absent: int = 0) -> List[Dict[str, object]]:
    """Group consecutive slots only when the same people are unavailable and part status matches."""
    eligible = [
        row for row in rows
        if len(row["unavailable_ids"]) <= max_absent and bool(row["part_ok"])
    ]
    if not eligible:
        return []

    blocks: List[Dict[str, object]] = []
    current: Optional[Dict[str, object]] = None

    for row in eligible:
        signature = tuple(sorted(str(mid) for mid in row["unavailable_ids"]))
        if (
            current
            and current["date"] == row["date"]
            and current["end"] == row["start"]
            and current["signature"] == signature
        ):
            current["end"] = row["end"]
            current["slot_ids"].append(row["slot_id"])
        else:
            if current:
                blocks.append(current)
            current = {
                "date": row["date"],
                "date_label": row["date_label"],
                "start": row["start"],
                "end": row["end"],
                "unavailable_names": list(row["unavailable_names"]),
                "signature": signature,
                "slot_ids": [row["slot_id"]],
                "total_count": row["total_count"],
            }
    if current:
        blocks.append(current)

    for block in blocks:
        minutes = int((block["end"] - block["start"]).total_seconds() // 60)
        block["minutes"] = minutes
        block["label"] = f"{block['start'].strftime('%H:%M')}~{block['end'].strftime('%H:%M')}"
    return blocks


def get_missing_submitters(team: Team, members: Sequence[Member], schedules: Dict[str, PersonalSchedule]) -> List[str]:
    member_by_id = {m.id: m for m in members}
    return [member_by_id[mid].name for mid in team.member_ids if mid in member_by_id and mid not in schedules]


def overlap(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return max(a_start, b_start) < min(a_end, b_end)


# -----------------------------------------------------------------------------
# UI helpers
# -----------------------------------------------------------------------------
def check_setup() -> bool:
    if get_gas_url():
        return True
    st.error("Google Apps Script URL이 설정되지 않았습니다.")
    st.code('GAS_WEB_APP_URL = "https://script.google.com/macros/s/.../exec"')
    return False


def require_admin() -> bool:
    expected = get_admin_pin()
    if not expected:
        st.warning("ADMIN_PIN이 설정되지 않아 현재 임원진 메뉴가 잠금 없이 열려 있습니다.")
        return True
    entered = st.text_input("임원진 PIN", type="password")
    if not entered:
        return False
    if entered != expected:
        st.error("PIN이 올바르지 않습니다.")
        return False
    return True


def show_period_summary(period: SchedulePeriod) -> None:
    st.caption(
        f"{period.name} · {period.start_date.strftime('%Y.%m.%d')} ~ {period.end_date.strftime('%Y.%m.%d')} · "
        f"{period.start_time.strftime('%H:%M')}~{period.end_time.strftime('%H:%M')} · {period.slot_minutes}분 단위"
    )


def render_schedule_picker(period: SchedulePeriod, member: Member, existing: Set[str]) -> List[str]:
    date_labels, time_labels, cell_map, _ = build_slot_maps(period)
    selected: List[str] = []

    mode = st.radio("보기 방식", ["모바일 친화형", "PC 격자형"], horizontal=True)
    if mode == "모바일 친화형":
        for date_label in date_labels:
            day_times = [t for t in time_labels if (date_label, t) in cell_map]
            defaults = [t for t in day_times if cell_map[(date_label, t)] in existing]
            chosen = st.multiselect(
                date_label,
                options=day_times,
                default=defaults,
                key=f"mobile_{period.id}_{member.id}_{date_label}",
            )
            selected.extend(cell_map[(date_label, t)] for t in chosen)
    else:
        chunks = [date_labels[i:i + 5] for i in range(0, len(date_labels), 5)]
        tabs = st.tabs([
            chunk[0] if len(chunk) == 1 else f"{idx + 1}주차: {chunk[0]} ~ {chunk[-1]}"
            for idx, chunk in enumerate(chunks)
        ])
        for tab, chunk in zip(tabs, chunks):
            with tab:
                headers = st.columns([1.4] + [1] * len(chunk))
                headers[0].markdown("**시간**")
                for idx, dlabel in enumerate(chunk, start=1):
                    headers[idx].markdown(f"**{dlabel}**")
                for tlabel in time_labels:
                    cols = st.columns([1.4] + [1] * len(chunk))
                    cols[0].markdown(f"**{tlabel}**")
                    for idx, dlabel in enumerate(chunk, start=1):
                        sid = cell_map.get((dlabel, tlabel))
                        if not sid:
                            cols[idx].write("-")
                            continue
                        checked = cols[idx].checkbox(
                            "가능",
                            value=sid in existing,
                            key=f"grid_{period.id}_{member.id}_{sid}",
                            label_visibility="collapsed",
                        )
                        if checked:
                            selected.append(sid)
    return selected


def render_date_blocks(blocks: Sequence[Dict[str, object]], max_absent: int) -> None:
    if not blocks:
        st.info("조건에 맞는 연속 가능 시간이 없습니다.")
        return
    by_date: Dict[str, List[Dict[str, object]]] = {}
    for block in blocks:
        by_date.setdefault(str(block["date_label"]), []).append(block)

    for date_label, day_blocks in by_date.items():
        st.markdown(f"#### {date_label}")
        for block in day_blocks:
            minutes = int(block["minutes"])
            duration = f"{minutes // 60}시간" if minutes % 60 == 0 else f"{minutes}분"
            unavailable = list(block["unavailable_names"])
            if not unavailable:
                st.success(f"{block['label']} · {duration} · 전원 가능")
            else:
                st.warning(f"{block['label']} · {duration} · 불가능: {', '.join(unavailable)}")


# -----------------------------------------------------------------------------
# Pages
# -----------------------------------------------------------------------------
def page_my_schedule(period: SchedulePeriod) -> None:
    st.header("🙋 내 스케줄 입력")
    show_period_summary(period)

    try:
        members = list_members(active_only=True)
    except Exception as exc:
        st.error(f"부원 명단을 불러오지 못했습니다: {exc}")
        return
    if not members:
        st.info("등록된 부원이 없습니다. 임원진 메뉴에서 부원을 먼저 등록해 주세요.")
        return

    label_to_member = {f"{m.name} · {m.part}": m for m in members}
    choice = st.selectbox("이름", [""] + list(label_to_member.keys()))
    if not choice:
        st.info("본인 이름을 선택하면 기존 스케줄을 불러옵니다.")
        return
    member = label_to_member[choice]

    try:
        existing_schedule = get_personal_schedule(period.id, member.id)
    except Exception as exc:
        st.error(f"기존 스케줄을 불러오지 못했습니다: {exc}")
        return
    existing = set(existing_schedule.selected_slots) if existing_schedule else set()
    if existing_schedule:
        st.caption("기존 제출 내용을 불러왔습니다. 체크를 바꾸고 다시 저장하면 수정됩니다.")

    selected = render_schedule_picker(period, member, existing)
    if st.button("스케줄 저장", type="primary"):
        try:
            save_personal_schedule(period.id, member.id, selected)
        except Exception as exc:
            st.error(f"저장 중 오류가 발생했습니다: {exc}")
            return
        st.success(f"저장되었습니다. 가능한 시간 {len(selected)}개가 선택되어 있습니다.")


def page_team_schedule(period: SchedulePeriod) -> None:
    st.header("🎸 팀별 스케줄")
    show_period_summary(period)
    try:
        teams = list_teams(period.id)
    except Exception as exc:
        st.error(f"팀 목록을 불러오지 못했습니다: {exc}")
        return
    if not teams:
        st.info("아직 생성된 팀이 없습니다.")
        return

    team_labels = {f"{t.name}{' · ' + t.songs if t.songs else ''}": t for t in teams}
    selected_label = st.selectbox("팀 선택", list(team_labels.keys()))
    team = team_labels[selected_label]

    try:
        team, members, schedules = get_team_bundle(team.id)
    except Exception as exc:
        st.error(f"팀 데이터를 불러오지 못했습니다: {exc}")
        return
    if not team:
        st.error("팀 정보를 찾을 수 없습니다.")
        return

    member_by_id = {m.id: m for m in members}
    st.write("**팀원:** " + ", ".join(f"{member_by_id[mid].name}({member_by_id[mid].part})" for mid in team.member_ids if mid in member_by_id))

    missing = get_missing_submitters(team, members, schedules)
    if missing:
        st.warning("개인 스케줄 미제출: " + ", ".join(missing))
    else:
        st.success("모든 팀원이 개인 스케줄을 제출했습니다.")

    rows = analyze_team_slots(period, team, members, schedules)
    max_absent = st.radio(
        "날짜별 가능시간 표시 기준",
        [0, 1],
        format_func=lambda n: "전원 가능만 보기" if n == 0 else "1명 불참까지 포함",
        horizontal=True,
    )
    blocks = group_rehearsal_blocks(rows, max_absent=max_absent)

    st.markdown("### 날짜별 연속 가능시간")
    render_date_blocks(blocks, max_absent)

    st.markdown("### 전체 시간표")
    table_rows = []
    for row in rows:
        table_rows.append({
            "날짜": row["date_label"],
            "시간": row["time_label"],
            "가능 인원": f"{row['available_count']}/{row['total_count']}",
            "불가능": ", ".join(row["unavailable_names"]) or "-",
            "파트": "충족" if row["part_ok"] else "부족",
            "부족 파트": ", ".join(row["missing_parts"]) or "-",
        })
    st.dataframe(table_rows, use_container_width=True, hide_index=True)


def page_admin(period: Optional[SchedulePeriod]) -> None:
    st.header("⚙️ 임원진 관리")
    if not require_admin():
        return

    tab_period, tab_members, tab_teams, tab_final = st.tabs(["일정 기간", "부원 관리", "팀 관리", "합주 확정"])

    with tab_period:
        st.subheader("일정 기간 설정")
        st.caption("새 기간을 저장하면 새 schedule_id가 생성되어 이전 제출과 섞이지 않습니다.")
        with st.form("period_form"):
            name = st.text_input("일정 이름", value=period.name if period else "", placeholder="예: 2026 가을 정기공연")
            col1, col2 = st.columns(2)
            with col1:
                start_date = st.date_input("시작 날짜", value=period.start_date if period else date.today())
                start_time = st.time_input("하루 시작 시간", value=period.start_time if period else time(18, 0), step=1800)
            with col2:
                end_date = st.date_input("종료 날짜", value=period.end_date if period else date.today() + timedelta(days=14))
                end_time = st.time_input("하루 종료 시간", value=period.end_time if period else time(22, 0), step=1800)
            slot_minutes = st.selectbox("시간 단위", [30, 60], index=0 if period and period.slot_minutes == 30 else 1)
            save_clicked = st.form_submit_button("새 일정 기간으로 저장", type="primary")
        if save_clicked:
            if not name.strip():
                st.error("일정 이름을 입력해 주세요.")
            elif end_date < start_date:
                st.error("종료 날짜가 시작 날짜보다 빠릅니다.")
            elif end_time <= start_time:
                st.error("종료 시간은 시작 시간보다 늦어야 합니다.")
            else:
                try:
                    new_id = save_period(name, start_date, end_date, start_time, end_time, slot_minutes)
                    st.success(f"새 일정 기간이 생성되었습니다. schedule_id: {new_id}")
                    st.rerun()
                except Exception as exc:
                    st.error(f"저장 중 오류: {exc}")

    with tab_members:
        st.subheader("부원 등록")
        st.caption("한 줄에 `이름,파트` 형식으로 여러 명을 한꺼번에 등록할 수 있습니다.")
        bulk = st.text_area("부원 명단", placeholder="서영,건반\n민지,보컬\n지훈,드럼")
        if st.button("부원 추가"):
            rows: List[Dict[str, str]] = []
            errors: List[str] = []
            for idx, line in enumerate(bulk.splitlines(), start=1):
                if not line.strip():
                    continue
                pieces = [p.strip() for p in line.split(",", 1)]
                if len(pieces) != 2 or not pieces[0]:
                    errors.append(f"{idx}번째 줄")
                    continue
                part = pieces[1] if pieces[1] in PARTS else "기타/그 외"
                rows.append({"name": pieces[0], "part": part})
            if errors:
                st.error("형식을 확인해 주세요: " + ", ".join(errors))
            elif not rows:
                st.error("추가할 부원을 입력해 주세요.")
            else:
                try:
                    add_members(rows)
                    st.success(f"{len(rows)}명을 추가했습니다.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"부원 추가 중 오류: {exc}")

        try:
            all_members = list_members(active_only=False)
            st.dataframe([
                {"이름": m.name, "파트": m.part, "활성": m.active, "member_id": m.id}
                for m in all_members
            ], use_container_width=True, hide_index=True)
        except Exception as exc:
            st.error(f"부원 목록을 불러오지 못했습니다: {exc}")

    with tab_teams:
        if not period:
            st.info("먼저 일정 기간을 설정해 주세요.")
        else:
            st.subheader("팀 생성")
            try:
                members = list_members(active_only=True)
            except Exception as exc:
                st.error(f"부원 목록을 불러오지 못했습니다: {exc}")
                members = []
            label_to_id = {f"{m.name} · {m.part}": m.id for m in members}
            with st.form("team_form"):
                team_name = st.text_input("팀명", placeholder="예: Ditto")
                songs = st.text_area("곡명", placeholder="여러 곡이면 줄바꿈")
                selected_member_labels = st.multiselect("팀원 선택", list(label_to_id.keys()))
                st.markdown("#### 필수 파트 조건")
                required_parts: Dict[str, int] = {}
                cols = st.columns(4)
                for idx, part in enumerate(PARTS):
                    with cols[idx % 4]:
                        required_parts[part] = st.number_input(
                            part,
                            min_value=0,
                            max_value=5,
                            value=DEFAULT_REQUIRED_PARTS.get(part, 0),
                            step=1,
                            key=f"req_{part}",
                        )
                create_clicked = st.form_submit_button("팀 생성", type="primary")
            if create_clicked:
                member_ids = [label_to_id[label] for label in selected_member_labels]
                if not team_name.strip():
                    st.error("팀명을 입력해 주세요.")
                elif not member_ids:
                    st.error("팀원을 한 명 이상 선택해 주세요.")
                else:
                    try:
                        team_id = create_team(period.id, team_name, songs, member_ids, required_parts)
                        st.success(f"팀이 생성되었습니다. team_id: {team_id}")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"팀 생성 중 오류: {exc}")

            st.subheader("현재 팀")
            try:
                teams = list_teams(period.id)
                for t in teams:
                    with st.container(border=True):
                        col_a, col_b = st.columns([5, 1])
                        col_a.markdown(f"**{t.name}**" + (f" · {t.songs}" if t.songs else ""))
                        if col_b.button("삭제", key=f"delete_team_{t.id}"):
                            delete_team(t.id)
                            st.rerun()
            except Exception as exc:
                st.error(f"팀 목록 오류: {exc}")

    with tab_final:
        if not period:
            st.info("먼저 일정 기간을 설정해 주세요.")
        else:
            st.subheader("합주시간 확정")
            try:
                teams = list_teams(period.id)
            except Exception as exc:
                st.error(f"팀 목록 오류: {exc}")
                teams = []

            if teams:
                team_labels = {t.name: t for t in teams}
                team_name = st.selectbox("팀", list(team_labels.keys()), key="final_team")
                selected_team = team_labels[team_name]
                try:
                    t, members, schedules = get_team_bundle(selected_team.id)
                except Exception as exc:
                    st.error(f"팀 데이터 오류: {exc}")
                    t, members, schedules = None, [], {}
                if t:
                    rows = analyze_team_slots(period, t, members, schedules)
                    blocks = group_rehearsal_blocks(rows, max_absent=0)
                    if blocks:
                        block_labels = {
                            f"{b['date_label']} {b['label']} ({int(b['minutes']) // 60}시간 {int(b['minutes']) % 60}분)": b
                            for b in blocks
                        }
                        chosen_label = st.selectbox("전원 가능한 연속 시간", list(block_labels.keys()))
                        b = block_labels[chosen_label]
                        if st.button("이 시간으로 합주 확정", type="primary"):
                            try:
                                save_final_schedule(period.id, t.id, b["start"], b["end"])
                                st.success("합주시간을 확정했습니다.")
                                st.rerun()
                            except Exception as exc:
                                st.error(f"확정 저장 오류: {exc}")
                    else:
                        st.info("전원이 가능한 연속 시간이 없습니다.")

            st.subheader("확정된 전체 합주표")
            try:
                finals = list_final_schedules(period.id)
                team_by_id = {t.id: t for t in teams}
                final_rows = []
                for f in finals:
                    start_dt = datetime.fromisoformat(str(f["start_dt"]))
                    end_dt = datetime.fromisoformat(str(f["end_dt"]))
                    team_obj = team_by_id.get(str(f["team_id"]))
                    final_rows.append({
                        "final_id": str(f.get("final_id", "")),
                        "날짜": f"{start_dt.strftime('%m/%d')}({weekday_kr(start_dt.date())})",
                        "시간": f"{start_dt.strftime('%H:%M')}~{end_dt.strftime('%H:%M')}",
                        "팀": team_obj.name if team_obj else str(f["team_id"]),
                        "team_id": str(f["team_id"]),
                        "start": start_dt,
                        "end": end_dt,
                    })
                st.dataframe([
                    {"날짜": r["날짜"], "시간": r["시간"], "팀": r["팀"]}
                    for r in final_rows
                ], use_container_width=True, hide_index=True)

                # member-level conflict detection
                conflicts: List[str] = []
                member_names = {m.id: m.name for m in list_members(active_only=False)}
                for i in range(len(final_rows)):
                    for j in range(i + 1, len(final_rows)):
                        a, b = final_rows[i], final_rows[j]
                        if not overlap(a["start"], a["end"], b["start"], b["end"]):
                            continue
                        ta = team_by_id.get(a["team_id"])
                        tb = team_by_id.get(b["team_id"])
                        if not ta or not tb:
                            continue
                        shared = set(ta.member_ids) & set(tb.member_ids)
                        for mid in shared:
                            conflicts.append(f"{member_names.get(mid, mid)}: {a['팀']} ↔ {b['팀']} ({a['날짜']})")
                if conflicts:
                    st.error("합주시간 중복이 있습니다.\n\n" + "\n\n".join(f"• {c}" for c in conflicts))
                elif final_rows:
                    st.success("현재 확정 합주 간 팀원 시간 중복이 없습니다.")
            except Exception as exc:
                st.error(f"확정 합주표를 불러오지 못했습니다: {exc}")


def main() -> None:
    st.set_page_config(page_title="밴드부 합주 일정", page_icon="🎸", layout="wide")
    st.title("🎸 밴드부 합주 일정")
    st.caption("개인 스케줄을 한 번 입력하고, 팀별 가능시간을 자동으로 계산합니다.")

    if not check_setup():
        return

    try:
        period = get_current_period()
    except Exception as exc:
        st.error(f"현재 일정 기간을 불러오지 못했습니다: {exc}")
        return

    menu = st.sidebar.radio("메뉴", ["내 스케줄 입력", "팀별 스케줄", "임원진 관리"])
    st.sidebar.divider()
    if period:
        st.sidebar.caption(f"현재 일정: {period.name}")
    else:
        st.sidebar.warning("현재 일정 기간이 없습니다.")

    if menu == "임원진 관리":
        page_admin(period)
        return

    if not period:
        st.info("임원진이 먼저 일정 기간을 설정해야 합니다.")
        return

    if menu == "내 스케줄 입력":
        page_my_schedule(period)
    elif menu == "팀별 스케줄":
        page_team_schedule(period)


if __name__ == "__main__":
    main()
